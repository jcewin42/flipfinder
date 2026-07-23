"""
Discord bot for two-way notification: posts new flip alerts, and lets you
give feedback (actual repair cost, actual sale price, a corrected unit
count, condition at sale) by replying directly to the alert message, or via
an explicit /feedback slash command as a fallback when casual phrasing
doesn't parse or you don't have the original message handy.

A webhook can't do this -- webhooks are post-only. This needs a real bot
with the message content intent enabled so it can read your replies.

Reply parsing itself lives in flipfinder/notifier/reply_parsing.py (kept
free of discord.py so it's unit-testable) -- this file just wires it up to
Discord messages and slash commands.
"""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from flipfinder.db import Database
from flipfinder.models import FeedbackEntry, ListingDetail, Offer, ValuationEstimate
from flipfinder.notifier.reply_parsing import (
    parse_casual_feedback,
    parse_condition_at_sale,
    parse_item_count_correction,
)
from flipfinder.pipeline.feedback_store import FeedbackStore

logger = logging.getLogger("flipfinder.discord")


def build_alert_embed(
    detail: ListingDetail, estimate: ValuationEstimate, offer: Offer, needs_confirmation: bool = False,
) -> discord.Embed:
    if needs_confirmation:
        color = discord.Color.gold()   # distinct from the economics-based colors below -- this is "please check", not "good/bad deal"
    elif offer.estimated_hourly_rate >= 40:
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

    if needs_confirmation:
        embed.add_field(
            name="⚠️ Confirm unit count",
            value=(
                f"Not sure if this is {estimate.estimated_item_count} unit(s) "
                f"({estimate.item_count_confidence:.0%} confident) -- numbers below assume "
                f"{estimate.estimated_item_count}. Reply with the actual count if you check it out "
                f"(e.g. \"actually there's 2\") or use /feedback."
            ),
            inline=False,
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
        embed.set_image(url=detail.photos[0].url)   # larger/more prominent than a thumbnail -- worth actually seeing
    embed.set_footer(
        text=f"Reply with actual cost/sale price/unit count once you know it. Listing id: {detail.id}"
    )
    return embed


def build_extra_photo_embeds(detail: ListingDetail, max_extra: int = 2) -> list[discord.Embed]:
    """
    Discord shows one embed's image prominently; additional photos need
    their own (minimal) embeds to appear in the same message. Returns up to
    max_extra extra embeds, each just an image tied back to the listing URL
    so they read as "more photos of this" rather than separate messages.
    """
    extras = []
    for photo in detail.photos[1:1 + max_extra]:
        extra = discord.Embed(url=detail.url)
        extra.set_image(url=photo.url)
        extras.append(extra)
    return extras


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
        item_count_correction = parse_item_count_correction(message.content)
        if item_count_correction is not None:
            parsed["actual_item_count"] = item_count_correction
        condition = parse_condition_at_sale(message.content)
        if condition is not None:
            parsed["condition_at_sale"] = condition

        if not parsed:
            await message.reply(
                "I couldn't parse that -- try e.g. \"spent $40, sold for $380\", "
                "\"actually there's 2\", \"as-is, didn't service it\", or use /feedback for explicit fields."
            )
            return

        self._record_feedback(listing_id, source, parsed, notes=message.content)
        summary_parts = []
        for key in ("actual_repair_cost", "actual_resale_value"):
            if key in parsed:
                summary_parts.append(f"{key.replace('actual_', '').replace('_', ' ')}: ${parsed[key]:.0f}")
        if "actual_item_count" in parsed:
            summary_parts.append(f"unit count: {parsed['actual_item_count']}")
        if "condition_at_sale" in parsed:
            summary_parts.append(f"condition at sale: {parsed['condition_at_sale']}")
        summary = ", ".join(summary_parts)
        await message.add_reaction("✅")
        await message.reply(f"Got it -- logged {summary} for this listing. Thanks, this helps future estimates.")

    def _record_feedback(self, listing_id: str, source: str, parsed: dict, notes: str = "") -> None:
        estimate_row = self.db.get_estimate_by_listing_id(listing_id)
        category_id = estimate_row["category_id"] if estimate_row else "unknown"
        source = estimate_row["source"] if estimate_row else source
        predicted_repair = estimate_row["estimated_repair_cost"] if estimate_row else None
        predicted_resale = estimate_row["estimated_resale_value"] if estimate_row else None
        predicted_item_count = estimate_row["estimated_item_count"] if estimate_row else None
        features = estimate_row["features"] if estimate_row else {}

        actual_repair_cost = parsed.get("actual_repair_cost")
        actual_resale_value = parsed.get("actual_resale_value")
        # A real $ outcome implies a purchase happened; a bare item-count
        # confirmation doesn't necessarily mean you bought it, so don't
        # infer was_purchased from that alone.
        was_purchased = True if (actual_repair_cost is not None or actual_resale_value is not None) else None

        entry = FeedbackEntry(
            listing_id=listing_id,
            source=source,
            category_id=category_id,
            features=features,
            predicted_repair_cost=predicted_repair,
            predicted_resale_value=predicted_resale,
            actual_repair_cost=actual_repair_cost,
            actual_resale_value=actual_resale_value,
            was_purchased=was_purchased,
            predicted_item_count=predicted_item_count,
            actual_item_count=parsed.get("actual_item_count"),
            condition_at_sale=parsed.get("condition_at_sale"),
            notes=notes,
        )
        self.feedback_store.record(entry)

    async def send_alert(
        self, detail: ListingDetail, estimate: ValuationEstimate, offer: Offer, needs_confirmation: bool = False,
    ) -> None:
        channel = self.get_channel(self.channel_id) or await self.fetch_channel(self.channel_id)
        embed = build_alert_embed(detail, estimate, offer, needs_confirmation)
        extra_embeds = build_extra_photo_embeds(detail)
        message = await channel.send(embeds=[embed, *extra_embeds])
        self.db.record_discord_alert(str(message.id), detail.id, detail.source)


@app_commands.command(name="feedback", description="Log actual outcome data for a listing")
@app_commands.describe(
    listing_id="The listing id from the alert message footer",
    actual_repair_cost="What you actually spent on extra repairs (beyond standard service), in USD",
    actual_resale_value="What you actually sold it for, in USD",
    actual_item_count="The actual number of motors, if different from what was estimated",
    condition_at_sale="Condition when resold, e.g. 'serviced and running', 'as-is, not serviced', 'parts only'",
    notes="Anything else worth remembering about this one",
)
async def feedback_command(
    interaction: discord.Interaction,
    listing_id: str,
    actual_repair_cost: Optional[float] = None,
    actual_resale_value: Optional[float] = None,
    actual_item_count: Optional[int] = None,
    condition_at_sale: Optional[str] = None,
    notes: Optional[str] = "",
) -> None:
    bot: FlipFinderBot = interaction.client  # type: ignore[assignment]
    parsed = {}
    if actual_repair_cost is not None:
        parsed["actual_repair_cost"] = actual_repair_cost
    if actual_resale_value is not None:
        parsed["actual_resale_value"] = actual_resale_value
    if actual_item_count is not None:
        parsed["actual_item_count"] = actual_item_count
    if condition_at_sale is not None:
        parsed["condition_at_sale"] = condition_at_sale

    if not parsed:
        await interaction.response.send_message(
            "Provide at least one of actual_repair_cost, actual_resale_value, actual_item_count, or condition_at_sale.",
            ephemeral=True,
        )
        return

    # source isn't known here without a DB lookup the estimate row already
    # provides -- _record_feedback resolves it from the stored estimate.
    bot._record_feedback(listing_id, source="unknown", parsed=parsed, notes=notes or "")
    await interaction.response.send_message(f"Logged feedback for listing {listing_id}. Thanks!", ephemeral=True)
