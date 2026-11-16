"""Integration: GET /api/my-insights and POST /api/my-insights/refresh over HTTP (2026-11-11).

The real api_server routes with the real momx modules behind them. Only Schwab is faked:
no test here can reach Schwab, and the reader's allow-list is proven through the route.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from api_harness import RELAYED, api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import schwab_history as sh

ET = ZoneInfo("America/New_York")
OWNER = "owner-insights@example.com"
VIEWER = "viewer-insights@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
USER = {ACCESS_EMAIL_HEADER: VIEWER}
HASH = "A1B2C3D4E5F6A7B8C9D0"
TODAY = datetime.now(ET).date()
SYMBOL = "SPY   " + (TODAY + timedelta(days=2)).strftime("%y%m%d") + "C00750000"


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_INSIGHTS_DIR", str(tmp_path / "insights"))
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path / "grade"))
    sh._READING.update(on=False, trigger=None, startedAt=None)
    return tmp_path


def utc(clock: str) -> str:
    local = datetime.fromisoformat(f"{TODAY.isoformat()}T{clock}:00").replace(tzinfo=ET)
    return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")


def tx(activity, clock, amount, price):
    return {"activityId": activity, "time": utc(clock), "type": "TRADE", "orderId": activity, "accountNumber": "87654321",
            "transferItems": [{"instrument": {"assetType": "CURRENCY"}, "cost": -0.66, "feeType": "COMMISSION"},
                              {"instrument": {"assetType": "OPTION", "symbol": SYMBOL, "underlyingSymbol": "SPY"},
                               "amount": amount, "price": price, "cost": -amount * price * 100}]}


class FakeSchwab:
    def __init__(self, fail=None):
        self.urls, self.fail = [], fail

    def __call__(self, url):
        self.urls.append(url)
        if self.fail and "accountNumbers" in url:
            raise self.fail
        if "accountNumbers" in url:
            return [{"accountNumber": "87654321", "hashValue": HASH}]
        return [tx("1", "09:45", 2, 1.00), tx("2", "10:05", -2, 1.50)]


def wait_for_read(seconds: float = 10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not sh._READING["on"]:
            return
        time.sleep(0.05)
    raise AssertionError("the read did not finish")


@pytest.fixture
def fake(monkeypatch):
    holder = {"fake": FakeSchwab()}
    monkeypatch.setattr(api_server, "_my_insights_transport", lambda: holder["fake"])
    monkeypatch.setattr(api_server, "_my_insights_enrich", lambda store: None)
    return holder


def test_owner_reads_the_page_and_it_never_calls_schwab(fake):
    with running_api() as api:
        status, body = api.get("/api/my-insights?days=7", headers=ADMIN)
    assert status == 200 and body["available"] is False and body["days"] == 7
    assert body["schwab"]["state"] == "never" and body["summary"]["trades"] == 0
    assert set(body["breakdowns"]) == {"setup", "timeOfDay", "dte", "delta", "callPut", "weekday", "ticker"}
    assert fake["fake"].urls == []


def test_regular_user_is_refused_both_routes(fake):
    with running_api() as api:
        assert api.get("/api/my-insights", headers=USER) == (403, {"error": "admin only"})
        assert api.post("/api/my-insights/refresh", {}, headers=USER) == (403, {"error": "admin only"})
    assert fake["fake"].urls == []


def test_signed_out_is_401(fake):
    with running_api() as api:
        status, _ = api.get("/api/my-insights", headers=RELAYED)
        assert status == 401
        status, _ = api.post("/api/my-insights/refresh", {}, headers=RELAYED)
        assert status == 401
    assert fake["fake"].urls == []


def test_refresh_reads_once_then_waits_an_hour(fake, scratch):
    with running_api() as api:
        status, body = api.post("/api/my-insights/refresh", {}, headers=ADMIN)
        assert status == 202 and body["ok"]
        wait_for_read()
        status, page = api.get("/api/my-insights?days=7", headers=ADMIN)
        again, refused = api.post("/api/my-insights/refresh", {}, headers=ADMIN)
    assert status == 200 and page["available"] and page["schwab"]["state"] == "ok" and page["schwab"]["accounts"] == ["…4321"]
    s = page["summary"]
    assert (s["trades"], s["pnl"], s["winRate"]) == (1, 98.68, 1.0)
    assert page["trades"][0]["underlying"] == "SPY" and page["trades"][0]["quantity"] == 2
    assert again == 429 and "hour" in refused["message"]
    assert len(fake["fake"].urls) == 3              # accountNumbers + two windows, and nothing for the refused click
    stored = (scratch / "insights" / "schwab_fills.json").read_text(encoding="utf-8")
    assert "87654321" not in stored and HASH not in stored


def test_a_401_from_schwab_stops_reading_for_the_day(fake):
    fake["fake"] = FakeSchwab(fail=sh.SchwabRefused("Schwab refused the sign-in (HTTP 401).", "signin", 401))
    with running_api() as api:
        assert api.post("/api/my-insights/refresh", {}, headers=ADMIN)[0] == 202
        wait_for_read()
        _, page = api.get("/api/my-insights", headers=ADMIN)
        status, body = api.post("/api/my-insights/refresh", {}, headers=ADMIN)
    assert page["schwab"]["state"] == "signin_needed" and page["schwab"]["signInNeeded"] and page["schwab"]["canRefresh"] is False
    assert status == 409 and "sign-in" in body["message"].lower()
    assert len(fake["fake"].urls) == 1
    # The daily read is also off for today.
    assert sh.run_daily_if_due(fake["fake"], datetime.now(ET).replace(hour=17)) is None and len(fake["fake"].urls) == 1


def test_the_allow_list_holds_through_the_route(fake, monkeypatch):
    """Even if the read code asked for an order path or a POST, nothing reaches Schwab."""
    def rogue_fetch(reader, start, end):
        for method, path in (("POST", f"/trader/v1/accounts/{HASH}/orders"), ("GET", f"/trader/v1/accounts/{HASH}/orders/1"),
                             ("DELETE", f"/trader/v1/accounts/{HASH}/orders/1")):
            try:
                sh.check_request(method, path)
            except sh.NotAllowed:
                continue
            raise AssertionError((method, path))
        reader.get(f"/trader/v1/accounts/{HASH}/orders/1")     # raises NotAllowed before sending
    monkeypatch.setattr(sh, "fetch", rogue_fetch)
    monkeypatch.setattr(sh.threading, "excepthook", lambda args: None)
    with running_api() as api:
        assert api.post("/api/my-insights/refresh", {}, headers=ADMIN)[0] == 202
        wait_for_read()
    assert fake["fake"].urls == []


def test_the_daily_read_wiring_stops_on_a_real_401(monkeypatch):
    """api_server's real transport (schwab_history.schwab_transport on the trading profile) with
    a fake client whose session answers 401: one GET, then stopped for the day."""
    from data import schwab_rate_limit
    for name in ("acquire", "note_failure", "note_edge_block", "note_rate_limited", "note_success"):
        monkeypatch.setattr(schwab_rate_limit.GATE, name, lambda *a, **k: None)
    calls = []

    class Response:
        status_code, text = 401, '{"error":"invalid_token"}'

    class Session:
        def get(self, url, **kw):
            calls.append(url)
            return Response()

    class Client:
        def _refuse_if_refresh_token_dead(self):
            return None

        def _library_client(self):
            return type("L", (), {"session": Session()})()
    monkeypatch.setattr(api_server, "_schwab_client_for_profile", lambda profile: Client())
    after_close = datetime(2026, 11, 11, 16, 35, tzinfo=ET)
    out = sh.run_daily_if_due(api_server._my_insights_transport(), now=after_close)
    assert out["kind"] == "signin" and len(calls) == 1 and calls[0].endswith("/trader/v1/accounts/accountNumbers")
    assert sh.run_daily_if_due(api_server._my_insights_transport(), now=after_close.replace(hour=18)) is None and len(calls) == 1


def test_no_route_serves_the_saved_trade_files(fake, scratch):
    sh.save_store({"fills": {"x": {"id": "x", "underlying": "SECRETFILL"}}})
    with running_api() as api:
        for path in ("/artifacts/insights/schwab_fills.json", "/insights/schwab_fills.json", "/api/insights/schwab_fills.json",
                     "/schwab_fills.json", "/api/my-insights/../insights/schwab_fills.json"):
            status, body = api.get(path, headers=ADMIN)
            assert "SECRETFILL" not in str(body), path


def test_a_network_error_never_shows_the_account_hash_to_anyone(monkeypatch):
    """The real transport, a real requests Session whose adapter answers accountNumbers and then
    raises the real ConnectionError(MaxRetryError(...)) whose message carries the full URL. After
    that failed read, neither the open GET /api/schwab/status (any signed-in user) nor the owner's
    page carries the account hash or the read's dates. Nothing is sent over the network."""
    import json

    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.exceptions import MaxRetryError, NewConnectionError

    from data import schwab_rate_limit

    class Adapter(HTTPAdapter):
        def send(self, request, **kwargs):
            if "accountNumbers" in request.url:
                response = requests.Response()
                response.status_code, response.url, response.request = 200, request.url, request
                response._content = json.dumps([{"accountNumber": "87654321", "hashValue": HASH}]).encode()
                return response
            path = request.url.split("api.schwabapi.com", 1)[1]
            reason = NewConnectionError(None, "Failed to establish a new connection: [Errno 11001] getaddrinfo failed")
            raise requests.exceptions.ConnectionError(MaxRetryError(None, path, reason), request=request)

    session = requests.Session()
    session.mount("https://", Adapter())

    class Client:
        def _refuse_if_refresh_token_dead(self):
            return None

        def _library_client(self):
            return type("L", (), {"session": session})()
    monkeypatch.setattr(api_server, "_schwab_client_for_profile", lambda profile: Client())
    monkeypatch.setattr(api_server, "_my_insights_enrich", lambda store: None)
    schwab_rate_limit.GATE.reset()
    try:
        with running_api() as api:
            assert api.post("/api/my-insights/refresh", {}, headers=ADMIN)[0] == 202
            wait_for_read()
            assert "ConnectionError" in str(sh.load_state().get("lastError"))      # the read did fail on the network
            status_code, open_status = api.get("/api/schwab/status", headers=USER)
            _, page = api.get("/api/my-insights", headers=ADMIN)
        assert status_code == 200
        for where, body in (("/api/schwab/status as a regular user", open_status), ("/api/my-insights", page)):
            text = json.dumps(body)
            assert HASH not in text, where
            assert "startDate" not in text, where
    finally:
        schwab_rate_limit.GATE.reset()
