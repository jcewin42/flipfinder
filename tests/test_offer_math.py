from flipfinder.models import ListingDetail, ValuationEstimate
from flipfinder.pipeline.offer import compute_offer
from flipfinder.routing.base import RoundTripEstimate


def _detail(price):
    return ListingDetail(
        id="1", source="sociavault", category_id="outboard_motors",
        title="t", description="d", price=price, url="u", photos=[],
        attributes={}, seller={}, location={}, posted_at=None,
    )


def _estimate(resale=800, repair_cost=50, repair_hours=1.0, confidence=0.9, item_count=1):
    return ValuationEstimate(
        estimated_resale_value=resale, estimated_repair_cost=repair_cost,
        estimated_repair_hours=repair_hours, confidence=confidence, reasoning="",
        estimated_item_count=item_count,
    )


def _travel(peak=0.5, offpeak=0.4, distance=20.0, traffic_aware=True):
    return RoundTripEstimate(distance_km=distance, peak_hours=peak, offpeak_hours=offpeak, traffic_aware=traffic_aware)


def test_good_flip_has_positive_offer_and_hourly_rate():
    detail = _detail(price=300)
    estimate = _estimate(resale=800, repair_cost=50, repair_hours=1.0)
    offer = compute_offer(
        detail, estimate, base_service_cost=150, base_service_hours=1.5,
        travel=_travel(peak=0.5, offpeak=0.4), travel_time_basis="peak",
        selling_overhead_hours=0.5, min_profit_flat=75, min_profit_pct=0.20,
    )

    # total_cost = 150 + 50 = 200; target_profit = max(75, 0.2*800=160) = 160
    # theoretical_max_offer = 800 - 200 - 160 = 440, capped at asking price 300
    assert offer.max_offer == 300
    assert offer.profit_if_bought_at_asking == 800 - 200 - 300  # 300
    # time = 0.5 pickup (peak) + (1.5 base + 1.0 extra) service + 0.5 overhead = 3.5h
    assert offer.total_time_hours == 3.5
    assert offer.estimated_hourly_rate == round(300 / 3.5, 2)
    assert offer.pickup_travel_hours_peak == 0.5
    assert offer.pickup_travel_hours_offpeak == 0.4


def test_travel_time_basis_selects_correct_value():
    detail = _detail(price=300)
    estimate = _estimate()
    travel = _travel(peak=2.0, offpeak=0.5)

    offer_peak = compute_offer(detail, estimate, 150, 1.5, travel, travel_time_basis="peak")
    offer_offpeak = compute_offer(detail, estimate, 150, 1.5, travel, travel_time_basis="offpeak")
    offer_avg = compute_offer(detail, estimate, 150, 1.5, travel, travel_time_basis="average")

    assert offer_peak.pickup_travel_hours == 2.0
    assert offer_offpeak.pickup_travel_hours == 0.5
    assert offer_avg.pickup_travel_hours == 1.25
    # same profit, less assumed travel time -> better hourly rate
    assert offer_offpeak.estimated_hourly_rate > offer_peak.estimated_hourly_rate


def test_bad_flip_can_have_negative_hourly_rate():
    detail = _detail(price=700)
    estimate = _estimate(resale=750, repair_cost=100, repair_hours=2.0)
    offer = compute_offer(detail, estimate, 150, 1.5, _travel(peak=1.0, offpeak=1.0))
    # total_cost = 250; target_profit = max(75, 150) = 150
    # profit at asking (700) = 750 - 250 - 700 = -200 -> negative hourly rate,
    # and it should NOT be clamped to zero (unlike the old flip_score).
    assert offer.profit_if_bought_at_asking < 0
    assert offer.estimated_hourly_rate < 0


def test_never_offers_more_than_asking_price():
    detail = _detail(price=100)
    estimate = _estimate(resale=5000, repair_cost=0, repair_hours=0, confidence=1.0)
    offer = compute_offer(detail, estimate, 50, 1.0, _travel(peak=0.5, offpeak=0.5))
    assert offer.max_offer <= 100


def test_longer_travel_time_reduces_hourly_rate_for_same_profit():
    detail = _detail(price=300)
    estimate = _estimate(resale=800, repair_cost=50, repair_hours=1.0)

    offer_close = compute_offer(detail, estimate, 150, 1.5, _travel(peak=0.5, offpeak=0.5))
    offer_far = compute_offer(detail, estimate, 150, 1.5, _travel(peak=4.0, offpeak=4.0))
    assert offer_far.estimated_hourly_rate < offer_close.estimated_hourly_rate


def test_unknown_distance_still_produces_a_rate():
    detail = _detail(price=300)
    estimate = _estimate(resale=800, repair_cost=50, repair_hours=1.0)
    unknown_travel = RoundTripEstimate(distance_km=None, peak_hours=None, offpeak_hours=None, traffic_aware=False)
    offer = compute_offer(detail, estimate, 150, 1.5, unknown_travel)
    assert offer.pickup_travel_hours is None
    assert offer.estimated_hourly_rate != 0


def test_multi_unit_listing_scales_service_cost_and_hours_by_item_count():
    detail = _detail(price=900)
    # 3 motors, total resale/repair figures as the AI would report them
    estimate = _estimate(resale=2400, repair_cost=150, repair_hours=3.0, item_count=3)
    offer = compute_offer(
        detail, estimate, base_service_cost=150, base_service_hours=1.5,
        travel=_travel(peak=1.0, offpeak=1.0), selling_overhead_hours=0.5,
    )
    # total_cost = (150 * 3) + 150 = 600; service_hours = (1.5*3) + 3.0 = 7.5
    assert offer.total_cost == 600.0
    assert offer.service_hours == 7.5
    # travel counted once, not multiplied by item count
    assert offer.total_time_hours == 1.0 + 7.5 + 0.5


def test_multi_unit_listing_can_justify_far_more_travel_time_than_single_unit():
    far_travel = _travel(peak=3.0, offpeak=3.0)  # a 3-hour round trip

    single = compute_offer(
        _detail(price=300), _estimate(resale=800, repair_cost=50, repair_hours=1.0, item_count=1),
        150, 1.5, far_travel,
    )
    triple = compute_offer(
        _detail(price=900), _estimate(resale=2400, repair_cost=150, repair_hours=3.0, item_count=3),
        150, 1.5, far_travel,
    )
    # Same round trip, same per-unit economics -- but tripling the units
    # should produce a meaningfully better hourly rate for the same drive,
    # since the fixed travel cost is now amortized across 3x the profit.
    assert triple.estimated_hourly_rate > single.estimated_hourly_rate


def test_item_count_of_zero_is_floored_to_one():
    detail = _detail(price=300)
    estimate = _estimate(item_count=0)
    offer = compute_offer(detail, estimate, 150, 1.5, _travel())
    assert offer.total_cost == 150 + 50  # treated as 1 unit, not 0
