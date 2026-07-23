"""
The free fallback: straight-line distance / assumed average speed, no real
roads, no traffic. Same number for peak and off-peak since there's no
traffic model behind it -- that's the honest answer, not a bug. Used as the
default routing backend, and as GoogleRoutesBackend's fallback if the real
API call fails for any reason.
"""
from __future__ import annotations

from typing import Optional

from flipfinder import geo
from flipfinder.routing.base import RoundTripEstimate, RoutingBackend


class HaversineRoutingBackend(RoutingBackend):
    name = "haversine"

    def __init__(self, avg_speed_kmh: float = 50.0):
        self.avg_speed_kmh = avg_speed_kmh

    def estimate_round_trip(
        self, origin_lat: float, origin_lon: float,
        dest_lat: Optional[float], dest_lon: Optional[float],
    ) -> RoundTripEstimate:
        if dest_lat is None or dest_lon is None:
            return RoundTripEstimate(None, None, None, traffic_aware=False)

        distance_km = geo.haversine_km(origin_lat, origin_lon, dest_lat, dest_lon)
        hours = geo.estimate_round_trip_hours(distance_km, self.avg_speed_kmh)
        return RoundTripEstimate(distance_km, hours, hours, traffic_aware=False)
