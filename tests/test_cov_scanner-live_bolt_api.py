"""Integration: the LIVE lightning bolt (momx/live_bolt.py) over real HTTP.

Two hops carry the bolt to the screen:
  * the scanner worker hands api_server the per-symbol seeds it harvested off
    the last board build (GET /api/momx-scanner/live-seeds);
  * api_server serves the per-second bolt state the board polls
    (GET /api/momx-live-bolts, ?full=1 every tenth poll for the H/L sort).

Nothing here starts the per-second loop: it would read the live :3010 worker
and lease symbols on the Schwab stream. The bolt book is seeded directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from api_harness import api_server, running_api
from momx import live_bolt, service
from worker_harness import running_worker

ET = ZoneInfo("America/New_York")


def _seed(t: int, close: float) -> dict:
    return {"v": live_bolt.SEED_VERSION, "t": t, "close": close, "ema4": close, "ema8": close}


def _board(*rows: dict) -> dict:
    return {"generatedAt": "2026-11-03T15:00:00+00:00", "rows": list(rows), "rest": [], "universe": [r["symbol"] for r in rows]}


def _row(symbol: str, seed: dict | None, rvol4h: float | None = None) -> dict:
    m5 = {"vwap": 10.0}
    if seed is not None:
        m5["liveSeed"] = seed
    return {"symbol": symbol, "scanPass": True, "m5": m5, "rvol": {"4h": {"value": rvol4h}}}


@pytest.fixture
def seeds(monkeypatch):
    monkeypatch.setattr(service, "_LIVE_SEEDS", {})
    return service._LIVE_SEEDS


def test_the_worker_serves_the_seeds_harvested_from_the_last_build_and_strips_them_off_the_rows(tmp_path, monkeypatch, seeds):
    board = _board(_row("AAA", _seed(1_793_721_600, 12.5), rvol4h=2.4), _row("BBB", _seed(1_793_721_600, 40.0)), _row("CCC", None))
    service._harvest_live_seeds("Watchlist", board)
    # The seed never reaches a browser or the archive: harvesting removes it.
    assert all("liveSeed" not in row["m5"] for row in board["rows"])

    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/live-seeds?list=Watchlist")
        assert status == 200, body
        assert body["list"] == "Watchlist"
        assert sorted(body["seeds"]) == ["AAA", "BBB"]           # CCC had no seed
        assert body["seeds"]["AAA"]["close"] == 12.5
        assert body["seeds"]["AAA"]["rvol4h"] == 2.4             # 4h RVOL rides on the seed

        # A name that left the board (list edit, options gate) leaves the bolt too.
        service._harvest_live_seeds("Watchlist", _board(_row("AAA", None)))
        status, body = worker.get("/api/momx-scanner/live-seeds?list=Watchlist")
        assert status == 200
        assert sorted(body["seeds"]) == ["AAA"]

        # No list = the union of every board, newest seed per symbol wins.
        service._harvest_live_seeds("Mag7", _board(_row("AAA", _seed(1_793_722_200, 13.0)), _row("DDD", _seed(1_793_721_600, 5.0))))
        status, body = worker.get("/api/momx-scanner/live-seeds")
        assert status == 200
        assert sorted(body["seeds"]) == ["AAA", "DDD"]
        assert body["seeds"]["AAA"]["close"] == 13.0


def test_a_list_with_no_build_yet_answers_with_no_seeds_rather_than_an_error(tmp_path, monkeypatch, seeds):
    # api_server polls this every 30 s; a 500 here would leave the bolt loop
    # holding stale seeds. live_seeds never raises, so it is an empty set.
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/live-seeds?list=Mag7")
    assert status == 200 and body == {"list": "Mag7", "seeds": {}}


def _stamp(at: datetime) -> str:
    return at.isoformat(timespec="seconds")


@pytest.fixture
def bolt_book(monkeypatch, tmp_path):
    """A real LiveBoltBook with one stepped state and one fire saved earlier
    today that has not been stepped since (the after-restart case)."""
    now = datetime(2026, 11, 3, 11, 30, tzinfo=ET)
    book = live_bolt.LiveBoltBook(tmp_path)
    book.load_day(now)
    book._state = {
        "AAA": {"on": True, "now": True, "firedAt": _stamp(now), "firstAt": _stamp(now - timedelta(minutes=20)),
                "families": ["MACD"], "opacity": 0.9, "hlPos": 0.8, "hlMomentum": 2.5, "gates": {}, "last": 12.6},
        "BBB": {"on": False, "now": False, "firedAt": None, "firstAt": None, "families": [], "opacity": None,
                "hlPos": 0.2, "hlMomentum": -1.1, "gates": {}, "last": 40.1},
    }
    book._fires = {"CCC": {"firstAt": (now - timedelta(hours=1)).timestamp(), "lastAt": (now - timedelta(minutes=40)).timestamp(),
                           "families": ["EMA"], "count": 2}}
    touches: list[int] = []
    monkeypatch.setitem(api_server._LIVE_BOLT, "book", book)
    monkeypatch.setitem(api_server._LIVE_BOLT, "seeds", {"AAA": {}, "BBB": {}})
    monkeypatch.setitem(api_server._LIVE_BOLT, "leased", frozenset({"AAA", "BBB"}))
    # The keep-alive must be touched on every poll, but must not start the loop.
    monkeypatch.setattr(api_server, "_live_bolt_touch", lambda: touches.append(1))
    monkeypatch.setattr(api_server, "_schwab_market_data_healthy", lambda: False)
    return touches


def test_the_board_poll_gets_only_names_that_fired_today_and_keeps_the_loop_awake(bolt_book):
    with running_api() as api:
        status, body = api.get("/api/momx-live-bolts")
    assert status == 200, body
    assert sorted(body["bolts"]) == ["AAA", "CCC"]               # BBB never fired: left out of the small poll
    assert body["bolts"]["AAA"]["now"] is True and body["bolts"]["AAA"]["on"] is True
    assert body["bolts"]["CCC"]["on"] is False and body["bolts"]["CCC"]["firedAt"]
    assert body["full"] is False
    assert body["serving"] is False                              # no Schwab stream: said honestly
    assert (body["leased"], body["seeds"]) == (2, 2)
    assert bolt_book == [1]


def test_full_adds_the_hl_momentum_of_every_symbol_for_the_sort(bolt_book):
    with running_api() as api:
        status, body = api.get("/api/momx-live-bolts?full=1")
    assert status == 200, body
    assert body["full"] is True
    assert sorted(body["bolts"]) == ["AAA", "BBB", "CCC"]
    assert body["bolts"]["BBB"] == {"hlMomentum": -1.1, "hlPos": 0.2}   # no fire fields on a name that never fired
    assert body["bolts"]["AAA"]["hlMomentum"] == 2.5
    assert "hlMomentum" in body["bolts"]["CCC"]


def test_before_any_screen_asked_there_is_no_book_and_the_answer_is_empty_not_an_error(monkeypatch):
    monkeypatch.setitem(api_server._LIVE_BOLT, "book", None)
    monkeypatch.setattr(api_server, "_live_bolt_touch", lambda: None)
    monkeypatch.setattr(api_server, "_schwab_market_data_healthy", lambda: True)
    with running_api() as api:
        status, body = api.get("/api/momx-live-bolts?full=1")
    assert status == 200, body
    assert body["bolts"] == {} and body["serving"] is True
