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


def test_rejects_irrelevant_listing_with_no_motor_signal():
    # SociaVault's search results include unrelated "suggested" listings
    # that don't hit any EXCLUDE_KEYWORDS -- confirmed live (Chrome Hearts
    # hat, Vinyl LPs, a basketball chain net, a plain cooler all passed
    # stage 1 before this positive-relevance check existed).
    profile = _profile()
    assert profile.quick_filter(_summary("Chrome hearts hat")) is False
    assert profile.quick_filter(_summary("Vinyl LP's")) is False
    assert profile.quick_filter(_summary("Cooler")) is False


def test_accepts_listing_with_only_a_brand_name_and_hp():
    # No "motor" or "outboard" in the title at all -- brand + HP alone
    # should still count as relevant.
    profile = _profile()
    assert profile.quick_filter(_summary("Mercury 60hp, good condition")) is True


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
