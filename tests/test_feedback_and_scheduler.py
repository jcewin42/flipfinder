import tempfile
from datetime import datetime

from flipfinder.db import Database
from flipfinder.models import FeedbackEntry
from flipfinder.pipeline.feedback_store import FeedbackStore
from flipfinder.scheduler import ScheduleConfig, next_run_time


def test_find_similar_ranks_closer_features_first():
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db = Database(f.name)
        store = FeedbackStore(db)

        store.record(FeedbackEntry(
            listing_id="a", category_id="outboard_motors",
            features={"brand": "yamaha", "hp": 40, "year": 2015},
            predicted_repair_cost=50, predicted_resale_value=700,
            actual_repair_cost=80, actual_resale_value=650, was_purchased=True,
        ))
        store.record(FeedbackEntry(
            listing_id="b", category_id="outboard_motors",
            features={"brand": "mercury", "hp": 150, "year": 2005},
            predicted_repair_cost=100, predicted_resale_value=2000,
            actual_repair_cost=300, actual_resale_value=1800, was_purchased=True,
        ))

        similar = store.find_similar("outboard_motors", {"brand": "yamaha", "hp": 45, "year": 2016}, k=2)
        assert similar[0].listing_id == "a"  # closer match should rank first


def test_fixed_times_schedule_picks_next_time_today():
    cfg = ScheduleConfig(mode="fixed_times", fixed_times=["07:00", "12:00", "18:00"])
    now = datetime(2026, 7, 21, 10, 0)
    result = next_run_time(cfg, now)
    assert result == datetime(2026, 7, 21, 12, 0)


def test_fixed_times_schedule_wraps_to_tomorrow():
    cfg = ScheduleConfig(mode="fixed_times", fixed_times=["07:00", "12:00", "18:00"])
    now = datetime(2026, 7, 21, 19, 0)
    result = next_run_time(cfg, now)
    assert result == datetime(2026, 7, 22, 7, 0)


def test_interval_schedule_respects_window():
    cfg = ScheduleConfig(mode="interval", interval_minutes=30, window_start="08:00", window_end="20:00")
    now = datetime(2026, 7, 21, 19, 50)
    result = next_run_time(cfg, now)
    # 19:50 + 30min = 20:20, past window_end 20:00 -> wraps to tomorrow's window_start
    assert result == datetime(2026, 7, 22, 8, 0)


def test_interval_schedule_before_window_jumps_to_start():
    cfg = ScheduleConfig(mode="interval", interval_minutes=30, window_start="08:00", window_end="20:00")
    now = datetime(2026, 7, 21, 5, 0)
    result = next_run_time(cfg, now)
    assert result == datetime(2026, 7, 21, 8, 0)
