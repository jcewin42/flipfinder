"""
Core data models used across the whole pipeline.

These are the shapes that flow between modules. Keeping them here (instead of
scattered per-module) is what lets sources, categories, and inference backends
be swapped independently -- they all speak this shared vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SearchSpec:
    """
    A source-agnostic description of what to search for.

    A category profile produces one or more of these. A source adapter
    translates it into whatever its underlying API/scraper needs. Neither
    side needs to know about the other's implementation details.
    """
    category_id: str
    query: str
    latitude: float
    longitude: float
    radius_km: float
    price_min: Optional[int] = None
    price_max: Optional[int] = None


@dataclass
class Photo:
    url: str
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class ListingSummary:
    """
    Lightweight listing data, as returned by a source's search/poll call.
    Cheap to obtain -- this is what stage 1 filtering runs against.
    """
    id: str
    source: str
    category_id: str
    title: str
    price: Optional[float]
    url: Optional[str]
    thumbnail_url: Optional[str]
    posted_at: Optional[datetime]
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    raw: dict = field(default_factory=dict)  # original source payload, for debugging


@dataclass
class ListingDetail:
    """
    Full listing data, as returned by a source's get_detail call.
    Only fetched for listings that pass stage 1 -- this is the "expensive" call,
    whether that expense is API cost or local inference/compute time.
    """
    id: str
    source: str
    category_id: str
    title: str
    description: str
    price: Optional[float]
    url: Optional[str]
    photos: list[Photo]
    attributes: dict            # e.g. {"Condition": "Used - Good", "Brand": "Yamaha"}
    seller: dict                # whatever public seller info the source provides
    location: dict
    posted_at: Optional[datetime]
    latitude: Optional[float] = None   # some sources' detail endpoint omits this --
    longitude: Optional[float] = None  # see main.py, which backfills from the ListingSummary if so
    raw: dict = field(default_factory=dict)


@dataclass
class ValuationEstimate:
    """Output of stage 2 AI valuation for one listing."""
    estimated_resale_value: float   # TOTAL across all units if this is a multi-motor listing
    estimated_repair_cost: float     # TOTAL additional repair cost across all units
    estimated_repair_hours: float    # TOTAL additional labor hours across all units
    confidence: float             # 0.0-1.0, the model's/heuristic's self-reported confidence
    reasoning: str                 # short free-text explanation, shown to the user
    estimated_item_count: int = 1   # how many distinct motors this listing appears to include
    raw_response: str = ""          # unparsed model output, kept for debugging/audit


@dataclass
class Offer:
    """Output of the offer math for one listing."""
    max_offer: float
    total_cost: float             # base service cost + estimated repair cost
    target_profit: float
    profit_if_bought_at_asking: float
    pickup_travel_hours: Optional[float]         # the one actually used in total_time_hours/hourly rate (see travel_time_basis config)
    pickup_travel_hours_peak: Optional[float]    # for display -- both shown regardless of which is "primary"
    pickup_travel_hours_offpeak: Optional[float]
    traffic_aware: bool                            # False if this came from the haversine fallback, not real routing
    service_hours: float                    # base_service_hours + estimated_repair_hours
    total_time_hours: float                 # pickup + service + selling overhead
    estimated_hourly_rate: float            # profit_if_bought_at_asking / total_time_hours


@dataclass
class FeedbackEntry:
    """
    A piece of ground truth the user has supplied after the fact: what they
    actually spent servicing an item and/or what they actually sold it for.
    This is the raw material the learning loop uses to get more accurate.
    """
    listing_id: str
    category_id: str
    features: dict                 # category_profile.feature_vector() output, stored for similarity search
    predicted_repair_cost: Optional[float]
    predicted_resale_value: Optional[float]
    actual_repair_cost: Optional[float]
    actual_resale_value: Optional[float]
    was_purchased: Optional[bool]
    notes: str = ""
    created_at: Optional[datetime] = None
