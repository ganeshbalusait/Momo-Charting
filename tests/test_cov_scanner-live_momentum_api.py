"""Integration: the momentum strip (NEW / LOST / RVOL-spike chips) over HTTP,
through api_server's proxy to a real scanner worker.

Each "build" below is what service._build_once publishes: a completed board
in the list's in-memory state with a new generatedAt. The worker diffs one
build against the previous one, once per generatedAt, so the strip's 15 s
poll never replays old events; Clear empties it without losing the baseline.
"""
from __future__ import annotations

from api_harness import running_api
from momx import service
from worker_harness import running_worker

UNIVERSE = ["AAA", "BBB", "CCC", "DDD"]


def _row(symbol: str, passed: bool, rvol5: float = 1.0, pct: float = 1.0) -> dict:
    return {"symbol": symbol, "scanPass": passed, "pctChange": pct, "scanReasons": ["rvol:5m"] if passed else [],
            "rvol": {"5m": {"value": rvol5, "bg": "black", "fg": "green"}}}


def _publish(name: str, stamp: str, rows: list[dict], *, bear_rows: list[dict] | None = None) -> None:
    """What a finished build leaves behind for service.snapshot()."""
    state = service._state(name)
    payload = {"list": name, "generatedAt": stamp, "direction": "bull", "universe": list(UNIVERSE),
               "rows": rows, "rest": [], "errors": {}}
    bear = {**payload, "direction": "bear", "rows": bear_rows if bear_rows is not None else []}
    with service._LOCK:
        state.payload, state.bear, state.error = payload, bear, ""


def _symbols(events, kind):
    return [e["symbol"] for e in events if e["type"] == kind]


def test_a_new_build_puts_new_lost_and_rvol_chips_on_the_strip_once(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        _publish("Watchlist", "build-1", [_row("AAA", True), _row("BBB", False), _row("CCC", True)])
        status, body = api.get("/api/momx-scanner/momentum?list=Watchlist")
        assert status == 200, body
        # The first build of the day is a cold start: no baseline, no burst of chips.
        assert body["events"] == [] and body["generatedAt"] == "build-1"

        _publish("Watchlist", "build-2", [_row("AAA", True, rvol5=3.4), _row("BBB", True, pct=4.2), _row("CCC", False)])
        status, body = api.get("/api/momx-scanner/momentum?list=Watchlist")
        assert status == 200, body
        events = body["events"]
        assert _symbols(events, "new_match") == ["BBB"]
        assert _symbols(events, "lost_match") == ["CCC"]
        assert _symbols(events, "rvol_spike") == ["AAA"]
        assert next(e for e in events if e["type"] == "new_match")["pctChange"] == 4.2

        # The 15 s poll before the next build: the same chips, not doubled.
        status, again = api.get("/api/momx-scanner/momentum?list=Watchlist")
        assert status == 200 and again["events"] == events


def test_clear_empties_the_strip_and_the_next_build_reports_only_what_changed_after_it(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        _publish("Watchlist", "b1", [_row("AAA", True), _row("BBB", False)])
        api.get("/api/momx-scanner/momentum?list=Watchlist")
        _publish("Watchlist", "b2", [_row("AAA", True), _row("BBB", True)])
        status, body = api.get("/api/momx-scanner/momentum?list=Watchlist")
        assert _symbols(body["events"], "new_match") == ["BBB"]

        status, cleared = api.post("/api/momx-scanner/momentum/clear", {"list": "Watchlist"})
        assert status == 200, cleared
        assert cleared["events"] == [] and cleared["cleared"] is True
        status, body = api.get("/api/momx-scanner/momentum?list=Watchlist")
        assert status == 200 and body["events"] == []

        # The baseline survived the clear: BBB staying matched is not news again,
        # only DDD arriving is.
        _publish("Watchlist", "b3", [_row("AAA", True), _row("BBB", True), _row("DDD", True)])
        status, body = api.get("/api/momx-scanner/momentum?list=Watchlist")
        assert status == 200
        assert [(e["type"], e["symbol"]) for e in body["events"]] == [("new_match", "DDD")]


def test_the_bear_board_keeps_its_own_strip(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        _publish("Mag7", "m1", [_row("AAA", False)], bear_rows=[_row("AAA", False)])
        api.get("/api/momx-scanner/momentum?list=Mag7")
        api.get("/api/momx-scanner/momentum?list=Mag7&dir=bear")
        _publish("Mag7", "m2", [_row("AAA", False)], bear_rows=[_row("AAA", True, pct=-3.0)])
        _, bull = api.get("/api/momx-scanner/momentum?list=Mag7")
        status, bear = api.get("/api/momx-scanner/momentum?list=Mag7&dir=bear")
    assert status == 200, bear
    assert bull["events"] == [] and bull["direction"] == "bull"
    assert bear["direction"] == "bear"
    assert [(e["type"], e["symbol"]) for e in bear["events"]] == [("new_match", "AAA")]


def test_a_ticker_removed_from_the_list_loses_its_chips_on_the_next_poll(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        _publish("Watchlist", "p1", [_row("AAA", False), _row("BBB", False)])
        api.get("/api/momx-scanner/momentum?list=Watchlist")
        _publish("Watchlist", "p2", [_row("AAA", True), _row("BBB", True)])
        _, body = api.get("/api/momx-scanner/momentum?list=Watchlist")
        assert sorted(_symbols(body["events"], "new_match")) == ["AAA", "BBB"]
        # He pastes a list without BBB: the warming placeholder already carries the new universe.
        state = service._state("Watchlist")
        with service._LOCK:
            state.payload = {**state.payload, "universe": ["AAA"], "rows": [_row("AAA", True)]}
        status, body = api.get("/api/momx-scanner/momentum?list=Watchlist")
    assert status == 200
    assert _symbols(body["events"], "new_match") == ["AAA"]


def test_an_unknown_list_is_a_400_with_the_reason(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        status, body = api.get("/api/momx-scanner/momentum?list=NoSuchList")
    assert status == 400, body
    assert "error" in body
