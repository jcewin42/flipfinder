"""
Prints alerts to stdout instead of Discord. Duck-types FlipFinderBot's
send_alert() so main.py's one-shot/dry-run mode can exercise the whole
pipeline (scheduler-free, single poll cycle) without needing a bot token,
a Discord server, or network access to Discord at all -- useful for
tuning stage 1 filters, category prompts, or offer math against real
listings before trusting the live bot with them.
"""
from __future__ import annotations

from flipfinder.models import ListingDetail, Offer, ValuationEstimate


class ConsoleNotifier:
    async def send_alert(self, detail: ListingDetail, estimate: ValuationEstimate, offer: Offer) -> None:
        peak = f"{offer.pickup_travel_hours_peak:.1f}h" if offer.pickup_travel_hours_peak is not None else "unknown"
        offpeak = f"{offer.pickup_travel_hours_offpeak:.1f}h" if offer.pickup_travel_hours_offpeak is not None else "unknown"
        routing = "real traffic-aware" if offer.traffic_aware else "straight-line estimate"
        print("=" * 70)
        print(f"{detail.title}  (id={detail.id})")
        print(f"  {detail.url}")
        if estimate.estimated_item_count > 1:
            print(f"  *** listing includes ~{estimate.estimated_item_count} motors -- figures below are TOTALS ***")
        print(f"  asking: ${detail.price}   est. resale: ${estimate.estimated_resale_value:,.0f}")
        print(f"  est. extra repair: ${estimate.estimated_repair_cost:,.0f} / {estimate.estimated_repair_hours:.1f}h")
        print(f"  suggested max offer: ${offer.max_offer:,.0f}")
        print(f"  profit at asking: ${offer.profit_if_bought_at_asking:,.0f}")
        print(f"  pickup ({routing}): peak {peak} / off-peak {offpeak}")
        print(f"  time: pickup {offer.pickup_travel_hours}h + service {offer.service_hours:.1f}h = total {offer.total_time_hours:.1f}h")
        print(f"  est. $/hour: ${offer.estimated_hourly_rate:,.0f}/hr   confidence: {estimate.confidence:.0%}")
        print(f"  reasoning: {estimate.reasoning}")
        print("=" * 70)
