"""Integration: the MAG7 OI scan (POST /api/oi-scan) and the MAG7 OI wall
board route (GET /api/mag7-oi-walls), through the real api_server handler over
HTTP.

The scan's chain sweep is replaced by a recorder that holds the job open until
the test releases it, so no scan thread ever reaches Schwab. The wall board's
Schwab and Tradier clients are fakes.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from api_harness import api_server, running_api
from test_cov_options_chain_feed_api import schwab_chain

MAG7 = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA"]


def wait_until(predicate, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def scan(monkeypatch):
    state = api_server.STATE
    release = threading.Event()
    swept: list[tuple[list[str], str]] = []

    def sweep(symbols=None, scan_label="", return_payload=False, **_kw):
        swept.append((list(symbols or []), scan_label))
        release.wait(10)

    monkeypatch.setattr(state, "scan_oi_watchlist", sweep)
    monkeypatch.setattr(state, "oi_scan_job", {})
    monkeypatch.setattr(state, "mag7_scanner_watchlist", ["METU", "AMDL", "AAPL"])
    for name in ("oi_action_message", "action_message"):
        monkeypatch.setattr(state, name, getattr(state, name, ""), raising=False)
    yield SimpleNamespace(state=state, release=release, swept=swept)
    release.set()
    wait_until(lambda: not state.oi_scan_job.get("running"))


def test_watchlist_oi_scanning_is_refused(scan):
    with running_api() as api:
        watchlist = api.post("/api/oi-scan", {"universe": "watchlist"})
        both = api.post("/api/oi-scan", {"universe": "both"})
    for status, body in (watchlist, both):
        assert status == 403
        assert body["error"].startswith("Watchlist OI scanning is disabled.")
    assert scan.swept == []


def test_scan_mag7_oi_starts_one_job_over_the_mapped_underlyings_and_a_second_click_is_busy(scan):
    with running_api() as api:
        status, first = api.post("/api/oi-scan", {"universe": "mag7"})
        assert status == 200, first
        assert wait_until(lambda: scan.swept), "the scan job never started"
        _, second = api.post("/api/oi-scan", {})                 # default universe is mag7
        scan.release.set()
        assert wait_until(lambda: not scan.state.oi_scan_job.get("running"))
    assert (first["started"], first["scanLabel"], first["symbolCount"]) == (True, "MAG7 OI Scanner", 3)
    assert scan.swept == [(["META", "AMD", "AAPL"], "MAG7 OI Scanner")]   # METU->META, AMDL->AMD
    assert (second["started"], second["busy"]) == (False, True)
    assert scan.state.oi_scan_job["finishedAt"] is not None


class WallSchwab:
    def __init__(self, error: str = ""):
        self.error = error
        self.symbols: list[str] = []

    def get_option_chain(self, symbol, **_kw):
        name = threading.current_thread().name
        if "process_request_thread" not in name and "ThreadPoolExecutor" not in name:
            raise RuntimeError("background collector refused by the test's fake broker")
        self.symbols.append(symbol)
        if self.error:
            raise RuntimeError(self.error)
        return schwab_chain(symbol, dtes=(1, 8))


class WallTradier:
    configured = True

    def __init__(self):
        self.symbols: list[str] = []

    def get_option_chain(self, symbol, **_kw):
        self.symbols.append(symbol)
        return schwab_chain(symbol, dtes=(1,))


@pytest.fixture
def walls(monkeypatch):
    state = api_server.STATE
    env = SimpleNamespace(schwab=WallSchwab(), tradier=WallTradier())
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [env.schwab])
    monkeypatch.setattr(api_server, "_house_tradier_client", lambda: env.tradier)
    monkeypatch.setattr(state, "mag7_oi_wall_cache", None)
    monkeypatch.setattr(state, "mag7_oi_wall_cache_timestamp", None)
    return env


def test_the_mag7_wall_board_reads_schwab_and_never_tradier_when_schwab_answers(walls):
    with running_api() as api:
        status, body = api.get("/api/mag7-oi-walls?force=true")
    assert status == 200, body
    assert sorted(walls.schwab.symbols) == sorted(MAG7)
    assert walls.tradier.symbols == []
    assert body["source"] == "Schwab/TOS option chain" and body["live"] is True
    assert body["symbols"] == MAG7


def test_the_mag7_wall_board_falls_back_to_tradier_and_says_so(walls):
    walls.schwab.error = "invalid_client: Unauthorized"
    with running_api() as api:
        status, body = api.get("/api/mag7-oi-walls?force=true")
    assert status == 200, body
    assert sorted(walls.tradier.symbols) == sorted(MAG7)
    assert body["source"] == "Tradier option chain (Schwab/TOS fallback)"
