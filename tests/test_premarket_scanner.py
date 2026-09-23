import json
from datetime import datetime
from pathlib import Path

from premarket_scanner import (
    EASTERN,
    aggregate_chart_bars,
    chart_bucket_time,
    rolling_average,
    rolling_stdev,
    squeeze_release_events,
    true_ranges,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _bars():
    return json.loads((FIXTURES / "squeeze_release_bars.json").read_text(encoding="utf-8"))


def _expected():
    return json.loads((FIXTURES / "squeeze_release_expected.json").read_text(encoding="utf-8"))


def test_matches_the_javascript_chart_event_for_event():
    """The scanner may never disagree with the flame the chart draws.

    The expected file is produced by scripts/generate_squeeze_fixture.mjs
    from the real frontend module. If this fails, fix the Python -- never
    the fixture.
    """
    bars = _bars()
    expected = _expected()
    assert any(expected[key] for key in expected), "fixture has no releases; regenerate it"
    for minutes in (60, 120, 240, 1440):
        assert squeeze_release_events(bars, minutes) == expected[str(minutes)], minutes


def test_rolling_stdev_is_population_with_partial_windows():
    values = rolling_stdev([1, 2, 3], 20)
    assert values[0] == 0
    assert values[1] == 0.5
    assert abs(values[2] - 0.816496580927726) < 1e-12


def test_true_range_seeds_from_high_low_then_uses_previous_close():
    bars = [
        {"high": 10.0, "low": 9.0, "close": 9.5},
        {"high": 12.0, "low": 11.0, "close": 11.5},
    ]
    # max(12-11, |12-9.5|, |11-9.5|) = 2.5
    assert true_ranges(bars) == [1.0, 2.5]


def _bucket_label(hour, minute, minutes):
    stamp = int(datetime(2026, 8, 20, hour, minute, tzinfo=EASTERN).timestamp())
    return datetime.fromtimestamp(chart_bucket_time(stamp, minutes), tz=EASTERN).strftime("%m-%d %H:%M")


def test_four_hour_buckets_use_the_tos_central_clock():
    """TOS aggregates equity bars from midnight Central, so the boundaries
    visible on this Eastern chart are 01:00 / 05:00 / 09:00 / 13:00."""
    assert _bucket_label(9, 30, 240) == "08-20 09:00"
    assert _bucket_label(8, 59, 240) == "08-20 05:00"
    assert _bucket_label(5, 0, 240) == "08-20 05:00"
    assert _bucket_label(0, 30, 240) == "08-19 21:00"  # previous day's bucket


def test_daily_and_two_hour_buckets_anchor_to_eastern_midnight():
    assert _bucket_label(7, 15, 1440) == "08-20 00:00"
    assert _bucket_label(7, 15, 120) == "08-20 06:00"


def test_one_hour_buckets_are_a_plain_utc_floor():
    assert chart_bucket_time(7200 + 59 * 60, 60) == 7200


def test_aggregation_takes_first_open_extremes_and_last_close():
    bars = [
        {"time": 1_700_000_000, "open": 1.0, "high": 5.0, "low": 0.5, "close": 2.0},
        {"time": 1_700_000_060, "open": 2.0, "high": 9.0, "low": 1.5, "close": 3.0},
    ]
    merged = aggregate_chart_bars(bars, 60)
    assert len(merged) == 1
    assert (merged[0]["open"], merged[0]["high"], merged[0]["low"], merged[0]["close"]) == (1.0, 9.0, 0.5, 3.0)


def test_aggregation_normalizes_like_the_chart():
    """normalizeChartCandleBars: sort, last-wins dedupe, and repair a high/low
    that has not caught up with open/close."""
    bars = [
        {"time": 1_700_000_060, "open": 2.0, "high": 2.5, "low": 1.5, "close": 3.0},
        {"time": 1_700_000_000, "open": 1.0, "high": 5.0, "low": 0.5, "close": 2.0},
        {"time": 1_700_000_000, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15},
    ]
    merged = aggregate_chart_bars(bars, 60)
    assert (merged[0]["open"], merged[0]["high"], merged[0]["low"], merged[0]["close"]) == (1.1, 3.0, 1.0, 3.0)


def test_rolling_average_fills_partial_windows():
    assert rolling_average([2, 4], 20) == [2.0, 3.0]
