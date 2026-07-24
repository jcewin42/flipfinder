from flipfinder.inference.base import InferenceBackend
from flipfinder.inference.claude_backend import ClaudeAPIBackend
from flipfinder.inference.mock import MockInferenceBackend

BACKEND_REGISTRY: dict[str, type[InferenceBackend]] = {
    "mock": MockInferenceBackend,
    "claude_api": ClaudeAPIBackend,
}


def build_backend(name: str, **kwargs) -> InferenceBackend:
    try:
        cls = BACKEND_REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Unknown inference backend {name!r}. Registered: {list(BACKEND_REGISTRY)}"
        )
    return cls(**kwargs)


__all__ = ["InferenceBackend", "build_backend", "BACKEND_REGISTRY"]
