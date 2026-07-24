"""
Calls the Claude API directly for AI valuations -- the Pi already has
internet access (it's calling SociaVault and Discord), so there's no need
for a second box just to relay prompts to a local model.

Downloads each listing photo by URL and skips ones that fail to fetch
rather than failing the whole valuation over a single bad photo URL.

Thinking is explicitly disabled by default. This backend is called once per
stage-1 survivor, automatically, at real volume -- unlike a one-off task,
cost and latency predictability matter more here than squeezing out extra
reasoning depth on what's fundamentally a structured extraction task
(read a description + a few photos, fill in a JSON valuation). Raise
`thinking` back to adaptive per-category in config.yaml if valuations turn
out to need it.
"""
from __future__ import annotations

import base64
import logging
from typing import Optional, Sequence

import anthropic
import requests

from flipfinder.inference.base import InferenceBackend

logger = logging.getLogger("flipfinder.inference.claude_api")

# Multi-image vision requests (up to image_count photos per listing) can run
# noticeably longer than a single-image or text-only call -- give real headroom.
DEFAULT_TIMEOUT = 120.0
DEFAULT_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 1024


def _guess_media_type(content: bytes, url: str) -> str:
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if content[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    lower = url.lower().split("?", 1)[0]
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".gif"):
        return "image/gif"
    if lower.endswith(".webp"):
        return "image/webp"
    return "image/jpeg"  # FB CDN photos are jpeg in practice; safe fallback


class ClaudeAPIBackend(InferenceBackend):
    name = "claude_api"

    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        timeout: float = DEFAULT_TIMEOUT,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ):
        self.model = model
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic(api_key=api_key, timeout=timeout)
        self._image_session = requests.Session()

    def _fetch_image_block(self, url: str) -> Optional[dict]:
        try:
            resp = self._image_session.get(url, timeout=15)
            resp.raise_for_status()
        except requests.RequestException:
            # Skip photos that fail to download rather than failing the
            # whole valuation over one bad image URL.
            return None
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": _guess_media_type(resp.content, url),
                "data": base64.standard_b64encode(resp.content).decode("utf-8"),
            },
        }

    def evaluate(self, prompt: str, image_urls: Optional[Sequence[str]] = None) -> str:
        content = [
            block for url in (image_urls or [])
            if (block := self._fetch_image_block(url)) is not None
        ]
        content.append({"type": "text", "text": prompt})

        response = self._client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            thinking={"type": "disabled"},
            messages=[{"role": "user", "content": content}],
        )
        return next((b.text for b in response.content if b.type == "text"), "")
