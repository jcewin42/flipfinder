import requests

from flipfinder.routing.google_routes import GoogleRoutesBackend, _next_departure
from flipfinder.routing.haversine import HaversineRoutingBackend


def test_haversine_backend_same_value_for_peak_and_offpeak():
    backend = HaversineRoutingBackend(avg_speed_kmh=50)
    est = backend.estimate_round_trip(38.8, -77.3, 39.0, -77.5)
    assert est.traffic_aware is False
    assert est.peak_hours == est.offpeak_hours
    assert est.distance_km is not None


def test_haversine_backend_none_when_destination_unknown():
    backend = HaversineRoutingBackend()
    est = backend.estimate_round_trip(38.8, -77.3, None, None)
    assert est.distance_km is None
    assert est.peak_hours is None
    assert est.offpeak_hours is None


def test_next_departure_rolls_to_tomorrow_if_time_passed():
    # If "now" were after 08:00, next_departure("08:00") must be in the future.
    dep = _next_departure("08:00", weekday_only=False)
    from datetime import datetime
    assert dep > datetime.now().astimezone()


def test_next_departure_skips_weekend_when_weekday_only():
    dep = _next_departure("08:00", weekday_only=True)
    assert dep.weekday() < 5  # Monday=0 .. Friday=4


def test_haversine_backend_reports_zero_api_calls():
    backend = HaversineRoutingBackend(avg_speed_kmh=50)
    est = backend.estimate_round_trip(38.8, -77.3, 39.0, -77.5)
    assert est.api_calls == 0


def test_google_routes_counts_two_calls_when_compute_both():
    backend = GoogleRoutesBackend(api_key="fake-key", compute_both=True)

    def fake_compute_route(*args, **kwargs):
        backend.total_calls_made += 1
        return 20.0, 0.5  # (distance_km, one_way_hours)

    backend._compute_route = fake_compute_route
    est = backend.estimate_round_trip(38.8, -77.3, 39.0, -77.5)
    assert est.api_calls == 2
    assert est.traffic_aware is True


def test_google_routes_counts_one_call_when_compute_both_false():
    backend = GoogleRoutesBackend(api_key="fake-key", compute_both=False, travel_time_basis="peak")

    def fake_compute_route(*args, **kwargs):
        backend.total_calls_made += 1
        return 20.0, 0.5

    backend._compute_route = fake_compute_route
    est = backend.estimate_round_trip(38.8, -77.3, 39.0, -77.5)
    assert est.api_calls == 1
    assert est.offpeak_hours is None


def test_google_routes_fallback_still_reports_attempted_call_count():
    backend = GoogleRoutesBackend(api_key="fake-key", compute_both=True, avg_speed_kmh_fallback=50.0)
    call_count = {"n": 0}

    def flaky_compute_route(*args, **kwargs):
        call_count["n"] += 1
        backend.total_calls_made += 1  # simulate the real method's own bookkeeping
        if call_count["n"] == 2:
            raise requests.exceptions.ConnectionError("simulated failure on 2nd call")
        return 20.0, 0.5

    backend._compute_route = flaky_compute_route
    est = backend.estimate_round_trip(38.8, -77.3, 39.0, -77.5)
    # Fell back to haversine for the result, but should still report that
    # 2 real network attempts were made (both count against quota/billing).
    assert est.traffic_aware is False
    assert est.api_calls == 2


def test_google_routes_falls_back_to_haversine_on_request_failure():
    backend = GoogleRoutesBackend(api_key="fake-key", avg_speed_kmh_fallback=50.0)

    def broken_post(*args, **kwargs):
        raise requests.exceptions.ConnectionError("simulated network failure")

    backend._session.post = broken_post  # throwaway instance, no need to restore

    est = backend.estimate_round_trip(38.8, -77.3, 39.0, -77.5)
    # Should have silently degraded to the haversine estimate rather than raising.
    assert est.traffic_aware is False
    assert est.peak_hours is not None
    assert est.peak_hours == est.offpeak_hours
