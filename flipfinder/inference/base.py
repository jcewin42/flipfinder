"""
Inference backend interface.

The pipeline asks "evaluate this prompt (optionally with images)" without
caring whether that's answered by a cloud API, a local model, or (in tests)
a canned response. Swap backends by changing config, not code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional, Sequence


class InferenceBackend(ABC):
    name: str = "unnamed-backend"

    @abstractmethod
    def evaluate(self, prompt: str, image_urls: Optional[Sequence[str]] = None) -> str:
        """Return the raw text response for a given valuation prompt."""
        raise NotImplementedError
