"""
Logging setup shared by both long-running mode and one-shot mode.

Writes to console (so you can watch it live over SSH) AND to a rotating log
file (so you can look back at what happened overnight without having left a
terminal open). 5MB x 5 files by default -- plenty for a hobby-scale poller,
easy to bump in config if you want more history.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def setup_logging(log_dir: str = "logs", level: str = "INFO") -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s")

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        Path(log_dir) / "flipfinder.log", maxBytes=5 * 1024 * 1024, backupCount=5,
    )
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # discord.py is chatty at INFO (gateway heartbeats, etc.) -- keep it at
    # WARNING so it doesn't drown out flipfinder's own logs.
    logging.getLogger("discord").setLevel(logging.WARNING)
