"""
Stage 1: cheap, source-agnostic screening.

This exists regardless of which source is active. The original two-stage
design was framed around avoiding SociaVault's per-call cost specifically --
but the real reason to filter early is to avoid spending EITHER API money OR
inference cost/latency on listings that are obviously not worth a full
valuation. That reasoning holds even with a free self-hosted monitor, so
this stage stays in the architecture either way.

No hard distance cutoff here -- the search radius passed to the source
already scopes results, and beyond that, distance is priced into the
$/hour math via travel time rather than filtered as a separate, item-value-
blind cutoff. A far-away multi-unit lot can be worth the drive in a way a
flat km limit can't tell apart from a far-away single cheap item.

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

from flipfinder.categories.base import CategoryProfile
from flipfinder.models import ListingSummary

logger = logging.getLogger("flipfinder.stage1")


def passes_stage1(
    summary: ListingSummary,
    category: CategoryProfile,
    require_photo: bool = False,
) -> bool:
    if require_photo and not summary.thumbnail_url:
        logger.debug("stage1 reject (no photo): %s", summary.title)
        return False

    if not category.quick_filter(summary):
        logger.debug("stage1 reject (category filter): %s", summary.title)
        return False

    return True
