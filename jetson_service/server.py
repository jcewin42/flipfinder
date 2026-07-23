"""
Runs on the Jetson. Exposes a single POST /evaluate endpoint that the Pi's
JetsonInferenceBackend (flipfinder/inference/jetson_client.py) calls over
the LAN. Stateless by design -- the Pi's SQLite database is the only source
of truth, so this service (or the model behind it) can be swapped freely.

ASSUMPTION (adjust to match your actual Jetson setup): this assumes you're
running Ollama locally on the Jetson with a vision-capable model pulled
(e.g. `ollama pull llama3.2-vision` or `qwen2.5vl`). If you end up running
something else (a raw llama.cpp server, vLLM, a HF transformers script),
only the _call_model() function below needs to change -- the /evaluate
contract (prompt + image_urls in, response text out) stays the same, so
flipfinder/inference/jetson_client.py never needs to know.

Run with:
    pip install -r jetson_service/requirements.txt
    uvicorn jetson_service.server:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import base64
import os

import requests
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2-vision")


class EvaluateRequest(BaseModel):
    prompt: str
    image_urls: list[str] = []


class EvaluateResponse(BaseModel):
    response: str


def _fetch_image_b64(url: str) -> str | None:
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        return base64.b64encode(resp.content).decode("utf-8")
    except requests.RequestException:
        # Skip photos that fail to download rather than failing the whole
        # valuation over one bad image URL.
        return None


def _call_model(prompt: str, image_urls: list[str]) -> str:
    images_b64 = [b64 for url in image_urls if (b64 := _fetch_image_b64(url)) is not None]

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": prompt, "images": images_b64}],
        "stream": False,
    }
    resp = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=180)
    resp.raise_for_status()
    return resp.json()["message"]["content"]


@app.post("/evaluate", response_model=EvaluateResponse)
def evaluate(req: EvaluateRequest) -> EvaluateResponse:
    return EvaluateResponse(response=_call_model(req.prompt, req.image_urls))


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": OLLAMA_MODEL}
