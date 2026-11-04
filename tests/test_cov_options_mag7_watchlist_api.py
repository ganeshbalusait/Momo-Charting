"""Integration: the Mag7 Watchlist page (the Mag7 option scanner's ticker
list) through the real /api/mag7-scanner-watchlist routes over HTTP, the real
DashboardState persister (saved as an app setting in the scratch database) and
the real gateway in front for PUT/DELETE.

The served dashboard is a scratch dict marked fresh, so no request rebuilds it
from a broker; the in-memory lists are restored after every test.
"""
from __future__ import annotations

import threading
from datetime import datetime
from types import SimpleNamespace

import pytest

from api_harness import RELAYED, api_server, running_api
from http_client import ApiClient

MASTER = ["AAPL", "AMD", "META", "NVDA", "USO", "SPY"]
MAG7 = ["METU", "AMDL", "AAPL"]          # leveraged tickers map to META / AMD for the option scan
OPTION_LIST = ["NVDA", "SPY"]


@pytest.fixture
def mag7(monkeypatch):
    state = api_server.STATE
    monkeypatch.setattr(api_server.settings.scanner, "default_universe", list(MASTER))
    monkeypatch.setattr(state, "mag7_scanner_watchlist", list(MAG7))
    monkeypatch.setattr(state, "option_watchlist", list(OPTION_LIST))
    for name in ("action_message", "mag7_scan_results", "mag7_candidate_results", "mag7_scan_timestamp"):
        monkeypatch.setattr(state, name, getattr(state, name, None), raising=False)
    monkeypatch.setattr(state, "dashboard_cache", {
        "mag7OptionWatchlistSource": list(MAG7),
        "mag7OptionWatchlist": ["META", "AMD", "AAPL"],
        "optionWatchlist": list(OPTION_LIST),
    })
    monkeypatch.setattr(state, "dashboard_cache_timestamp", datetime.now().astimezone())
    return SimpleNamespace(state=state)


def stored(env) -> str:
    return env.state.repository.get_app_settings().get("mag7_scanner_watchlist", "")


def test_adding_a_ticker_saves_the_list(mag7):
    with running_api() as api:
        status, _body = api.post("/api/mag7-scanner-watchlist", {"ticker": "uso"})
        _, listed = api.get("/api/mag7-scanner-watchlist")
    assert status == 201
    assert mag7.state.mag7_scanner_watchlist == MAG7 + ["USO"]
    assert stored(mag7) == ",".join(MAG7 + ["USO"])        # survives a restart
    assert listed == {"tickers": MAG7 + ["USO"]}


def test_a_blank_ticker_is_refused(mag7):
    with running_api() as api:
        status, body = api.post("/api/mag7-scanner-watchlist", {"ticker": ""})
    assert (status, body) == (400, {"error": "ticker is required."})
    assert mag7.state.mag7_scanner_watchlist == MAG7


def test_rename_keeps_its_place_and_a_missing_ticker_is_404(mag7):
    with running_api() as api:
        status, _ = api.request("PUT", "/api/mag7-scanner-watchlist/METU", {"ticker": "nvdl"})
        missing_status, missing = api.request("PUT", "/api/mag7-scanner-watchlist/TSLL", {"ticker": "TSLA"})
    assert status == 200
    assert mag7.state.mag7_scanner_watchlist == ["NVDL", "AMDL", "AAPL"]
    assert (missing_status, missing) == (404, {"error": "Ticker not found."})


def test_delete_removes_only_that_ticker(mag7):
    with running_api() as api:
        status, _ = api.request("DELETE", "/api/mag7-scanner-watchlist/amdl")
    assert status == 200
    assert mag7.state.mag7_scanner_watchlist == ["METU", "AAPL"]
    assert stored(mag7) == "METU,AAPL"


@pytest.mark.xfail(strict=True, reason=(
    "Deleting the last Mag7 ticker raises ValueError('Mag7 scanner watchlist cannot be empty.'), "
    "which the generic handler turns into HTTP 500 instead of a plain 400."))
def test_deleting_the_last_ticker_is_a_plain_400(mag7, monkeypatch):
    monkeypatch.setattr(mag7.state, "mag7_scanner_watchlist", ["AAPL"])
    with running_api() as api:
        status, body = api.request("DELETE", "/api/mag7-scanner-watchlist/AAPL")
    assert mag7.state.mag7_scanner_watchlist == ["AAPL"]
    assert (status, body) == (400, {"error": "Mag7 scanner watchlist cannot be empty."})


@pytest.mark.xfail(strict=True, reason=(
    "The Mag7 persister patches the served dashboard's optionWatchlist key but not "
    "mag7OptionWatchlistSource/mag7OptionWatchlist, so the ticker just added does not appear "
    "on the Mag7 Watchlist page until the 60 s dashboard cache rebuilds."))
def test_an_added_ticker_shows_on_the_very_next_read(mag7):
    with running_api() as api:
        status, body = api.post("/api/mag7-scanner-watchlist", {"ticker": "USO"})
        _, dashboard = api.get("/api/dashboard")
    assert status == 201
    assert "USO" in body["mag7OptionWatchlistSource"]
    assert "USO" in dashboard["mag7OptionWatchlistSource"] and "USO" in dashboard["mag7OptionWatchlist"]


@pytest.mark.xfail(strict=True, reason=(
    "Editing the Mag7 list overwrites the served optionWatchlist with the Mag7 tickers "
    "(METU, AMDL...), so the Option Watchlist page shows the wrong list until the cache rebuilds."))
def test_editing_the_mag7_list_leaves_the_option_watchlist_alone(mag7):
    with running_api() as api:
        api.post("/api/mag7-scanner-watchlist", {"ticker": "USO"})
        _, dashboard = api.get("/api/dashboard")
    assert dashboard["optionWatchlist"] == OPTION_LIST


def test_a_signed_out_tunnel_visitor_cannot_edit(mag7):
    with running_api() as api:
        status, _ = api.post("/api/mag7-scanner-watchlist", {"ticker": "USO"}, headers=RELAYED)
    assert status == 401
    assert mag7.state.mag7_scanner_watchlist == MAG7


def test_edit_and_delete_through_the_gateway_answer_json_not_501(mag7, monkeypatch, tmp_path):
    """2026-08-23: Mag7 delete returned the gateway's 501 HTML page."""
    import gateway

    monkeypatch.setattr(gateway, "CHAIN_BLOBS", tmp_path / "no_blobs")
    with running_api() as api:
        handler = type("TestGatewayHandler", (gateway.GatewayHandler,), {"pipeline_base": api.base_url})
        server = gateway.GatewayServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            front = ApiClient(f"http://127.0.0.1:{server.server_address[1]}")
            put_status, _ = front.request("PUT", "/api/mag7-scanner-watchlist/METU", {"ticker": "NVDL"})
            del_status, _ = front.request("DELETE", "/api/mag7-scanner-watchlist/NVDL")
            content_type = front.last_headers["Content-Type"]
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    assert (put_status, del_status, content_type) == (200, 200, "application/json")
    assert mag7.state.mag7_scanner_watchlist == ["AMDL", "AAPL"]
