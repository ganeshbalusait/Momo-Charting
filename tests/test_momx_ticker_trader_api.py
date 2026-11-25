"""Integration: the Ticker trader routes (2026-11-13) through the real api_server
proxy to the real worker handler and the real engine. Quotes and the saved chain
are fakes; the state file lives in a scratch folder. Owner only."""
from __future__ import annotations

import json
from datetime import datetime, timedelta

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
            trader.tick()                                     # a price update after the buy records the bid
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
    # The list's money next to the ticker (2026-11-18): 3 held, bought at $1.10, bid $1.00 -> -$30 open.
    assert run["openPl"] == -30.0 and run["net"] == 0 and run["liveNet"] == -30.0
    assert stopped["run"]["openPl"] is None and stopped["run"]["liveNet"] == -30.0
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

    # A fixed weekday at 10:00 ET, inside the fixture chain's life (it expires 2026-11-20). This used to be TODAY's
    # date, so from 2026-11-21 on the chain had expired and the run answered "Not bought" before any quote was read.
    # Nothing here needs the real date: the route's own clock (time.time) is pinned to the same moment below.
    now = datetime(2026, 11, 13, 10, 0, 0, tzinfo=ticker_trader.ET)
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


def test_a_contract_over_ten_percent_is_still_bought_once_when_the_account_can_pay(tmp_path, monkeypatch, trader):
    # 2026-11-16: LLY at $440 a contract was skipped on his $2,000 account ("Bot is not taking trade properly").
    trader._quotes_fetch = lambda symbols, at=datetime(2026, 11, 13, 10, 0, 0, tzinfo=ticker_trader.ET).timestamp() - 1: {
        "asOf": at, "quotes": {s: {**q, "quoteAt": at} for s, q in {"NVDA": {"last": 100.0}, CALL: {"bid": 4.3, "ask": 4.4}}.items() if s in symbols}}
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            status, started = api.post(ROUTE + "/start", {"ticker": "nvda", "side": "call", "size": 2000}, headers=ADMIN)
            trader.tick()
            trader.tick()
            _, after = api.get(ROUTE, headers=ADMIN)
    run = after["runs"][0]
    assert status == 200 and started["run"]["startBalance"] == 2000.0
    assert run["status"] == "open" and run["open"]["qty"] == 1 and run["open"]["cost"] == 440.0
    assert "1 not 3: one contract costs more than 10% of the account" in run["open"]["why"]
    assert "Not bought" not in run["note"]


def test_one_account_for_every_ticker_through_the_proxy_and_reset_from_the_list_page(tmp_path, monkeypatch, trader):
    """2026-11-16: the read carries the one account; a buy takes its cash down; Reset with no id resets all of it."""
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            _, before = api.get(ROUTE, headers=ADMIN)
            api.post(ROUTE + "/start", {"ticker": "nvda", "side": "call", "size": 2000}, headers=ADMIN)
            trader.tick()
            trader.tick()
            _, bought = api.get(ROUTE, headers=ADMIN)
            reset_status, reset = api.post(ROUTE + "/reset", {"size": 5000}, headers=ADMIN)
            _, after = api.get(ROUTE, headers=ADMIN)
            bad_status, bad = api.post(ROUTE + "/reset", {"size": 100}, headers=ADMIN)
            user_status, _ = api.post(ROUTE + "/reset", {"size": 5000}, headers=USER)
    assert before["account"]["startBalance"] == 10000.0 and before["account"]["cash"] == 10000.0
    account = bought["account"]
    # 2 x $1.10 = $220 is 10% of $2,000 at most ($200 fits one... 1 contract): the cash is down by what was paid.
    cost = bought["runs"][0]["open"]["cost"]
    assert account["startBalance"] == 2000.0 and account["cash"] == 2000.0 - cost and account["invested"] == cost
    assert bought["runs"][0]["balance"] == account["cash"] and bought["runs"][0]["account"] == account
    assert reset_status == 200 and reset["ok"] is True and reset["run"] is None and reset["account"]["cash"] == 5000.0
    assert after["account"]["startBalance"] == 5000.0 and after["runs"][0]["open"] is None and after["runs"][0]["trades"] == []
    assert bad_status == 400 and "Account size" in bad["error"] and user_status == 403


