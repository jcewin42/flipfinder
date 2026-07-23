"""
A canned-response backend. Useful for unit tests and for dry-running the
whole pipeline (scheduler -> stage1 -> stage2 -> offer -> notify) before the
Jetson is wired up or reachable.
"""
from __future__ import annotations

import json
from typing import Optional, Sequence

from flipfinder.inference.base import InferenceBackend


class MockInferenceBackend(InferenceBackend):
    name = "mock"

    def __init__(self, fixed_response: Optional[dict] = None):
        self.fixed_response = fixed_response or {
            "estimated_resale_value": 500.0,
            "estimated_repair_cost": 75.0,
            "estimated_repair_hours": 1.0,
            "estimated_item_count": 1,
            "confidence": 0.5,
            "reasoning": "Mock response -- no real inference backend configured.",
        }

    def evaluate(self, prompt: str, image_urls: Optional[Sequence[str]] = None) -> str:
        return json.dumps(self.fixed_response)
