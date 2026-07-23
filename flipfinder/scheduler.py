"""
Scheduler.

Deliberately simple: either fixed times of day, or an interval within a
daily window. Neither is assumed to be "correct" -- that's an empirical
question about when sellers in your area actually post, which you don't
have data on yet. What this module DOES guarantee is that every run gets
logged (timestamp, category, counts, duration, errors) via db.record_poll,
regardless of which mode you use.

That log is the dataset a future adaptive scheduler needs. Don't skip
logging to "simplify" this later -- the whole point is to make the dumb
version observable so it can be replaced with confidence instead of guesses.

Each category runs on its own independent async loop, so different
categories can have completely different schedules without touching this
file.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta
from typing import Awaitable, Callable

logger = logging.getLogger("flipfinder.scheduler")


@dataclass
class ScheduleConfig:
    mode: str = "interval"                      # "fixed_times" or "interval"
    fixed_times: list[str] = field(default_factory=list)   # e.g. ["07:00", "12:30", "18:00"]
    interval_minutes: int = 60
    window_start: str = "00:00"
    window_end: str = "23:59"


def _parse_hhmm(s: str) -> dt_time:
    h, m = s.split(":")
    return dt_time(int(h), int(m))


def _next_fixed_time(fixed_times: list[str], now: datetime) -> datetime:
    today_times = sorted(
        now.replace(hour=t.hour, minute=t.minute, second=0, microsecond=0)
        for t in (_parse_hhmm(s) for s in fixed_times)
    )
    for t in today_times:
        if t > now:
            return t
    # nothing left today -> first configured time tomorrow
    return today_times[0] + timedelta(days=1)


def _next_interval_time(cfg: ScheduleConfig, now: datetime) -> datetime:
    window_start = now.replace(
        hour=_parse_hhmm(cfg.window_start).hour, minute=_parse_hhmm(cfg.window_start).minute,
        second=0, microsecond=0,
    )
    window_end = now.replace(
        hour=_parse_hhmm(cfg.window_end).hour, minute=_parse_hhmm(cfg.window_end).minute,
        second=0, microsecond=0,
    )

    if now < window_start:
        return window_start
    if now > window_end:
        return window_start + timedelta(days=1)

    candidate = now + timedelta(minutes=cfg.interval_minutes)
    if candidate > window_end:
        return window_start + timedelta(days=1)
    return candidate


def next_run_time(cfg: ScheduleConfig, now: datetime) -> datetime:
    if cfg.mode == "fixed_times":
        return _next_fixed_time(cfg.fixed_times, now)
    elif cfg.mode == "interval":
        return _next_interval_time(cfg, now)
    raise ValueError(f"Unknown schedule mode: {cfg.mode!r}")


PollCallback = Callable[[str], Awaitable[dict]]


class Scheduler:
    def __init__(self, schedules: dict[str, ScheduleConfig], on_poll: PollCallback):
        """
        schedules: category_id -> ScheduleConfig
        on_poll: async function(category_id) -> dict with keys
                 listings_seen, new_listings, passed_stage1, detail_calls_made, alerts_sent
                 (see main.py's run_poll_cycle for the expected shape)
        """
        self.schedules = schedules
        self.on_poll = on_poll

    async def _run_category_loop(self, category_id: str, cfg: ScheduleConfig) -> None:
        while True:
            now = datetime.now()
            run_at = next_run_time(cfg, now)
            sleep_seconds = max(0.0, (run_at - now).total_seconds())
            logger.info("Next %s poll at %s (sleeping %.0fs)", category_id, run_at, sleep_seconds)
            await asyncio.sleep(sleep_seconds)

            started = datetime.now()
            error = None
            result = {}
            try:
                result = await self.on_poll(category_id)
            except Exception as exc:  # noqa: BLE001 - never let one bad poll kill the loop
                logger.exception("Poll failed for %s", category_id)
                error = str(exc)
            duration_ms = int((datetime.now() - started).total_seconds() * 1000)

            # db.record_poll is called by main.py's on_poll wrapper, not here,
            # so this module doesn't need a Database dependency directly.
            if error:
                logger.warning("%s poll errored after %dms: %s", category_id, duration_ms, error)

    async def run_forever(self) -> None:
        await asyncio.gather(
            *(self._run_category_loop(cat_id, cfg) for cat_id, cfg in self.schedules.items())
        )
