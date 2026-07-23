import tempfile
from datetime import datetime

from flipfinder.db import Database
from flipfinder.pipeline import market_stats as market_stats_mod
from flipfinder.pipeline.market_stats import get_time_on_market_stats


def _db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return Database(f.name)


class FakeSource:
    """Minimal stand-in for a SourceAdapter, just implementing what
    run_due_lifecycle_checks needs."""
    name = "fakesrc"

    def __init__(self, responses: dict):
        self.responses = responses  # listing_id -> True/False/None
        self.calls = []

    def check_still_active(self, listing_id):
        self.calls.append(listing_id)
        return self.responses.get(listing_id, True)


def test_mark_processed_and_has_processed():
    db = _db()
    assert db.has_processed("1", "src") is False
    db.mark_processed("1", "src", "cat", "title", 100, "url", True)
    assert db.has_processed("1", "src") is True


def test_register_for_tracking_not_due_until_delay_elapses():
    db = _db()
    db.register_for_tracking("1", "src", "cat", 100, now="2026-01-01T00:00:00+00:00", first_check_delay_days=1.0)

    not_yet = db.get_due_lifecycle_checks("cat", "src", now="2026-01-01T12:00:00+00:00", limit=10)
    assert not_yet == []

    due = db.get_due_lifecycle_checks("cat", "src", now="2026-01-02T00:00:01+00:00", limit=10)
    assert len(due) == 1
    assert due[0]["listing_id"] == "1"


def test_record_lifecycle_check_still_active_reschedules_with_backoff():
    db = _db()
    db.register_for_tracking("1", "src", "cat", 100, now="2026-01-01T00:00:00+00:00", first_check_delay_days=1.0)

    db.record_lifecycle_check(
        "1", "src", still_active=True, now="2026-01-02T00:00:00+00:00",
        backoff_days=[1, 2, 4], max_tracking_days=45,
    )
    # check_count becomes 1 -> backoff_days[min(1, 2)] = backoff_days[1] = 2 days
    due_before = db.get_due_lifecycle_checks("cat", "src", now="2026-01-03T12:00:00+00:00", limit=10)
    assert due_before == []  # not due yet (next check should be ~2026-01-04)

    due_after = db.get_due_lifecycle_checks("cat", "src", now="2026-01-04T00:00:01+00:00", limit=10)
    assert len(due_after) == 1


def test_record_lifecycle_check_delisted_stops_future_checks():
    db = _db()
    db.register_for_tracking("1", "src", "cat", 100, now="2026-01-01T00:00:00+00:00", first_check_delay_days=1.0)
    db.record_lifecycle_check(
        "1", "src", still_active=False, now="2026-01-02T00:00:00+00:00",
        backoff_days=[1, 2, 4], max_tracking_days=45,
    )

    due = db.get_due_lifecycle_checks("cat", "src", now="2026-06-01T00:00:00+00:00", limit=10)
    assert due == []

    rows = db.get_delisted_in_price_range("cat", 50, 150)
    assert len(rows) == 1
    assert rows[0]["first_checked_at"] == "2026-01-01T00:00:00+00:00"
    assert rows[0]["delisted_at"] == "2026-01-02T00:00:00+00:00"


def test_max_tracking_days_marks_stale_and_stops_checks():
    db = _db()
    db.register_for_tracking("1", "src", "cat", 100, now="2026-01-01T00:00:00+00:00", first_check_delay_days=1.0)
    # simulate a check happening 100 days later -- past max_tracking_days=45
    db.record_lifecycle_check(
        "1", "src", still_active=True, now="2026-04-11T00:00:00+00:00",
        backoff_days=[1, 2, 4], max_tracking_days=45,
    )
    due = db.get_due_lifecycle_checks("cat", "src", now="2027-01-01T00:00:00+00:00", limit=10)
    assert due == []  # marked stale, not active -- no more checks scheduled


def test_run_due_lifecycle_checks_updates_via_fake_source():
    db = _db()
    db.register_for_tracking("sold-item", "fakesrc", "cat", 100, now="2026-01-01T00:00:00+00:00", first_check_delay_days=1.0)
    db.register_for_tracking("still-up-item", "fakesrc", "cat", 100, now="2026-01-01T00:00:00+00:00", first_check_delay_days=1.0)

    source = FakeSource({"sold-item": False, "still-up-item": True})
    result = market_stats_mod.run_due_lifecycle_checks(
        db, source, "cat", now="2026-01-02T00:00:00+00:00",
        max_checks=10, backoff_days=[1, 2, 4], max_tracking_days=45,
    )
    assert result == {"checked": 2, "delisted": 1}
    assert set(source.calls) == {"sold-item", "still-up-item"}

    rows = db.get_delisted_in_price_range("cat", 50, 150)
    assert len(rows) == 1
    assert rows[0]["first_checked_at"] == "2026-01-01T00:00:00+00:00"


def test_market_stats_none_until_minimum_sample_reached():
    db = _db()
    db.register_for_tracking("1", "src", "cat", 100, now="2026-01-01T00:00:00+00:00", first_check_delay_days=1.0)
    db.record_lifecycle_check("1", "src", False, "2026-01-02T00:00:00+00:00", [1, 2, 4], 45)

    stats = get_time_on_market_stats(db, "cat", price=100, min_sample=5)
    assert stats is None


def test_market_stats_available_once_sample_reached():
    db = _db()
    for i in range(5):
        listing_id = f"item-{i}"
        db.register_for_tracking(listing_id, "src", "cat", 100, now="2026-01-01T00:00:00+00:00", first_check_delay_days=1.0)
        db.record_lifecycle_check(listing_id, "src", False, "2026-01-03T00:00:00+00:00", [1, 2, 4], 45)

    stats = get_time_on_market_stats(db, "cat", price=100, min_sample=5)
    assert stats is not None
    assert stats["sample_size"] == 5
    assert stats["median_days_on_market"] == 2.0  # Jan 1 -> Jan 3
