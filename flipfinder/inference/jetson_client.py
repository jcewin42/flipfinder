"""
Calls the local inference service running on the Jetson (see
jetson_service/server.py) over the LAN. The Pi stays the source of truth for
data; the Jetson is stateless -- it just receives a prompt (+ optional image
URLs) and returns text. This keeps the Jetson trivially replaceable: swap the
box, change the model it runs, or point at a cloud API instead, and only
this file (or the config it reads from) needs to know.
"""
from __future__ import annotations

from typing import Optional, Sequence

import requests

from flipfinder.inference.base import InferenceBackend


class JetsonInferenceBackend(InferenceBackend):
    name = "jetson"

    def __init__(self, base_url: str, timeout: float = 60.0):
        """
        base_url example: "http://192.168.1.50:8000"
        (matches jetson_service/server.py's default port)
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()

    def evaluate(self, prompt: str, image_urls: Optional[Sequence[str]] = None) -> str:
        resp = self._session.post(
            f"{self.base_url}/evaluate",
            json={"prompt": prompt, "image_urls": list(image_urls or [])},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()["response"]
