import json
from datetime import datetime, date
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

    The fixture is generated from frontend/src/squeezeRelease.js, the exact
    module App.jsx runs. If this fails the PYTHON is wrong -- regenerate the
    fixture only when the JavaScript itself intentionally changed.
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


def test_rolling_average_fills_partial_windows():
    assert rolling_average([2, 4], 20) == [2.0, 3.0]


def test_true_range_seeds_from_high_low_then_uses_previous_close():
    bars = [
        {"high": 10.0, "low": 9.0, "close": 9.5},
        {"high": 12.0, "low": 11.0, "close": 11.5},
    ]
    # Bar 0 seeds from its own range. Bar 1 is the gap-aware maximum of
    # 12-11=1, |12-9.5|=2.5 and |11-9.5|=1.5.
    assert true_ranges(bars) == [1.0, 2.5]


def _at(hour, minute=0):
    return int(datetime(2026, 8, 20, hour, minute, tzinfo=EASTERN).timestamp())


def _bucket_label(hour, minute, minutes):
    return datetime.fromtimestamp(
        chart_bucket_time(_at(hour, minute), minutes), tz=EASTERN
    ).strftime("%H:%M")


def test_four_hour_buckets_use_the_tos_central_clock():
    """TOS aggregates equity bars from midnight Central, so the boundaries
    visible on this Eastern chart are 01:00 / 05:00 / 09:00 / 13:00."""
    assert _bucket_label(9, 30, 240) == "09:00"
    assert _bucket_label(8, 59, 240) == "05:00"
    assert _bucket_label(5, 0, 240) == "05:00"
    assert _bucket_label(0, 30, 240) == "21:00"  # previous day's bucket


def test_daily_buckets_anchor_to_eastern_midnight_and_two_hour_to_central():
    # TOS 2h bars run 01/03/05/07/09... ET (midnight Central), like the 4h.
    assert _bucket_label(7, 15, 1440) == "00:00"
    assert _bucket_label(7, 15, 120) == "07:00"
    assert _bucket_label(9, 5, 120) == "09:00"


def test_aggregation_takes_first_open_extremes_and_last_close():
    bars = [
        {"time": 1_700_000_000, "open": 1.0, "high": 5.0, "low": 0.5, "close": 2.0},
        {"time": 1_700_000_060, "open": 2.0, "high": 9.0, "low": 1.5, "close": 3.0},
    ]
    merged = aggregate_chart_bars(bars, 60)
    assert len(merged) == 1
    assert (merged[0]["open"], merged[0]["high"], merged[0]["low"], merged[0]["close"]) == (1.0, 9.0, 0.5, 3.0)


# ----------------------------------------------------------------------
# Window, match rule and strength score
# ----------------------------------------------------------------------

from premarket_scanner import (  # noqa: E402
    merge_live_tail,
    premarket_scan_row,
    premarket_window,
    score_strength,
    window_call_signals,
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
        "liveForming": False,
    }


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
    assert [signal["label"] for signal in window_call_signals(signals, start, end)] == ["CALL2H"]


def test_colour_tier_ranks_cyan_above_yellow():
    """Cyan (9x20) is the slower, more-confirmed cross and is 2.4x rarer in
    the real tapes. The trader's rule, from a COIN chart carrying a yellow
    CALL4H beside a cyan CALL2H that he called strong."""
    y = _call("CALL2H", "4x8", _now())
    y2 = _call("CALL4H", "4x8", _now())
    c = _call("CALL2H", "9x20", _now())
    c2 = _call("CALL4H", "9x20", _now())

    assert score_strength([y], [])[1] == "WEAK"          # one yellow alone
    assert score_strength([y, y2], [])[1] == "MODERATE"  # two yellow
    assert score_strength([c], [])[1] == "MODERATE"      # any single cyan
    assert score_strength([c, c2], [])[1] == "STRONG"    # two cyan
    assert score_strength([y, c], [])[1] == "STRONG"     # the COIN case


def test_fire_only_setups_are_capped_at_moderate():
    """Refined by the trader across two charts on 2026-08-21: AAPL with
    CALL5 + one 1h fire was "weak because only fire signals"; WRBY with
    1h + 2h + 4h fires was "more than 1 fire only" = MODERATE. STRONG stays
    reserved for setups carrying an actual cross, because a squeeze release
    is volatility expanding, not direction confirmed."""
    fires = [{"minutes": m} for m in (60, 120, 240, 1440)]
    assert score_strength([], fires[:1])[1] == "WEAK"
    for count in range(2, 5):
        assert score_strength([], fires[:count])[1] == "MODERATE", count


