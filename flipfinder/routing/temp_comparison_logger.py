"""
TEMPORARY diagnostic code -- delete this file and its call sites in main.py
(search for "TEMP-COMPARISON") once you've collected enough side-by-side
data to trust (or distrust) the haversine estimate and no longer need this.

Logs haversine vs Google Routes results for the SAME listing, independent of
which one is actually configured as routing.backend (the "active" backend
driving real decisions is untouched by this -- this is purely diagnostic
logging bolted on next to it).

Enable with routing.log_comparison: true in config.yaml. Requires
routing.google_routes.api_key to be set even if routing.backend: haversine
is what's actually driving decisions.

COST WARNING: if your active backend is haversine (free) but you turn this
on, you WILL start incurring real Google Routes API calls (and consuming
its free monthly quota) purely to produce these comparison log lines --
that's the whole point, but it's not free once enabled. See main.py for
where the resulting api_calls get folded into the routing_calls_made count
that lands in poll_log, so at least this doesn't blindside your usage
tracking.
"""
from __future__ import annotations

import logging
from typing import Optional

from flipfinder.routing.google_routes import GoogleRoutesBackend
from flipfinder.routing.haversine import HaversineRoutingBackend

logger = logging.getLogger("flipfinder.routing.temp_comparison")

_google: Optional[GoogleRoutesBackend] = None
_haversine: Optional[HaversineRoutingBackend] = None


def log_comparison(
    config: dict,
    listing_title: str,
    origin_lat: float,
    origin_lon: float,
    dest_lat: Optional[float],
    dest_lon: Optional[float],
) -> int:
    """Logs a side-by-side comparison line. Returns the number of real
    Google API calls made (0 if skipped/unavailable), so the caller can
    fold it into routing_calls_made for accurate cost tracking."""
    global _google, _haversine

    if dest_lat is None or dest_lon is None:
        return 0

    routing_cfg = config.get("routing", {})
    google_cfg = routing_cfg.get("google_routes") or {}
    if not google_cfg.get("api_key"):
        logger.warning(
            "routing.log_comparison is enabled but routing.google_routes.api_key isn't set -- skipping comparison"
        )
        return 0

    if _google is None:
        _google = GoogleRoutesBackend(**google_cfg)
    if _haversine is None:
        avg_speed = routing_cfg.get("haversine", {}).get("avg_speed_kmh", 50.0)
        _haversine = HaversineRoutingBackend(avg_speed_kmh=avg_speed)

    haversine_est = _haversine.estimate_round_trip(origin_lat, origin_lon, dest_lat, dest_lon)
    google_est = _google.estimate_round_trip(origin_lat, origin_lon, dest_lat, dest_lon)

    def fmt(hours: Optional[float]) -> str:
        return f"{hours:.2f}h" if hours is not None else "n/a"

    logger.info(
        "[TEMP-COMPARISON] %s | haversine: dist=%.1fkm peak=%s offpeak=%s | "
        "google: dist=%s peak=%s offpeak=%s (traffic_aware=%s)",
        listing_title,
        haversine_est.distance_km or -1, fmt(haversine_est.peak_hours), fmt(haversine_est.offpeak_hours),
        f"{google_est.distance_km:.1f}km" if google_est.distance_km is not None else "n/a",
        fmt(google_est.peak_hours), fmt(google_est.offpeak_hours), google_est.traffic_aware,
    )
    return google_est.api_calls
