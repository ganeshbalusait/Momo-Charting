"""Integration: the Bot accounts routes (2026-11-26) through the real api_server proxy to the
real worker handler and the real accounts (momx/bot_accounts.py), with the real Ticker trader
engine behind the Ticker account. Quotes and the saved chain are fakes; the paper day files,
the Research day files, the runs file and the Ticker trader's state live in a scratch folder.
No broker is involved anywhere. Owner only."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from api_harness import api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import bot_accounts, bot_desk, paper_mirror, ticker_trader
from test_momx_ticker_trader import CALL, chain
from worker_harness import running_worker

OWNER = "owner-bot-accounts@example.com"
VIEWER = "viewer-bot-accounts@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
USER = {ACCESS_EMAIL_HEADER: VIEWER}
ROUTE = "/api/momx-scanner/bot-accounts"
DAY = "2026-11-20"
NOW = datetime(2026, 11, 20, 11, 0, 0, tzinfo=bot_accounts.ET)
ACCOUNT_KEYS = {"key", "label", "kind", "note", "run", "value", "free", "invested", "dayPnl", "openPnl", "totalPnl",
                "positions", "orders", "journal", "skippedNoMoney", "pastRuns", "resets", "canTradeTicker", "error"}


def stamp(hour, minute=0):
    return f"{DAY}T{hour:02d}:{minute:02d}:00-05:00"


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """The worker's accounts over scratch files, with a real (simulated) Ticker trader."""
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-11-13")
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK", {})
    paper, grade = tmp_path / "momx_paper_mirror", tmp_path / "grade"
    (paper / "paper3").mkdir(parents=True)
    (paper / "paper3" / f"{DAY}.json").write_text(json.dumps({"day": DAY, "rows": {
        "bull|level1|AAPL|a": {"side": "bull", "rule": "level1", "symbol": "AAPL", "at": stamp(10), "contract": "AAPL  261127C00150000",
                               "qty": 3, "buyLimit": 1.0, "buyFill": 1.0, "buyAt": stamp(10), "status": "closed", "pl": -90.0,
                               "closedAt": stamp(10, 30), "exitPlan": paper_mirror.EXIT_PLAN},
        "bull|level2|MSFT|b": {"side": "bull", "rule": "level2", "symbol": "MSFT", "at": stamp(10, 40), "contract": "MSFT  261127C00400000",
                               "qty": 3, "held": 3, "buyLimit": 2.0, "buyFill": 2.0, "buyAt": stamp(10, 40), "status": "open",
                               "markMid": 2.5, "exitPlan": paper_mirror.EXIT_PLAN}}}), encoding="utf-8")
    (grade / "momx_option_track").mkdir(parents=True)
    (grade / "momx_option_track" / f"{DAY}.json").write_text(json.dumps({"trades": [
        {"rule": "level1", "symbol": "NVDA", "at": stamp(10), "entryAt": stamp(10), "status": "closed", "eContract": "NVDA  261127C00150000",
         "eEntry": 1.0, "eExit": 1.5, "exitAt": stamp(10, 20), "eExitPriceAt": stamp(10, 20)},
        # The same signal saved again on a higher level, and a rule that is not a Level: neither adds to the account.
        {"rule": "level2", "symbol": "NVDA", "at": stamp(10), "entryAt": stamp(10), "status": "closed", "eContract": "NVDA  261127C00150000",
         "eEntry": 1.0, "eExit": 1.5, "exitAt": stamp(10, 20), "eExitPriceAt": stamp(10, 20)},
        {"rule": "go_macd", "symbol": "TSLA", "at": stamp(10, 5), "entryAt": stamp(10, 5), "status": "closed", "eContract": "TSLA  261127C00300000",
         "eEntry": 9.0, "eExit": 1.0, "exitAt": stamp(10, 30), "eExitPriceAt": stamp(10, 30)}]}), encoding="utf-8")
    clock = {"now": NOW}
    quotes = {"NVDA": {"last": 100.0}, CALL: {"bid": 1.0, "ask": 1.1}}

    def fetch(symbols):
        at = clock["now"].timestamp() - 1
        return {"asOf": at, "quotes": {s: {**quotes[s], "quoteAt": at} for s in symbols if s in quotes}}

    trader = ticker_trader.TickerTrader(tmp_path / "momx_ticker_trader", quotes_fetch=fetch, chain_fetch=lambda symbol: chain(),
                                        clock=lambda: clock["now"])
    monkeypatch.setattr(ticker_trader, "_TRADERS", [trader])
    made = bot_accounts.BotAccounts(tmp_path / "momx_bot_accounts", lambda account: paper / account,
                                    lambda: [("bull", grade), ("bear", grade / "bear")], ticker_trader.current,
                                    clock=lambda: clock["now"])
    monkeypatch.setattr(bot_accounts, "_BOOKS", [made])
    made.trader, made.runs_file = trader, tmp_path / "momx_bot_accounts" / bot_accounts.RUNS_FILE
    return made