def test_the_option_stop_and_a_dead_quote_reach_the_screen_and_the_journal_through_the_proxy(tmp_path, monkeypatch):
    """2026-11-24: the option's own bid down 50% sells the trade (the stock never reached its stop), the read and the
    journal say which stop fired and the real loss, and an option with no live bid is flagged instead of "In a trade"."""
    clock = {"now": datetime(2026, 11, 13, 10, 0, 0, tzinfo=ticker_trader.ET)}
    quotes = {"NVDA": {"last": 100.0}, CALL: {"bid": 1.0, "ask": 1.1}}

    def fetch(symbols):
        at = clock["now"].timestamp() - 1
        return {"asOf": at, "quotes": {s: {**quotes[s], "quoteAt": at} for s in symbols if s in quotes}}

    made = ticker_trader.TickerTrader(tmp_path / "momx_ticker_trader", quotes_fetch=fetch, chain_fetch=lambda s: chain(),
                                      clock=lambda: clock["now"])
    monkeypatch.setattr(ticker_trader, "_TRADERS", [made])

    def tick(times=1):
        for _ in range(times):
            clock["now"] += timedelta(seconds=10)
            made.tick()

    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            api.post(ROUTE + "/start", {"ticker": "nvda", "side": "call"}, headers=ADMIN)
            tick(2)
            _, held = api.get(ROUTE, headers=ADMIN)
            quotes[CALL] = {"bid": 0.5, "ask": 0.6}           # NVDA still at 100.00, far above the stock stop
            tick()
            _, first = api.get(ROUTE, headers=ADMIN)         # 2026-11-25 (changed on purpose): one check sells nothing
            tick()                                            # the second check in a row sells
            _, sold = api.get(ROUTE, headers=ADMIN)
            journal_status, journal = api.get(ROUTE + "/journal?id=NVDA-CALL", headers=ADMIN)
            api.post(ROUTE + "/again", {"id": "NVDA-CALL"}, headers=ADMIN)
            quotes[CALL] = {"bid": 1.0, "ask": 1.1}
            tick(2)
            del quotes[CALL]                                  # the option's quote disappears: no bid at all
            tick(13)
            _, dead = api.get(ROUTE, headers=ADMIN)
    open_trade = held["runs"][0]["open"]
    assert open_trade["stopOption"] == 0.55 and open_trade["stopStock"] < 100.0 and held["runs"][0]["quoteDead"] is False
    assert first["runs"][0]["open"]["held"] == 3 and first["runs"][0]["trades"] == []
    assert first["runs"][0]["note"] == ("The option's bid $0.50 is down 55% from the $1.10 paid, at the 50% option stop: "
                                        "sold if the next price check says the same. Holding 3 of 3.")
    run = sold["runs"][0]
    trade = run["trades"][0]
    assert run["open"] is None and run["status"] == "idle" and run["stock"] == 100.0
    assert trade["reason"] == "Option stop: the option's bid $0.50 is down 55% from the $1.10 paid (the stop is 50%), sold 3 at $0.50."
    assert trade["stopFired"] == "option" and trade["stopLossPct"] == 55 and trade["pl"] == -180.0 and trade["plPct"] == -54.5
    card = journal["trades"][0]
    assert journal_status == 200 and card["outcome"] == "stopped" and card["stopFired"] == "option" and card["stopLossPct"] == 55
    assert card["events"][0]["text"] == trade["reason"]
    run = dead["runs"][0]
    assert run["open"]["held"] == 3 and run["quoteDead"] is True
    assert run["note"] == "No live price on the 104 Call for 2 min: the option stop cannot be checked. Still holding 3 of 3."
    # The account says one open trade is counted at an old price (the screen marks it "price is old").
    assert dead["account"]["oldPriceTrades"] == 1 and dead["account"]["openTrades"] == 1 and held["account"]["oldPriceTrades"] == 0


