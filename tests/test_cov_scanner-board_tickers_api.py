"""Integration: the Tickers box (Add / Set / Restore) on the real scanner worker.

Add APPENDS to the list on screen and says what was already there; Set
REPLACES one list and only that one; Restore puts a built-in list back on its
default tickers and refuses a user list (there is nothing to restore it to).
The derived "21 EMA Touch" lists fill themselves and refuse every edit.
All writes go to the harness's scratch universe file.
"""
from __future__ import annotations

import json

import pytest

from momx import board, ema_touch
from worker_harness import running_worker

UNIVERSE = "/api/momx-scanner/universe"
LISTS = "/api/momx-scanner/lists"


def saved_lists() -> dict:
    return json.loads(board.universe_path().read_text(encoding="utf-8"))["lists"]


def test_add_appends_reports_duplicates_and_leaves_mag7_alone(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        mag7_before = board.load_universe("Mag7")
        status, body = worker.post(UNIVERSE + "/add", {"text": "avgo spy, AAPL", "list": "Watchlist"})
        again = worker.post(UNIVERSE + "/add", {"text": "AVGO", "list": "watchlist"})[1]
        mag7_after = board.load_universe("Mag7")
    assert status == 200, body
    assert body["added"] == ["AVGO", "AAPL"] and body["already"] == ["SPY"]
    assert (body["universeBefore"], body["universeCount"]) == (2, 4)
    # The Watchlist used to follow the default list; now it holds its own copy, and says so.
    assert body["materialised"] is True and body["noop"] is False
    assert board.load_universe("Watchlist") == ["SPY", "QQQ", "AVGO", "AAPL"]
    # Re-typing a ticker already there changes nothing and rebuilds nothing.
    assert again["noop"] is True and again["added"] == [] and again["already"] == ["AVGO"]
    assert mag7_after == mag7_before


def test_set_replaces_only_the_named_list(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.post(LISTS + "/create", {"name": "Swing", "text": "AMD INTC"})
        worker.post(UNIVERSE + "/add", {"text": "MU", "list": "Watchlist"})
        before = saved_lists()
        status, body = worker.post(UNIVERSE, {"text": "TSLA\nNVDA", "list": "Swing"})
        after = saved_lists()
    assert status == 200, body
    assert (body["ok"], body["list"], body["universe"]) == (True, "Swing", ["TSLA", "NVDA"])
    assert after["Swing"] == ["TSLA", "NVDA"]
    assert after["Watchlist"] == before["Watchlist"] == ["SPY", "QQQ", "MU"]


def test_set_with_no_usable_ticker_changes_nothing(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.post(UNIVERSE + "/add", {"text": "MU", "list": "Watchlist"})
        before = saved_lists()
        status, body = worker.post(UNIVERSE, {"text": "12.50 -- N/A", "list": "Watchlist"})
        after = saved_lists()
    assert status == 200
    assert body["ok"] is False and body["error"] == "No usable tickers found in that text."
    assert body["universe"] == ["SPY", "QQQ", "MU"]
    assert after == before


def test_restore_puts_a_built_in_list_back_on_its_defaults(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.post(UNIVERSE, {"text": "TSLA", "list": "Watchlist"})
        status, body = worker.post(UNIVERSE + "/reset", {"list": "Watchlist"})
    assert status == 200, body
    assert (body["restored"], body["universe"], body["universeCount"]) == (True, ["SPY", "QQQ"], 2)
    assert "Watchlist" not in saved_lists()          # follows the default list again, no frozen copy


def test_restore_refuses_a_user_list_and_deletes_nothing(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.post(LISTS + "/create", {"name": "Swing", "text": "AMD"})
        status, body = worker.post(UNIVERSE + "/reset", {"list": "Swing"})
    assert status == 400
    assert body["error"].startswith("Swing is your own list, so there is nothing to restore it to.")
    assert saved_lists()["Swing"] == ["AMD"]


@pytest.mark.parametrize("text, expected", [
    ("Symbol,Description,Last\nAAPL,APPLE INC,180.52\nNVDA,NVIDIA CORP,120.10", ["AAPL", "NVDA"]),
    ("NET LOW OPEN AAPL", ["NET", "LOW", "OPEN", "AAPL"]),
    ("$SPX.X brk.b aapl", ["$SPX.X", "BRK.B", "AAPL"]),
    ("aapl msft, nvda;tsla|amd", ["AAPL", "MSFT", "NVDA", "TSLA", "AMD"]),
])
def test_a_pasted_export_parses_the_same_over_http_as_in_the_board(tmp_path, monkeypatch, text, expected):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.post(LISTS + "/create", {"name": "Paste"})
        status, body = worker.post(UNIVERSE + "/add", {"text": text, "list": "Paste"})
    assert status == 200, body
    assert body["added"] == expected == board.parse_universe(text)


def test_an_ema_touch_list_refuses_typed_tickers(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_EMA_LISTS", "1")
    monkeypatch.setenv("AGX_MOMX_EMA_TOUCH_PATH", str(tmp_path / "ema_cache.json"))
    monkeypatch.setenv("AGX_MOMX_EMA_TF_PATH", str(tmp_path / "ema_tf.json"))
    monkeypatch.setattr(ema_touch, "start_refresher", lambda universe: None)
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.post(UNIVERSE + "/add", {"text": "AAPL", "list": "21 EMA Touch Monthly"})
        replace = worker.post(UNIVERSE, {"text": "AAPL", "list": "21 EMA Touch Monthly"})
    assert status == 400
    assert "fills itself from your Watchlist" in body["error"]
    assert replace[0] == 400