def test_owner_reads_all_four_accounts_through_the_proxy(tmp_path, monkeypatch, desk):
    desk.trader.start("NVDA", "CALL")
    for _ in range(3):
        desk.trader.tick()                                                    # picks, buys 3 at the $1.10 ask, records the $1.00 bid
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            status, body = api.get(ROUTE, headers=ADMIN)
    assert status == 200 and body["ok"] is True and body["asOf"] == "2026-11-20T11:00:00-05:00"
    assert body["order"] == ["paper3", "paper5", "research", "ticker"] and list(body["accounts"]) == body["order"]
    a = body["accounts"]
    assert all(ACCOUNT_KEYS <= set(v) and v["key"] == k and v["error"] is None for k, v in a.items())
    assert [a[k]["label"] for k in body["order"]] == ["Paper 3", "Paper 5", "Research", "Ticker"]
    assert [a[k]["kind"] for k in body["order"]] == ["paper", "paper", "virtual", "simulated"]
    # Paper 3: -$90 closed, an open trade at +$150 that cost $600.
    p3 = a["paper3"]
    assert p3["run"] == {"n": 1, "money": 10000.0, "perTrade": 300.0, "startedAt": "2026-11-13T00:00:00-05:00", "capped": False}
    assert (p3["value"], p3["totalPnl"], p3["openPnl"], p3["invested"], p3["free"], p3["dayPnl"]) == (10060.0, 60.0, 150.0, 600.0, 9310.0, 60.0)
    assert p3["positions"] == [{"symbol": "MSFT", "contract": "400C · Nov 27", "qty": 3, "entry": 2.0, "mark": 2.5, "pnl": 150.0,
                                "pct": 25.0, "at": stamp(10, 40)}]
    assert [(o["symbol"], o["status"]) for o in p3["orders"]] == [("MSFT", "Open"), ("AAPL", "Closed")]
    assert p3["journal"]["tiles"]["netPnl"] == -90.0 and p3["journal"]["days"] == [{"day": DAY, "pnl": -90.0, "trades": 1}]
    assert a["paper5"]["value"] == 10000.0 and a["paper5"]["orders"] == []
    assert a["research"]["totalPnl"] == 50.0 and a["research"]["run"]["capped"] is False and a["research"]["run"]["perTrade"] is None
    # Level signals only, one signal once: the level2 copy and the go_macd trade are left out.
    assert a["research"]["journal"]["tiles"]["closed"] == 1 and "Level" in a["research"]["note"]
    # Ticker: the engine's own account - 3 bought at $1.10, bid $1.00.
    assert a["ticker"]["canTradeTicker"] is True and a["ticker"]["openPnl"] == -30.0 and a["ticker"]["invested"] == 330.0
    assert a["ticker"]["value"] == desk.trader.account()["value"] == 9970.0 and a["ticker"]["free"] == desk.trader.account()["cash"]
    assert not desk.runs_file.exists()


def test_owner_resets_an_account_and_changes_its_money_per_trade(tmp_path, monkeypatch, desk):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            api_server._MOMX_BOT_DESK[30] = (0.0, {"copy": "from before the reset"})
            status, reset = api.post(ROUTE + "/reset", {"account": "paper3", "money": 5000, "perTrade": 250}, headers=ADMIN)
            cleared = dict(api_server._MOMX_BOT_DESK)
            money_status, changed = api.post(ROUTE + "/money", {"account": "paper3", "perTrade": 400}, headers=ADMIN)
            _, after = api.get(ROUTE, headers=ADMIN)
    assert status == 200 and reset["ok"] is True and ACCOUNT_KEYS <= set(reset["account"])
    assert reset["account"]["run"] == {"n": 2, "money": 5000.0, "perTrade": 250.0, "startedAt": "2026-11-20T11:00:00-05:00", "capped": True}
    assert cleared == {}                                                      # the Bot desk's saved copy is not served after a write
    assert money_status == 200 and changed["account"]["run"]["perTrade"] == 400.0
    p3 = after["accounts"]["paper3"]
    assert p3["run"]["n"] == 2 and p3["run"]["perTrade"] == 400.0 and p3["resets"] == 1
    # The trade closed before the reset stays in run 1; the open one is still held and counts in run 2.
    assert p3["journal"]["tiles"]["closed"] == 0 and p3["value"] == 5150.0 and p3["free"] == 4400.0
    past = p3["pastRuns"][0]
    assert (past["n"], past["money"], past["endValue"], past["pnl"], past["pnlPct"], past["trades"]) == (1, 10000.0, 10060.0, 60.0, 0.6, 1)
    assert past["endedAt"] == "2026-11-20T11:00:00-05:00" and past["days"] == [{"day": DAY, "pnl": -90.0, "trades": 1}]
    assert past["tiles"]["losses"] == 1
    assert after["accounts"]["paper5"]["resets"] == 0 and after["accounts"]["research"]["resets"] == 0
    saved = json.loads(desk.runs_file.read_text(encoding="utf-8"))
    assert saved["accounts"]["paper3"]["run"]["perTrade"] == 400.0 and [e["action"] for e in saved["log"]] == ["reset", "perTrade"]
    assert {e["who"] for e in saved["log"]} == {OWNER}
    # The live paper bot's read: this account is capped now, the other is not.
    assert desk.paper_cap("paper3", tmp_path / "momx_paper_mirror" / "paper3", DAY).per_trade == 400.0
    assert desk.paper_cap("paper5", tmp_path / "momx_paper_mirror" / "paper5", DAY) is None


