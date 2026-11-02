"""The 21/50 EMA touch lists (momx/ema_touch.py + momx/board.py + service)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from momx import board, ema_touch


def _bars(closes, start_year=2020):
    out = []
    for i, close in enumerate(closes):
        year, month = start_year + i // 12, i % 12 + 1
        out.append({"period": f"{year:04d}-{month:02d}", "close": close, "low": close})
    return out


def test_ema_matches_tos_expaverage():
    assert ema_touch.ema([10.0, 20.0, 30.0], 3) == pytest.approx([10.0, 15.0, 22.5])


def test_touch_needs_uptrend_and_a_low_at_the_level():
    bars = _bars([100 + i for i in range(60)])
    period = bars[-1]["period"]
    level = ema_touch.ema([b["close"] for b in bars[:-1]], 21)[-1]
    bars[-1]["low"] = level - 0.01
    state = ema_touch.touch_state(bars, 21, period)
    assert state["uptrend"] and state["touched"] and state["pass"]
    bars[-1]["low"] = level + 0.01
    assert ema_touch.touch_state(bars, 21, period)["pass"] is False


def test_downtrend_touch_does_not_pass():
    bars = _bars([200 - i for i in range(60)])
    bars[-1]["low"] = 1.0
    state = ema_touch.touch_state(bars, 21, bars[-1]["period"])
    assert state["touched"] and not state["uptrend"] and not state["pass"]


def test_too_little_history_is_no_reading():
    bars = _bars([100 + i for i in range(60)])  # 59 completed < 2 x 50
    assert ema_touch.touch_state(bars, 50, bars[-1]["period"]) is None


def test_current_bar_is_not_part_of_the_level():
    bars = _bars([100 + i for i in range(60)])
    period = bars[-1]["period"]
    a = ema_touch.touch_state(bars, 21, period)["level"]
    bars[-1]["close"] = 10_000.0
    assert ema_touch.touch_state(bars, 21, period)["level"] == a


def test_names_round_trip():
    assert ema_touch.parse_name("21 ema touch weekly") == (21, "Weekly")
    assert ema_touch.parse_name("50 EMA Touch Daily") == (50, "Daily")
    assert ema_touch.parse_name("Watchlist") is None


def test_name_resolution_does_not_recalculate_ema_universes(lists_on, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Name-only lookup must not calculate EMA touchers')
    monkeypatch.setattr(ema_touch, 'touchers', forbidden)
    assert board.resolve_list_name('watchlist', path=lists_on) == 'Watchlist'
    assert board.active_list(path=lists_on) == board.DEFAULT_LIST_NAME
    assert '21 EMA Touch Monthly' in board.list_names(lists_on)
    ema_touch.set_timeframe(21, 'Daily')
    assert board.resolve_list_name('21 ema touch daily', path=lists_on) == '21 EMA Touch Daily'
    lists_on.write_text(json.dumps({'active':'Custom', 'lists':{'Custom':['SPY']}}))
    assert board.active_list(path=lists_on) == 'Custom'
    assert 'Custom' in board.list_names(lists_on)


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
    universe.write_text(json.dumps({"lists": {"Watchlist": ["AAA", "BBB", "CCC"]}}))
    yes, no = {"pass": True}, {"pass": False}
    cache.write_text(json.dumps({
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "symbols": {
            "AAA": {"21:Monthly": yes, "50:Monthly": no, "21:Weekly": no, "50:Weekly": yes},
            "BBB": {"21:Monthly": yes, "50:Monthly": yes, "21:Weekly": yes, "50:Weekly": yes},
            "CCC": {"21:Monthly": no, "50:Monthly": None, "21:Weekly": no, "50:Weekly": no},
        },
    }))
    yield universe
    ema_touch._CACHE.clear()
    ema_touch._CACHE_MTIME = None


def test_lists_are_the_watchlist_members_that_pass(lists_on):
    names = board.list_names(lists_on)
    assert names[:4] == ["Mag7", "Watchlist", "21 EMA Touch Monthly", "50 EMA Touch Monthly"]
    assert board.load_universe("21 EMA Touch Monthly", path=lists_on) == ["AAA", "BBB"]
    assert board.load_universe("50 ema touch monthly", path=lists_on) == ["BBB"]


def test_timeframe_switch_renames_the_list(lists_on):
    assert ema_touch.set_timeframe(50, "weekly") == "50 EMA Touch Weekly"
    names = board.list_names(lists_on)
    assert "50 EMA Touch Weekly" in names and "50 EMA Touch Monthly" not in names
    assert "21 EMA Touch Monthly" in names
    assert board.load_universe("50 EMA Touch Weekly", path=lists_on) == ["AAA", "BBB"]
    with pytest.raises(board.UnknownListError):
        board.load_universe("50 EMA Touch Monthly", path=lists_on)
    with pytest.raises(ValueError):
        ema_touch.set_timeframe(50, "Hourly")
    with pytest.raises(ValueError):
        ema_touch.set_timeframe(9, "Weekly")


def test_stale_cache_gives_empty_lists(lists_on):
    doc = json.loads(ema_touch.cache_path().read_text())
    doc["fetchedAt"] = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    ema_touch.cache_path().write_text(json.dumps(doc))
    ema_touch._CACHE_MTIME = None
    assert board.load_universe("21 EMA Touch Monthly", path=lists_on) == []


def test_derived_lists_are_never_saved_and_refuse_edits(lists_on):
    board.set_active_list("21 EMA Touch Monthly", path=lists_on)
    saved = json.loads(lists_on.read_text())
    assert "21 EMA Touch Monthly" not in saved["lists"]
    assert board.active_list(path=lists_on) == "21 EMA Touch Monthly"
    for call in (
        lambda: board.add_symbols("ZZZ", "21 EMA Touch Monthly", path=lists_on),
        lambda: board.remove_symbols("AAA", "21 EMA Touch Monthly", path=lists_on),
        lambda: board.save_universe(["ZZZ"], "50 EMA Touch Monthly", path=lists_on),
        lambda: board.rename_list("50 EMA Touch Monthly", "Mine", path=lists_on),
        lambda: board.delete_list("50 EMA Touch Monthly", path=lists_on),
        lambda: board.reset_universe("50 EMA Touch Monthly", path=lists_on),
    ):
        with pytest.raises(board.UnknownListError, match="fills itself"):
            call()
    assert board.is_builtin("21 EMA Touch Monthly")
    # A list of the user's own may not take a timeframe name either.
    with pytest.raises(board.UnknownListError):
        board.save_universe(["ZZZ"], "21 EMA Touch Daily", path=lists_on, create=True)


def test_unfetched_symbols_are_queued_for_the_refresher(lists_on):
    ema_touch._WANTED.clear()
    ema_touch.touchers(["AAA", "NEW1"], 21)
    assert "NEW1" in ema_touch._WANTED
    ema_touch._WANTED.clear()


def test_switched_off_means_no_lists(lists_on, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_EMA_LISTS", "0")
    assert "21 EMA Touch Monthly" not in board.list_names(lists_on)


def test_service_switch_moves_the_active_list(lists_on, monkeypatch):
    from momx import service

    monkeypatch.setattr(board, "universe_path", lambda: lists_on)
    monkeypatch.setattr(service, "start_warmer", lambda: None)
    monkeypatch.setattr(service, "refresh_now", lambda name=None: None)
    board.set_active_list("21 EMA Touch Monthly", path=lists_on)
    out = service.set_ema_timeframe(21, "Daily")
    assert out == {"ok": True, "list": "21 EMA Touch Daily", "previous": "21 EMA Touch Monthly",
                   "active": "21 EMA Touch Daily"}
    entry = next(e for e in service.lists()["lists"] if e["name"] == "21 EMA Touch Daily")
    assert entry["emaLength"] == 21 and entry["timeframe"] == "Daily"
    assert entry["timeframes"] == ["Monthly", "Weekly", "Daily"] and entry["builtIn"]
    assert service.set_ema_timeframe(21, "Yearly")["ok"] is False
