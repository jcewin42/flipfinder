"""
SQLite storage.

One file, one source of truth, lives on the Pi. The Jetson stays stateless
(see inference/jetson_client.py) so it's replaceable without any data
migration. Every poll, every listing, every estimate, and every piece of
user feedback gets logged -- that history is what future scheduling and
learning improvements will be built on, so log generously even for things
you don't act on yet.

WAL mode is enabled so you can safely open this file in a separate SQLite
browser/tool while the app is running (e.g. to poke around poll_log or
listings) without blocking writes.

Delisting detection: earlier versions of this tried to infer "gone" from a
listing's absence in search results. That doesn't work on SociaVault --
search only returns the first page or so of results, FB's own ranking mixes
in older "suggested" listings unpredictably, and the status/listed_at
fields search returns are unreliable. So instead, listings that pass stage 1
get explicitly registered for periodic get_detail()-based lifecycle checks
(see lifecycle_tracking table + flipfinder/pipeline/market_stats.py), with
backoff so re-checking doesn't become its own API cost problem.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

logger = logging.getLogger("flipfinder.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id TEXT NOT NULL,
    source TEXT NOT NULL,
    category_id TEXT NOT NULL,
    title TEXT,
    price REAL,
    url TEXT,
    first_seen_at TEXT NOT NULL,
    passed_stage1 INTEGER,
    PRIMARY KEY (id, source)
);

CREATE TABLE IF NOT EXISTS lifecycle_tracking (
    listing_id TEXT NOT NULL,
    source TEXT NOT NULL,
    category_id TEXT NOT NULL,
    price_at_registration REAL,
    first_checked_at TEXT NOT NULL,
    last_checked_at TEXT NOT NULL,
    next_check_at TEXT NOT NULL,
    check_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',   -- 'active' | 'delisted' | 'stale'
    delisted_at TEXT,
    PRIMARY KEY (listing_id, source)
);

CREATE TABLE IF NOT EXISTS estimates (
    listing_id TEXT NOT NULL,
    source TEXT NOT NULL,
    category_id TEXT NOT NULL,
    features TEXT,                     -- JSON blob, category_profile.feature_vector() output
    estimated_resale_value REAL,
    estimated_repair_cost REAL,
    estimated_repair_hours REAL,
    estimated_item_count INTEGER,
    confidence REAL,
    reasoning TEXT,
    raw_response TEXT,
    max_offer REAL,
    pickup_travel_hours REAL,
    pickup_travel_hours_peak REAL,
    pickup_travel_hours_offpeak REAL,
    traffic_aware INTEGER,
    service_hours REAL,
    total_time_hours REAL,
    estimated_hourly_rate REAL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (listing_id, source)
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id TEXT NOT NULL,
    category_id TEXT NOT NULL,
    features TEXT NOT NULL,           -- JSON blob
    predicted_repair_cost REAL,
    predicted_resale_value REAL,
    actual_repair_cost REAL,
    actual_resale_value REAL,
    was_purchased INTEGER,
    notes TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS poll_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category_id TEXT NOT NULL,
    source TEXT NOT NULL,
    started_at TEXT NOT NULL,
    duration_ms INTEGER,
    listings_seen INTEGER,
    new_listings INTEGER,
    passed_stage1 INTEGER,
    detail_calls_made INTEGER,
    alerts_sent INTEGER,
    lifecycle_checks_made INTEGER,
    lifecycle_newly_delisted INTEGER,
    routing_calls_made INTEGER,
    error TEXT
);

CREATE TABLE IF NOT EXISTS discord_alerts (
    message_id TEXT PRIMARY KEY,
    listing_id TEXT NOT NULL,
    source TEXT NOT NULL,
    sent_at TEXT NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # -- listings (stage1/dedup bookkeeping) --------------------------------

    def has_processed(self, listing_id: str, source: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT passed_stage1 FROM listings WHERE id = ? AND source = ?",
                (listing_id, source),
            ).fetchone()
            return row is not None and row["passed_stage1"] is not None

    def mark_processed(
        self, listing_id: str, source: str, category_id: str, title: str,
        price: Optional[float], url: Optional[str], passed_stage1: bool,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO listings
                   (id, source, category_id, title, price, url, first_seen_at, passed_stage1)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (listing_id, source, category_id, title, price, url, now_iso(), int(passed_stage1)),
            )

    # -- lifecycle tracking (delisting detection via periodic rechecks) -----
    #
    # Only listings that passed stage 1 get registered -- that keeps the
    # volume of periodic get_detail() rechecks proportional to "things you
    # actually cared about," not the raw search firehose.

    def register_for_tracking(
        self, listing_id: str, source: str, category_id: str, price: Optional[float],
        now: str, first_check_delay_days: float,
    ) -> None:
        next_check = _add_days(now, first_check_delay_days)
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO lifecycle_tracking
                   (listing_id, source, category_id, price_at_registration,
                    first_checked_at, last_checked_at, next_check_at, check_count, status)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'active')""",
                (listing_id, source, category_id, price, now, now, next_check),
            )

    def get_due_lifecycle_checks(self, category_id: str, source: str, now: str, limit: int) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM lifecycle_tracking
                   WHERE category_id = ? AND source = ? AND status = 'active' AND next_check_at <= ?
                   ORDER BY next_check_at ASC LIMIT ?""",
                (category_id, source, now, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def record_lifecycle_check(
        self, listing_id: str, source: str, still_active: Optional[bool], now: str,
        backoff_days: list[float], max_tracking_days: float,
    ) -> None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM lifecycle_tracking WHERE listing_id = ? AND source = ?",
                (listing_id, source),
            ).fetchone()
            if row is None:
                return

            if still_active is False:
                conn.execute(
                    "UPDATE lifecycle_tracking SET status = 'delisted', delisted_at = ?, last_checked_at = ? "
                    "WHERE listing_id = ? AND source = ?",
                    (now, now, listing_id, source),
                )
                return

            # still_active is True or None (inconclusive) -- either way, keep
            # tracking, but only None doesn't count as a "confirmed still up"
            # check for backoff purposes... in practice we still advance the
            # schedule so a persistently-erroring listing doesn't get checked
            # every single poll forever.
            new_count = row["check_count"] + 1
            first_checked = datetime.fromisoformat(row["first_checked_at"])
            now_dt = datetime.fromisoformat(now)
            age_days = (now_dt - first_checked).total_seconds() / 86400

            if age_days >= max_tracking_days:
                conn.execute(
                    "UPDATE lifecycle_tracking SET status = 'stale', last_checked_at = ?, check_count = ? "
                    "WHERE listing_id = ? AND source = ?",
                    (now, new_count, listing_id, source),
                )
                return

            delay = backoff_days[min(new_count, len(backoff_days) - 1)]
            next_check = _add_days(now, delay)
            conn.execute(
                "UPDATE lifecycle_tracking SET last_checked_at = ?, next_check_at = ?, check_count = ? "
                "WHERE listing_id = ? AND source = ?",
                (now, next_check, new_count, listing_id, source),
            )

    def get_delisted_in_price_range(self, category_id: str, low: float, high: float) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT first_checked_at, delisted_at, price_at_registration FROM lifecycle_tracking
                   WHERE category_id = ? AND status = 'delisted'
                     AND price_at_registration BETWEEN ? AND ?""",
                (category_id, low, high),
            ).fetchall()
            return [dict(r) for r in rows]

    # -- estimates ----------------------------------------------------------

    def record_estimate(
        self,
        listing_id: str,
        source: str,
        category_id: str,
        features: dict,
        estimated_resale_value: float,
        estimated_repair_cost: float,
        estimated_repair_hours: float,
        estimated_item_count: int,
        confidence: float,
        reasoning: str,
        raw_response: str,
        max_offer: float,
        pickup_travel_hours: Optional[float],
        pickup_travel_hours_peak: Optional[float],
        pickup_travel_hours_offpeak: Optional[float],
        traffic_aware: bool,
        service_hours: float,
        total_time_hours: float,
        estimated_hourly_rate: float,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO estimates
                   (listing_id, source, category_id, features, estimated_resale_value,
                    estimated_repair_cost, estimated_repair_hours, estimated_item_count,
                    confidence, reasoning, raw_response,
                    max_offer, pickup_travel_hours, pickup_travel_hours_peak, pickup_travel_hours_offpeak,
                    traffic_aware, service_hours, total_time_hours, estimated_hourly_rate, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (listing_id, source, category_id, json.dumps(features), estimated_resale_value,
                 estimated_repair_cost, estimated_repair_hours, estimated_item_count,
                 confidence, reasoning, raw_response,
                 max_offer, pickup_travel_hours, pickup_travel_hours_peak, pickup_travel_hours_offpeak,
                 int(traffic_aware), service_hours, total_time_hours, estimated_hourly_rate, now_iso()),
            )

    # -- feedback -----------------------------------------------------------

    def record_feedback(
        self,
        listing_id: str,
        category_id: str,
        features: dict,
        predicted_repair_cost: Optional[float],
        predicted_resale_value: Optional[float],
        actual_repair_cost: Optional[float],
        actual_resale_value: Optional[float],
        was_purchased: Optional[bool],
        notes: str = "",
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO feedback
                   (listing_id, category_id, features, predicted_repair_cost,
                    predicted_resale_value, actual_repair_cost, actual_resale_value,
                    was_purchased, notes, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (listing_id, category_id, json.dumps(features), predicted_repair_cost,
                 predicted_resale_value, actual_repair_cost, actual_resale_value,
                 None if was_purchased is None else int(was_purchased), notes, now_iso()),
            )

    def get_feedback_for_category(self, category_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM feedback WHERE category_id = ?", (category_id,)
            ).fetchall()
            out = []
            for row in rows:
                d = dict(row)
                d["features"] = json.loads(d["features"])
                out.append(d)
            return out

    # -- poll log -------------------------------------------------------------

    def record_poll(
        self,
        category_id: str,
        source: str,
        started_at: str,
        duration_ms: int,
        listings_seen: int,
        new_listings: int,
        passed_stage1: int,
        detail_calls_made: int,
        alerts_sent: int,
        lifecycle_checks_made: int = 0,
        lifecycle_newly_delisted: int = 0,
        routing_calls_made: int = 0,
        error: Optional[str] = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO poll_log
                   (category_id, source, started_at, duration_ms, listings_seen,
                    new_listings, passed_stage1, detail_calls_made, alerts_sent,
                    lifecycle_checks_made, lifecycle_newly_delisted, routing_calls_made, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (category_id, source, started_at, duration_ms, listings_seen,
                 new_listings, passed_stage1, detail_calls_made, alerts_sent,
                 lifecycle_checks_made, lifecycle_newly_delisted, routing_calls_made, error),
            )
        logger.info(
            "poll %s/%s: %d seen, %d new, %d passed stage1, %d detail calls, "
            "%d alerts, %d lifecycle checks (%d newly delisted), %d routing API calls, %dms%s",
            category_id, source, listings_seen, new_listings, passed_stage1,
            detail_calls_made, alerts_sent, lifecycle_checks_made, lifecycle_newly_delisted,
            routing_calls_made, duration_ms, f", error={error}" if error else "",
        )

    # -- discord alert -> listing mapping (for reply-based feedback) --------

    def record_discord_alert(self, message_id: str, listing_id: str, source: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO discord_alerts (message_id, listing_id, source, sent_at) VALUES (?, ?, ?, ?)",
                (message_id, listing_id, source, now_iso()),
            )

    def get_listing_id_for_message(self, message_id: str) -> Optional[tuple[str, str]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT listing_id, source FROM discord_alerts WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            return (row["listing_id"], row["source"]) if row else None

    def get_estimate(self, listing_id: str, source: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM estimates WHERE listing_id = ? AND source = ?",
                (listing_id, source),
            ).fetchone()
            if row is None:
                return None
            d = dict(row)
            d["features"] = json.loads(d["features"]) if d["features"] else {}
            return d

    def get_estimate_by_listing_id(self, listing_id: str) -> Optional[dict]:
        """Look up an estimate by listing_id alone, for callers (like the
        Discord bot) that don't know which source it came from."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM estimates WHERE listing_id = ? LIMIT 1", (listing_id,)
            ).fetchone()
            if row is None:
                return None
            d = dict(row)
            d["features"] = json.loads(d["features"]) if d["features"] else {}
            return d


def _add_days(iso_timestamp: str, days: float) -> str:
    from datetime import timedelta
    dt = datetime.fromisoformat(iso_timestamp)
    return (dt + timedelta(days=days)).isoformat()
