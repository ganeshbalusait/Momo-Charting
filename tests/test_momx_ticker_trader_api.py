"""Integration: the Ticker trader routes (2026-11-13) through the real api_server
proxy to the real worker handler and the real engine. Quotes and the saved chain
are fakes; the state file lives in a scratch folder. Owner only."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from api_harness import running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import ticker_trader
from test_momx_ticker_trader import CALL, chain
from worker_harness import running_worker

OWNER = "owner-ticker-trader@example.com"
VIEWER = "viewer-ticker-trader@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
USER = {ACCESS_EMAIL_HEADER: VIEWER}
ROUTE = "/api/momx-scanner/ticker-trader"


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture
def trader(tmp_path, monkeypatch):
    clock = {"now": datetime(2026, 11, 13, 10, 0, 0, tzinfo=ticker_trader.ET)}
    quotes = {"NVDA": {"last": 100.0}, CALL: {"bid": 1.0, "ask": 1.1}}

    def fetch(symbols):
        at = clock["now"].timestamp() - 1
        return {"asOf": at, "quotes": {s: {**quotes[s], "quoteAt": at} for s in symbols if s in quotes}}

    def chain_of(symbol):
        if symbol != "NVDA":
            raise FileNotFoundError(symbol)
        return chain()

    made = ticker_trader.TickerTrader(tmp_path / "momx_ticker_trader", quotes_fetch=fetch, chain_fetch=chain_of,
                                      clock=lambda: clock["now"])
    monkeypatch.setattr(ticker_trader, "_TRADERS", [made])
    return made


def test_owner_runs_a_ticker_through_the_proxy(tmp_path, monkeypatch, trader):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            empty_status, empty = api.get(ROUTE, headers=ADMIN)
            status, started = api.post(ROUTE + "/start", {"ticker": "nvda", "side": "call"}, headers=ADMIN)
            trader.tick()
            trader.tick()
            _, after = api.get(ROUTE, headers=ADMIN)
            stop_status, stopping = api.post(ROUTE + "/stop", {"id": "NVDA-CALL"}, headers=ADMIN)
            trader.tick()                                     # Stop answers at once; the next tick sells
            _, sold = api.get(ROUTE, headers=ADMIN)
            stopped = {"run": sold["runs"][0]}
            again_status, again = api.post(ROUTE + "/again", {"id": "NVDA-CALL"}, headers=ADMIN)
            reset_status, reset = api.post(ROUTE + "/reset", {"id": "NVDA-CALL"}, headers=ADMIN)
    assert empty_status == 200 and empty["runs"] == [] and empty["simulated"] is True
    assert status == 200 and started["ok"] is True and started["run"]["id"] == "NVDA-CALL" and started["run"]["status"] == "waiting"
    run = after["runs"][0]
    assert run["status"] == "open" and run["open"]["contract"] == CALL and run["open"]["entry"] == 1.1 and run["open"]["qty"] == 3
    assert stopping["run"]["stopping"] is True and stopping["run"]["open"]["held"] == 3
    assert stop_status == 200 and stopped["run"]["status"] == "stopped" and stopped["run"]["trades"][0]["pl"] == -30.0
    assert again_status == 200 and again["run"]["status"] == "waiting"
    assert reset_status == 200 and reset["run"]["balance"] == 10000.0 and reset["run"]["trades"] == []
    saved = json.loads((tmp_path / "momx_ticker_trader" / "state.json").read_text(encoding="utf-8"))
    assert list(saved["runs"]) == ["NVDA-CALL"]


def test_a_regular_user_is_refused_everywhere(tmp_path, monkeypatch, trader):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            get_status, _ = api.get(ROUTE, headers=USER)
            posts = [api.post(ROUTE + "/" + action, {"ticker": "NVDA", "side": "CALL", "id": "NVDA-CALL"}, headers=USER)[0]
                     for action in ("start", "stop", "reset", "again")]
        # The worker refuses by itself too (no admin header reaches it).
        worker_get, _ = worker.get(ROUTE)
        worker_post, _ = worker.post(ROUTE + "/start", {"ticker": "NVDA", "side": "CALL"})
    assert get_status == 403 and posts == [403, 403, 403, 403]
    assert worker_get == 403 and worker_post == 403
    assert trader.snapshot()["runs"] == []


def test_bad_requests_are_refused_in_plain_words(tmp_path, monkeypatch, trader):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            bad_ticker = api.post(ROUTE + "/start", {"ticker": "NV1DA", "side": "CALL"}, headers=ADMIN)
            no_chain = api.post(ROUTE + "/start", {"ticker": "ZZZZ", "side": "CALL"}, headers=ADMIN)
            bad_side = api.post(ROUTE + "/start", {"ticker": "NVDA", "side": "STRADDLE"}, headers=ADMIN)
            unknown = api.post(ROUTE + "/stop", {"id": "AAPL-CALL"}, headers=ADMIN)
            not_action = api.post(ROUTE + "/buy", {"id": "NVDA-CALL"}, headers=ADMIN)
    assert bad_ticker[0] == 400 and "Type a ticker" in bad_ticker[1]["error"]
    assert no_chain[0] == 400 and "No saved option chain for ZZZZ" in no_chain[1]["error"]
    assert bad_side[0] == 400 and "Pick Call or Put" in bad_side[1]["error"]
    assert unknown[0] == 404 and not_action[0] == 404
    assert trader.snapshot()["runs"] == []


class _SchwabClient:
    def __init__(self, data):
        self.data = data

    def get_quotes(self, symbols):
        return {s: self.data[s] for s in symbols if s in self.data}


def test_the_quote_reply_carries_each_quotes_own_time_and_keeps_its_old_fields(monkeypatch):
    """/api/option-quotes: quoteAt is Schwab's quote time (seconds), None when it sent
    none. bid / ask / mid / last and asOf are exactly what the paper bots already read."""
    import api_server

    client = _SchwabClient({CALL: {"bid": 1.0, "ask": 1.2, "last_price": 1.15, "quote_time_ms": 1_762_272_000_500},
                            "NVDA": {"bid": 99.9, "ask": 100.1, "last_price": 100.0},
                            "DEAD": {"bid": 0.0, "ask": 0.0, "quote_time_ms": 1_762_272_000_000}})
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [client])
    state = api_server.DashboardState.__new__(api_server.DashboardState)
    before = datetime.now().timestamp()
    doc = state.option_quotes([CALL, "NVDA", "DEAD"])
    assert before - 1 <= doc["asOf"] <= before + 5 and set(doc) == {"asOf", "quotes"}
    assert doc["quotes"][CALL] == {"bid": 1.0, "ask": 1.2, "mid": 1.1, "last": 1.15, "quoteAt": 1_762_272_000.5}
    assert doc["quotes"]["NVDA"] == {"bid": 99.9, "ask": 100.1, "mid": 100.0, "last": 100.0, "quoteAt": None}
    assert "DEAD" not in doc["quotes"]


def test_a_reply_stamped_now_around_an_old_quote_is_not_traded(tmp_path, monkeypatch):
    """The real route's reply into the real engine: asOf is always "now", so only
    quoteAt can tell the engine the ask is yesterday's."""
    import api_server

    now = datetime.now(ticker_trader.ET).replace(hour=10, minute=0, second=0, microsecond=0)
    while now.weekday() >= 5:
        now = now.replace(day=now.day - 1)
    old_ms = (now.timestamp() - 18 * 3600) * 1000.0
    data = {CALL: {"bid": 1.0, "ask": 1.1, "quote_time_ms": old_ms},
            "NVDA": {"bid": 99.9, "ask": 100.1, "last_price": 100.0, "quote_time_ms": now.timestamp() * 1000.0}}
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [_SchwabClient(data)])
    monkeypatch.setattr(api_server.time, "time", lambda: now.timestamp())
    state = api_server.DashboardState.__new__(api_server.DashboardState)
    made = ticker_trader.TickerTrader(tmp_path / "tt", quotes_fetch=state.option_quotes,
                                      chain_fetch=lambda symbol: chain(), clock=lambda: now)
    made.start("NVDA", "CALL")
    for _ in range(3):
        made.tick()
    run = made.snapshot()["runs"][0]
    assert run["open"] is None and run["note"] == "Waiting for today's first quote on the 104 Call"
    data[CALL]["quote_time_ms"] = (now.timestamp() - 3) * 1000.0
    made.tick()
    assert made.snapshot()["runs"][0]["open"]["entry"] == 1.1


