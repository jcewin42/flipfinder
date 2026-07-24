"""
Outboard motor category profile.

This is the first category. Snowblowers, lawn mowers, motorcycles, cars, and
boats should each become their own file following this same shape -- a
CategoryProfile subclass plus an entry in categories/__init__.py. Nothing
outside this file should need to change to add them.
"""
from __future__ import annotations

import json
import re
from typing import Optional, Sequence

from flipfinder.categories.base import CategoryProfile
from flipfinder.models import FeedbackEntry, ListingDetail, ListingSummary, SearchSpec, ValuationEstimate

BRANDS = [
    "yamaha", "mercury", "evinrude", "johnson", "suzuki", "honda",
    "tohatsu", "nissan", "force", "mariner",
]

# Listings that mention any of these are almost never a whole flippable motor.
EXCLUDE_KEYWORDS = [
    "trolling motor", "lower unit only", "lower unit", "prop only",
    "propeller only", "cowl only", "cowling only", "carb only",
    "carburetor only", "parts only", "for parts only", "no motor",
    "motor not included", "inboard", "sterndrive", "i/o ", "mercruiser",
    "boat and motor", "boat with motor", "boat trailer",
]

HP_PATTERN = re.compile(r"(\d{1,3}(?:\.\d)?)\s*(?:hp|horsepower)\b", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"\b(19[8-9]\d|20[0-4]\d)\b")
# Coarse, best-effort signal for feature_vector bucketing ONLY -- not the
# source of truth for pricing math (that's the AI's estimated_item_count,
# which reads the full description/photos and is asked explicitly). This
# just keeps feedback similarity search from blending single-motor and
# multi-motor past outcomes together when a rough guess is easy enough.
ITEM_COUNT_PATTERN = re.compile(
    r"\b(\d{1,2})\s*(?:outboards?|motors?)\b|\blot\s+of\s+(\d{1,2})\b", re.IGNORECASE
)


def _guess_item_count(text: str) -> int:
    match = ITEM_COUNT_PATTERN.search(text)
    if not match:
        return 1
    count = next(g for g in match.groups() if g is not None)
    return max(1, int(count))


