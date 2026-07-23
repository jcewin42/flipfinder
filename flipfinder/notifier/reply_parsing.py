"""
Pure parsing helpers for casual Discord replies. Deliberately kept free of
any discord.py import so these are unit-testable without discord installed
(see tests/test_reply_parsing.py) -- discord_bot.py imports from here rather
than defining this inline.

All of this is intentionally simple regex, not NLP -- odd phrasing fails to
parse rather than silently misreading a number, and the slash command is
always the reliable fallback.
"""
from __future__ import annotations

import re
from typing import Optional

COST_PATTERN = re.compile(r"(?:spent|cost|paid|repair(?:ed)?)\D{0,10}\$?(\d+(?:\.\d+)?)", re.IGNORECASE)
SALE_PATTERN = re.compile(r"(?:sold|sale|got|flipped)\D{0,10}\$?(\d+(?:\.\d+)?)", re.IGNORECASE)
ITEM_COUNT_PATTERN = re.compile(
    r"(?:actually|it'?s|there'?s|there are|only|just)\D{0,12}(\d{1,2})\b", re.IGNORECASE
)

# Ordered keyword checks, first match wins -- condition-at-sale phrasing is
# too open-ended for a single regex to do well. This is rougher than the
# other parsers on purpose: /feedback condition_at_sale:"..." (free text) is
# the reliable path for anything nuanced. This just catches the common cases.
_CONDITION_KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bparts?\s*(?:only|motor)\b|\bfor\s*parts\b", re.IGNORECASE), "parts_only"),
    (re.compile(r"\bnot\s*running\b|\bwon'?t\s*start\b|\bdoesn'?t\s*run\b", re.IGNORECASE), "not_running"),
    (re.compile(r"\bas[\s-]?is\b|\bdidn'?t\s*service\b|\bdid\s*not\s*service\b|\bno\s*service\b", re.IGNORECASE), "as-is, not serviced"),
    (re.compile(r"\bfully\s*serviced\b|\bserviced\s*(?:and|,)?\s*running\b|\bran\s*great\b", re.IGNORECASE), "serviced_running"),
]


def parse_condition_at_sale(text: str) -> Optional[str]:
    """Best-effort classification of a casual condition-at-sale description.
    Returns None if nothing recognizable matched -- prefer /feedback
    condition_at_sale:"..." for anything this doesn't catch cleanly."""
    for pattern, label in _CONDITION_KEYWORDS:
        if pattern.search(text):
            return label
    return None


def parse_casual_feedback(text: str) -> dict:
    """Best-effort extraction of $ amounts from a casual reply. Returns a
    dict with whichever of actual_repair_cost / actual_resale_value it
    could find (missing keys are omitted, not None -- caller decides
    defaults)."""
    out = {}
    cost_match = COST_PATTERN.search(text)
    sale_match = SALE_PATTERN.search(text)
    if cost_match:
        out["actual_repair_cost"] = float(cost_match.group(1))
    if sale_match:
        out["actual_resale_value"] = float(sale_match.group(1))
    return out


def parse_item_count_correction(text: str) -> Optional[int]:
    """Best-effort extraction of a corrected unit count from a casual reply
    like "actually there's 2" or "just 1 motor". Returns None if nothing
    matched -- callers should fall back to the /feedback slash command's
    explicit actual_item_count parameter for anything this misses."""
    match = ITEM_COUNT_PATTERN.search(text)
    return int(match.group(1)) if match else None
