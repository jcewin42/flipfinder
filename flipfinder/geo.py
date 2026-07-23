"""
Straight-line distance and a rough drive-time estimate.

This is a deliberately simple heuristic (haversine distance / assumed
average speed), not a real routing call -- no traffic, no actual roads. It's
enough to separate "30 minutes away" from "2 hours away" for the hourly-rate
math, which is the thing that actually matters here. If it's ever worth the
precision, swap estimate_drive_time_hours's body for a real routing API
(OSRM self-hosted, Google Distance Matrix, etc.) -- nothing outside this
file needs to change since callers only depend on this function's signature.
"""
from __future__ import annotations

import math
from typing import Optional

EARTH_RADIUS_KM = 6371.0


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def estimate_round_trip_hours(distance_km: Optional[float], avg_speed_kmh: float = 50.0) -> Optional[float]:
    """Round-trip travel time (there to pick up, back home) in hours."""
    if distance_km is None:
        return None
    return (2 * distance_km) / avg_speed_kmh
