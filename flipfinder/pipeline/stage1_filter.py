"""
Stage 1: cheap, source-agnostic screening.

This exists regardless of which source is active. The original two-stage
design was framed around avoiding SociaVault's per-call cost specifically --
but the real reason to filter early is to avoid spending EITHER API money OR
Jetson inference time/thermal budget on listings that are obviously not
worth a full valuation. That reasoning holds even with a free self-hosted
monitor, so this stage stays in the architecture either way.

Distance is checked here rather than in the category profile because it's
generic across every category -- how far you're willing to drive doesn't
depend on what you're flipping. Category-specific logic (keyword excludes,
price sanity) stays in the category profile's quick_filter().

Photo presence is also checked here, generically, for the same reason: "no
photo" is a well-known cheap signal for spam/placeholder listings, and
thumbnail_url comes back free with every search result -- no extra API
call needed to check it. This is OPT-IN (require_photo, default handled by
caller) rather than assumed reliable: this project has already been burned
once by a SociaVault field that looked populated in their docs but was null
in practice (search-result status/listed_at). Watch stage1 reject logs for
false positives before trusting this fully; it's cheap to disable if
thumbnail_url turns out not to behave the way you'd expect.
"""
from __future__ import annotations

import logging
from typing import Optional

from flipfinder.categories.base import CategoryProfile
from flipfinder.models import ListingSummary

logger = logging.getLogger("flipfinder.stage1")


def passes_stage1(
    summary: ListingSummary,
    category: CategoryProfile,
    distance_km: Optional[float] = None,
    max_distance_km: Optional[float] = None,
    require_photo: bool = False,
) -> bool:
    if distance_km is not None and max_distance_km is not None and distance_km > max_distance_km:
        logger.debug(
            "stage1 reject (distance): %s is %.0fkm away, max is %.0fkm",
            summary.title, distance_km, max_distance_km,
        )
        return False

    if require_photo and not summary.thumbnail_url:
        logger.debug("stage1 reject (no photo): %s", summary.title)
        return False

    if not category.quick_filter(summary):
        logger.debug("stage1 reject (category filter): %s", summary.title)
        return False

    return True
