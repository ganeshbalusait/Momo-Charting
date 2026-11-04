"""Integration: the Auto Alert page (automatic OI-ladder alerts) and its
dated history, through the real /api/oi-auto-alerts routes over HTTP and the
real DashboardState alert methods behind them.

The state is tests/test_oi_auto_alert_state.py's make_state: the real alert
code on a fake settings repository, with the chain read faked per ticker, so a
ladder build never reaches Schwab and nothing is saved to a real database. The
serving-process flag is switched on so the persistence path actually runs
(against the fake repository).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from api_harness import api_server, running_api
from test_oi_auto_alert_state import ET, FakeRepository, chain_rows, make_state, on_time_build

MAG7 = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA"]


@pytest.fixture
def alerts(monkeypatch):
    monkeypatch.setattr(api_server, "_IS_SERVING_PROCESS", True)
    repository = FakeRepository()
    state = make_state(repository)
    state.repository_for_test = repository
    return state


def confirm(day: str, symbol: str, side: str, strike: float, hour: int = 10) -> dict:
    return {
        "id": f"{symbol}:{side}:{day}:{hour}", "symbol": symbol, "side": side, "kind": "confirm",
        "level": {"strike": strike}, "crossedLevels": [{"strike": strike}], "price": strike + 1.0,
        "at": f"{day}T{hour:02d}:00:00-05:00",
    }


def test_the_page_payload_has_everything_the_drawer_draws(alerts):
    with running_api(state=alerts) as api:
        status, body = api.get("/api/oi-auto-alerts")
    assert status == 200, body
    for key in ("enabled", "includeMag7", "manualSymbols", "maxManualSymbols", "rows", "pendingSymbols",
                "events", "sessionAlerts", "serverTime", "status", "message", "session", "rules"):
        assert key in body, key
    assert (body["enabled"], body["includeMag7"], body["manualSymbols"]) == (True, True, [])
    assert body["pendingSymbols"] == MAG7 and body["rows"] == []      # "Building OI ladder..." cards
    assert body["message"] == "Waiting for the 9:15 AM ET OI-ladder build."
    assert body["rules"]["refreshTimeEt"] == "09:15"


def test_adding_a_ticker_queues_its_ladder_and_saves_the_list(alerts):
    with running_api(state=alerts) as api:
        status, body = api.post("/api/oi-auto-alerts/symbols", {"add": "amd"})
    assert status == 200, body
    assert body["manualSymbols"] == ["AMD"] and "AMD" in body["pendingSymbols"]
    assert alerts.oi_auto_alert_refresh_requests == [(["AMD"], "added")]
    assert alerts.payload_calls == [], "the ladder is built by the worker, never on the request"
    saved = alerts.repository_for_test.settings
    assert json.loads(saved["oi_auto_alert_manual_symbols"]) == ["AMD"]


def test_bad_adds_are_refused_with_the_reason(alerts):
    with running_api(state=alerts) as api:
        bad = api.post("/api/oi-auto-alerts/symbols", {"add": "1bad"})
        empty = api.post("/api/oi-auto-alerts/symbols", {})
        mag7 = api.post("/api/oi-auto-alerts/symbols", {"add": "TSLA"})   # MAG7 already watches it
        alerts.oi_auto_alert_manual_symbols = [f"T{index:02d}" for index in range(30)]
        full = api.post("/api/oi-auto-alerts/symbols", {"add": "AMD"})
    assert bad == (400, {"error": "Enter a valid ticker symbol (letters, digits, . or -)."})
    assert empty == (400, {"error": "Provide a symbol to add or remove."})
    assert mag7[0] == 200 and mag7[1]["manualSymbols"] == []
    assert full == (400, {"error": "Manual list is full (30 tickers). Remove one first."})


def test_removing_one_ticker_keeps_the_others(alerts):
    with running_api(state=alerts) as api:
        api.post("/api/oi-auto-alerts/symbols", {"add": "AMD"})
        api.post("/api/oi-auto-alerts/symbols", {"add": "NFLX"})
        status, body = api.post("/api/oi-auto-alerts/symbols", {"remove": "amd"})
    assert status == 200
    assert body["manualSymbols"] == ["NFLX"]
    assert json.loads(alerts.repository_for_test.settings["oi_auto_alert_manual_symbols"]) == ["NFLX"]


def test_pausing_and_dropping_mag7(alerts):
    alerts.oi_auto_alert_rows["AAPL"] = {"symbol": "AAPL", "status": "armed", "callLevels": [], "putLevels": []}
    with running_api(state=alerts) as api:
        _, paused = api.post("/api/oi-auto-alerts/settings", {"enabled": False})
        _, no_mag7 = api.post("/api/oi-auto-alerts/settings", {"includeMag7": False})
    assert paused["enabled"] is False and paused["status"] == "Paused"
    assert paused["message"] == "Automatic OI alerts are paused."
    assert no_mag7["includeMag7"] is False
    assert no_mag7["pendingSymbols"] == [] and "AAPL" not in alerts.oi_auto_alert_rows
    saved = alerts.repository_for_test.settings
    assert (saved["oi_auto_alert_enabled"], saved["oi_auto_alert_include_mag7"]) == ("false", "false")


def test_a_rebuild_is_queued_not_run_on_the_request(alerts):
    with running_api(state=alerts) as api:
        status, body = api.post("/api/oi-auto-alerts/refresh", {"symbol": "aapl"})
        _, everything = api.post("/api/oi-auto-alerts/refresh", {})
    assert status == 200 and body["refreshRequested"] is True and everything["refreshRequested"] is True
    assert alerts.oi_auto_alert_refresh_requests == [(["AAPL"], "manual"), (None, "manual")]
    assert alerts.payload_calls == []


def test_a_morning_ladder_build_shows_as_an_armed_card_and_re_arm_gives_a_side_back(alerts):
    alerts.update_oi_auto_alert_settings({"includeMag7": False, "manualSymbols": ["TSLA"]})
    alerts.oi_auto_alert_refresh_requests = []
    alerts.payloads["TSLA"] = {
        "live": True, "symbol": "TSLA", "source": "Schwab", "scannedAt": on_time_build().isoformat(),
        "underlyingPrice": 341.63, "selectedExpiryChainRows": chain_rows(),
    }
    alerts._oi_auto_alert_refresh_levels(None, reason="morning", now=on_time_build())
    alerts.oi_auto_alert_rows["TSLA"]["callFiredAt"] = datetime.now(ET).isoformat()   # its CALL alert went
    with running_api(state=alerts) as api:
        _, before = api.get("/api/oi-auto-alerts")
        status, after = api.post("/api/oi-auto-alerts/rearm", {"symbol": "tsla"})
        missing = api.post("/api/oi-auto-alerts/rearm", {"symbol": "ZZZZ"})
        nothing = api.post("/api/oi-auto-alerts/rearm", {})
    row = before["rows"][0]
    assert row["symbol"] == "TSLA" and row["sourceGroup"] == "manual"
    assert [level["strike"] for level in row["callLevels"]] == [345, 350, 360]
    assert [level["strike"] for level in row["putLevels"]] == [340, 330]
    assert row["callState"] == "fired" and row["rearmAvailable"] is True
    assert status == 200 and after["rows"][0]["callState"] != "fired"
    assert missing == (404, {"error": "ZZZZ is not being watched."})
    assert nothing == (400, {"error": "Provide a symbol to re-arm, or all: true."})


# --------------------------------------------------------------------------- dated history

def test_history_route_serves_one_session_with_rewritten_messages(alerts):
    today = datetime.now(ET).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    alerts._oi_auto_alert_record_events([
        confirm(yesterday, "TSLA", "CALL", 335),
        confirm(yesterday, "AMD", "PUT", 485, hour=11),
        confirm(today.isoformat(), "AAPL", "CALL", 307.5),
        {"id": "touch-1", "symbol": "META", "side": "CALL", "kind": "touch", "level": {"strike": 565},
         "price": 565.2, "at": f"{yesterday}T10:30:00-05:00"},
    ])
    with running_api(state=alerts) as api:
        status, history = api.get(f"/api/oi-auto-alerts/history?date={yesterday}")
        bad_status, fallback = api.get("/api/oi-auto-alerts/history?date=not-a-date")
        _, payload = api.get("/api/oi-auto-alerts")
    assert status == 200, history
    assert (history["date"], history["alertCount"], history["symbols"]) == (yesterday, 2, ["AMD", "TSLA"])
    assert [event["symbol"] for event in history["events"]] == ["AMD", "TSLA"]      # newest first
    assert all(event["message"] and event["symbol"] in event["message"] for event in history["events"])
    assert history["availableDates"] == [today.isoformat(), yesterday]
    assert (bad_status, fallback["date"]) == (200, today.isoformat())               # never a 500
    days = payload["sessionAlerts"]["days"]
    assert [day["date"] for day in days] == [today.isoformat(), yesterday]           # newest first
    assert (days[1]["symbolCount"], days[1]["alertCount"]) == (2, 2)                  # the touch is not an alert
