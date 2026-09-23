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


# --- Window filter, match rule and strength score -------------------------

from premarket_scanner import (  # noqa: E402
    premarket_scan_row,
    premarket_window,
    score_strength,
    window_call_signals,
    window_fires,
)


def _now():
    return datetime(2026, 8, 20, 8, 15, tzinfo=EASTERN)


def _call(label, family, when):
    return {
        "label": label,
        "family": family,
        "color": "yellow" if family == "4x8" else "cyan",
        "direction": "CALL",
        "time": int(when.timestamp()),
        "liveForming": True,
    }


def _tape_until(when):
    cutoff = int(when.timestamp())
    return [bar for bar in _bars() if int(bar["time"]) <= cutoff]


def _fires_at(when):
    start, end = premarket_window(when)
    return window_fires(_tape_until(when), start, end)


def test_window_spans_0600_to_0930_eastern():
    start, end = premarket_window(_now())
    assert datetime.fromtimestamp(start, tz=EASTERN).strftime("%H:%M") == "06:00"
    assert datetime.fromtimestamp(end, tz=EASTERN).strftime("%H:%M") == "09:30"


def test_window_boundaries_are_inclusive():
    start, end = premarket_window(_now())
    inside = [
        _call("CALL2H", "4x8", datetime.fromtimestamp(start, tz=EASTERN)),
        _call("CALL4H", "9x20", datetime.fromtimestamp(end, tz=EASTERN)),
    ]
    outside = [
        _call("CALL2H", "4x8", datetime(2026, 8, 20, 5, 59, 59, tzinfo=EASTERN)),
        _call("CALL2H", "4x8", datetime(2026, 8, 20, 9, 30, 1, tzinfo=EASTERN)),
    ]
    assert len(window_call_signals(inside + outside, start, end)) == 2


def test_only_confirmed_call_labels_count():
    """C2H/C4H mean the higher timeframe is not confirming; the user asked
    for CALL specifically, so they are context, never score."""
    start, end = premarket_window(_now())
    when = datetime(2026, 8, 20, 7, 0, tzinfo=EASTERN)
    signals = [
        _call("CALL2H", "4x8", when),
        _call("C2H", "4x8", when),
        _call("C4H", "9x20", when),
        {**_call("CALL2H", "4x8", when), "direction": "PUT"},
    ]
    kept = window_call_signals(signals, start, end)
    assert [signal["label"] for signal in kept] == ["CALL2H"]


def test_strength_thresholds():
    def calls(count):
        return [_call("CALL2H", "4x8", _now())] * count

    assert score_strength(calls(1), [])[1] == "WEAK"
    assert score_strength(calls(2), [])[1] == "WEAK"
    assert score_strength(calls(3), [])[1] == "MODERATE"
    assert score_strength(calls(2), [{"minutes": 60}])[1] == "MODERATE"
    assert score_strength(calls(2), [{"minutes": 60}, {"minutes": 120}]) == (4, "STRONG")


def test_intraday_fires_qualify_on_bucket_close_inside_the_window():
    # 2026-11-18: the 05:00-06:00 1h and 04:00-06:00 2h buckets both release
    # and close exactly on the 06:00 window open.
    fires = _fires_at(datetime(2026, 11, 18, 9, 30, tzinfo=EASTERN))
    assert sorted(fire["label"] for fire in fires) == ["1h", "2h"]
    assert all(
        datetime.fromtimestamp(fire["closeTime"], tz=EASTERN).strftime("%H:%M") == "06:00"
        for fire in fires
    )
    # 2026-10-16: the 07:00-08:00 1h bucket closes at 08:00.
    labels = [fire["label"] for fire in _fires_at(datetime(2026, 10, 16, 9, 0, tzinfo=EASTERN))]
    assert labels == ["1h"]


def test_a_still_forming_bucket_is_not_a_fire_yet():
    """Regression guard for live commit 461d4af: 2026-10-15's 06:00-07:00
    release only exists once that bucket has closed."""
    assert _fires_at(datetime(2026, 10, 15, 6, 55, tzinfo=EASTERN)) == []
    labels = [fire["label"] for fire in _fires_at(datetime(2026, 10, 15, 7, 0, tzinfo=EASTERN))]
    assert labels == ["1h"]


def test_most_recent_closed_daily_release_counts_with_its_own_date():
    # The fixture's only daily release is Friday 2026-10-23. On Monday's
    # premarket that Friday candle is the most recently CLOSED daily.
    now = datetime(2026, 10, 26, 7, 0, tzinfo=EASTERN)
    daily = [fire for fire in _fires_at(now) if fire["label"] == "D"]
    assert len(daily) == 1
    assert datetime.fromtimestamp(daily[0]["bucketTime"], tz=EASTERN).date().isoformat() == "2026-10-23"

    row = premarket_scan_row("NVDA", {"bars": _tape_until(now), "mtfSignals": []}, now)
    assert row is not None
    assert row["fires"] == ["D"]
    assert row["fireDates"]["D"] == "2026-10-23"


def test_an_older_daily_release_is_never_reported():
    """Defect from the first build: the daily branch took the newest release
    anywhere in the tape, so an August scan reported a July fire the chart
    would not draw. Only the most recently CLOSED daily candle counts."""
    for day in (27, 28, 29, 30):
        now = datetime(2026, 10, day, 7, 0, tzinfo=EASTERN)
        assert [fire for fire in _fires_at(now) if fire["label"] == "D"] == [], day
    now = datetime(2026, 11, 20, 7, 0, tzinfo=EASTERN)
    row = premarket_scan_row("NVDA", {"bars": _tape_until(now), "mtfSignals": []}, now)
    assert row is None or "D" not in row["fires"]


def test_each_fire_timeframe_alone_is_enough_for_a_row():
    payload = {"bars": [{"time": 1, "close": 100.0}], "mtfSignals": []}
    for minutes, label in ((60, "1h"), (120, "2h"), (240, "4h"), (1440, "D")):
        fire = {"minutes": minutes, "bucketTime": 1, "closeTime": 1, "tone": "bull", "label": label}
        row = premarket_scan_row("MSFT", payload, _now(), fires=[fire])
        assert row is not None, label
        assert row["strength"] == "WEAK"
        assert row["fires"] == [label]


def test_a_row_carries_both_families_and_sorts_first_time():
    when = datetime(2026, 8, 20, 7, 5, tzinfo=EASTERN)
    later = datetime(2026, 8, 20, 8, 5, tzinfo=EASTERN)
    payload = {
        "bars": [{"time": 1, "close": 101.234}],
        "mtfSignals": [
            _call("CALL4H", "9x20", later),
            _call("CALL2H", "4x8", when),
            _call("C2H", "9x20", when),
        ],
    }
    row = premarket_scan_row("aapl", payload, _now(), fires=[])
    assert row["symbol"] == "AAPL"
    assert row["signals48"] == ["CALL2H"]
    assert row["signals920"] == ["CALL4H"]
    assert row["score"] == 2
    assert row["signalAt"] == when.isoformat()
    assert row["lastPrice"] == 101.23
    # scanner.py hardcodes liveForming=True on every signal, so a FORMING
    # flag would be true on every row forever. It is deliberately absent.
    assert "forming" not in row


def test_no_signals_means_no_row():
    payload = {"bars": [{"time": 1, "close": 100.0}], "mtfSignals": []}
    assert premarket_scan_row("MSFT", payload, _now(), fires=[]) is None


def test_a_cold_payload_yields_no_row():
    assert premarket_scan_row("MSFT", {}, _now()) is None
    assert premarket_scan_row("MSFT", None, _now()) is None
