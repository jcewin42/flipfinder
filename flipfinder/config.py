"""
Loads config.yaml and substitutes ${ENV_VAR} placeholders from the
environment (so API keys/tokens live in .env, not in the committed yaml).

Deliberately returns a plain nested dict rather than a rigid dataclass tree --
config.yaml is meant to grow (new categories, new sources) without this file
needing matching changes.

Missing env vars do NOT raise -- they log a warning and leave the "${VAR}"
placeholder in place. This matters because config.yaml routinely has
inactive backend sections (e.g. google_routes settings sitting unused while
routing.backend: haversine is selected) -- forcing every placeholder in the
whole file to resolve would defeat the point of having swappable backends
you don't all need configured at once. If an unresolved placeholder ever
DOES get used (e.g. you forgot to set a key for the backend you actually
selected), it'll surface as an auth/API error at call time instead, which
--once mode makes easy to catch.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("flipfinder.config")

_ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _substitute_env(value: Any) -> Any:
    if isinstance(value, str):
        def replace(match: re.Match) -> str:
            var_name = match.group(1)
            resolved = os.environ.get(var_name)
            if resolved is None:
                logger.warning(
                    "Environment variable %s referenced in config but not set -- "
                    "leaving placeholder as-is. This is only a problem if the "
                    "backend/section using it is actually active.",
                    var_name,
                )
                return match.group(0)
            return resolved
        return _ENV_PATTERN.sub(replace, value)
    if isinstance(value, dict):
        return {k: _substitute_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_env(v) for v in value]
    return value


def load_config(path: str = "config.yaml") -> dict:
    text = Path(path).read_text()
    raw = yaml.safe_load(text)
    return _substitute_env(raw)
