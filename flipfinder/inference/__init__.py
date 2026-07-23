from flipfinder.inference.base import InferenceBackend
from flipfinder.inference.jetson_client import JetsonInferenceBackend
from flipfinder.inference.mock import MockInferenceBackend

BACKEND_REGISTRY: dict[str, type[InferenceBackend]] = {
    "jetson": JetsonInferenceBackend,
    "mock": MockInferenceBackend,
    # "claude_api": ClaudeAPIBackend,   # <- future cloud fallback goes here
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
