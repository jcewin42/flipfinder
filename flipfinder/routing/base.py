"""
Routing backend interface.

Same modularity pattern as sources/categories/inference: the pipeline asks
"how long is the round trip to this listing, at peak and off-peak times"
without caring whether that's a free straight-line estimate or a real
traffic-aware routing API. Swap by changing config.yaml, not code.

Only called for listings that already passed stage 1 -- that's a much
smaller set than raw search hits, which is what makes a paid routing API
affordable here even though it wouldn't be if called per search result.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class RoundTripEstimate:
    distance_km: Optional[float]
    peak_hours: Optional[float]      # round-trip time assuming a representative peak-traffic departure
    offpeak_hours: Optional[float]   # round-trip time assuming a representative off-peak departure
    traffic_aware: bool               # False for the haversine fallback, True for a real routing backend
    api_calls: int = 0                 # billable network requests made to produce this estimate (0 for haversine)


class RoutingBackend(ABC):
    name: str = "unnamed-routing-backend"

    @abstractmethod
    def estimate_round_trip(
        self, origin_lat: float, origin_lon: float,
        dest_lat: Optional[float], dest_lon: Optional[float],
    ) -> RoundTripEstimate:
        """Return None-filled fields if dest coordinates are unknown, rather than raising."""
        raise NotImplementedError