def test_the_account_size_passes_through_the_proxy_and_bad_sizes_are_refused(tmp_path, monkeypatch, trader):
    """His size buttons (2026-11-13): start {ticker, side, size} and reset {id, size}."""
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            too_small = api.post(ROUTE + "/start", {"ticker": "NVDA", "side": "CALL", "size": 100}, headers=ADMIN)
            not_number = api.post(ROUTE + "/start", {"ticker": "NVDA", "side": "CALL", "size": "lots"}, headers=ADMIN)
            started = api.post(ROUTE + "/start", {"ticker": "NVDA", "side": "CALL", "size": 5000}, headers=ADMIN)
            _, listed = api.get(ROUTE, headers=ADMIN)
            too_big = api.post(ROUTE + "/reset", {"id": "NVDA-CALL", "size": 2_000_000}, headers=ADMIN)
            reset = api.post(ROUTE + "/reset", {"id": "NVDA-CALL", "size": 100000}, headers=ADMIN)
    assert too_small[0] == 400 and "between $500 and $1,000,000" in too_small[1]["error"]
    assert not_number[0] == 400 and "Account size" in not_number[1]["error"]
    assert started[0] == 200 and started[1]["run"]["startBalance"] == 5000.0 and started[1]["run"]["balance"] == 5000.0
    assert listed["runs"][0]["startBalance"] == 5000.0 and listed["runs"][0]["net"] == 0
    assert too_big[0] == 400 and "between $500 and $1,000,000" in too_big[1]["error"]
    assert reset[0] == 200 and reset[1]["run"]["startBalance"] == 100000.0 and reset[1]["run"]["balance"] == 100000.0
    assert reset[1]["run"]["trades"] == [] and "fresh $100,000" in reset[1]["run"]["note"]
