"""
Feedback store: records what the user actually spent/sold items for, and
retrieves the most similar past feedback for a new listing.

Starting point for the learning loop: with zero historical data on day one,
there's nothing to train a model on. So instead of training anything, new
valuations are corrected by retrieval -- pull the closest few past
corrections and hand them to the category profile to fold into its prompt as
few-shot examples (see OutboardMotorProfile.build_valuation_prompt).

This is deliberately simple (linear scan + a hand-rolled distance function,
no vector DB, no ML dependency). Once you've got real volume per category
(order of 30-50+ logged outcomes), it's worth revisiting whether a proper
learned correction model beats retrieval -- same "keep it simple until you
have data" philosophy as the scheduler.
"""
from __future__ import annotations

from flipfinder.db import Database
from flipfinder.models import FeedbackEntry


def _feature_distance(a: dict, b: dict) -> float:
    """
    Lower is more similar. Numeric fields contribute normalized absolute
    difference; categorical fields contribute a flat penalty on mismatch.
    Missing values on either side contribute a mild penalty (neither a full
    match nor a full mismatch) so partial listings still get compared.
    """
    distance = 0.0
    keys = set(a) | set(b)
    for key in keys:
        av, bv = a.get(key), b.get(key)
        if av is None or bv is None:
            distance += 0.5
        elif isinstance(av, (int, float)) and isinstance(bv, (int, float)):
            denom = max(abs(av), abs(bv), 1.0)
            distance += min(abs(av - bv) / denom, 1.0)
        else:
            distance += 0.0 if av == bv else 1.0
    return distance


class FeedbackStore:
    def __init__(self, db: Database):
        self.db = db

    def record(self, entry: FeedbackEntry) -> None:
        self.db.record_feedback(
            listing_id=entry.listing_id,
            category_id=entry.category_id,
            features=entry.features,
            predicted_repair_cost=entry.predicted_repair_cost,
            predicted_resale_value=entry.predicted_resale_value,
            actual_repair_cost=entry.actual_repair_cost,
            actual_resale_value=entry.actual_resale_value,
            was_purchased=entry.was_purchased,
            notes=entry.notes,
        )

    def find_similar(self, category_id: str, features: dict, k: int = 5) -> list[FeedbackEntry]:
        rows = self.db.get_feedback_for_category(category_id)
        # Only feedback with an actual outcome is useful as a correction example.
        rows = [r for r in rows if r["actual_repair_cost"] is not None or r["actual_resale_value"] is not None]
        rows.sort(key=lambda r: _feature_distance(features, r["features"]))
        top = rows[:k]
        return [
            FeedbackEntry(
                listing_id=r["listing_id"],
                category_id=r["category_id"],
                features=r["features"],
                predicted_repair_cost=r["predicted_repair_cost"],
                predicted_resale_value=r["predicted_resale_value"],
                actual_repair_cost=r["actual_repair_cost"],
                actual_resale_value=r["actual_resale_value"],
                was_purchased=bool(r["was_purchased"]) if r["was_purchased"] is not None else None,
                notes=r["notes"] or "",
            )
            for r in top
        ]
