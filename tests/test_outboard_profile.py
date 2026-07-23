import json

from flipfinder.categories.outboard_motors import OutboardMotorProfile
from flipfinder.models import ListingDetail


def _profile():
    return OutboardMotorProfile(
        latitude=38.8, longitude=-77.3, radius_km=40,
        base_service_cost=150, base_service_hours=1.5,
    )


def _detail(title="Yamaha 40hp outboard", description=""):
    return ListingDetail(
        id="1", source="sociavault", category_id="outboard_motors",
        title=title, description=description, price=500, url="u", photos=[],
        attributes={}, seller={}, location={}, posted_at=None,
    )


def test_parse_valuation_response_defaults_item_count_to_one():
    profile = _profile()
    raw = json.dumps({
        "estimated_resale_value": 500, "estimated_repair_cost": 50,
        "estimated_repair_hours": 1.0, "confidence": 0.8, "reasoning": "fine",
    })
    estimate = profile.parse_valuation_response(raw)
    assert estimate.estimated_item_count == 1


def test_parse_valuation_response_reads_explicit_item_count():
    profile = _profile()
    raw = json.dumps({
        "estimated_resale_value": 2000, "estimated_repair_cost": 200,
        "estimated_repair_hours": 4.0, "estimated_item_count": 4,
        "confidence": 0.7, "reasoning": "estate lot of 4 motors",
    })
    estimate = profile.parse_valuation_response(raw)
    assert estimate.estimated_item_count == 4


def test_parse_valuation_response_floors_invalid_item_count_to_one():
    profile = _profile()
    raw = json.dumps({
        "estimated_resale_value": 500, "estimated_repair_cost": 50,
        "estimated_repair_hours": 1.0, "estimated_item_count": 0,
        "confidence": 0.8, "reasoning": "fine",
    })
    estimate = profile.parse_valuation_response(raw)
    assert estimate.estimated_item_count == 1


def test_feature_vector_guesses_item_count_from_title():
    profile = _profile()
    detail = _detail(title="3 outboards for sale, all running")
    features = profile.feature_vector(detail)
    assert features["guessed_item_count"] == 3


def test_feature_vector_guesses_lot_of_phrasing():
    profile = _profile()
    detail = _detail(title="Estate sale", description="Lot of 5 old outboard motors, as-is")
    features = profile.feature_vector(detail)
    assert features["guessed_item_count"] == 5


def test_feature_vector_defaults_to_one_for_ordinary_listing():
    profile = _profile()
    detail = _detail(title="Yamaha 40hp outboard motor")
    features = profile.feature_vector(detail)
    assert features["guessed_item_count"] == 1
