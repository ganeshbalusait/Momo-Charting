"""Integration: My Watchlist (the master ticker list) through the real
/api/watchlist routes over HTTP, the real DashboardState persister behind them,
and the real gateway in front for PUT/DELETE.

Safety: the persister writes watchlist.txt in the REPO ROOT (config
WATCHLIST_PATH), which tests/conftest.py does not isolate and the write guard
does not cover - every test here points it at a scratch file first. The Alpaca
stream re-arm is a recorder, the served dashboard is a scratch dict, and the
in-memory lists the scanner, option bot and Mag7 scan read are restored after
each test.
"""
from __future__ import annotations

import threading
from datetime import datetime
from types import SimpleNamespace

import pytest

from api_harness import RELAYED, api_server, running_api
from http_client import ApiClient

MASTER = ["AAPL", "NVDA", "PLTR", "META", "SPY"]


@pytest.fixture
def watchlist(monkeypatch, tmp_path):
    state = api_server.STATE
    settings = api_server.settings
    path = tmp_path / "watchlist.txt"
    path.write_text("\n".join(MASTER) + "\n", encoding="utf-8")
    streamed: list[list[str]] = []
    monkeypatch.setattr(api_server, "WATCHLIST_PATH", path)
    monkeypatch.setattr(settings.scanner, "default_universe", list(MASTER))
    scanner = getattr(state, "scanner", None)
    if scanner is not None and getattr(scanner, "settings", None) is not None:
        monkeypatch.setattr(scanner.settings, "default_universe", list(MASTER))
    monkeypatch.setattr(state, "client", SimpleNamespace(ensure_streaming=lambda symbols: streamed.append(list(symbols))))
    for name in ("earnings_calendar_cache", "action_message", "scan_results", "candidate_results", "scan_timestamp"):
        monkeypatch.setattr(state, name, getattr(state, name, None), raising=False)
    monkeypatch.setattr(state, "option_watchlist", ["AAPL", "NVDA", "PLTR"])
    monkeypatch.setattr(state, "mag7_scanner_watchlist", ["AAPL", "NVDA", "METU"])
    monkeypatch.setattr(state, "option_bot_config", {**state.option_bot_config, "watchlistSource": "option"})
    # The payload being served: fresh, so no request rebuilds it from brokers.
    monkeypatch.setattr(state, "dashboard_cache", {
        "watchlist": list(MASTER),
        "optionWatchlist": ["AAPL", "NVDA", "PLTR"],
        "activeOptionWatchlist": ["AAPL", "NVDA", "PLTR"],
        "mag7OptionWatchlist": ["AAPL", "NVDA", "META"],
    })
    monkeypatch.setattr(state, "dashboard_cache_timestamp", datetime.now().astimezone())
    return SimpleNamespace(path=path, streamed=streamed, settings=settings)


def saved(env) -> list[str]:
    return env.path.read_text(encoding="utf-8").split()


def test_adding_a_ticker_saves_it_everywhere_the_scanners_read(watchlist):
    with running_api() as api:
        status, body = api.post("/api/watchlist", {"ticker": " pltr "})        # already there: no duplicate
        assert status == 201 and body["watchlist"].count("PLTR") == 1
        status, body = api.post("/api/watchlist", {"ticker": "now"})
    assert status == 201, body
    assert body["watchlist"] == MASTER + ["NOW"]
    assert saved(watchlist) == MASTER + ["NOW"]                                # watchlist.txt
    assert watchlist.settings.scanner.default_universe == MASTER + ["NOW"]     # the MomX Watchlist seed
    assert watchlist.streamed[-1] == MASTER + ["NOW", "SPY"]                   # Alpaca stream re-armed


def test_a_blank_ticker_is_refused_and_nothing_is_written(watchlist):
    with running_api() as api:
        status, body = api.post("/api/watchlist", {"ticker": "   "})
    assert (status, body) == (400, {"error": "ticker is required."})
    assert saved(watchlist) == MASTER and watchlist.streamed == []


@pytest.mark.xfail(strict=True, reason=(
    "The master watchlist accepts a malformed ticker: POST {ticker:'1bad'} saves '1BAD' into "
    "watchlist.txt and every scanner, while every other ticker box in the app refuses it "
    "with the [A-Z][A-Z0-9.-]{0,9} rule."))
