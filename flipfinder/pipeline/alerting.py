"""
Whether a valued listing gets sent to you, separated out as a pure function
so the gating logic (including the unit-count-uncertainty bypass) is
directly testable without spinning up the whole poll cycle.
"""
from __future__ import annotations

from typing import Optional


def should_alert(
    hourly_rate: float,
    confidence: float,
    item_count_confidence: float,
    min_hourly_rate: Optional[float],
    item_count_confidence_threshold: float,
) -> bool:
    """
    Normally gates on hourly rate clearing the bar. But if the AI is
    uncertain about the unit count specifically, the listing gets surfaced
    REGARDLESS of what the hourly rate says -- an undercounted multi-unit
    listing could look like a bad deal under the wrong assumed count and
    never reach you at all otherwise, which is worse than one extra "not
    sure, can you check?" alert.

    min_hourly_rate=None disables the threshold entirely -- every listing
    with a usable valuation (confidence > 0) gets surfaced regardless of
    rate. Meant for calibrating what a real threshold should be against
    actual local listings before locking one in -- not a permanent setting.
    """
    if confidence <= 0:
        return False  # totally failed to parse a usable valuation -- nothing to act on
    if item_count_confidence < item_count_confidence_threshold:
        return True
    if min_hourly_rate is None:
        return True
    return hourly_rate >= min_hourly_rate
