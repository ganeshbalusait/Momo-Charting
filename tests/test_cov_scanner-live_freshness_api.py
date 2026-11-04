"""Integration: what the board says about its own data, over real HTTP.

  * Every served board carries `freshness` (scan start/finish, newest and
    oldest bar, the provider note) and, on TOS data, `tosStatus` - the
    "TOS loading n/N" toolbar note reads it. Both are stamped at SERVE time.
  * That status line must never cost the board: with the tape store busy the
    board still answers, with the last known count marked refreshing.
  * Switching the data source is global, so api_server refuses a non-admin
    BEFORE the worker sees the request.
Tapes, the clock and the source file are scratch fakes.
"""
from __future__ import annotations

import threading
import time

import pandas as pd
import pytest

import momx_worker
from api_harness import api_server, running_api
from momx import feed, service
from worker_harness import running_worker

WALL = 1_793_721_600.0     # 2026-11-03 10:40 ET


def _publish(name: str, universe: list[str], rows: list[dict]) -> None:
    payload = {"list": name, "generatedAt": "2026-11-03T15:41:00+00:00", "scanStartedAt": "2026-11-03T15:40:10+00:00",
               "scanCompletedAt": "2026-11-03T15:40:55+00:00", "tapeAsOf": "2026-11-03T15:35:00+00:00",
               "direction": "bull", "universe": universe, "rows": rows, "rest": [], "errors": {}}
    state = service._state(name)
    with service._LOCK:
        state.payload, state.bear, state.error = payload, {**payload, "direction": "bear"}, ""


def _rows() -> list[dict]:
    return [{"symbol": "AAA", "marketDataAt": "2026-11-03T15:35:00+00:00", "scanPass": True},
            {"symbol": "BBB", "marketDataAt": "2026-11-03T15:20:00+00:00", "scanPass": False},
            {"symbol": "CCC", "scanPass": False}]


@pytest.fixture
def tapes(monkeypatch):
    """AAA holds all three TOS tapes (loaded), BBB only a 5m one (pending)."""
    frame = pd.DataFrame({"close": [1.0, 2.0]})
    stores = {}
    for kind in ("5m", "30m", "daily"):
        store = feed._KindStore(WALL - 600)
        store.tapes["AAA"] = frame
        store.fetched_at["AAA"] = WALL - 90
        if kind == "5m":
            store.tapes["BBB"] = frame
        stores[kind] = store
    monkeypatch.setattr(feed, "_STORE", stores)
    monkeypatch.setattr(feed, "_TOS_STATUS_CACHE", {})
    monkeypatch.setattr(feed, "_wall_clock", lambda: WALL)
    return stores


def test_a_tos_board_carries_its_freshness_and_how_many_tapes_are_held(tmp_path, monkeypatch, tapes):
    monkeypatch.setattr(feed, "SCHWAB_ONLY", True)
    with running_worker(tmp_path, monkeypatch) as worker:
        _publish("Mag7", ["AAA", "BBB", "CCC"], _rows())
        status, body = worker.get("/api/momx-scanner?list=Mag7")
    assert status == 200, body
    assert body["dataSource"] == "tos"
    fresh = body["freshness"]
    assert fresh["scanStartedAt"] == "2026-11-03T15:40:10+00:00" and fresh["scanCompletedAt"] == "2026-11-03T15:40:55+00:00"
    assert fresh["newestDataAt"] == "2026-11-03T15:35:00+00:00" and fresh["oldestDataAt"] == "2026-11-03T15:20:00+00:00"
    assert (fresh["rowsWithTime"], fresh["unknownTimeRows"]) == (2, 1)
    assert (fresh["barIntervalSeconds"], fresh["barTimestampRole"]) == (300, "start")
    assert fresh["providerDelayMinutes"] is None and "TOS" in fresh["providerNote"]
    assert fresh["servedAt"]
    assert body["tosStatus"] == {"source": "tos", "total": 3, "loaded": 1, "pending": 2, "oldestSeconds": 90}


def test_an_alpaca_board_says_its_provider_delay_and_has_no_tos_count(tmp_path, monkeypatch, tapes):
    monkeypatch.setattr(feed, "SCHWAB_ONLY", False)
    with running_worker(tmp_path, monkeypatch) as worker:
        _publish("Mag7", ["AAA"], _rows()[:1])
        status, body = worker.get("/api/momx-scanner?list=Mag7")
    assert status == 200, body
    assert body["dataSource"] == "alpaca"
    assert body["freshness"]["providerDelayMinutes"] in (16, 20)
    assert "tosStatus" not in body


