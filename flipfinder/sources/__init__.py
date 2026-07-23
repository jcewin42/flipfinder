"""
Source registry. Add new sources here so they can be referenced by name in
config.yaml (source: sociavault) instead of importing classes directly
throughout the codebase.
"""
from flipfinder.sources.base import SearchResult, SourceAdapter
from flipfinder.sources.sociavault import SociaVaultSource

SOURCE_REGISTRY: dict[str, type[SourceAdapter]] = {
    "sociavault": SociaVaultSource,
    # "own_monitor": OwnMonitorSource,   # <- future self-hosted source goes here
}


def build_source(name: str, **kwargs) -> SourceAdapter:
    try:
        cls = SOURCE_REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Unknown source {name!r}. Registered sources: {list(SOURCE_REGISTRY)}"
        )
    return cls(**kwargs)


__all__ = ["SourceAdapter", "SearchResult", "build_source", "SOURCE_REGISTRY"]