def test_the_wrby_chart_the_trader_called_moderate():
    """1h + 2h + 4h fires, no CALL2H/CALL4H (C5 is a 5-minute signal)."""
    fires = [{"minutes": 60}, {"minutes": 120}, {"minutes": 240}]
    assert score_strength([], fires) == (3, "MODERATE")


def test_fires_do_add_once_a_cross_exists():
    y = _call("CALL2H", "4x8", _now())
    c = _call("CALL2H", "9x20", _now())
    two = [{"minutes": 60}, {"minutes": 120}]
    assert score_strength([y], [])[1] == "WEAK"          # lone yellow
    assert score_strength([y], two)[1] == "MODERATE"     # ...lifted by fires
    assert score_strength([c], [])[1] == "MODERATE"      # lone cyan
    assert score_strength([c], two)[1] == "STRONG"       # ...lifted by fires


def test_the_aapl_chart_the_trader_called_weak():
    """CALL5 is a 5-minute signal and never counts, so this is fire-only."""
    assert score_strength([], [{"minutes": 60}]) == (1, "WEAK")


def test_score_stays_a_plain_count_for_sorting():
    y = _call("CALL2H", "4x8", _now())
    c = _call("CALL4H", "9x20", _now())
    assert score_strength([y, c], [{"minutes": 60}])[0] == 3


def test_a_lone_fire_no_longer_produces_a_row():
    """TOS parity (2026-08-24): the row set must match the TOS scan, which
    has no squeeze-fire condition. Fires stay on a row that exists for a
    2h/4h cross or a cyan D-M cross; alone they create nothing."""
    payload = {"bars": [{"time": 1, "close": 100.0}], "mtfSignals": []}
    fire = {"minutes": 1440, "closeTime": 1, "tone": "bull", "label": "D"}
    assert premarket_scan_row("MSTR", payload, _now(), fires=[fire]) is None


def test_no_signals_means_no_row():
    payload = {"bars": [{"time": 1, "close": 100.0}], "mtfSignals": []}
    assert premarket_scan_row("MSTR", payload, _now(), fires=[]) is None


def test_a_cold_payload_yields_no_row():
    assert premarket_scan_row("MSTR", {}, _now()) is None