def test_a_malformed_ticker_is_refused(watchlist):
    with running_api() as api:
        status, _body = api.post("/api/watchlist", {"ticker": "1bad"})
    assert status == 400
    assert "1BAD" not in saved(watchlist)


def test_rename_keeps_the_tickers_place_and_a_missing_one_is_404(watchlist):
    with running_api() as api:
        status, body = api.request("PUT", "/api/watchlist/PLTR", {"ticker": "now"})
        assert status == 200, body
        assert body["watchlist"] == ["AAPL", "NVDA", "NOW", "META", "SPY"]
        status, missing = api.request("PUT", "/api/watchlist/ZZZZ", {"ticker": "TSLA"})
        status_blank, blank = api.request("PUT", "/api/watchlist/AAPL", {"ticker": ""})
    assert (status, missing) == (404, {"error": "Ticker not found."})
    assert (status_blank, blank) == (400, {"error": "Replacement ticker is required."})
    assert saved(watchlist) == ["AAPL", "NVDA", "NOW", "META", "SPY"]


def test_a_delete_reaches_every_narrow_list_on_the_next_poll(watchlist):
    """2026-08-27: EA survived deletion on the MomX board. A delete must drop the
    ticker from the option list, the bot's active list and the Mag7 scan in
    the same breath - on the very next dashboard poll, not after 60 s."""
    with running_api() as api:
        status, body = api.request("DELETE", "/api/watchlist/nvda")
        assert status == 200, body
        _, dashboard = api.get("/api/dashboard")
    for key in ("watchlist", "optionWatchlist", "activeOptionWatchlist", "mag7OptionWatchlist"):
        assert "NVDA" not in dashboard[key], key
    assert dashboard["watchlist"] == ["AAPL", "PLTR", "META", "SPY"]
    assert dashboard["mag7OptionWatchlist"] == ["AAPL", "META"]               # METU maps to META, still listed
    assert saved(watchlist) == ["AAPL", "PLTR", "META", "SPY"]
    from momx import board
    assert "NVDA" not in board.settings.scanner.default_universe              # the MomX Watchlist seed


@pytest.mark.xfail(strict=True, reason=(
    "Deleting the last ticker raises ValueError('Watchlist cannot be empty.') which the "
    "generic handler turns into HTTP 500 - the screen reads it as a server outage."))
def test_deleting_the_last_ticker_is_a_plain_400(watchlist, monkeypatch):
    monkeypatch.setattr(watchlist.settings.scanner, "default_universe", ["AAPL"])
    watchlist.path.write_text("AAPL\n", encoding="utf-8")
    with running_api() as api:
        status, body = api.request("DELETE", "/api/watchlist/AAPL")
    assert saved(watchlist) == ["AAPL"], "the list itself is never emptied"
    assert (status, body) == (400, {"error": "Watchlist cannot be empty."})


def test_a_signed_out_tunnel_visitor_cannot_change_the_list(watchlist):
    with running_api() as api:
        add_status, _ = api.post("/api/watchlist", {"ticker": "TSLA"}, headers=RELAYED)
        del_status, _ = api.request("DELETE", "/api/watchlist/AAPL", headers=RELAYED)
    assert (add_status, del_status) == (401, 401)
    assert saved(watchlist) == MASTER and watchlist.streamed == []


def test_edit_and_delete_through_the_gateway_answer_json_not_501(watchlist, monkeypatch, tmp_path):
    """2026-08-23: the gateway had no do_PUT/do_DELETE, so Edit and Delete got
    a 501 HTML page that the screen showed as 'API is unavailable'."""
    import gateway

    monkeypatch.setattr(gateway, "CHAIN_BLOBS", tmp_path / "no_blobs")
    with running_api() as api:
        handler = type("TestGatewayHandler", (gateway.GatewayHandler,), {"pipeline_base": api.base_url})
        server = gateway.GatewayServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            front = ApiClient(f"http://127.0.0.1:{server.server_address[1]}")
            put_status, renamed = front.request("PUT", "/api/watchlist/PLTR", {"ticker": "NOW"})
            del_status, deleted = front.request("DELETE", "/api/watchlist/NOW")
            content_type = front.last_headers["Content-Type"]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    assert (put_status, del_status, content_type) == (200, 200, "application/json")
    assert "NOW" in renamed["watchlist"] and "NOW" not in deleted["watchlist"]
    assert saved(watchlist) == ["AAPL", "NVDA", "META", "SPY"]