def test_resetting_the_ticker_account_resets_the_real_ticker_trader(tmp_path, monkeypatch, desk):
    desk.trader.start("NVDA", "CALL")
    for _ in range(3):
        desk.trader.tick()
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            status, reset = api.post(ROUTE + "/reset", {"account": "ticker", "money": 2000}, headers=ADMIN)
            money_status, refused = api.post(ROUTE + "/money", {"account": "ticker", "perTrade": 100}, headers=ADMIN)
    assert status == 200 and reset["account"]["run"]["n"] == 2 and reset["account"]["run"]["money"] == 2000.0
    assert reset["account"]["value"] == 2000.0 and reset["account"]["positions"] == []
    assert reset["account"]["pastRuns"][0]["endValue"] == 9970.0 and reset["account"]["pastRuns"][0]["openAtEnd"] == 1
    assert desk.trader.account()["startBalance"] == 2000.0 and desk.trader.snapshot()["runs"][0]["open"] is None
    assert money_status == 409 and refused["ok"] is False and "sizes each trade itself" in refused["error"]


def test_a_regular_user_is_refused_everywhere(tmp_path, monkeypatch, desk):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            get_status, _ = api.get(ROUTE, headers=USER)
            reset_status, _ = api.post(ROUTE + "/reset", {"account": "paper3", "money": 5000}, headers=USER)
            money_status, _ = api.post(ROUTE + "/money", {"account": "paper3", "perTrade": 100}, headers=USER)
        # The worker refuses by itself too (no admin header reaches it).
        worker_get, _ = worker.get(ROUTE)
        worker_reset, _ = worker.post(ROUTE + "/reset", {"account": "paper3", "money": 5000}, headers={"X-AGX-User": VIEWER})
        worker_money, _ = worker.post(ROUTE + "/money", {"account": "paper3", "perTrade": 100})
    assert (get_status, reset_status, money_status) == (403, 403, 403)
    assert (worker_get, worker_reset, worker_money) == (403, 403, 403)
    assert not desk.runs_file.exists()


def test_bad_requests_are_refused_in_plain_words_and_change_nothing(tmp_path, monkeypatch, desk):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            unknown = api.post(ROUTE + "/reset", {"account": "paper9", "money": 5000}, headers=ADMIN)
            small = api.post(ROUTE + "/reset", {"account": "paper3", "money": 100}, headers=ADMIN)
            words = api.post(ROUTE + "/reset", {"account": "paper3", "money": "a lot"}, headers=ADMIN)
            share = api.post(ROUTE + "/reset", {"account": "paper3", "money": 5000, "perTrade": 6000}, headers=ADMIN)
            early = api.post(ROUTE + "/money", {"account": "paper3", "perTrade": 200}, headers=ADMIN)
        listed = worker.post(ROUTE + "/reset", ["paper3"], headers={"X-AGX-User": OWNER})
    assert unknown[0] == 400 and unknown[1] == {"ok": False, "error": "Pick one of the four accounts: Paper 3, Paper 5, Research or Ticker."}
    assert small[0] == 400 and "between $500 and $1,000,000" in small[1]["error"]
    assert words[0] == 400 and "must be a number of dollars" in words[1]["error"]
    assert share[0] == 400 and "between $50 and the account's $5,000" in share[1]["error"]
    assert early[0] == 409 and "has not been reset yet" in early[1]["error"]
    assert listed[0] == 400 and listed[1]["ok"] is False                      # not a 500 with a Python error
    assert not desk.runs_file.exists()


def test_the_routes_say_so_when_the_accounts_are_not_running(tmp_path, monkeypatch, desk):
    monkeypatch.setattr(bot_accounts, "_BOOKS", [])
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        get_status, got = worker.get(ROUTE, headers={"X-AGX-User": OWNER})
        post_status, posted = worker.post(ROUTE + "/reset", {"account": "paper3", "money": 5000}, headers={"X-AGX-User": OWNER})
    assert get_status == 503 and post_status == 503
    assert got["error"] == posted["error"] == "The Bot accounts are not running yet."
