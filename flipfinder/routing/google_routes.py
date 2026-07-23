"""
Real, traffic-aware routing via Google's Routes API (computeRoutes,
TRAFFIC_AWARE_OPTIMAL preference). Built against Google's published Routes
API reference as of mid-2026:

    POST https://routes.googleapis.com/directions/v2:computeRoutes
    headers: X-Goog-Api-Key, X-Goog-FieldMask (required -- unset it and
             every field comes back empty, not an error)
    body: origin/destination as {location: {latLng: {latitude, longitude}}},
          travelMode: "DRIVE", routingPreference: "TRAFFIC_AWARE_OPTIMAL",
          trafficModel: "BEST_GUESS", departureTime: RFC3339 UTC, e.g.
          "2026-07-22T12:00:00Z"

departureTime MUST be in the future -- a past timestamp is rejected outright.
So "peak" and "offpeak" aren't fixed dates, they're "the next upcoming
occurrence of this time of day" (see _next_departure), recomputed fresh each
call. TRAFFIC_AWARE_OPTIMAL + a future departureTime gives a historical-
pattern-based prediction (not live conditions), which is exactly what you
want for "is 8am on a random future weekday typically bad" rather than
"what's traffic doing right now."

Cost note: this makes TWO calls per listing (peak + offpeak), since Routes
API doesn't return multiple traffic-model predictions in one request. Only
called for listings that already passed stage 1, so volume is naturally
small, but if you want to halve it, see config's routing.compute_both --
setting it false only computes whichever of peak/offpeak matches
travel_time_basis.

Setup: enable the Routes API on a Google Cloud project (requires a billing
account attached, even though hobby-scale usage here should stay within the
$200/month free credit), create an API key, restrict it to the Routes API.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import requests

from flipfinder.routing.base import RoundTripEstimate, RoutingBackend
from flipfinder.routing.haversine import HaversineRoutingBackend

logger = logging.getLogger("flipfinder.routing.google")

COMPUTE_ROUTES_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"


def _next_departure(hhmm: str, weekday_only: bool) -> datetime:
    """Next future occurrence of local time hh:mm (optionally weekdays only).
    departureTime must be in the future, so "today at 8am" rolls to
    tomorrow once 8am has already passed today."""
    hour, minute = (int(x) for x in hhmm.split(":"))
    now = datetime.now().astimezone()
    candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    if weekday_only:
        while candidate.weekday() >= 5:  # Saturday=5, Sunday=6
            candidate += timedelta(days=1)
    return candidate


class GoogleRoutesBackend(RoutingBackend):
    name = "google_routes"

    def __init__(
        self,
        api_key: str,
        peak_time: str = "08:00",
        offpeak_time: str = "14:00",
        weekday_only: bool = True,
        compute_both: bool = True,
        travel_time_basis: str = "peak",   # used if compute_both is False, to decide which one to skip
        avg_speed_kmh_fallback: float = 50.0,
        timeout: float = 10.0,
    ):
        self.api_key = api_key
        self.peak_time = peak_time
        self.offpeak_time = offpeak_time
        self.weekday_only = weekday_only
        self.compute_both = compute_both
        self.travel_time_basis = travel_time_basis
        self.timeout = timeout
        self._session = requests.Session()
        self._fallback = HaversineRoutingBackend(avg_speed_kmh=avg_speed_kmh_fallback)
        self.total_calls_made = 0   # cumulative counter, for cost tracking -- see flipfinder/main.py

    def _compute_route(
        self, origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float, departure_time: datetime,
    ) -> tuple[float, float]:
        """Returns (distance_km, one_way_duration_hours)."""
        body = {
            "origin": {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lon}}},
            "destination": {"location": {"latLng": {"latitude": dest_lat, "longitude": dest_lon}}},
            "travelMode": "DRIVE",
            "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
            "trafficModel": "BEST_GUESS",
            "departureTime": departure_time.astimezone().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        self.total_calls_made += 1  # count the attempt regardless of outcome -- Google's quota/billing sees the request either way
        resp = self._session.post(
            COMPUTE_ROUTES_URL,
            json=body,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": self.api_key,
                "X-Goog-FieldMask": "routes.duration,routes.distanceMeters",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        route = resp.json()["routes"][0]
        duration_seconds = int(str(route["duration"]).rstrip("s"))
        distance_km = route["distanceMeters"] / 1000.0
        return distance_km, duration_seconds / 3600.0

    def estimate_round_trip(
        self, origin_lat: float, origin_lon: float,
        dest_lat: Optional[float], dest_lon: Optional[float],
    ) -> RoundTripEstimate:
        if dest_lat is None or dest_lon is None:
            return RoundTripEstimate(None, None, None, traffic_aware=False, api_calls=0)

        calls_before = self.total_calls_made
        try:
            distance_km = peak_hours = offpeak_hours = None

            if self.compute_both or self.travel_time_basis == "peak":
                dist, dur = self._compute_route(
                    origin_lat, origin_lon, dest_lat, dest_lon, _next_departure(self.peak_time, self.weekday_only),
                )
                distance_km, peak_hours = dist, dur * 2

            if self.compute_both or self.travel_time_basis == "offpeak":
                dist, dur = self._compute_route(
                    origin_lat, origin_lon, dest_lat, dest_lon, _next_departure(self.offpeak_time, self.weekday_only),
                )
                distance_km, offpeak_hours = dist, dur * 2

            calls_made = self.total_calls_made - calls_before
            return RoundTripEstimate(distance_km, peak_hours, offpeak_hours, traffic_aware=True, api_calls=calls_made)
        except (requests.RequestException, KeyError, IndexError, ValueError) as exc:
            logger.warning("Google Routes API call failed (%s), falling back to haversine estimate", exc)
            calls_made = self.total_calls_made - calls_before
            fallback_estimate = self._fallback.estimate_round_trip(origin_lat, origin_lon, dest_lat, dest_lon)
            fallback_estimate.api_calls = calls_made  # still counts against quota even though we didn't use the result
            return fallback_estimate