# ---- 2026-11-25 (round 2) ----

def _old_nvda():
    """His real NVDA trade of 2026-11-19, as the engine saved it before the option stop existed."""
    at = "2026-11-19T10:31:14-05:00"
    return {"id": "NVDA-CALL-1-1795098725", "day": "2026-11-19", "ticker": "NVDA", "side": "CALL", "contract": CALL, "strike": 162.5,
            "expiry": "2026-11-20", "qty": 2, "held": 0, "split": [1, 1, 0], "entry": 0.7, "entrySpot": 155.74, "cost": 140.0,
            "boughtAt": "2026-11-19T09:32:05-05:00", "closedAt": at, "stopStart": 153.02, "stopStock": 153.02, "stopKind": "stop",
            "t1": 158.0, "t2": 160.0, "t3": 162.5, "targets": [], "hits": [], "status": "closed", "pl": -88.0, "plPct": -62.9,
            "balanceAfter": 1912.0, "reason": "Stop: stock traded through 153.02, sold 2 at $0.26.",
            "sells": [{"at": at, "qty": 2, "price": 0.26, "pl": -88.0}],
            "events": [{"at": at, "text": "Stop: stock traded through 153.02, sold 2 at $0.26."}]}


def test_round_two_through_the_proxy_bad_tick_entry_guard_all_quotes_gone_and_an_old_trades_card(tmp_path, monkeypatch):
    """One bad quote then a normal one sells nothing; a contract whose bid is already at the option stop is not bought;
    with every quote gone the loud note shows; and a trade saved before the option stop gets its real loss in the journal."""
    folder = tmp_path / "momx_ticker_trader"
    folder.mkdir(parents=True)
    saved = {"version": 2, "account": {"startBalance": 10000.0},
             "runs": {"NVDA-CALL": {"id": "NVDA-CALL", "ticker": "NVDA", "side": "CALL", "status": "idle", "createdAt": "2026-11-19T09:32:00-05:00",
                                    "open": None, "trades": [_old_nvda()], "pick": None}}}
    (folder / "state.json").write_text(json.dumps(saved), encoding="utf-8")
    clock = {"now": datetime(2026, 11, 20, 10, 0, 0, tzinfo=ticker_trader.ET)}       # the Friday the fixture chain expires
    quotes = {"NVDA": {"last": 100.0}, CALL: {"bid": 0.10, "ask": 0.20}}

    def fetch(symbols):
        at = clock["now"].timestamp() - 1
        return {"asOf": at, "quotes": {s: {**quotes[s], "quoteAt": at} for s in symbols if s in quotes}}

    made = ticker_trader.TickerTrader(folder, quotes_fetch=fetch, chain_fetch=lambda s: chain(), clock=lambda: clock["now"])
    monkeypatch.setattr(ticker_trader, "_TRADERS", [made])

    def tick(times=1):
        for _ in range(times):
            clock["now"] += timedelta(seconds=10)
            made.tick()

    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            journal_status, journal = api.get(ROUTE + "/journal?id=NVDA-CALL", headers=ADMIN)
            api.post(ROUTE + "/again", {"id": "NVDA-CALL"}, headers=ADMIN)
            tick(2)                                           # bid $0.10 under a $0.20 ask: not bought
            _, refused = api.get(ROUTE, headers=ADMIN)
            quotes[CALL] = {"bid": 1.0, "ask": 1.1}
            api.post(ROUTE + "/again", {"id": "NVDA-CALL"}, headers=ADMIN)
            tick(2)
            quotes[CALL] = {"bid": 0.2, "ask": 1.1}           # one bad quote
            tick()
            _, bad = api.get(ROUTE, headers=ADMIN)
            quotes[CALL] = {"bid": 1.0, "ask": 1.1}           # then a normal one
            tick()
            _, normal = api.get(ROUTE, headers=ADMIN)
            quotes.clear()                                    # every quote gone, the stock's too
            tick(13)
            _, gone = api.get(ROUTE, headers=ADMIN)
    card = journal["trades"][0]
    assert journal_status == 200 and card["stopOption"] is None and card["plPct"] == -62.9
    assert card["stopFired"] == "stock" and card["stopLossPct"] == 63
    run = refused["runs"][0]
    assert run["open"] is None and run["status"] == "idle" and run["tradeCount"] == 1 and refused["account"]["invested"] == 0
    assert run["note"] == ("Not bought: the 104 Call's buyers ($0.10) are already 50% under the asking price ($0.20), "
                           "so it would be sold at a loss at once.")
    assert bad["runs"][0]["open"]["held"] == 3 and bad["runs"][0]["note"].startswith("The option's bid $0.20 is down 82% from the $1.10 paid")
    run = normal["runs"][0]
    assert run["open"]["held"] == 3 and run["tradeCount"] == 1 and run["note"] == "In a trade: holding 3 of 3"
    run = gone["runs"][0]
    assert run["open"]["held"] == 3 and run["quoteDead"] is True and gone["account"]["oldPriceTrades"] == 1
    assert run["note"] == ("No live price on the 104 Call for 2 min, and none on NVDA either: neither stop can be checked. "
                           "Still holding 3 of 3.")


