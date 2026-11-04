"""Integration: only the owner (an admin account) may touch the trading routes.

Every route here places, closes or cancels orders, starts a bot, changes sizing
or switches the broker profile - or reads the owner's Bot / paper books. Each is
driven over real HTTP through api_server (and the scanner worker) with the
trading state replaced by a spy, so a refused request is proven to have done
NOTHING, and an allowed one is proven to reach exactly the method behind it.

Who is asking is real, not patched: a scratch account is created and the
request carries the verified-identity header the gateway stamps
(X-AGX-Access-Email), the same path a Cloudflare visitor takes.
"""
from __future__ import annotations

import pytest

import momx_worker
from api_harness import RELAYED, api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from worker_harness import running_worker

OWNER = "owner-cov-trading@example.com"
VIEWER = "viewer-cov-trading@example.com"


def as_user(email: str) -> dict:
    return {ACCESS_EMAIL_HEADER: email}


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    # Module scope: creating an account hashes a password (~1 s each).
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


class SpyState:
    """Stands in for api_server.STATE: records every method the route calls."""

    def __init__(self, dashboard: dict | None = None):
        self.calls: list[tuple] = []
        self._dashboard = dashboard or {"ok": True}

    def dashboard_payload(self):
        return dict(self._dashboard)

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)

        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return {"result": {"status": "spy"}, "dashboard": {}}

        return record


# (route, body, the STATE method it reaches)
TRADING_POSTS = [
    ("/api/close-position", {"symbol": "AAPL"}, "close_position"),
    ("/api/close-all-positions", {}, "close_all_positions"),
    ("/api/close-option-position", {"optionSymbol": "NVDA261120C00200000"}, "close_option_position"),
    ("/api/close-all-option-positions", {}, "close_all_option_positions"),
    ("/api/manage-option-positions", {}, "manage_option_positions_now"),
    ("/api/cancel-stale-option-buy-orders", {}, "cancel_stale_option_buy_orders"),
    ("/api/option-paper-trades", {"underlyingSymbol": "NVDA", "optionSymbol": "NVDA CALL | Next Weekly | ATM"}, "log_option_paper_trade"),
    ("/api/option-bot-control", {"state": "Running"}, "set_option_bot_state"),
    ("/api/bot-control", {"state": "Running"}, "set_bot_state"),
    ("/api/execute-best-trade", {}, "execute_best_trade"),
    ("/api/execute-all-trades", {}, "execute_all_trades"),
    ("/api/risk-settings", {"tradeAmount": 500}, "update_risk_settings"),
    ("/api/option-risk-settings", {"dailyTradeAmount": "500"}, "update_option_risk_settings"),
    ("/api/option-bot-settings", {"watchlistSource": "momx"}, "update_option_bot_config"),
    ("/api/account-select", {"profileId": "paper3"}, "select_account"),
]
UNGATED = {"/api/manage-option-positions", "/api/cancel-stale-option-buy-orders"}


GATED_POSTS = [case for case in TRADING_POSTS if case[0] not in UNGATED]


def _post_all(cases, headers, dashboard=None):
    """One server for every route (each server start seeds a hashed login)."""
    outcomes = []
    with running_api(state=SpyState(dashboard)) as api:
        for route, body, method in cases:
            spy = api_server.STATE
            spy.calls.clear()
            status, reply = api.post(route, body, headers=headers)
            outcomes.append((route, method, status, reply, list(spy.calls)))
    return outcomes


def test_a_signed_in_non_admin_is_refused_on_every_gated_trading_post_and_nothing_runs():
    for route, _method, status, reply, calls in _post_all(GATED_POSTS, as_user(VIEWER)):
        assert calls == [], f"{route} ran {calls} for a non-admin"
        assert (status, reply.get("error")) == (403, "Administrator access is required."), route


@pytest.mark.xfail(strict=True, reason=(
    "/api/manage-option-positions and /api/cancel-stale-option-buy-orders have no admin gate in "
    "api_server.do_POST: any signed-in user can make the server act on the owner's Alpaca option orders"))
def test_manage_stops_and_cancel_stale_buys_are_refused_to_a_non_admin():
    ungated = [case for case in TRADING_POSTS if case[0] in UNGATED]
    for route, _method, status, _reply, calls in _post_all(ungated, as_user(VIEWER)):
        assert calls == [], f"{route} ran {calls} for a non-admin"
        assert status == 403, route


def test_a_signed_out_tunnel_visitor_gets_401_on_every_trading_post_and_nothing_runs():
    for route, _method, status, reply, calls in _post_all(TRADING_POSTS, RELAYED):
        assert (status, reply.get("error")) == (401, "Sign in to continue."), route
        assert calls == [], route


