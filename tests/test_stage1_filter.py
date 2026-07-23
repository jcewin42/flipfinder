from flipfinder.categories.outboard_motors import OutboardMotorProfile
from flipfinder.models import ListingSummary
from flipfinder.pipeline.stage1_filter import passes_stage1


def _profile():
    return OutboardMotorProfile(
        latitude=38.8, longitude=-77.3, radius_km=40,
        base_service_cost=150, price_min=50, price_max=6000,
    )


def _summary(title, price=500):
    return ListingSummary(
        id="1", source="sociavault", category_id="outboard_motors",
        title=title, price=price, url="u", thumbnail_url=None, posted_at=None,
    )


def test_accepts_plausible_outboard_listing():
    profile = _profile()
    assert profile.quick_filter(_summary("Yamaha 40hp outboard motor, runs great")) is True


def test_rejects_parts_only_listing():
    profile = _profile()
    assert profile.quick_filter(_summary("Lower unit only for Mercury 60hp")) is False


def test_rejects_trolling_motor():
    profile = _profile()
    assert profile.quick_filter(_summary("MinnKota trolling motor 55lb thrust")) is False


def test_rejects_out_of_range_price():
    profile = _profile()
    assert profile.quick_filter(_summary("Yamaha 40hp outboard", price=10)) is False
    assert profile.quick_filter(_summary("Yamaha 40hp outboard", price=9000)) is False


def test_rejects_boat_and_motor_package():
    profile = _profile()
    assert profile.quick_filter(_summary("14ft boat and motor package, Evinrude 25hp")) is False


def test_passes_stage1_rejects_beyond_max_distance():
    profile = _profile()
    summary = _summary("Yamaha 40hp outboard, runs great")
    assert passes_stage1(summary, profile, distance_km=150, max_distance_km=60) is False


def test_passes_stage1_accepts_within_max_distance():
    profile = _profile()
    summary = _summary("Yamaha 40hp outboard, runs great")
    assert passes_stage1(summary, profile, distance_km=20, max_distance_km=60) is True


def test_passes_stage1_fails_open_when_distance_unknown():
    profile = _profile()
    summary = _summary("Yamaha 40hp outboard, runs great")
    # No coordinates available for this listing -- don't silently drop a
    # possibly-good listing just because distance couldn't be computed.
    assert passes_stage1(summary, profile, distance_km=None, max_distance_km=60) is True


def test_passes_stage1_ignores_photo_by_default():
    profile = _profile()
    summary = _summary("Yamaha 40hp outboard, runs great")
    assert summary.thumbnail_url is None
    assert passes_stage1(summary, profile) is True  # require_photo defaults False


def test_passes_stage1_rejects_no_photo_when_required():
    profile = _profile()
    summary = _summary("Yamaha 40hp outboard, runs great")
    assert passes_stage1(summary, profile, require_photo=True) is False


def test_passes_stage1_accepts_with_photo_when_required():
    profile = _profile()
    summary = _summary("Yamaha 40hp outboard, runs great")
    summary.thumbnail_url = "http://example.com/thumb.jpg"
    assert passes_stage1(summary, profile, require_photo=True) is True
