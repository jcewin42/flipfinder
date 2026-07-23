"""
Time-on-market tracking via periodic lifecycle rechecks.

Earlier version of this inferred "delisted" from a listing's absence in
search results. That doesn't hold up on SociaVault: search only reliably
returns the first page or so of results per query, FB's own ranking mixes
in older "suggested" listings unpredictably (a last-24h query can surface
week-old listings you simply hadn't seen yet), and the status/listed_at
fields search returns are confirmed unreliable (always null in testing).

So instead: only listings that pass stage 1 get registered for tracking
(register_for_tracking) -- that's a much smaller set than raw search hits,
which is what makes periodic get_detail()-based rechecks (via
SourceAdapter.check_still_active(), which costs the same as a detail call)
affordable. Each check schedules the next one with backoff (checking daily
is precise enough for "did this take hours or weeks to sell"; checking
every 45-minute poll would waste API calls for no real precision gain), and
tracking stops entirely after max_tracking_days -- a listing still up that
long is probably stale and not worth further spend either way.

This still doesn't distinguish "sold" from "removed for some other reason"
(seller changed their mind, a scam listing got taken down, etc.) -- it's a
rough proxy on purpose. Worth revisiting once you have enough volume to
notice if the proxy is misleading you.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone
from typing import Optional

from flipfinder.db import Database
from flipfinder.sources.base import SourceAdapter

logger = logging.getLogger("flipfinder.market_stats")


def register_for_tracking(
    db: Database, listing_id: str, source: str, category_id: str, price: Optional[float],
    now: str, first_check_delay_days: float = 1.0,
) -> None:
    """Call once, right after a listing passes stage 1 (and ideally gets an
    estimate) -- NOT for every raw search hit, to keep recheck volume small."""
    db.register_for_tracking(listing_id, source, category_id, price, now, first_check_delay_days)


def run_due_lifecycle_checks(
    db: Database,
    source_adapter: SourceAdapter,
    category_id: str,
    now: Optional[str] = None,
    max_checks: int = 10,
    backoff_days: Optional[list[float]] = None,
    max_tracking_days: float = 45.0,
) -> dict:
    """
    Call once per poll cycle. Picks up to max_checks listings whose next
    scheduled recheck is due, calls source_adapter.check_still_active() on
    each, and updates their tracking state. Returns {"checked": n, "delisted": n}.
    """
    now = now or datetime.now(timezone.utc).isoformat()
    backoff_days = backoff_days or [1.0, 2.0, 4.0, 7.0, 14.0]

    due = db.get_due_lifecycle_checks(category_id, source_adapter.name, now, max_checks)
    delisted = 0
    for row in due:
        try:
            still_active = source_adapter.check_still_active(row["listing_id"])
        except Exception:  # noqa: BLE001 -- one bad check shouldn't break the whole batch
            logger.exception("check_still_active failed for %s", row["listing_id"])
            still_active = None

        db.record_lifecycle_check(
            row["listing_id"], source_adapter.name, still_active, now, backoff_days, max_tracking_days,
        )
        if still_active is False:
            delisted += 1

    return {"checked": len(due), "delisted": delisted}


def get_time_on_market_stats(
    db: Database, category_id: str, price: Optional[float], price_band_pct: float = 0.25,
    min_sample: int = 5,
) -> Optional[dict]:
    """
    Median days-on-market for recently delisted listings in this category
    priced within +/- price_band_pct of `price`. Returns None if there
    isn't enough data yet -- that's expected for a while after setup, and
    the category profile should just omit this context from the prompt
    when it's None rather than presenting an unreliable stat as fact.
    """
    if price is None:
        return None
    low, high = price * (1 - price_band_pct), price * (1 + price_band_pct)
    rows = db.get_delisted_in_price_range(category_id, low, high)
    if len(rows) < min_sample:
        return None

    days = []
    for r in rows:
        first_checked = datetime.fromisoformat(r["first_checked_at"])
        delisted = datetime.fromisoformat(r["delisted_at"])
        days.append(max(0.0, (delisted - first_checked).total_seconds() / 86400))

    return {
        "sample_size": len(days),
        "median_days_on_market": round(statistics.median(days), 1),
        "price_range": (round(low), round(high)),
    }
