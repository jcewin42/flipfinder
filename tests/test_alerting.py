from flipfinder.pipeline.alerting import should_alert


def test_alerts_when_hourly_rate_clears_bar():
    assert should_alert(hourly_rate=50, confidence=0.8, item_count_confidence=0.9,
                         min_hourly_rate=20, item_count_confidence_threshold=0.6) is True


def test_does_not_alert_when_hourly_rate_below_bar_and_count_confident():
    assert should_alert(hourly_rate=5, confidence=0.8, item_count_confidence=0.9,
                         min_hourly_rate=20, item_count_confidence_threshold=0.6) is False


def test_alerts_anyway_when_item_count_uncertain_even_if_rate_is_low():
    # This is the whole point: an undercounted multi-unit listing could look
    # bad under the wrong assumed count and would otherwise never reach you.
    assert should_alert(hourly_rate=5, confidence=0.8, item_count_confidence=0.3,
                         min_hourly_rate=20, item_count_confidence_threshold=0.6) is True


def test_never_alerts_on_zero_confidence_regardless_of_other_signals():
    assert should_alert(hourly_rate=100, confidence=0.0, item_count_confidence=0.2,
                         min_hourly_rate=20, item_count_confidence_threshold=0.6) is False


def test_item_count_confidence_exactly_at_threshold_does_not_bypass():
    # Only STRICTLY below the threshold counts as "uncertain".
    assert should_alert(hourly_rate=5, confidence=0.8, item_count_confidence=0.6,
                         min_hourly_rate=20, item_count_confidence_threshold=0.6) is False


def test_none_threshold_alerts_on_everything_with_a_usable_valuation():
    # min_hourly_rate=None is the "see everything while calibrating" mode --
    # any usable valuation alerts regardless of rate, even a very low or
    # negative one.
    assert should_alert(hourly_rate=-50, confidence=0.8, item_count_confidence=0.9,
                         min_hourly_rate=None, item_count_confidence_threshold=0.6) is True


def test_none_threshold_still_respects_zero_confidence():
    # The confidence gate still applies -- None only disables the rate bar.
    assert should_alert(hourly_rate=100, confidence=0.0, item_count_confidence=0.9,
                         min_hourly_rate=None, item_count_confidence_threshold=0.6) is False