def test_a_contract_with_no_bid_never_reaches_the_engine_so_it_claims_no_reason(tmp_path, monkeypatch):
    """What REALLY arrives: the real quote route (api_server.option_quotes) leaves a contract out of its reply when
    Schwab sends no bid, or no ask. The engine then cannot tell which side is missing, so its note must not say
    "No buyer is bidding" (round 1's test used a quote shape with an ask and no bid, which this route never sends)."""
    import api_server

    now = datetime(2026, 11, 13, 10, 0, 0, tzinfo=ticker_trader.ET)
    clock = {"now": now}
    fresh_ms = lambda: (clock["now"].timestamp() - 2) * 1000.0      # noqa: E731
    data = {CALL: {"bid": 1.0, "ask": 1.1, "quote_time_ms": fresh_ms()},
            "NVDA": {"bid": 99.9, "ask": 100.1, "last_price": 100.0}}
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [_SchwabClient(data)])
    monkeypatch.setattr(api_server.time, "time", lambda: clock["now"].timestamp())
    state = api_server.DashboardState.__new__(api_server.DashboardState)
    made = ticker_trader.TickerTrader(tmp_path / "tt", quotes_fetch=state.option_quotes,
                                      chain_fetch=lambda symbol: chain(), clock=lambda: clock["now"])
    made.start("NVDA", "CALL")
    made.tick()
    made.tick()
    stop = made.snapshot()["runs"][0]["open"]["stopStock"]
    data["NVDA"] = {"bid": stop - 0.6, "ask": stop - 0.4, "last_price": stop - 0.5}          # the stock stop is reached
    for name, quote in (("no bid", {"ask": 0.3}), ("no ask", {"bid": 0.2, "ask": 0.0}), ("no quote", None)):
        clock["now"] += timedelta(seconds=10)
        if quote is None:
            data.pop(CALL)
        else:
            data[CALL] = {**quote, "quote_time_ms": fresh_ms()}
        assert CALL not in state.option_quotes([CALL, "NVDA"])["quotes"], name     # the route drops it: the engine sees nothing
        made.tick()
        run = made.snapshot()["runs"][0]
        assert run["open"]["held"] == 3, name
        assert run["note"] == f"Stop reached at {stop:.2f}: Waiting for a fresh quote on the 104 Call (no quote under 60 s old)", name
        assert "buyer" not in run["note"] and "seller" not in run["note"], name
    # A real $0.00 bid under a live ask DOES arrive, and is a price: the stock stop sells there.
    clock["now"] += timedelta(seconds=10)
    data[CALL] = {"bid": 0.0, "ask": 0.05, "quote_time_ms": fresh_ms()}
    assert state.option_quotes([CALL])["quotes"][CALL]["bid"] == 0.0
    made.tick()
    sold = made.snapshot()["runs"][0]["trades"][0]
    assert sold["reason"] == f"Stop: stock traded through {stop:.2f}, sold 3 at $0.00, down 100% from the $1.10 paid."
