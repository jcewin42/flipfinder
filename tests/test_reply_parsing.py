from flipfinder.notifier.reply_parsing import (
    parse_casual_feedback,
    parse_condition_at_sale,
    parse_item_count_correction,
)


def test_parse_casual_feedback_extracts_both_cost_and_sale():
    parsed = parse_casual_feedback("spent $40 on a carb kit, sold it for $380")
    assert parsed == {"actual_repair_cost": 40.0, "actual_resale_value": 380.0}


def test_parse_casual_feedback_handles_partial_info():
    assert parse_casual_feedback("just sold it for 500") == {"actual_resale_value": 500.0}
    assert parse_casual_feedback("paid 25 for a new spark plug") == {"actual_repair_cost": 25.0}


def test_parse_casual_feedback_empty_when_nothing_matches():
    assert parse_casual_feedback("looks like a good one, checking it out tomorrow") == {}


def test_parse_item_count_correction_common_phrasings():
    assert parse_item_count_correction("actually there's 2") == 2
    assert parse_item_count_correction("just 1 motor in the listing") == 1
    assert parse_item_count_correction("there are 3 total") == 3
    assert parse_item_count_correction("only 1 was running") == 1


def test_parse_item_count_correction_none_when_no_match():
    assert parse_item_count_correction("looks good, going to check it out") is None


def test_parse_condition_at_sale_recognizes_common_phrasings():
    assert parse_condition_at_sale("sold as-is, didn't service it") == "as-is, not serviced"
    assert parse_condition_at_sale("fully serviced and running great") == "serviced_running"
    assert parse_condition_at_sale("sold for parts only") == "parts_only"
    assert parse_condition_at_sale("not running, buyer knew") == "not_running"


def test_parse_condition_at_sale_none_when_unrecognized():
    assert parse_condition_at_sale("sold it to a nice guy from Craigslist") is None