class OutboardMotorProfile(CategoryProfile):
    category_id = "outboard_motors"

    def __init__(
        self,
        latitude: float,
        longitude: float,
        radius_km: float,
        base_service_cost: float,
        base_service_hours: float = 1.5,
        price_min: Optional[int] = None,
        price_max: Optional[int] = None,
        search_strategy: str = "broad",
        image_count: int = 3,
    ):
        """
        search_strategy: "broad" runs one generic query ("outboard motor"),
        cheapest on a per-search-credit source like SociaVault. "thorough"
        additionally runs one query per brand -- better coverage of niche
        brands FB's own search ranking might bury under a generic query, at
        the cost of ~11x the search credits per poll. Start broad; switch to
        thorough only if you notice you're missing listings you find by
        browsing manually.
        """
        self.latitude = latitude
        self.longitude = longitude
        self.radius_km = radius_km
        self.base_service_cost = base_service_cost
        self.base_service_hours = base_service_hours
        self.price_min = price_min
        self.price_max = price_max
        self.search_strategy = search_strategy
        self.image_count = image_count

    def search_specs(self) -> Sequence[SearchSpec]:
        if self.search_strategy == "thorough":
            queries = ["outboard motor"] + [f"{brand} outboard" for brand in BRANDS]
        else:
            queries = ["outboard motor"]

        return [
            SearchSpec(
                category_id=self.category_id,
                query=q,
                latitude=self.latitude,
                longitude=self.longitude,
                radius_km=self.radius_km,
                price_min=self.price_min,
                price_max=self.price_max,
            )
            for q in queries
        ]

    def quick_filter(self, summary: ListingSummary) -> bool:
        title = (summary.title or "").lower()

        if any(kw in title for kw in EXCLUDE_KEYWORDS):
            return False

        # Positive relevance check. SociaVault's search results for a query
        # like "outboard motor" routinely include unrelated "suggested"
        # listings (see the delisting-detection notes in README on search
        # ranking noise) that don't hit any EXCLUDE_KEYWORDS either -- confirmed
        # live: things like "Chrome hearts hat" and "Vinyl LP's" passed stage 1
        # and reached a paid Claude call before this check existed. Require
        # some actual signal this is an outboard motor listing.
        is_relevant = (
            "outboard" in title
            or "motor" in title
            or HP_PATTERN.search(title)
            or any(brand in title for brand in BRANDS)
        )
        if not is_relevant:
            return False

        if summary.price is not None:
            if self.price_min is not None and summary.price < self.price_min:
                return False
            if self.price_max is not None and summary.price > self.price_max:
                return False

        return True

    def build_valuation_prompt(
        self,
        detail: ListingDetail,
        similar_feedback: Sequence[FeedbackEntry],
        market_stats: Optional[dict] = None,
    ) -> str:
        attrs = "\n".join(f"- {k}: {v}" for k, v in detail.attributes.items()) or "(none provided)"

        feedback_block = "(no past feedback yet for similar listings)"
        if similar_feedback:
            lines = []
            for fb in similar_feedback:
                line = (
                    f"- Similar past listing (features: {fb.features}): "
                    f"predicted repair cost ${fb.predicted_repair_cost}, "
                    f"actual repair cost ${fb.actual_repair_cost}; "
                    f"predicted resale ${fb.predicted_resale_value}, "
                    f"actual resale ${fb.actual_resale_value}."
                )
                if fb.actual_resale_value is not None:
                    line += (
                        f" Condition at sale: {fb.condition_at_sale}."
                        if fb.condition_at_sale
                        else " (condition at sale not recorded -- weight this comp cautiously, "
                             "it may not have been fully serviced before resale)."
                    )
                if fb.actual_item_count is not None:
                    line += (
                        f" You were uncertain about the unit count and guessed "
                        f"{fb.predicted_item_count}; it was actually {fb.actual_item_count}."
                    )
                if fb.notes:
                    line += f" Notes: {fb.notes}"
                lines.append(line)
            feedback_block = "\n".join(lines)

        market_block = "(not enough local sales history yet to calibrate against)"
        if market_stats:
            lo, hi = market_stats["price_range"]
            market_block = (
                f"Based on {market_stats['sample_size']} similarly priced (${lo}-${hi}) listings that "
                f"recently sold/were removed in this area, the median time-on-market was "
                f"{market_stats['median_days_on_market']} days. Listings priced right or below local "
                f"market value tend to disappear fast (hours to a few days); listings sitting for weeks "
                f"are usually overpriced for this market -- use this to judge whether THIS asking price "
                f"looks like a good local deal."
            )

        return f"""You are helping a small engine reseller evaluate a used outboard motor listing for flip potential.

Listing title: {detail.title}
Asking price: ${detail.price}
Description: {detail.description}
Attributes:
{attrs}

The reseller's standard service (performed on EVERY unit) already costs ${self.base_service_cost}
and takes {self.base_service_hours} hours per unit -- do NOT include this baseline in your numbers.
Only estimate the ADDITIONAL repair cost and ADDITIONAL labor hours beyond that per-unit baseline.

IMPORTANT -- some listings include more than one outboard motor (an estate clear-out, a shop
closing, "3 outboards, take all for $X"). If this listing includes multiple distinct motors,
your estimated_resale_value, estimated_repair_cost, and estimated_repair_hours should be TOTALS
across ALL units combined, not just one -- and set estimated_item_count to how many you count.
For an ordinary single-motor listing, estimated_item_count is 1.

Report item_count_confidence separately from your overall confidence -- specifically how sure
you are about the NUMBER of units, not the valuation. Lower it when: the wording is ambiguous
("outboards" plural with no number given), photos suggest a different count than the text states,
the listing is vague about what exactly is included, or you're genuinely guessing between two
plausible counts. A listing can have high overall confidence in the price/condition assessment
while still having low item_count_confidence if the unit count itself is unclear.

If photos are attached, examine them and weigh what you see alongside the text -- don't just
attach them as decoration:
- Visible condition: corrosion, missing or damaged parts (prop, cowling, lower unit, fuel lines),
  rust, cracked housings, overall wear. If photos show meaningfully better or worse condition than
  the text description implies, trust the photos and adjust estimated_repair_cost/hours accordingly
  -- sellers often under- or over-describe condition in the text.
- Unit count: if multiple distinct motors are clearly visible together in the photos, that's
  strong evidence for estimated_item_count and should raise your item_count_confidence. If photos
  only show one angle, are blurry, or don't clearly resolve how many motors are present, keep
  item_count_confidence appropriately low even if the text hints at multiple units.
- If no usable photos were provided, or they don't show enough to judge condition/count
  confidently, say so explicitly in reasoning and lower confidence/item_count_confidence
  accordingly rather than guessing as if you'd seen clear photos.

Local market timing:
{market_block}

Calibration from similar past listings this reseller has actually flipped (use these to correct
your estimate toward this reseller's real experience -- their actual costs/prices are ground truth):
{feedback_block}

Respond with ONLY a JSON object, no other text:
{{
  "estimated_resale_value": <number, USD, TOTAL across all units, what everything here would sell for after the standard service and any additional repair>,
  "estimated_repair_cost": <number, USD, TOTAL additional repair cost across all units beyond the standard service>,
  "estimated_repair_hours": <number, TOTAL additional labor hours across all units beyond the standard service>,
  "estimated_item_count": <integer, how many distinct outboard motors this listing includes -- 1 for an ordinary single-motor listing>,
  "item_count_confidence": <number 0.0-1.0, how sure you are specifically about that count, separate from overall confidence>,
  "confidence": <number 0.0-1.0>,
  "reasoning": "<one or two sentence explanation, noting the unit count if more than one, and WHY if item_count_confidence is low>"
}}"""

    def parse_valuation_response(self, raw_response: str) -> ValuationEstimate:
        text = raw_response.strip()
        # Strip markdown code fences if the model wrapped its JSON in them.
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())

        try:
            data = json.loads(text)
            return ValuationEstimate(
                estimated_resale_value=float(data["estimated_resale_value"]),
                estimated_repair_cost=float(data["estimated_repair_cost"]),
                estimated_repair_hours=float(data.get("estimated_repair_hours", 0.0)),
                confidence=float(data.get("confidence", 0.5)),
                reasoning=str(data.get("reasoning", "")),
                estimated_item_count=max(1, int(data.get("estimated_item_count", 1))),
                item_count_confidence=min(1.0, max(0.0, float(data.get("item_count_confidence", 1.0)))),
                raw_response=raw_response,
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            # Fail loud but don't crash the whole pipeline over one bad
            # response -- return a zero-confidence estimate so the caller
            # can decide to skip/flag this listing instead of alerting on
            # garbage numbers.
            return ValuationEstimate(
                estimated_resale_value=0.0,
                estimated_repair_cost=0.0,
                estimated_repair_hours=0.0,
                confidence=0.0,
                reasoning=f"Failed to parse inference response: {exc}",
                raw_response=raw_response,
            )

    def feature_vector(self, detail: ListingDetail) -> dict:
        text = f"{detail.title} {detail.description}".lower()

        brand = next((b for b in BRANDS if b in text), None)
        hp_match = HP_PATTERN.search(text)
        year_match = YEAR_PATTERN.search(text)

        return {
            "brand": brand,
            "hp": float(hp_match.group(1)) if hp_match else None,
            "year": int(year_match.group(1)) if year_match else None,
            "condition": detail.attributes.get("Condition"),
            "price": detail.price,
            "guessed_item_count": _guess_item_count(text),
        }