def test_a_busy_tape_store_never_holds_the_board(tmp_path, monkeypatch, tapes):
    monkeypatch.setattr(feed, "SCHWAB_ONLY", True)
    with running_worker(tmp_path, monkeypatch) as worker:
        _publish("Mag7", ["AAA", "BBB", "CCC"], _rows())
        _, warm = worker.get("/api/momx-scanner?list=Mag7")     # caches the count
        holder_ready, release = threading.Event(), threading.Event()

        def hold():
            with feed._CACHE_LOCK:
                holder_ready.set()
                release.wait(10)

        holder = threading.Thread(target=hold, daemon=True)
        holder.start()
        holder_ready.wait(5)
        try:
            started = time.monotonic()
            status, body = worker.get("/api/momx-scanner?list=Mag7")
            took = time.monotonic() - started
        finally:
            release.set()
            holder.join(5)
    assert status == 200, body
    assert took < 3
    assert body["rows"] and body["tosStatus"]["refreshing"] is True
    assert body["tosStatus"]["loaded"] == warm["tosStatus"]["loaded"] == 1


def test_the_tos_debug_route_reports_per_symbol_ages_for_the_asked_symbols_only(tmp_path, monkeypatch, tapes):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/tos-debug?symbols=aaa,ZZZ")
        all_status, everything = worker.get("/api/momx-scanner/tos-debug")
    assert status == 200, body
    assert body["source"] in ("tos", "alpaca") and body["budgetPerMinute"] == feed.TOS_REQUESTS_PER_MINUTE
    assert body["kinds"]["5m"]["held"] == 2
    assert body["kinds"]["5m"]["fetchedAgeSeconds"] == {"AAA": 90.0}
    assert all_status == 200 and everything["kinds"]["5m"]["fetchedAgeSeconds"] is None   # unscoped: counts only


@pytest.fixture
def switch(monkeypatch, tmp_path):
    seen = {"checks": [], "sets": []}
    monkeypatch.setenv(feed.DATA_SOURCE_PATH_ENV, str(tmp_path / "source.json"))
    monkeypatch.setattr(feed, "SCHWAB_ONLY", True)
    monkeypatch.setattr(feed, "clear_cache", lambda: None)
    real_set = feed.set_data_source
    monkeypatch.setattr(feed, "set_data_source", lambda source: seen["sets"].append(source) or real_set(source))
    monkeypatch.setattr(momx_worker, "viewer_is_admin",
                        lambda who, db_path=None: seen["checks"].append(who) or str(who or "").lower() == "boss@x.com")
    monkeypatch.setattr(momx_worker.service, "request_rebuild", lambda name=None: {"ok": True, "queued": True})
    return seen


def test_a_member_cannot_switch_the_scanners_data_source_and_the_worker_never_sees_it(tmp_path, monkeypatch, switch):
    monkeypatch.setattr(api_server.ApiHandler, "_session_user", lambda self: {"email": "member@x.com", "role": "member"})
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        status, body = api.post("/api/momx-scanner/data-source", {"source": "tos"})
        read_status, read = api.get("/api/momx-scanner/data-source")
    assert status == 403, body
    assert switch["checks"] == [] and switch["sets"] == []      # refused before the worker
    assert read_status == 200 and read == {"source": "tos"}     # anyone may READ it


def test_an_admin_switch_reaches_the_worker_which_checks_again(tmp_path, monkeypatch, switch):
    monkeypatch.setattr(api_server.ApiHandler, "_session_user", lambda self: {"email": "boss@x.com", "role": "admin"})
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        status, body = api.post("/api/momx-scanner/data-source", {"source": "tos"})
        bad_status, bad = api.post("/api/momx-scanner/data-source", {"source": "alpaca"})
    assert status == 200, body
    assert body == {"source": "tos", "rebuild": {"ok": True, "queued": True}}
    assert switch["checks"][0] == "boss@x.com"                  # the forwarded session email
    # TOS is permanent since 2026-10-01: Alpaca is refused with the reason.
    assert bad_status == 400 and "TOS data permanently" in bad["error"]
    assert feed.SCHWAB_ONLY is True
