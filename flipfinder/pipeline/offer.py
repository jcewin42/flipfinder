"""
Turns a ValuationEstimate into a concrete "offer this much" number and a
concrete $/hour figure -- what you're actually optimizing for, rather than
an arbitrary 0-100 score. The same profit margin looks very different at
30 minutes round-trip versus 2 hours, and the hourly rate is what naturally
captures that instead of needing a separate "is this too far" judgment call.

Multi-unit listings (a single listing selling several motors at once) are
handled here, not with special-case logic: base_service_cost/hours are
PER-UNIT fixed costs, so they get multiplied by estimate.estimated_item_count,
while travel time is charged exactly once regardless of unit count (it's one
stop either way). That's the whole mechanism that lets a 3-motor lot
tolerate a much longer drive than any single motor would -- the hourly rate
naturally reflects it without a separate multi-unit rule.

travel_time_basis picks which of peak/offpeak/average feeds the actual
hourly-rate math (default "peak" -- the conservative choice: if it's still a
good rate assuming rush-hour traffic, off-peak can only look better). Both
figures are still carried on the Offer for display either way.

target_profit supports both a flat dollar floor and a percentage of resale
value -- whichever is larger applies, and both are WHOLE-DEAL thresholds
(not per-unit) -- "$75 minimum profit to bother with this trip at all",
not "$75 per motor".

Unlike the old flip_score, estimated_hourly_rate is NOT clamped at zero --
a bad deal can show a negative $/hour, which is real information ("you'd
lose money and time on this one"), not something to hide.
"""
from __future__ import annotations

from flipfinder.models import ListingDetail, Offer, ValuationEstimate
from flipfinder.routing.base import RoundTripEstimate

MIN_TIME_HOURS = 0.1  # floor to avoid divide-by-zero on a hypothetical zero-effort flip


def _pick_travel_hours(travel: RoundTripEstimate, basis: str) -> float | None:
    if basis == "offpeak":
        return travel.offpeak_hours if travel.offpeak_hours is not None else travel.peak_hours
    if basis == "average":
        vals = [h for h in (travel.peak_hours, travel.offpeak_hours) if h is not None]
        return sum(vals) / len(vals) if vals else None
    # default: "peak" -- conservative
    return travel.peak_hours if travel.peak_hours is not None else travel.offpeak_hours


def compute_offer(
    detail: ListingDetail,
    estimate: ValuationEstimate,
    base_service_cost: float,
    base_service_hours: float,
    travel: RoundTripEstimate,
    travel_time_basis: str = "peak",
    selling_overhead_hours: float = 0.5,
    min_profit_flat: float = 75.0,
    min_profit_pct: float = 0.20,
) -> Offer:
    item_count = max(1, estimate.estimated_item_count)
    total_service_cost = base_service_cost * item_count
    total_cost = total_service_cost + estimate.estimated_repair_cost
    target_profit = max(min_profit_flat, min_profit_pct * estimate.estimated_resale_value)

    theoretical_max_offer = estimate.estimated_resale_value - total_cost - target_profit
    # Never suggest offering more than the asking price.
    asking_price = detail.price if detail.price is not None else theoretical_max_offer
    max_offer = max(0.0, min(theoretical_max_offer, asking_price))

    profit_if_bought_at_asking = estimate.estimated_resale_value - total_cost - asking_price

    pickup_travel_hours = _pick_travel_hours(travel, travel_time_basis)
    # base_service_hours is per unit (mirrors base_service_cost); estimated_repair_hours
    # from the AI is already a TOTAL across all units (see the valuation prompt).
    service_hours = (base_service_hours * item_count) + estimate.estimated_repair_hours
    # Travel happens once per trip regardless of how many units the listing includes --
    # that's what makes a multi-unit lot able to justify more distance than a single motor.
    total_time_hours = max(MIN_TIME_HOURS, (pickup_travel_hours or 0.0) + service_hours + selling_overhead_hours)
    estimated_hourly_rate = profit_if_bought_at_asking / total_time_hours

    return Offer(
        max_offer=round(max_offer, 2),
        total_cost=round(total_cost, 2),
        target_profit=round(target_profit, 2),
        profit_if_bought_at_asking=round(profit_if_bought_at_asking, 2),
        pickup_travel_hours=round(pickup_travel_hours, 2) if pickup_travel_hours is not None else None,
        pickup_travel_hours_peak=round(travel.peak_hours, 2) if travel.peak_hours is not None else None,
        pickup_travel_hours_offpeak=round(travel.offpeak_hours, 2) if travel.offpeak_hours is not None else None,
        traffic_aware=travel.traffic_aware,
        service_hours=round(service_hours, 2),
        total_time_hours=round(total_time_hours, 2),
        estimated_hourly_rate=round(estimated_hourly_rate, 2),
    )
