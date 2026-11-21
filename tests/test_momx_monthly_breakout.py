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
    monkeypatch.setattr(mb, "breaks_at", lambda *a, **k: {("MIDDLE", "down"): (10.0, 50.0)})
    out = state(BASES["MIDDLE_down"])["down"]
    assert out["level"] == 10.0 and out["target"] is None


def test_names(monkeypatch):
    monkeypatch.setenv("AGX_MOMX_EMA_LISTS", "1")
    assert mb.list_names() == ["Monthly Breakout", "Monthly Breakdown"]
    assert mb.parse_name("monthly breakdown") == "Monthly Breakdown"
    assert mb.parse_name("Monthly") is None


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