def test_live_tail_extends_the_cached_tape():
    cached = [{"time": 100, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]
    live = [{"time": 160, "open": 1.5, "high": 3.0, "low": 1.4, "close": 2.9}]
    assert [bar["time"] for bar in merge_live_tail(cached, live)] == [100, 160]


def test_live_bars_overlapping_the_cached_tape_are_ignored():
    """studyBars is a 30-minute tape. Letting a 1-minute live bar replace a
    30-minute bar at the same timestamp would silently discard that bucket's
    real high/low, so only the strictly-newer tail is appended."""
    cached = [
        {"time": 100, "open": 1.0, "high": 9.0, "low": 0.5, "close": 1.5},
        {"time": 200, "open": 1.5, "high": 8.0, "low": 1.0, "close": 2.0},
    ]
    live = [
        {"time": 200, "open": 1.9, "high": 2.1, "low": 1.9, "close": 2.0},
        {"time": 260, "open": 2.0, "high": 2.5, "low": 2.0, "close": 2.4},
    ]
    merged = merge_live_tail(cached, live)
    assert [bar["time"] for bar in merged] == [100, 200, 260]
    assert merged[1]["high"] == 8.0  # the cached 30m high survives


def test_merge_survives_an_empty_or_missing_live_feed():
    cached = [{"time": 100, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]
    assert merge_live_tail(cached, []) == cached
    assert merge_live_tail(cached, None) == cached
    assert merge_live_tail([], [{"time": 5, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}])[0]["time"] == 5


def _daily_series(days, *, flat_close=100.0):
    """Daily bars at Eastern midnight, flat enough to sit in a squeeze."""
    bars = []
    base = int(datetime(2026, 6, 1, 12, 0, tzinfo=EASTERN).timestamp())
    for index in range(days):
        bars.append({
            "time": base + index * 86400,
            "open": flat_close, "high": flat_close, "low": flat_close, "close": flat_close,
        })
    return bars


def test_a_stale_daily_release_is_not_reported():
    """A daily release from weeks ago is not on today's chart, so it must not
    be in today's scan. Regression: an August scan surfaced a July fire."""
    from premarket_scanner import window_fires

    bars = _daily_series(40)
    # An old release: one wide bar early, then flat again for weeks.
    bars[25] = {**bars[25], "high": 160.0, "low": 40.0, "close": 150.0}
    bars[26] = {**bars[26], "open": 150.0, "high": 150.0, "low": 150.0, "close": 150.0}
    last_day = datetime.fromtimestamp(bars[-1]["time"], tz=EASTERN)
    start, end = premarket_window(last_day)
    labels = [fire["label"] for fire in window_fires(bars, start, end)]
    assert "D" not in labels


def test_forming_is_derived_from_bucket_close_not_the_broken_flag():
    """A CALL2H sits on a 2h bucket that locks two hours after its open; a
    CALL4H locks four hours after. Until the tape reaches that close, the
    signal can still repaint away (META did exactly this at 08:00 on
    2026-08-21, dropping from STRONG to nothing while the user watched).
    liveForming from the engine is hardcoded True and must not be used."""
    # Signals now come from the TOS scan on the tape itself: a rise that
    # starts at 09:00 puts a CALL2H on the 09:00 2h bucket (locks at 11:00).
    day = datetime(2026, 8, 18, 4, 0, tzinfo=EASTERN)
    rise = datetime(2026, 8, 21, 9, 0, tzinfo=EASTERN)
    price_at = _flat_then_rise(int(rise.timestamp()))

    def payload(last_hhmm):
        h, m = last_hhmm
        last = datetime(2026, 8, 21, h, m, tzinfo=EASTERN)
        minutes = int((last.timestamp() - day.timestamp()) // 60) + 1
        return {"bars": _minute_bars(day, minutes, price_at), "mtfSignals": []}

    now = datetime(2026, 8, 21, 9, 15, tzinfo=EASTERN)
    early = premarket_scan_row("TSLA", payload((9, 15)), now)
    assert "CALL2H" in early["signals48"]
    assert early["forming"] is True          # 2h bucket (09-11) not closed yet

    locked = premarket_scan_row("TSLA", payload((11, 5)), datetime(2026, 8, 21, 11, 5, tzinfo=EASTERN))
    assert "CALL2H" in locked["signals48"]
    assert "CALL4H" not in locked["signals48"] or locked["forming"] is True
    # With only the 2h cross, the 09:00 bucket closed at 11:00: cannot repaint.
    two_hour_only = [s for s in locked["signals48"] + locked["signals920"] if s == "CALL2H"]
    assert two_hour_only
    if "CALL4H" not in locked["signals48"] and "CALL4H" not in locked["signals920"]:
        assert locked["forming"] is False


# --- TOS "AlertX Bull Momo" parity -------------------------------------------
from premarket_scanner import (  # noqa: E402
    TOS_SCAN_LOOKBACK_BARS,
    change_percent,
    crosses_above,
    ema_series,
    previous_session_close,
    tos_bull_momo_signals,
)


def _minute_bars(start, minutes, price_at):
    """One-minute bars from ``start`` (ET datetime) for ``minutes`` minutes."""
    out = []
    base = int(start.timestamp())
    for index in range(minutes):
        when = base + index * 60
        price = price_at(when)
        out.append({"time": when, "open": price, "high": price, "low": price, "close": price, "volume": 100})
    return out


def _flat_then_rise(rise_from_epoch, base_price=100.0, step=0.5):
    def price_at(when):
        if when < rise_from_epoch:
            return base_price
        return base_price + step * ((when - rise_from_epoch) // 60 + 1)
    return price_at


def test_ema_matches_thinkscript_seed_and_alpha():
    assert ema_series([10, 10, 10], 4) == [10, 10, 10]
    ema = ema_series([10, 20], 4)
    assert ema[1] == 10 + (2 / 5) * 10
    assert crosses_above([1, 3], [2, 2], 1) is True
    assert crosses_above([3, 3], [2, 2], 1) is False   # already above: no cross
    assert crosses_above([1, 3], [2, 2], 0) is False


def test_two_hour_cross_counts_only_on_the_0900_bucket():
    # Flat tape for four days, then a rise that starts exactly at 09:00 ET on
    # 2026-08-20 -> the 09:00 2h bucket (TOS clock: 07/09/11...) closes above
    # -> c48/c920 cross there.
    day = datetime(2026, 8, 17, 4, 0, tzinfo=EASTERN)
    rise = datetime(2026, 8, 20, 9, 0, tzinfo=EASTERN)
    bars = _minute_bars(day, 4 * 24 * 60, _flat_then_rise(int(rise.timestamp())))
    signals = tos_bull_momo_signals(bars, None)
    two_hour = [s for s in signals if s["timeframe"] == "2H"]
    assert {s["family"] for s in two_hour} == {"4x8", "9x20"}
    assert all(datetime.fromtimestamp(s["time"], tz=EASTERN).strftime("%H:%M") == "09:00" for s in two_hour)
    assert all(s["label"] == "CALL2H" and s["direction"] == "CALL" for s in two_hour)

    # The same rise starting at 07:00 lands on the 07:00 2h bucket, which the
    # TOS window (SecondsFromTime(0800) >= 0) excludes; the 4h side sees it on
    # the 05:00 TOS bucket, also outside 07:00-09:30.
    rise_early = datetime(2026, 8, 20, 7, 0, tzinfo=EASTERN)
    early = tos_bull_momo_signals(_minute_bars(day, 4 * 24 * 60, _flat_then_rise(int(rise_early.timestamp()))), None)
    assert early == []


def test_four_hour_cross_counts_on_the_0900_bucket():
    day = datetime(2026, 8, 17, 4, 0, tzinfo=EASTERN)
    rise = datetime(2026, 8, 20, 9, 0, tzinfo=EASTERN)
    bars = _minute_bars(day, 4 * 24 * 60, _flat_then_rise(int(rise.timestamp())))
    four_hour = [s for s in tos_bull_momo_signals(bars, None) if s["timeframe"] == "4H"]
    assert [s["family"] for s in four_hour] == ["4x8", "9x20"]
    assert all(datetime.fromtimestamp(s["time"], tz=EASTERN).strftime("%H:%M") == "09:00" for s in four_hour)
    assert all(s["label"] == "CALL4H" for s in four_hour)


def test_lookback_is_twelve_buckets_like_highest_12():
    # A 09:00 2h cross on 2026-08-18 is 12+ 2h buckets behind a tape that runs
    # to 2026-08-20 -> gone from the 2h scan (yesterday's would still count).
    day = datetime(2026, 8, 17, 4, 0, tzinfo=EASTERN)
    rise = datetime(2026, 8, 18, 9, 0, tzinfo=EASTERN)
    bars = _minute_bars(day, 4 * 24 * 60, _flat_then_rise(int(rise.timestamp())))
    assert [s for s in tos_bull_momo_signals(bars, None) if s["timeframe"] == "2H"] == []
    assert TOS_SCAN_LOOKBACK_BARS == 12


def test_study_tape_supplies_depth_when_the_live_tape_is_one_session():
    # Live one-minute tape = today only; the 30-minute study tape carries the
    # history the EMAs and the 12-bar lookback need.
    history_start = datetime(2026, 8, 12, 4, 0, tzinfo=EASTERN)
    rise = datetime(2026, 8, 20, 9, 0, tzinfo=EASTERN)
    price_at = _flat_then_rise(int(rise.timestamp()))
    study = [
        {**bar} for bar in _minute_bars(history_start, 8 * 24 * 60, price_at) if bar["time"] % 1800 == 0
    ]
    live = _minute_bars(datetime(2026, 8, 20, 4, 0, tzinfo=EASTERN), 6 * 60, price_at)
    signals = tos_bull_momo_signals(live, study)
    assert any(s["label"] == "CALL2H" for s in signals)


def test_change_percent_uses_the_prior_regular_close():
    now = datetime(2026, 8, 20, 8, 15, tzinfo=EASTERN)
    bars = [
        {"time": int(datetime(2026, 8, 19, 15, 59, tzinfo=EASTERN).timestamp()), "close": 200.0},
        {"time": int(datetime(2026, 8, 19, 16, 0, tzinfo=EASTERN).timestamp()), "close": 202.0},
        {"time": int(datetime(2026, 8, 19, 18, 30, tzinfo=EASTERN).timestamp()), "close": 190.0},  # after-hours, ignored
        {"time": int(datetime(2026, 8, 20, 8, 0, tzinfo=EASTERN).timestamp()), "close": 212.1},
    ]
    assert previous_session_close(bars, now) == 202.0
    assert change_percent(bars, now) == 5.0
    assert change_percent([], now) is None


def test_scan_row_reports_change_percent_and_ignores_penny_stocks():
    now = datetime(2026, 8, 20, 9, 15, tzinfo=EASTERN)
    day = datetime(2026, 8, 17, 4, 0, tzinfo=EASTERN)
    rise = datetime(2026, 8, 20, 9, 0, tzinfo=EASTERN)
    bars = _minute_bars(day, 3 * 24 * 60 + 5 * 60 + 15, _flat_then_rise(int(rise.timestamp())))
    row = premarket_scan_row("TEST", {"bars": bars, "mtfSignals": []}, now)
    assert row is not None
    assert "CALL2H" in row["signals48"] and "CALL2H" in row["signals920"]
    assert isinstance(row["changePct"], float)
    penny = [{**bar, "close": bar["close"] / 100, "open": bar["open"] / 100} for bar in bars]
    assert premarket_scan_row("TEST", {"bars": penny, "mtfSignals": []}, now) is None


def test_a_partial_leading_live_bucket_never_replaces_the_coarse_one():
    from premarket_scanner import tos_scan_bars
    day = datetime(2026, 8, 17, 4, 0, tzinfo=EASTERN)
    price_at = _flat_then_rise(int(datetime(2026, 8, 20, 9, 0, tzinfo=EASTERN).timestamp()))
    full = _minute_bars(day, 4 * 24 * 60, price_at)
    study = [bar for bar in full if bar["time"] % 1800 == 0]
    # Live tail begins at 07:35 on 8/20 - inside the 07:00 two-hour bucket.
    tail_start = datetime(2026, 8, 20, 7, 35, tzinfo=EASTERN)
    live = [bar for bar in full if bar["time"] >= int(tail_start.timestamp())]
    merged = tos_scan_bars(live, study, 120)
    seven = next(bar for bar in merged if datetime.fromtimestamp(bar["time"], tz=EASTERN).strftime("%m/%d %H:%M") == "08/20 07:00")
    coarse_seven = next(bar for bar in aggregate_chart_bars(study, 120) if bar["time"] == seven["time"])
    assert seven == coarse_seven                  # complete coarse bucket kept
    nine = next(bar for bar in merged if datetime.fromtimestamp(bar["time"], tz=EASTERN).strftime("%m/%d %H:%M") == "08/20 09:00")
    assert nine["open"] == price_at(int(datetime(2026, 8, 20, 9, 0, tzinfo=EASTERN).timestamp()))


def test_change_percent_falls_back_to_the_study_tape_for_the_prior_close():
    now = datetime(2026, 8, 20, 9, 15, tzinfo=EASTERN)
    day = datetime(2026, 8, 17, 4, 0, tzinfo=EASTERN)
    price_at = _flat_then_rise(int(datetime(2026, 8, 20, 9, 0, tzinfo=EASTERN).timestamp()))
    full = _minute_bars(day, 3 * 24 * 60 + 5 * 60 + 15, price_at)
    study = [bar for bar in full if bar["time"] % 1800 == 0]
    live = [bar for bar in full if bar["time"] >= int(datetime(2026, 8, 20, 4, 0, tzinfo=EASTERN).timestamp())]
    row = premarket_scan_row("TEST", {"bars": live, "studyBars": study, "mtfSignals": []}, now)
    assert row is not None
    assert isinstance(row["changePct"], float) and row["changePct"] > 0


def test_scan_row_uses_the_chart_engine_signals_when_present():
    """The scanner must never disagree with the chart: when the payload
    carries the chart engine's crosses, the row is built from them (window +
    12-bar lookback only). META 2026-08-24: the scanner's own EMA path showed
    a CALL4H the chart never printed."""
    from premarket_scanner import CHART_ENGINE_MODE, tos_scan_from_chart_signals
    now = datetime(2026, 8, 24, 9, 15, tzinfo=EASTERN)
    day = datetime(2026, 8, 17, 4, 0, tzinfo=EASTERN)
    bars = _minute_bars(day, 7 * 24 * 60 + 5 * 60 + 15, lambda when: 100.0)   # flat: own engine finds nothing

    def stamp(y, m, d, hh):
        return int(datetime(y, m, d, hh, 0, tzinfo=EASTERN).timestamp())

    chart_signals = [
        # 2h cross on the 09:00 bucket today - inside the 08:00-09:30 window
        {"family": "4x8", "timeframe": "2H", "direction": "CALL", "label": "CALL2H", "time": stamp(2026, 8, 24, 9), "candleTimestamp": stamp(2026, 8, 24, 9)},
        # compact C4H = the cross fired (daily not confirming): the scan counts it
        {"family": "9x20", "timeframe": "4H", "direction": "CALL", "label": "C4H", "time": stamp(2026, 8, 24, 9), "candleTimestamp": stamp(2026, 8, 24, 9)},
        # 4h cross on Friday 01:00 - outside the 07:00-09:30 window: ignored
        {"family": "4x8", "timeframe": "4H", "direction": "CALL", "label": "CALL4H", "time": stamp(2026, 8, 21, 1), "candleTimestamp": stamp(2026, 8, 21, 1)},
        # PUT never counts
        {"family": "4x8", "timeframe": "2H", "direction": "PUT", "label": "PUT2H", "time": stamp(2026, 8, 24, 9), "candleTimestamp": stamp(2026, 8, 24, 9)},
    ]
    scan = tos_scan_from_chart_signals(chart_signals, bars, None)
    assert [(s["family"], s["label"]) for s in scan] == [("4x8", "CALL2H"), ("9x20", "CALL4H")]

    payload = {"bars": bars, "mtfSignals": chart_signals, "mtfSignalMode": CHART_ENGINE_MODE, "mtfSignalsPending": False}
    row = premarket_scan_row("META", payload, now)
    assert row is not None
    assert row["signals48"] == ["CALL2H"]
    assert row["signals920"] == ["CALL4H"]

    # Without the chart engine's mode the scanner falls back to its own path
    # (flat tape -> no crosses -> no row).
    assert premarket_scan_row("META", {"bars": bars, "mtfSignals": chart_signals}, now) is None


def test_yesterdays_premarket_cross_rolls_off_todays_scanner():
    """The TOS 12-bar lookback reaches into yesterday and the window check is
    time-of-day only, so a cross from yesterday's 09:00 premarket used to show
    on today's scanner (META/AMZN/AAPL showed 8/24 rows at 8 AM on 8/25). The
    premarket scanner is a TODAY view - yesterday belongs in history."""
    # Rise starts YESTERDAY at 09:00 ET; tape runs to today 08:00.
    day = datetime(2026, 8, 18, 4, 0, tzinfo=EASTERN)
    rise = datetime(2026, 8, 24, 9, 0, tzinfo=EASTERN)
    price_at = _flat_then_rise(int(rise.timestamp()))
    bars = _minute_bars(day, int((datetime(2026, 8, 25, 8, 0, tzinfo=EASTERN).timestamp() - day.timestamp()) // 60), price_at)

    # Sanity: the raw TOS scan DOES still fire yesterday's 09:00 cross (12-bar
    # lookback), so without the today-filter the row would appear.
    raw = [s for s in tos_bull_momo_signals(bars, None)
           if datetime.fromtimestamp(s["time"], tz=EASTERN).date() == date(2026, 8, 24)]
    assert raw, "expected the raw scan to still see yesterday's cross"

    # But the scanner row at 8 AM today must NOT show it (no cross today yet).
    now = datetime(2026, 8, 25, 8, 0, tzinfo=EASTERN)
    row = premarket_scan_row("META", {"bars": bars, "mtfSignals": []}, now)
    assert row is None, "yesterday's premarket cross must roll off today's live scanner"

    # A fresh cross TODAY (08:00) shows normally.
    rise_today = datetime(2026, 8, 25, 9, 0, tzinfo=EASTERN)
    bars_today = _minute_bars(day, int((datetime(2026, 8, 25, 9, 15, tzinfo=EASTERN).timestamp() - day.timestamp()) // 60), _flat_then_rise(int(rise_today.timestamp())))
    row_today = premarket_scan_row("META", {"bars": bars_today, "mtfSignals": []}, datetime(2026, 8, 25, 9, 15, tzinfo=EASTERN))
    assert row_today is not None
    assert datetime.fromisoformat(row_today["latestSignalAt"]).date() == date(2026, 8, 25)
