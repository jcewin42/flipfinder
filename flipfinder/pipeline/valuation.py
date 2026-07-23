"""
Stage 2: full valuation.

Only runs for listings that passed stage 1. Pulls similar past feedback and
local time-on-market stats, builds the category-specific prompt, calls
whichever inference backend is configured, and parses the result. None of
this cares whether the backend is the Jetson or something else -- see
inference/base.py.
"""
from __future__ import annotations

from flipfinder.categories.base import CategoryProfile
from flipfinder.db import Database
from flipfinder.inference.base import InferenceBackend
from flipfinder.models import ListingDetail, ValuationEstimate
from flipfinder.pipeline import market_stats as market_stats_mod
from flipfinder.pipeline.feedback_store import FeedbackStore


def evaluate_listing(
    detail: ListingDetail,
    category: CategoryProfile,
    feedback_store: FeedbackStore,
    inference_backend: InferenceBackend,
    db: Database,
    feedback_k: int = 5,
) -> ValuationEstimate:
    features = category.feature_vector(detail)
    similar_feedback = feedback_store.find_similar(category.category_id, features, k=feedback_k)
    stats = market_stats_mod.get_time_on_market_stats(db, category.category_id, detail.price)
    prompt = category.build_valuation_prompt(detail, similar_feedback, market_stats=stats)
    image_urls = [p.url for p in detail.photos[:3]]  # keep the request cheap; a few photos is plenty of context
    raw_response = inference_backend.evaluate(prompt, image_urls=image_urls)
    return category.parse_valuation_response(raw_response)
