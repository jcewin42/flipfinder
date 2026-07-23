"""
Discord bot for two-way notification: posts new flip alerts, and lets you
give feedback (actual repair cost / actual sale price) by replying directly
to the alert message, or via an explicit /feedback slash command as a
fallback when you don't have the original message handy.

A webhook can't do this -- webhooks are post-only. This needs a real bot
with the message content intent enabled so it can read your replies.

Reply parsing is intentionally simple (regex over a few keyword patterns),
not NLP. Natural phrasing like "spent 40 on a carb kit, sold for 380" works;
anything odder should fall back to /feedback with explicit fields.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from flipfinder.db import Database
from flipfinder.models import FeedbackEntry, ListingDetail, Offer, ValuationEstimate
from flipfinder.pipeline.feedback_store import FeedbackStore

logger = logging.getLogger("flipfinder.discord")

COST_PATTERN = re.compile(r"(?:spent|cost|paid|repair(?:ed)?)\D{0,10}\$?(\d+(?:\.\d+)?)", re.IGNORECASE)
SALE_PATTERN = re.compile(r"(?:sold|sale|got|flipped)\D{0,10}\$?(\d+(?:\.\d+)?)", re.IGNORECASE)


def parse_casual_feedback(text: str) -> dict:
    """Best-effort extraction of $ amounts from a casual reply. Returns a
    dict with whichever of actual_repair_cost / actual_resale_value it
    could find (missing keys are omitted, not None -- caller decides
    defaults)."""
    out = {}
    cost_match = COST_PATTERN.search(text)
    sale_match = SALE_PATTERN.search(text)
    if cost_match:
        out["actual_repair_cost"] = float(cost_match.group(1))
    if sale_match:
        out["actual_resale_value"] = float(sale_match.group(1))
    return out


def build_alert_embed(detail: ListingDetail, estimate: ValuationEstimate, offer: Offer) -> discord.Embed:
    if offer.estimated_hourly_rate >= 40:
        color = discord.Color.green()
    elif offer.estimated_hourly_rate >= 0:
        color = discord.Color.orange()
    else:
        color = discord.Color.red()

    embed = discord.Embed(
        title=detail.title[:256],
        url=detail.url,
        description=estimate.reasoning[:2000] if estimate.reasoning else None,
        color=color,
    )
    embed.add_field(name="Asking price", value=f"${detail.price:,.0f}" if detail.price else "n/a")
    if estimate.estimated_item_count > 1:
        embed.add_field(name="Units in this listing", value=str(estimate.estimated_item_count))
    embed.add_field(name="Est. resale value", value=f"${estimate.estimated_resale_value:,.0f}" + (" (total)" if estimate.estimated_item_count > 1 else ""))
    embed.add_field(name="Est. extra repair", value=f"${estimate.estimated_repair_cost:,.0f}" + (" (total)" if estimate.estimated_item_count > 1 else ""))
    embed.add_field(name="Suggested max offer", value=f"${offer.max_offer:,.0f}")
    embed.add_field(name="Profit at asking", value=f"${offer.profit_if_bought_at_asking:,.0f}")
    embed.add_field(name="Est. $/hour", value=f"${offer.estimated_hourly_rate:,.0f}/hr")
    peak = f"{offer.pickup_travel_hours_peak:.1f}h" if offer.pickup_travel_hours_peak is not None else "unknown"
    offpeak = f"{offer.pickup_travel_hours_offpeak:.1f}h" if offer.pickup_travel_hours_offpeak is not None else "unknown"
    embed.add_field(name="Pickup (peak traffic)", value=peak, inline=True)
    embed.add_field(name="Pickup (off-peak)", value=offpeak, inline=True)
    embed.add_field(name="Routing", value="real traffic-aware" if offer.traffic_aware else "straight-line estimate", inline=True)
    embed.add_field(name="Service time (est.)", value=f"{offer.service_hours:.1f}h", inline=True)
    embed.add_field(name="Total time (used)", value=f"{offer.total_time_hours:.1f}h", inline=True)
    embed.add_field(name="Confidence", value=f"{estimate.confidence:.0%}", inline=True)
    if detail.photos:
        embed.set_thumbnail(url=detail.photos[0].url)
    embed.set_footer(text=f"Reply to this message with actual cost/sale price once you know it. Listing id: {detail.id}")
    return embed


class FlipFinderBot(commands.Bot):
    def __init__(self, db: Database, feedback_store: FeedbackStore, channel_id: int, **kwargs):
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!", intents=intents, **kwargs)
        self.db = db
        self.feedback_store = feedback_store
        self.channel_id = channel_id

    async def setup_hook(self) -> None:
        self.tree.add_command(feedback_command)
        await self.tree.sync()

    async def on_ready(self) -> None:
        logger.info("Discord bot logged in as %s", self.user)

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        await self.process_commands(message)

        if message.reference is None:
            return
        ref_id = str(message.reference.message_id)
        mapping = self.db.get_listing_id_for_message(ref_id)
        if mapping is None:
            return
        listing_id, source = mapping

        parsed = parse_casual_feedback(message.content)
        if not parsed:
            await message.reply(
                "I couldn't find a dollar amount in that -- try e.g. \"spent $40, sold for $380\", "
                "or use /feedback for explicit fields."
            )
            return

        self._record_feedback(listing_id, parsed, notes=message.content)
        summary = ", ".join(f"{k.replace('actual_', '').replace('_', ' ')}: ${v:.0f}" for k, v in parsed.items())
        await message.add_reaction("✅")
        await message.reply(f"Got it -- logged {summary} for this listing. Thanks, this helps future estimates.")

    def _record_feedback(self, listing_id: str, parsed: dict, notes: str = "") -> None:
        estimate_row = self.db.get_estimate_by_listing_id(listing_id)
        category_id = estimate_row["category_id"] if estimate_row else "unknown"
        predicted_repair = estimate_row["estimated_repair_cost"] if estimate_row else None
        predicted_resale = estimate_row["estimated_resale_value"] if estimate_row else None
        features = estimate_row["features"] if estimate_row else {}

        entry = FeedbackEntry(
            listing_id=listing_id,
            category_id=category_id,
            features=features,
            predicted_repair_cost=predicted_repair,
            predicted_resale_value=predicted_resale,
            actual_repair_cost=parsed.get("actual_repair_cost"),
            actual_resale_value=parsed.get("actual_resale_value"),
            was_purchased=True,
            notes=notes,
        )
        self.feedback_store.record(entry)

    async def send_alert(self, detail: ListingDetail, estimate: ValuationEstimate, offer: Offer) -> None:
        channel = self.get_channel(self.channel_id) or await self.fetch_channel(self.channel_id)
        embed = build_alert_embed(detail, estimate, offer)
        message = await channel.send(embed=embed)
        self.db.record_discord_alert(str(message.id), detail.id, detail.source)


@app_commands.command(name="feedback", description="Log actual repair cost and/or sale price for a listing")
@app_commands.describe(
    listing_id="The listing id from the alert message footer",
    actual_repair_cost="What you actually spent on extra repairs (beyond standard service), in USD",
    actual_resale_value="What you actually sold it for, in USD",
    notes="Anything else worth remembering about this one",
)
async def feedback_command(
    interaction: discord.Interaction,
    listing_id: str,
    actual_repair_cost: Optional[float] = None,
    actual_resale_value: Optional[float] = None,
    notes: Optional[str] = "",
) -> None:
    bot: FlipFinderBot = interaction.client  # type: ignore[assignment]
    parsed = {}
    if actual_repair_cost is not None:
        parsed["actual_repair_cost"] = actual_repair_cost
    if actual_resale_value is not None:
        parsed["actual_resale_value"] = actual_resale_value

    if not parsed:
        await interaction.response.send_message("Provide at least one of actual_repair_cost or actual_resale_value.", ephemeral=True)
        return

    bot._record_feedback(listing_id, parsed, notes=notes or "")
    await interaction.response.send_message(f"Logged feedback for listing {listing_id}. Thanks!", ephemeral=True)
