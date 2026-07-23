from flipfinder.geo import estimate_round_trip_hours, haversine_km


def test_haversine_zero_distance_for_same_point():
    assert haversine_km(38.8, -77.3, 38.8, -77.3) == 0.0


def test_haversine_known_distance_roughly_correct():
    # Washington, DC to Baltimore, MD is roughly 55-65km straight-line.
    dc = (38.9072, -77.0369)
    baltimore = (39.2904, -76.6122)
    dist = haversine_km(*dc, *baltimore)
    assert 50 < dist < 70


def test_round_trip_hours_doubles_one_way_distance():
    # 50km at 50km/h one-way = 1h; round trip = 2h
    assert estimate_round_trip_hours(50, avg_speed_kmh=50) == 2.0


def test_round_trip_hours_none_when_distance_unknown():
    assert estimate_round_trip_hours(None, avg_speed_kmh=50) is None
