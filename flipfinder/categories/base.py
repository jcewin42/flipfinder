"""
Category profile interface.

This is the modularity boundary for item categories (outboard motors today;
snowblowers, motorcycles, cars, boats later). A profile owns everything that
is specific to *what kind of thing* is being flipped:

  - how to search for it (search_specs)
  - how to cheaply tell if a listing is even worth a detail fetch (quick_filter)
  - the fixed cost of your standard service for this category
  - how to ask the AI backend to value an item in this category, including
    folding in similar past feedback so the estimate self-corrects
  - how to turn the AI's raw response into a structured ValuationEstimate
  - what features to record for feedback-based similarity search later

To add a new category: subclass CategoryProfile, implement the methods
below, and register it in flipfinder/categories/__init__.py.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence

from flipfinder.models import (
    FeedbackEntry,
    ListingDetail,
    ListingSummary,
    SearchSpec,
    ValuationEstimate,
)


class CategoryProfile(ABC):
    category_id: str
    base_service_cost: float
    base_service_hours: float   # fixed labor time for your standard service, mirrors base_service_cost

    @abstractmethod
    def search_specs(self) -> Sequence[SearchSpec]:
        """
        One or more searches to run for this category (e.g. multiple brand
        keywords). Location/radius typically come from shared config, but the
        profile decides the query terms and price bounds.

        How many searches you run is a real cost tradeoff on metered sources
        (SociaVault charges a credit per search call, independent of results
        returned) -- see the search_strategy pattern in OutboardMotorProfile.
        """
        raise NotImplementedError

    @abstractmethod
    def quick_filter(self, summary: ListingSummary) -> bool:
        """
        Cheap, source-agnostic stage 1 check. Return True if this listing is
        worth a full detail fetch + AI valuation. This is the filter that
        keeps you from burning API calls / Jetson inference time on listings
        that are obviously irrelevant (wrong item, parts-only, price outlier).
        Distance-based rejection is handled generically in
        pipeline/stage1_filter.py, not here.
        """
        raise NotImplementedError

    @abstractmethod
    def build_valuation_prompt(
        self,
        detail: ListingDetail,
        similar_feedback: Sequence[FeedbackEntry],
        market_stats: Optional[dict] = None,
    ) -> str:
        """
        Build the prompt sent to the inference backend. Should ask for
        estimated resale value (after your standard service), estimated
        *additional* repair cost AND hours beyond that standard service, and
        a confidence score -- and should fold in similar_feedback as
        few-shot correction examples, plus market_stats (recent local
        time-on-market data for similarly priced listings, if any exists yet)
        to help calibrate whether the asking price looks like a good local
        deal or not.
        """
        raise NotImplementedError

    @abstractmethod
    def parse_valuation_response(self, raw_response: str) -> ValuationEstimate:
        """Parse the inference backend's raw text/JSON into a ValuationEstimate."""
        raise NotImplementedError

    @abstractmethod
    def feature_vector(self, detail: ListingDetail) -> dict:
        """
        A small dict of normalized features (brand, model, hp/year/whatever
        matters for this category) used for feedback nearest-neighbor lookup.
        Keep it flat and JSON-serializable.
        """
        raise NotImplementedError