def test_the_owner_passes_the_gate_and_each_route_reaches_exactly_its_method():
    for route, method, status, reply, calls in _post_all(TRADING_POSTS, as_user(OWNER)):
        assert status in (200, 201), (route, reply)
        assert [call[0] for call in calls] == [method], route


def test_owner_arguments_are_parsed_before_the_state_is_called():
    spy = SpyState()
    with running_api(state=spy) as api:
        api.post("/api/close-position", {"symbol": " aapl "}, headers=as_user(OWNER))
        api.post("/api/option-bot-control", {"state": "paused"}, headers=as_user(OWNER))
        bad_status, bad = api.post("/api/bot-control", {"state": "Go"}, headers=as_user(OWNER))
        missing_status, missing = api.post("/api/account-select", {}, headers=as_user(OWNER))
    assert spy.calls[0] == ("close_position", ("AAPL",), {})
    assert spy.calls[1] == ("set_option_bot_state", ("Paused",), {})
    assert (bad_status, bad["error"]) == (400, "State must be Running, Paused, or Stopped.")
    assert (missing_status, missing["error"]) == (400, "profileId is required.")
    assert len(spy.calls) == 2


ADMIN_WORKER_READS = [
    "/api/momx-scanner/bot",
    "/api/momx-scanner/paper-mirror",
    "/api/momx-scanner/paper-learning",
    "/api/momx-scanner/ai-report",
]


@pytest.fixture
def quiet_owner_reads(tmp_path, monkeypatch):
    """The owner's reads must not touch the live artifacts/ (AI model, learning team)."""
    from momx import ai_filter, paper_learning, paper_mirror

    monkeypatch.setattr(ai_filter, "_dir", lambda: tmp_path / "momx_ai_filter")
    monkeypatch.setattr(paper_learning, "_TEAM", None)
    monkeypatch.setattr(paper_mirror, "_MIRRORS", [])


def test_the_worker_refuses_its_owner_reads_to_users_and_to_anonymous_callers(tmp_path, monkeypatch, quiet_owner_reads):
    # The real role check: momx_worker.viewer_is_admin reads the scratch user table.
    with running_worker(tmp_path, monkeypatch) as worker:
        for path in ADMIN_WORKER_READS:
            user_status, user_body = worker.get(path, headers={"X-AGX-User": VIEWER})
            bare_status, bare_body = worker.get(path)
            owner_status, owner_body = worker.get(path, headers={"X-AGX-User": OWNER})
            assert (user_status, user_body) == (403, {"error": "admin only"}), path
            assert (bare_status, bare_body) == (403, {"error": "admin only"}), path
            assert owner_status == 200, (path, owner_body)


def test_the_proxy_forwards_the_session_identity_never_the_clients_own_header(tmp_path, monkeypatch, quiet_owner_reads):
    seen: list[str] = []

    def recorder(email, db_path=None):
        seen.append(str(email or ""))
        return str(email or "").lower() == OWNER

    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        monkeypatch.setattr(momx_worker, "viewer_is_admin", recorder)
        forged = {**as_user(VIEWER), "X-AGX-User": OWNER}
        status, body = api.get("/api/momx-scanner/paper-learning", headers=forged)
        owner_status, owner_body = api.get("/api/momx-scanner/paper-learning", headers=as_user(OWNER))
    assert status == 403, body
    assert seen[0] == VIEWER, f"the worker was told {seen[0]!r}, not the signed-in viewer"
    assert owner_status == 200 and owner_body["mode"] == "post_trade"


def test_the_api_server_bot_route_refuses_a_user_before_reading_any_trade(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    with running_api(state=SpyState()) as api:
        status, body = api.get("/api/momx-scanner/bot?days=1", headers=as_user(VIEWER))
    assert (status, body) == (403, {"error": "admin only"})


@pytest.mark.xfail(strict=True, reason=(
    "GET /api/dashboard sends the owner's open positions, option positions and account equity "
    "to every signed-in user; the pages that show them are admin-only"))
def test_a_non_admin_dashboard_read_carries_none_of_the_owners_positions_or_equity():
    owner_book = {
        "openPositions": [{"symbol": "AAPL", "qty": 10, "unrealized_pl": 42.0}],
        "optionPositions": [{"contract": "NVDA261120C00200000", "quantity": 2}],
        "status": {"accountEquity": 25000.0, "buyingPower": 50000.0},
    }
    with running_api(state=SpyState(owner_book)) as api:
        owner_status, owner_view = api.get("/api/dashboard?compact=true", headers=as_user(OWNER))
        status, body = api.get("/api/dashboard?compact=true", headers=as_user(VIEWER))
    assert owner_status == 200 and owner_view["openPositions"][0]["symbol"] == "AAPL"
    assert status == 200
    assert not body.get("openPositions")
    assert not body.get("optionPositions")
    assert (body.get("status") or {}).get("accountEquity") is None
