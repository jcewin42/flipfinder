import tempfile

from flipfinder.db import Database


def _db():
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    return Database(f.name)


def test_feedback_upsert_creates_one_row_per_listing():
    db = _db()
    db.record_feedback(
        listing_id="1", source="sociavault", category_id="outboard_motors",
        features={"brand": "yamaha"}, predicted_repair_cost=50, predicted_resale_value=700,
        actual_repair_cost=None, actual_resale_value=None, was_purchased=None,
        predicted_item_count=1, actual_item_count=2, notes="confirmed 2 units",
    )
    db.record_feedback(
        listing_id="1", source="sociavault", category_id="outboard_motors",
        features={"brand": "yamaha"}, predicted_repair_cost=50, predicted_resale_value=700,
        actual_repair_cost=80, actual_resale_value=650, was_purchased=True,
        predicted_item_count=1, notes="finally sold it",
    )
    rows = db.get_feedback_for_category("outboard_motors")
    assert len(rows) == 1  # not two fragmented rows
    row = rows[0]
    assert row["actual_item_count"] == 2       # preserved from the first call
    assert row["actual_repair_cost"] == 80       # added by the second call
    assert row["actual_resale_value"] == 650
    assert "confirmed 2 units" in row["notes"]
    assert "finally sold it" in row["notes"]


def test_feedback_upsert_does_not_clobber_existing_value_with_none():
    db = _db()
    db.record_feedback(
        listing_id="1", source="sociavault", category_id="outboard_motors",
        features={}, predicted_repair_cost=50, predicted_resale_value=700,
        actual_repair_cost=80, actual_resale_value=None, was_purchased=True,
    )
    # A later call only reporting resale value shouldn't erase the repair cost already recorded.
    db.record_feedback(
        listing_id="1", source="sociavault", category_id="outboard_motors",
        features={}, predicted_repair_cost=50, predicted_resale_value=700,
        actual_repair_cost=None, actual_resale_value=650, was_purchased=None,
    )
    rows = db.get_feedback_for_category("outboard_motors")
    assert len(rows) == 1
    assert rows[0]["actual_repair_cost"] == 80
    assert rows[0]["actual_resale_value"] == 650
    assert rows[0]["was_purchased"] == 1  # also not clobbered by the later None


def test_feedback_upsert_condition_at_sale_round_trips():
    db = _db()
    db.record_feedback(
        listing_id="1", source="sociavault", category_id="outboard_motors",
        features={}, predicted_repair_cost=50, predicted_resale_value=700,
        actual_repair_cost=80, actual_resale_value=650, was_purchased=True,
        condition_at_sale="serviced_running",
    )
    row = db.get_feedback_for_category("outboard_motors")[0]
    assert row["condition_at_sale"] == "serviced_running"


def test_different_sources_get_separate_feedback_rows():
    db = _db()
    db.record_feedback(
        listing_id="same-id", source="sociavault", category_id="outboard_motors",
        features={}, predicted_repair_cost=1, predicted_resale_value=1,
        actual_repair_cost=1, actual_resale_value=1, was_purchased=True,
    )
    db.record_feedback(
        listing_id="same-id", source="own_monitor", category_id="outboard_motors",
        features={}, predicted_repair_cost=2, predicted_resale_value=2,
        actual_repair_cost=2, actual_resale_value=2, was_purchased=True,
    )
    rows = db.get_feedback_for_category("outboard_motors")
    assert len(rows) == 2
