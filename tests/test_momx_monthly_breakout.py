"""Monthly Breakout / Monthly Breakdown lists (momx/monthly_breakout.py).

Unit layer: the four patterns, both directions, the EMA filter, live vs
confirmed, no look-ahead, and the list plumbing in momx/board.py and
momx/service.py (derived, never saved, no list slot used). The detector was
checked against the back-test code month-for-month on 120 real tickers
(0 mismatches in 597 signals) before these were written.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from momx import board, ema_touch, monthly_breakout as mb

# 62 months of steady rise (EMAs below price) or fall (EMAs above), then a base.
RISE = [30.0 + i for i in range(62)]
FALL = [150.0 - i for i in range(62)]
BASES = {
    "BOTTOM_up": RISE + [100, 92, 85, 92, 99, 93, 89, 95, 100, 96, 93, 97, 99, 108],
    "MIDDLE_up": RISE + [100, 92, 85, 92, 100, 93, 85, 93, 100, 92, 85, 93, 99, 110],
    "SYM_up": RISE + [112, 104, 86, 96, 107, 100, 89, 96, 103, 99, 92, 95, 97, 105],
    "TOP_down": FALL + [80, 88, 95, 88, 82, 88, 92, 86, 81, 85, 89, 84, 81, 72],
    "MIDDLE_down": FALL + [80, 88, 95, 88, 80, 88, 95, 88, 80, 88, 95, 88, 81, 70],
}


def tape(closes, spread=0.02, start=2015):
    out = []
    for i, close in enumerate(closes):
        year, month = start + i // 12, i % 12 + 1
        out.append({"period": f"{year:04d}-{month:02d}", "close": float(close),
                    "high": close * (1 + spread), "low": close * (1 - spread)})
    return out


def hlc(bars):
    return [b["high"] for b in bars], [b["low"] for b in bars], [b["close"] for b in bars]


def state(closes):
    bars = tape(closes)
    return mb.breakout_state(bars, bars[-1]["period"])


@pytest.mark.parametrize("shape,pattern,direction", [
    ("BOTTOM_up", "BOTTOM", "up"),
    ("MIDDLE_up", "MIDDLE", "up"),
    ("SYM_up", "SYM", "up"),
    ("TOP_down", "TOP", "down"),
    ("MIDDLE_down", "MIDDLE", "down"),
])
def test_each_pattern_is_found_on_its_breakout_month(shape, pattern, direction):
    h, l, c = hlc(tape(BASES[shape]))
    assert (pattern, direction) in mb.breaks_at(h, l, c, len(c) - 1)
    # ...and not on the month before (the close had not crossed yet).
    assert (pattern, direction) not in mb.breaks_at(h, l, c, len(c) - 2)


@pytest.mark.parametrize("shape,label,direction", [
    ("BOTTOM_up", "BOTTOM", "up"),     # also a range break; BOTTOM ranks first
    ("MIDDLE_up", "MIDDLE", "up"),
    ("SYM_up", "SYM", "up"),           # also a TOP-up break; SYM ranks first
    ("MIDDLE_down", "MIDDLE", "down"),
])
def test_state_labels_the_strongest_pattern_and_the_other_side_is_empty(shape, label, direction):
    out = state(BASES[shape])
    other = "down" if direction == "up" else "up"
    assert out[direction]["pattern"] == label and out[other] is None
    assert out[direction]["status"] == "live"
    assert out[direction]["label"] == mb.PATTERN_LABELS[label]


def test_bottom_up_line_target_and_emas():
    out = state(BASES["BOTTOM_up"])["up"]
    assert out["level"] == pytest.approx(102.0)            # the flat top: 100 * 1.02
    assert out["target"] > out["level"]                    # line + base height
    assert out["close"] > out["ema21"] > out["ema50"]


def test_ema_filter_blocks_an_up_break_in_a_downtrend():
    # Same base, but it sits after a long fall: the 50 EMA is still above.
    closes = FALL + BASES["BOTTOM_up"][62:]
    h, l, c = hlc(tape(closes))
    assert ("BOTTOM", "up") in mb.breaks_at(h, l, c, len(c) - 1)
    assert state(closes)["up"] is None


def test_ema_filter_blocks_a_down_break_in_an_uptrend():
    closes = RISE + BASES["MIDDLE_down"][62:]
    assert state(closes)["down"] is None


def test_last_month_break_is_confirmed_while_the_new_month_runs():
    closes = BASES["BOTTOM_up"] + [109.0]
    out = state(closes)["up"]
    assert out["status"] == "confirmed" and out["period"] == tape(closes)[-2]["period"]


def test_no_current_month_bar_means_last_month_is_confirmed():
    bars = tape(BASES["BOTTOM_up"])                       # last bar 2021-04
    out = mb.breakout_state(bars, "2021-05")
    assert out["up"]["status"] == "confirmed" and out["up"]["period"] == "2021-04"


def test_a_last_bar_months_old_is_not_a_current_signal():
    # Halted / delisted: the break happened, but not last month.
    bars = tape(BASES["BOTTOM_up"])
    assert mb.breakout_state(bars, "2021-08") == {"up": None, "down": None}
    assert mb._previous_month("2026-01") == "2025-12" and mb._previous_month("x") == ""


def test_a_break_that_fails_two_months_later_is_no_longer_listed():
    closes = BASES["BOTTOM_up"] + [109.0, 101.0]
    assert state(closes)["up"] is None


def test_no_look_ahead_later_months_never_change_a_month_verdict():
    h, l, c = hlc(tape(BASES["SYM_up"] + [90.0, 120.0, 80.0]))
    i = len(BASES["SYM_up"]) - 1
    assert mb.breaks_at(h, l, c, i) == mb.breaks_at(h[:i + 1], l[:i + 1], c[:i + 1], i)


def test_too_little_history_is_no_reading():
    assert state(BASES["BOTTOM_up"][-(mb.MIN_MONTHS - 1):]) is None


def test_flat_placeholder_months_before_a_listing_are_ignored():
    # A SPAC's flat pre-listing months must not count toward MIN_MONTHS.
    closes = [25.0] * 40 + BASES["BOTTOM_up"][-40:]
    bars = tape(closes)
    for b in bars[:40]:
        b["low"] = b["high"] = b["close"]
    assert mb.breakout_state(bars, bars[-1]["period"]) is None


def test_a_breakdown_target_at_or_below_zero_is_left_out(monkeypatch):
    # A base taller than the price: line 10, height 50 -> projected -40.
    monkeypatch.setattr(mb, "_breaks", lambda *a, **k: {("MIDDLE", "down"): (10.0, 50.0, [])})
    out = state(BASES["MIDDLE_down"])["down"]
    assert out["level"] == 10.0 and out["target"] is None


def test_names(monkeypatch):
    monkeypatch.setenv("AGX_MOMX_EMA_LISTS", "1")
    assert mb.list_names() == ["Monthly Breakout", "Monthly Breakdown"]
    assert mb.parse_name("monthly breakdown") == ("down", "Monthly")
    assert mb.parse_name("Weekly Breakout") == ("up", "Weekly")
    assert mb.parse_name("daily breakdown") == ("down", "Daily")
    assert mb.parse_name("Monthly") is None and mb.parse_name("Hourly Breakout") is None


# ---------------------------------------------------------------------------
# the lists (board + service)
# ---------------------------------------------------------------------------


@pytest.fixture
def lists_on(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_EMA_LISTS", "1")
    cache = tmp_path / "momx_ema_touch.json"
    monkeypatch.setenv("AGX_MOMX_EMA_TOUCH_PATH", str(cache))
    monkeypatch.setenv("AGX_MOMX_EMA_TF_PATH", str(tmp_path / "momx_ema_touch_tf.json"))
    ema_touch._CACHE.clear()
    ema_touch._CACHE.update({"symbols": {}})
    ema_touch._CACHE_MTIME = None
    universe = tmp_path / "universe.json"
    universe.write_text(json.dumps({"lists": {"Watchlist": ["AAA", "BBB", "CCC", "DDD"]}}))
    up = state(BASES["BOTTOM_up"])
    down = state(BASES["MIDDLE_down"])
    cache.write_text(json.dumps({
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "symbols": {
            "AAA": {mb.CACHE_KEY: up},
            "BBB": {mb.CACHE_KEY: {"up": None, "down": None}},
            "CCC": {mb.CACHE_KEY: down},
            "DDD": {mb.CACHE_KEY: None},                   # too little history
        },
    }))
    yield universe
    ema_touch._CACHE.clear()
    ema_touch._CACHE_MTIME = None


def test_lists_are_the_watchlist_members_with_a_break(lists_on):
    names = board.list_names(lists_on)
    assert names[2:6] == ["21 EMA Touch Monthly", "50 EMA Touch Monthly", "Monthly Breakout", "Monthly Breakdown"]
    assert board.load_universe("Monthly Breakout", path=lists_on) == ["AAA"]
    assert board.load_universe("monthly breakdown", path=lists_on) == ["CCC"]


def test_stale_cache_gives_empty_lists(lists_on):
    doc = json.loads(ema_touch.cache_path().read_text())
    doc["fetchedAt"] = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    ema_touch.cache_path().write_text(json.dumps(doc))
    ema_touch._CACHE_MTIME = None
    assert board.load_universe("Monthly Breakout", path=lists_on) == []


def test_breakout_lists_are_never_saved_and_refuse_edits(lists_on):
    board.set_active_list("Monthly Breakout", path=lists_on)
    saved = json.loads(lists_on.read_text())
    assert "Monthly Breakout" not in saved["lists"]
    assert board.active_list(path=lists_on) == "Monthly Breakout"
    for call in (
        lambda: board.add_symbols("ZZZ", "Monthly Breakout", path=lists_on),
        lambda: board.remove_symbols("AAA", "Monthly Breakout", path=lists_on),
        lambda: board.save_universe(["ZZZ"], "Monthly Breakdown", path=lists_on),
        lambda: board.rename_list("Monthly Breakdown", "Mine", path=lists_on),
        lambda: board.delete_list("Monthly Breakdown", path=lists_on),
    ):
        with pytest.raises(board.UnknownListError, match="chart-pattern break"):
            call()
    assert board.is_builtin("Monthly Breakout") and board.is_derived("Monthly Breakdown")
    with pytest.raises(board.UnknownListError):
        board.save_universe(["ZZZ"], "Monthly Breakout", path=lists_on, create=True)


def test_switched_off_means_no_lists(lists_on, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_EMA_LISTS", "0")
    assert "Monthly Breakout" not in board.list_names(lists_on)


def test_service_lists_carry_the_pattern_details_and_the_derived_flag(lists_on, monkeypatch):
    from momx import service

    monkeypatch.setattr(board, "universe_path", lambda: lists_on)
    entries = {e["name"]: e for e in service.lists()["lists"]}
    bull, bear = entries["Monthly Breakout"], entries["Monthly Breakdown"]
    assert bull["breakout"] == "up" and bear["breakout"] == "down"
    assert list(bull["breakouts"]) == ["AAA"] and bull["breakouts"]["AAA"]["pattern"] == "BOTTOM"
    assert list(bear["breakouts"]) == ["CCC"] and bear["breakouts"]["CCC"]["pattern"] == "MIDDLE"
    assert bull["derived"] and bull["builtIn"] and bull["count"] == 1
    assert entries["21 EMA Touch Monthly"]["derived"] is True
    assert entries["Watchlist"]["derived"] is False and "breakout" not in entries["Watchlist"]


def test_breakout_lists_do_not_use_up_a_list_slot(lists_on, monkeypatch):
    from momx import service

    monkeypatch.setattr(board, "universe_path", lambda: lists_on)
    monkeypatch.setattr(service, "start_warmer", lambda: None)
    monkeypatch.setattr(service, "refresh_now", lambda name=None: None)
    # Mag7 + Watchlist + 4 own lists = 6 slots; the 4 computed lists are free.
    for name in ("Own1", "Own2", "Own3", "Own4"):
        assert service.create_list(name)["ok"] is True, name
    assert service.create_list("Own5")["ok"] is False


def test_refresh_stores_the_breakout_verdict_beside_the_ema_touch(lists_on, monkeypatch):
    rows = [dict(b, period=b["period"]) for b in tape(BASES["BOTTOM_up"])]

    def fake_fetch(symbols, tf, now):
        return {"AAA": rows} if tf == "Monthly" else {}

    monkeypatch.setattr(ema_touch, "_fetch", fake_fetch)
    summary = ema_touch.refresh(["AAA"], now=datetime.now(timezone.utc))
    assert summary["breakout:up"] == 1 and summary["breakout:down"] == 0
    saved = json.loads(ema_touch.cache_path().read_text())["symbols"]["AAA"][mb.CACHE_KEY]
    assert saved["up"]["pattern"] == "BOTTOM" and saved["up"]["status"] == "live"


def test_one_bad_tape_does_not_sink_the_refresh(lists_on, monkeypatch):
    bad = [{"period": "2020-01", "close": "x", "high": 1, "low": 1}] * 70

    monkeypatch.setattr(ema_touch, "_fetch", lambda s, tf, now: {"AAA": bad} if tf == "Monthly" else {})
    monkeypatch.setattr(ema_touch, "touch_state", lambda *a, **k: None)
    ema_touch.refresh(["AAA"], now=datetime.now(timezone.utc))
    assert json.loads(ema_touch.cache_path().read_text())["symbols"]["AAA"][mb.CACHE_KEY] is None


# ---------------------------------------------------------------------------
# Daily / Weekly (2026-11-20: "I see only monthly where is D/W")
# ---------------------------------------------------------------------------


def day_tape(closes, spread=0.02):
    start = datetime(2025, 1, 1)
    return [{"period": (start + timedelta(days=i)).strftime("%Y-%m-%d"), "close": float(c),
             "high": c * (1 + spread), "low": c * (1 - spread)} for i, c in enumerate(closes)]


def test_the_same_patterns_are_found_on_daily_bars():
    bars = day_tape(BASES["BOTTOM_up"])
    out = mb.breakout_state(bars, bars[-1]["period"], bars[-2]["period"])
    assert out["up"]["pattern"] == "BOTTOM" and out["up"]["status"] == "live"
    assert out["up"]["period"] == bars[-1]["period"]


def test_daily_confirmed_uses_the_previous_bar_it_is_given():
    bars = day_tape(BASES["BOTTOM_up"])
    # The newest session has no bar for this symbol: yesterday's close is the confirmed one.
    later = (datetime.strptime(bars[-1]["period"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    out = mb.breakout_state(bars, later, bars[-1]["period"])
    assert out["up"]["status"] == "confirmed"
    # An older last bar (halted) is not a current signal.
    assert mb.breakout_state(bars, later, later) == {"up": None, "down": None}


def test_the_chart_setting_is_per_list_and_renames_it(lists_on):
    assert mb.list_names() == ["Monthly Breakout", "Monthly Breakdown"]
    assert mb.set_timeframe("up", "weekly") == "Weekly Breakout"
    assert mb.list_names() == ["Weekly Breakout", "Monthly Breakdown"]
    assert mb.set_timeframe("down", "Daily") == "Daily Breakdown"
    assert mb.timeframe("up") == "Weekly" and mb.timeframe("down") == "Daily"
    # The EMA lists' own setting in the same file is untouched.
    assert ema_touch.timeframe(21) == "Monthly"
    with pytest.raises(ValueError):
        mb.set_timeframe("up", "Hourly")
    with pytest.raises(ValueError):
        mb.set_timeframe("sideways", "Daily")


def test_a_weekly_list_reads_the_weekly_verdicts(lists_on):
    doc = json.loads(ema_touch.cache_path().read_text())
    doc["symbols"]["BBB"][mb.cache_key("Weekly")] = state(BASES["BOTTOM_up"])
    ema_touch.cache_path().write_text(json.dumps(doc))
    ema_touch._CACHE_MTIME = None
    mb.set_timeframe("up", "Weekly")
    assert "Weekly Breakout" in board.list_names(lists_on)
    assert board.load_universe("Weekly Breakout", path=lists_on) == ["BBB"]
    assert board.is_derived("Monthly Breakout") and board.is_derived("Daily Breakdown")


def test_refresh_stores_a_verdict_for_every_timeframe(lists_on, monkeypatch):
    month_rows = tape(BASES["BOTTOM_up"])
    day_rows = day_tape(BASES["MIDDLE_down"])
    monkeypatch.setattr(ema_touch, "_fetch",
                        lambda s, tf, now: {"AAA": month_rows if tf == "Monthly" else day_rows})
    summary = ema_touch.refresh(["AAA"], now=datetime.now(timezone.utc))
    saved = json.loads(ema_touch.cache_path().read_text())["symbols"]["AAA"]
    assert saved[mb.cache_key("Monthly")]["up"]["pattern"] == "BOTTOM"
    assert saved[mb.cache_key("Daily")]["down"]["pattern"] == "MIDDLE"
    assert saved[mb.cache_key("Weekly")]["down"]["status"] == "live"
    assert summary["breakout:down:Daily"] == 1 and summary["breakout:up"] == 1


def test_service_sends_the_dropdown_and_switches_the_list(lists_on, monkeypatch):
    from momx import service

    monkeypatch.setattr(board, "universe_path", lambda: lists_on)
    monkeypatch.setattr(service, "start_warmer", lambda: None)
    monkeypatch.setattr(service, "refresh_now", lambda name=None: None)
    entries = {e["name"]: e for e in service.lists()["lists"]}
    assert entries["Monthly Breakout"]["timeframe"] == "Monthly"
    assert entries["Monthly Breakout"]["timeframes"] == ["Monthly", "Weekly", "Daily"]
    board.set_active_list("Monthly Breakout", path=lists_on)
    out = service.set_breakout_timeframe("up", "Daily")
    assert out == {"ok": True, "list": "Daily Breakout", "previous": "Monthly Breakout", "active": "Daily Breakout"}
    assert service.set_breakout_timeframe("up", "Hourly")["ok"] is False
    assert service.set_breakout_timeframe("left", "Daily")["ok"] is False


# ---------------------------------------------------------------------------
# the chart: any ticker, D/W/M, with the pattern's lines (2026-11-20, "in
# scanner any tickers can we do in chart patterns in D/W/M when we open the
# chart ... so I know which pattern")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape,roles", [
    ("BOTTOM_up", ["resistance", "rising lows"]),
    ("MIDDLE_up", ["resistance", "support"]),
    ("SYM_up", ["falling highs", "rising lows"]),
    ("MIDDLE_down", ["resistance", "support"]),
])
def test_each_signal_carries_its_lines_ending_on_the_breakout_bar(shape, roles):
    bars = tape(BASES[shape])
    signal = state(BASES[shape])["up" if shape.endswith("up") else "down"]
    lines = signal["lines"]
    assert [line["role"] for line in lines] == roles
    assert all(line["to"] == bars[-1]["period"] for line in lines)
    assert all(line["from"] < line["to"] and line["bars"] >= 2 for line in lines)
    flat = [line for line in lines if line["role"] in ("resistance", "support")]
    assert all(line["fromPrice"] == line["toPrice"] for line in flat)
    # The line that broke is the signal's level.
    assert any(abs(line["toPrice"] - signal["level"]) < 1e-3 for line in lines)


@pytest.mark.parametrize("shape", ["BOTTOM_up", "MIDDLE_up", "SYM_up", "MIDDLE_down"])
def test_each_line_carries_the_swing_points_that_made_it(shape):
    # 2026-11-21, "I cannot see the range properly ... see the sample image":
    # the chart circles each touch like his pictures.
    bars = tape(BASES[shape])
    by_period = {bar["period"]: bar for bar in bars}
    signal = state(BASES[shape])["up" if shape.endswith("up") else "down"]
    for line in signal["lines"]:
        touches = line["touches"]
        assert len(touches) >= 2, line["role"]
        assert [t["period"] for t in touches] == sorted(t["period"] for t in touches)
        assert all(line["from"] <= t["period"] < line["to"] for t in touches)
        upper = line["role"] in ("resistance", "falling highs")
        for t in touches:
            bar = by_period[t["period"]]
            assert t["price"] == pytest.approx(bar["high"] if upper else bar["low"], abs=1e-4)


def test_sloped_lines_slope_the_right_way():
    lines = {line["role"]: line for line in state(BASES["SYM_up"])["up"]["lines"]}
    assert lines["falling highs"]["toPrice"] < lines["falling highs"]["fromPrice"]
    assert lines["rising lows"]["toPrice"] > lines["rising lows"]["fromPrice"]


def test_chart_patterns_answer_a_watchlist_ticker_from_the_scanner_cache(lists_on):
    doc = json.loads(ema_touch.cache_path().read_text())
    for tf in mb.TIMEFRAMES:
        doc["symbols"]["AAA"][mb.cache_key(tf)] = state(BASES["BOTTOM_up"]) if tf == "Weekly" else None
    ema_touch.cache_path().write_text(json.dumps(doc))
    ema_touch._CACHE_MTIME = None
    out = mb.chart_patterns("aaa", fetch=lambda *a: pytest.fail("a cached ticker must not fetch"))
    assert out["ok"] and out["source"] == "scanner" and out["symbol"] == "AAA"
    assert out["patterns"]["Weekly"]["up"]["pattern"] == "BOTTOM" and out["patterns"]["Monthly"] is None


def test_chart_patterns_fetch_any_other_ticker_once_and_never_queue_it(lists_on):
    mb._ON_DEMAND.clear()
    calls = []
    monthly = tape(BASES["MIDDLE_up"])
    daily = day_tape(BASES["SYM_up"])

    def fetch(symbols, tf, now):
        calls.append((tuple(symbols), tf))
        return {"ZZZ": monthly if tf == "Monthly" else daily}

    out = mb.chart_patterns("ZZZ", fetch=fetch)
    assert out["ok"] and out["source"] == "fetched"
    assert out["patterns"]["Monthly"]["up"]["pattern"] == "MIDDLE"
    assert out["patterns"]["Daily"]["up"]["pattern"] == "SYM" and out["patterns"]["Daily"]["up"]["lines"]
    assert sorted(tf for _, tf in calls) == ["Daily", "Monthly", "Weekly"]
    again = mb.chart_patterns("zzz", fetch=lambda *a: pytest.fail("kept for 10 minutes"))
    assert again == out
    assert "ZZZ" not in ema_touch._WANTED, "a chart must not grow the scanner's refresh list"
    mb._ON_DEMAND.clear()


def test_chart_patterns_refuse_junk_and_survive_a_failed_fetch(lists_on):
    mb._ON_DEMAND.clear()
    assert mb.chart_patterns("")["ok"] is False
    assert mb.chart_patterns("AAPL;DROP")["ok"] is False
    def boom(*a):
        raise ConnectionError("down")
    out = mb.chart_patterns("QQQQ", fetch=boom)
    assert out["ok"] is False and "QQQQ" in out["error"]
    assert "QQQQ" not in mb._ON_DEMAND
