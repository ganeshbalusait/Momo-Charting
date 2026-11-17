"""Unit: the Ticker trader engine (momx/ticker_trader.py) with fake quotes, a fake
saved chain and a fake clock. Simulated only: the source test at the end proves the
module imports no broker code."""
from __future__ import annotations

import ast
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from momx import chart_plan, ticker_trader
from momx.ticker_trader import ET, TickerTrader, TraderError

EXPIRY = "2026-11-20"
CALL, PUT = "NVDA  261120C00104000", "NVDA  261120P00096000"


def chain(move=4.0):
    rows = []
    for strike, delta in ((101, 0.42), (102, 0.33), (103, 0.25), (104, 0.19), (105, 0.12), (106, 0.08), (108, 0.04)):
        rows.append({"symbol": f"NVDA  261120C{strike * 1000:08d}", "side": "CALL", "expiry": EXPIRY, "strike": strike,
                     "delta": delta, "gamma": 0.03, "bid": 0.95, "ask": 1.05, "expected_move": move,
                     "days_to_expiration": 7, "open_interest": 5000 if strike == 106 else 100})
    for strike, delta in ((99, -0.42), (98, -0.33), (97, -0.25), (96, -0.19), (95, -0.12), (94, -0.08), (92, -0.04)):
        rows.append({"symbol": f"NVDA  261120P{strike * 1000:08d}", "side": "PUT", "expiry": EXPIRY, "strike": strike,
                     "delta": delta, "gamma": 0.03, "bid": 0.95, "ask": 1.05, "expected_move": move,
                     "days_to_expiration": 7, "open_interest": 5000 if strike == 94 else 100})
    return {"underlyingPrice": 100.0, "scannedAt": "2026-11-13T09:45:00-05:00", "selectedExpiryChainRows": rows,
            "currentAtm": {"expiry": EXPIRY, "expectedMove": move}}


class World:
    """The fake clock, the fake quote feed and the fake saved chains."""

    def __init__(self, tmp_path):
        self.now = datetime(2026, 11, 13, 10, 0, 0, tzinfo=ET)       # a Friday
        self.dir = Path(tmp_path) / "tt"
        self.quotes: dict = {}
        self.age = 1.0              # how old each quote's OWN time (quoteAt) is
        self.reply_age = 1.0        # how old the reply's asOf is
        self.timed = True           # False = a server from before quote times (no quoteAt at all)
        self.during = None          # called while the quote call is in flight
        self.calls: list = []
        self.chains = {"NVDA": chain()}

    def fetch(self, symbols):
        self.calls.append(list(symbols))
        if self.during:
            self.during()
        out = {}
        for name in symbols:
            if name in self.quotes:
                out[name] = dict(self.quotes[name])
                if self.timed:
                    out[name].setdefault("quoteAt", self.now.timestamp() - self.age)
        return {"asOf": self.now.timestamp() - self.reply_age, "quotes": out}

    def chain_of(self, symbol):
        if symbol not in self.chains:
            raise FileNotFoundError(symbol)
        return self.chains[symbol]

    def trader(self):
        return TickerTrader(self.dir, quotes_fetch=self.fetch, chain_fetch=self.chain_of, clock=lambda: self.now)

    def set(self, stock, bid=1.0, ask=1.1, contract=CALL):
        self.quotes["NVDA"] = {"last": stock}
        self.quotes[contract] = {"bid": bid, "ask": ask}

    def step(self, trader, seconds=10):
        self.now += timedelta(seconds=seconds)
        trader.tick()


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def run_of(trader, run_id="NVDA-CALL"):
    return next(r for r in trader.snapshot()["runs"] if r["id"] == run_id)


def bought(world, side="CALL", ask=1.1, contract=CALL):
    trader = world.trader()
    world.set(100.0, bid=ask - 0.1, ask=ask, contract=contract)
    trader.start("nvda", side)
    world.step(trader)      # picks the contract from the stock price
    world.step(trader)      # buys at its ask
    return trader


def test_buys_the_charts_contract_at_the_ask_with_the_charts_levels(world):
    trader = bought(world)
    run = run_of(trader)
    trade = run["open"]
    plan = chart_plan.from_chain(world.chains["NVDA"], bear=False, expiry=EXPIRY, strike=104, entry=1.1, spot=100.0,
                                 today="2026-11-13")
    assert run["status"] == "open" and trade["contract"] == CALL and trade["strike"] == 104
    assert trade["entry"] == 1.1 and trade["qty"] == 3 and trade["cost"] == 330.0 and trade["split"] == [1, 1, 1]
    assert (trade["stopStock"], trade["t1"], trade["t2"], trade["t3"]) == (plan["stopStock"], 102.0, 104.0, 106.0)
    assert trade["stopStock"] < 100 and run["balance"] == 9670.0 and run["startBalance"] == 10000.0   # the buy took $330 of cash
    assert all(len(call) == len(set(call)) for call in world.calls) and len(world.calls) == 2   # one batched call per tick


def test_at_most_ten_percent_of_the_account_in_one_trade(world):
    trader = bought(world, ask=4.0)                          # $400 a contract: 2 fit in $1,000
    assert run_of(trader)["open"]["qty"] == 2
    world2 = World(world.dir.parent / "b")
    trader2 = bought(world2, ask=12.0)                       # $1,200 a contract: over 10%, so just one (2026-11-16)
    trade = run_of(trader2)["open"]
    assert trade["qty"] == 1 and trade["cost"] == 1200.0 and trade["split"] == chart_plan.split_contracts(1)
    assert "1 not 3: one contract costs more than 10% of the account" in trade["why"]


@pytest.mark.parametrize("balance, ask, qty", [
    (10000, 1.0, 3), (10000, 4.0, 2), (10000, 10.0, 1), (10000, 12.0, 1), (10000, 100.0, 1), (10000, 100.01, 0),
    (2000, 0.72, 2), (2000, 4.4, 1), (2000, 3.9, 1), (2000, 20.0, 1), (2000, 20.5, 0), (500, 6.0, 0), (2000, 0, 0),
])
def test_size_three_within_ten_percent_else_one_the_account_can_pay_for(balance, ask, qty):
    assert ticker_trader.size_for(balance, ask) == qty


def test_his_lly_morning_a_440_contract_on_a_2000_account_is_bought_once(tmp_path):
    world = World(tmp_path / "lly")
    trader = world.trader()
    world.set(100.0, bid=4.3, ask=4.4)
    trader.start("nvda", "CALL", 2000)
    world.step(trader)
    world.step(trader)
    run = run_of(trader)
    assert run["status"] == "open" and run["open"]["qty"] == 1 and run["open"]["cost"] == 440.0
    assert "Not bought" not in run["note"]


def test_stock_through_the_stop_sells_everything_at_the_bid(world):
    trader = bought(world)
    stop = run_of(trader)["open"]["stopStock"]
    world.set(stop - 0.05, bid=0.5, ask=0.6)
    world.step(trader)
    run = run_of(trader)
    trade = run["trades"][0]
    assert run["open"] is None and run["status"] == "idle"
    assert trade["reason"] == f"Stop: stock traded through {stop:.2f}, sold 3 at $0.50."
    assert trade["pl"] == -180.0 and run["balance"] == 9820.0 and trade["balanceAfter"] == 9820.0 and run["wins"] == 0


def test_three_contracts_scale_out_at_t1_t2_t3_and_the_stop_follows(world):
    trader = bought(world)
    world.set(102.1, bid=1.8, ask=1.9)
    world.step(trader)
    trade = run_of(trader)["open"]
    assert trade["held"] == 2 and trade["stopStock"] == 100.0 and trade["stopKind"] == "break-even"
    assert trade["hits"][0]["target"] == "T1" and trade["hits"][0]["sold"] == 1 and trade["hits"][0]["at"]
    world.set(104.2, bid=2.6, ask=2.7)
    world.step(trader)
    trade = run_of(trader)["open"]
    assert trade["held"] == 1 and trade["stopStock"] == 102.0 and trade["stopKind"] == "T1"
    world.set(106.0, bid=3.6, ask=3.7)
    world.step(trader)
    run = run_of(trader)
    trade = run["trades"][0]
    assert trade["reason"] == ("T1 hit: sold 1 at $1.80, stop to break-even. T2 hit: sold 1 at $2.60, stop to T1. "
                               "T3 hit: sold 1 at $3.60.")
    assert trade["pl"] == 470.0 and run["balance"] == 10470.0 and run["wins"] == 1 and run["net"] == 470.0


def test_break_even_stop_after_t1(world):
    trader = bought(world)
    world.set(102.1, bid=1.8, ask=1.9)
    world.step(trader)
    world.set(99.9, bid=1.0, ask=1.1)
    world.step(trader)
    trade = run_of(trader)["trades"][0]
    assert "Break-even stop: stock traded through 100.00, sold 2 at $1.00." in trade["reason"]
    assert trade["pl"] == 50.0


def test_two_contracts_are_done_at_t2_and_one_contract_sells_at_t2(world):
    trader = bought(world, ask=4.0)
    world.set(104.5, bid=6.0, ask=6.1)                        # through T1 and T2 in one tick
    world.step(trader)
    run = run_of(trader)
    assert run["open"] is None and [h["sold"] for h in run["trades"][0]["hits"]] == [1, 1]
    assert run["trades"][0]["pl"] == 400.0
    world1 = World(world.dir.parent / "one")
    trader1 = bought(world1, ask=6.0)                        # $600: one contract
    assert run_of(trader1)["open"]["qty"] == 1
    world1.set(102.5, bid=7.0, ask=7.1)
    world1.step(trader1)
    trade = run_of(trader1)["open"]
    assert trade["held"] == 1 and trade["stopKind"] == "break-even" and trade["events"][0]["text"] == "T1 hit: none sold, stop to break-even."
    world1.set(104.0, bid=8.0, ask=8.1)
    world1.step(trader1)
    assert run_of(trader1)["trades"][0]["pl"] == 200.0


def test_puts_are_the_mirror(world):
    trader = bought(world, side="PUT", contract=PUT)
    trade = run_of(trader, "NVDA-PUT")["open"]
    assert trade["contract"] == PUT and (trade["t1"], trade["t2"], trade["t3"]) == (98.0, 96.0, 94.0)
    assert trade["stopStock"] > 100
    world.set(97.9, bid=1.9, ask=2.0, contract=PUT)
    world.step(trader)
    trade = run_of(trader, "NVDA-PUT")["open"]
    assert trade["held"] == 2 and trade["stopStock"] == 100.0
    world.set(100.2, bid=1.0, ask=1.1, contract=PUT)
    world.step(trader)
    assert run_of(trader, "NVDA-PUT")["open"] is None


def test_everything_left_is_closed_at_1550(world):
    trader = bought(world)
    world.now = datetime(2026, 11, 13, 15, 49, 55, tzinfo=ET)
    world.step(trader, 1)
    assert run_of(trader)["open"] is not None
    world.set(100.5, bid=1.2, ask=1.3)
    world.step(trader, 10)
    trade = run_of(trader)["trades"][0]
    assert trade["reason"] == "15:50 close: sold 3 at the bid $1.20." and trade["pl"] == 30.0


def test_a_stale_quote_waits_and_never_invents_a_price(world):
    trader = world.trader()
    world.set(100.0)
    world.reply_age = 20.0                                    # the reply itself is old
    trader.start("NVDA", "CALL")
    world.step(trader)
    world.step(trader)
    run = run_of(trader)
    assert run["open"] is None and run["status"] == "waiting" and "no quote under 60 s old" in run["note"]
    world.reply_age = 1.0
    world.step(trader)
    world.step(trader)
    stop = run_of(trader)["open"]["stopStock"]
    world.reply_age = 20.0
    world.set(stop - 1, bid=0.4, ask=0.5)
    world.step(trader)
    assert run_of(trader)["open"]["held"] == 3 and "no quote under 60 s old" in run_of(trader)["note"]
    world.reply_age = 1.0
    world.quotes.pop(CALL)                                    # the stock is fresh but the option has no bid
    world.step(trader)
    run = run_of(trader)
    assert run["open"]["held"] == 3
    assert run["note"] == f"Stop reached at {stop:.2f}: Waiting for a fresh quote on the 104 Call (no quote under 60 s old)"


def test_a_restart_resumes_the_open_trade_without_selling_twice(world):
    trader = bought(world)
    world.set(102.1, bid=1.8, ask=1.9)
    world.step(trader)
    again = world.trader()                                    # the worker restarted
    world.step(again)
    world.step(again)
    trade = run_of(again)["open"]
    assert trade["held"] == 2 and len(trade["sells"]) == 1 and len(trade["hits"]) == 1
    assert run_of(again)["balance"] == 9850.0 and run_of(again)["net"] == 70.0      # $10,000 + $70 - the $220 still held
    saved = json.loads((world.dir / "state.json").read_text(encoding="utf-8"))
    assert saved["runs"]["NVDA-CALL"]["open"]["held"] == 2 and not (world.dir / "state.json.tmp").exists()


def test_market_closed_waits_for_the_open(world):
    world.now = datetime(2026, 11, 13, 8, 0, 0, tzinfo=ET)
    trader = world.trader()
    world.set(100.0)
    trader.start("NVDA", "CALL")
    world.step(trader)
    run = run_of(trader)
    assert run["status"] == "waiting" and run["note"] == "Market closed: buys at the 9:30 open" and world.calls == []
    world.now = datetime(2026, 11, 14, 11, 0, 0, tzinfo=ET)   # Saturday
    world.step(trader)
    assert run_of(trader)["open"] is None and world.calls == []
    world.now = datetime(2026, 11, 16, 9, 30, 0, tzinfo=ET)
    world.step(trader)
    world.step(trader)
    assert run_of(trader)["open"]["boughtAt"].startswith("2026-11-16T09:30")


def test_no_re_entry_until_trade_again_and_reset_starts_fresh(world):
    trader = bought(world)
    world.set(90.0, bid=0.3, ask=0.4)
    world.step(trader)
    world.set(100.0)
    for _ in range(3):
        world.step(trader)
    run = run_of(trader)
    assert run["open"] is None and run["tradeCount"] == 1 and run["balance"] == 9760.0
    trader.again("NVDA-CALL")
    world.step(trader)
    world.step(trader)
    assert run_of(trader)["open"] is not None
    with pytest.raises(TraderError) as refused:
        trader.again("NVDA-CALL")
    assert refused.value.status == 409
    out = trader.reset("NVDA-CALL")
    assert out["balance"] == 10000.0 and out["trades"] == [] and out["open"] is None and out["status"] == "idle"
    world.step(trader)
    assert run_of(trader)["open"] is None


def test_stop_button_sells_at_the_bid_and_stops_the_run(world):
    trader = bought(world)
    world.set(100.4, bid=1.3, ask=1.4)
    asked = len(world.calls)
    out = trader.stop("NVDA-CALL")
    # Stop answers at once and asks for no quote itself: the next tick sells.
    assert len(world.calls) == asked and out["open"]["held"] == 3 and out["stopping"] is True
    assert out["note"] == "Stopping: selling at the next fresh bid"
    world.step(trader)
    out = run_of(trader)
    assert out["status"] == "stopped" and out["open"] is None
    assert out["trades"][0]["reason"] == "Stopped by you: sold 3 at the bid $1.30." and out["balance"] == 10060.0
    world.step(trader)
    assert run_of(trader)["open"] is None and run_of(trader)["tradeCount"] == 1


def test_start_validates_the_ticker_the_side_and_the_saved_chain(world):
    trader = world.trader()
    for ticker, side, text in (("NV1DA", "CALL", "Type a ticker"), ("", "CALL", "Type a ticker"), ("TOOLONGX", "CALL", "Type a ticker"),
                               ("NVDA", "BOTH", "Pick Call or Put"), ("ZZZZ", "CALL", "No saved option chain for ZZZZ")):
        with pytest.raises(TraderError) as refused:
            trader.start(ticker, side)
        assert refused.value.status == 400 and text in refused.value.message
    assert trader.snapshot()["runs"] == []
    with pytest.raises(TraderError) as missing:
        trader.stop("AAPL-CALL")
    assert missing.value.status == 404


def test_at_most_ten_runs(world):
    trader = world.trader()
    names = ["AA", "BB", "CC", "DD", "EE"]
    for name in names + ["FF"]:
        world.chains[name] = chain()
    for name in names:
        trader.start(name, "CALL")
        trader.start(name, "PUT")
    with pytest.raises(TraderError) as full:
        trader.start("FF", "CALL")
    assert full.value.status == 409 and len(trader.snapshot()["runs"]) == 10


def test_the_module_has_no_broker_code():
    """Simulated only: nothing here can place an order."""
    path = Path(ticker_trader.__file__)
    source = path.read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported |= {f"{node.module}.{a.name}" for a in node.names}
    allowed = {"__future__", "__future__.annotations", "json", "math", "os", "re", "threading", "time", "datetime",
               "datetime.datetime", "pathlib", "pathlib.Path", "typing", "typing.Any", "typing.Callable", "typing.Mapping",
               "zoneinfo", "zoneinfo.ZoneInfo", "momx", "momx.chart_plan", "momx.option_track",
               "momx.option_track._default_plan_chain", "momx.option_track.OPTION_QUOTES_URL",
               "momx.option_track._http_json",
               # The journal (2026-11-13); its own source test (test_momx_ticker_journal.py) holds it to no broker code.
               "momx.ticker_journal"}
    assert imported <= allowed, imported - allowed
    low = source.lower()
    for word in ("alpaca", "schwab", "submit_order", "place_order", "paper_mirror", "client_factory", "requests", "urllib"):
        assert word not in low, word


# ---- 2026-11-13 review: a $0.00 bid, the expiry exit, the quote's own time, the lock ----

def test_a_real_zero_bid_is_a_price_the_1550_close_books_the_loss(world):
    trader = bought(world)
    world.now = datetime(2026, 11, 13, 15, 50, 5, tzinfo=ET)
    world.set(100.2, bid=0.0, ask=0.02)                       # worthless, but a real market
    world.step(trader)
    run = run_of(trader)
    trade = run["trades"][0]
    assert run["open"] is None and trade["reason"] == "15:50 close: sold 3 at the bid $0.00."
    assert trade["pl"] == -330.0 and run["balance"] == 9670.0 and trade["sells"][0]["price"] == 0.0


def test_a_zero_bid_sells_at_the_stop_and_at_a_target_too(world):
    trader = bought(world)
    stop = run_of(trader)["open"]["stopStock"]
    world.set(stop - 0.05, bid=0.0, ask=0.05)
    world.step(trader)
    assert run_of(trader)["trades"][0]["reason"] == f"Stop: stock traded through {stop:.2f}, sold 3 at $0.00."
    world2 = World(world.dir.parent / "z")
    trader2 = bought(world2)
    world2.set(102.1, bid=0.0, ask=0.05)
    world2.step(trader2)
    trade = run_of(trader2)["open"]
    assert trade["held"] == 2 and trade["hits"][0]["price"] == 0.0 and trade["bid"] == 0.0
    # A zero bid with NO ask is not a market: nothing sells.
    world3 = World(world.dir.parent / "n")
    trader3 = bought(world3)
    world3.set(90.0, bid=0.0, ask=0.0)
    world3.step(trader3)
    assert run_of(trader3)["open"]["held"] == 3


def test_an_expired_contract_with_no_quote_is_closed_worthless(world):
    trader = bought(world)                                    # expires 2026-11-20
    world.quotes.pop(CALL)
    world.now = datetime(2026, 11, 20, 15, 59, 0, tzinfo=ET)  # expiry day, still trading: it waits
    world.step(trader)
    assert run_of(trader)["open"]["held"] == 3
    world.now = datetime(2026, 11, 20, 16, 0, 5, tzinfo=ET)   # expiry day after 16:00, no quote
    world.step(trader)
    run = run_of(trader)
    trade = run["trades"][0]
    assert run["open"] is None and run["status"] == "idle"
    assert trade["reason"] == "Expired worthless: closed 3 at $0.00." and trade["pl"] == -330.0
    assert run["balance"] == 9670.0 and trade["status"] == "closed"


def test_an_expired_contract_is_closed_days_later_with_the_market_shut(world):
    trader = bought(world)
    world.quotes.clear()                                      # no quote at all, not even the stock
    world.now = datetime(2026, 11, 21, 11, 0, 0, tzinfo=ET)   # the Saturday after expiry
    world.step(trader)
    run = run_of(trader)
    assert run["open"] is None and run["trades"][0]["reason"] == "Expired worthless: closed 3 at $0.00."
    assert run["balance"] == 9670.0
    # An old server (no quote times) or a failing quote call never keeps it open either.
    world2 = World(world.dir.parent / "old")
    trader2 = bought(world2)
    world2.timed = False
    world2.now = datetime(2026, 11, 23, 10, 0, 0, tzinfo=ET)
    world2.step(trader2)
    assert run_of(trader2)["open"] is None and "Expired worthless" in run_of(trader2)["trades"][0]["reason"]


def test_an_expired_contract_in_the_money_is_closed_at_its_intrinsic_value(world):
    trader = bought(world)
    world.quotes.pop(CALL)
    world.quotes["NVDA"] = {"last": 107.0}
    world.now = datetime(2026, 11, 23, 9, 45, 0, tzinfo=ET)   # the Monday after expiry
    world.step(trader)
    run = run_of(trader)
    trade = run["trades"][0]
    assert trade["reason"] == ("Expired in the money: no quote, closed 3 at its intrinsic value $3.00 "
                               "(NVDA at 107.00, strike 104).")
    assert trade["pl"] == 570.0 and run["balance"] == 10570.0
    world2 = World(world.dir.parent / "put")
    trader2 = bought(world2, side="PUT", contract=PUT)        # the 96 Put
    world2.quotes.pop(PUT)
    world2.quotes["NVDA"] = {"last": 95.0}
    world2.now = datetime(2026, 11, 23, 9, 45, 0, tzinfo=ET)
    world2.step(trader2)
    assert "intrinsic value $1.00" in run_of(trader2, "NVDA-PUT")["trades"][0]["reason"]


def test_yesterdays_closing_quote_is_never_bought_at_the_open(world):
    """Armed before the open: at 09:30 the reply is stamped "now" but the option's
    own quote time is still yesterday's close. It waits for today's first quote."""
    world.now = datetime(2026, 11, 13, 8, 0, 0, tzinfo=ET)
    trader = world.trader()
    world.set(100.0)
    trader.start("NVDA", "CALL")
    yesterday = datetime(2026, 11, 12, 16, 0, 0, tzinfo=ET).timestamp()
    world.quotes[CALL]["quoteAt"] = yesterday
    world.now = datetime(2026, 11, 13, 9, 30, 0, tzinfo=ET)
    for _ in range(4):
        world.step(trader)
    run = run_of(trader)
    assert run["open"] is None and run["status"] == "waiting"
    assert run["note"] == "Waiting for today's first quote on the 104 Call"
    world.quotes[CALL]["quoteAt"] = datetime(2026, 11, 13, 9, 29, 59, tzinfo=ET).timestamp()   # a pre-open quote
    world.step(trader)
    assert run_of(trader)["open"] is None
    world.quotes[CALL].pop("quoteAt")                         # today's first real quote
    world.step(trader)
    assert run_of(trader)["open"]["entry"] == 1.1


def test_a_holidays_frozen_quotes_are_never_traded(world):
    world.now = datetime(2026, 11, 16, 9, 30, 0, tzinfo=ET)   # a weekday with no market: nothing quotes all day
    trader = world.trader()
    frozen = datetime(2026, 11, 13, 16, 0, 0, tzinfo=ET).timestamp()
    world.quotes["NVDA"] = {"last": 100.0, "quoteAt": frozen}
    world.quotes[CALL] = {"bid": 1.0, "ask": 1.1, "quoteAt": frozen}
    trader.start("NVDA", "CALL")
    for _ in range(6):
        world.step(trader)
    run = run_of(trader)
    assert run["open"] is None and run["note"] == "Waiting for today's first quote on NVDA"
    world.quotes["NVDA"] = {"last": 100.0, "quoteAt": None}   # a stock quote with no time is taken as it is
    for _ in range(3):
        world.step(trader)
    run = run_of(trader)
    assert run["open"] is None and run["note"] == "Waiting for today's first quote on the 104 Call"


def test_a_quote_older_than_60_seconds_is_not_used_to_buy_or_sell(world):
    trader = world.trader()
    world.set(100.0)
    trader.start("NVDA", "CALL")
    world.step(trader)
    world.age = 61.0
    world.step(trader)
    run = run_of(trader)
    assert run["open"] is None and "no quote under 60 s old" in run["note"]
    world.age = 59.0
    world.step(trader)
    world.step(trader)
    stop = run_of(trader)["open"]["stopStock"]
    world.set(stop - 1, bid=0.4, ask=0.5)
    world.age = 61.0
    world.step(trader)
    assert run_of(trader)["open"]["held"] == 3
    world.quotes["NVDA"]["quoteAt"] = world.now.timestamp() + 9   # the stock fresh, the option 61 s old
    world.step(trader)
    run = run_of(trader)
    assert run["open"]["held"] == 3
    assert run["note"] == f"Stop reached at {stop:.2f}: Waiting for a fresh quote on the 104 Call (no quote under 60 s old)"
    world.age = 1.0
    world.quotes["NVDA"].pop("quoteAt")
    world.step(trader)
    assert run_of(trader)["open"] is None and run_of(trader)["trades"][0]["pl"] == -210.0
    assert trader.snapshot()["quoteSeconds"] == 60.0


def test_a_server_without_quote_times_never_trades(world):
    world.timed = False
    trader = world.trader()
    world.set(100.0)
    trader.start("NVDA", "CALL")
    for _ in range(4):
        world.step(trader)
    run = run_of(trader)
    assert run["open"] is None and run["note"] == "Waiting for quote times (restart needed)"
    world.timed = True
    world.step(trader)
    world.step(trader)
    stop = run_of(trader)["open"]["stopStock"]
    world.timed = False
    world.set(stop - 1, bid=0.4, ask=0.5)
    world.step(trader)
    run = run_of(trader)
    assert run["open"]["held"] == 3 and run["note"] == "Waiting for quote times (restart needed)"


def _from_another_thread(call):
    """Run `call` on another thread; (finished within 2 s, its result)."""
    box = {}
    thread = threading.Thread(target=lambda: box.update(out=call()), daemon=True)
    thread.start()
    thread.join(2.0)
    return not thread.is_alive(), box.get("out")


def test_the_page_and_the_buttons_are_not_blocked_while_quotes_are_fetched(world):
    trader = bought(world)
    seen = []
    world.during = lambda: seen.append(_from_another_thread(trader.snapshot)[0])
    world.set(100.4, bid=1.3, ask=1.4)
    world.step(trader)
    assert seen == [True]                                     # the read answered during the quote call
    seen.clear()
    world.during = lambda: seen.append(_from_another_thread(lambda: trader.stop("NVDA-CALL"))[0])
    world.step(trader)                                        # Stop pressed mid-call: it returns at once ...
    assert seen == [True]
    run = run_of(trader)                                      # ... and that same tick sells at the fresh bid
    assert run["status"] == "stopped" and run["trades"][0]["reason"] == "Stopped by you: sold 3 at the bid $1.30."


def test_a_run_reset_or_stopped_while_quotes_are_fetched_is_left_alone(world):
    trader = world.trader()
    world.set(100.0)
    trader.start("NVDA", "CALL")
    world.step(trader)                                        # picked: the next tick would buy
    world.during = lambda: _from_another_thread(lambda: trader.stop("NVDA-CALL"))
    world.step(trader)
    run = run_of(trader)
    assert run["open"] is None and run["status"] == "stopped" and run["tradeCount"] == 0
    world.during = None
    trader2 = bought(World(world.dir.parent / "r"))
    w2 = World(world.dir.parent / "r")
    w2.now = world.now
    held = bought(w2)
    stop = run_of(held)["open"]["stopStock"]
    w2.set(stop - 1, bid=0.4, ask=0.5)
    w2.during = lambda: _from_another_thread(lambda: held.reset("NVDA-CALL"))
    w2.step(held)
    run = run_of(held)                                        # reset mid-call: the old trade is not sold into the fresh account
    assert run["open"] is None and run["trades"] == [] and run["balance"] == 10000.0 and run["status"] == "idle"
    assert trader2 is not None


def test_the_quote_call_has_its_own_short_timeout(monkeypatch):
    from momx import option_track
    asked = {}

    def fake(url, timeout=40.0):
        asked.update(url=url, timeout=timeout)
        return {"asOf": 0, "quotes": {}}

    monkeypatch.setattr(option_track, "_http_json", fake)
    ticker_trader._default_quotes([CALL, "NVDA"])
    assert asked["timeout"] == 5.0 == ticker_trader.FETCH_SECONDS
    assert asked["url"] == option_track.OPTION_QUOTES_URL + "NVDA%20%20261120C00104000,NVDA"


# ---- account size (2026-11-13: "So I Change it 10k,5k, 2k or 100K", "What ever I want") ----

def test_the_account_size_is_picked_at_start_and_defaults_to_10000(world):
    trader = world.trader()
    run = trader.start("nvda", "CALL", 5000)
    assert run["balance"] == 5000.0 and run["startBalance"] == 5000.0 and run["net"] == 0
    world.chains["AMD"] = chain()
    other = trader.start("amd", "CALL")
    assert other["balance"] == 5000.0 and other["startBalance"] == 5000.0       # the same account, not a second one
    assert trader.start("nvda", "PUT", "2500.4")["startBalance"] == 5000.0       # a later ticker cannot change the size
    fresh = TickerTrader(world.dir.parent / "fresh", quotes_fetch=world.fetch, chain_fetch=world.chain_of, clock=lambda: world.now)
    assert fresh.start("nvda", "CALL")["startBalance"] == 10000.0               # no size named: $10,000
    whole = TickerTrader(world.dir.parent / "whole", quotes_fetch=world.fetch, chain_fetch=world.chain_of, clock=lambda: world.now)
    assert whole.start("nvda", "CALL", "2500.4")["startBalance"] == 2500.0      # whole dollars
    again = world.trader()                                                     # saved and reloaded
    assert run_of(again)["startBalance"] == 5000.0


def test_bad_account_sizes_are_refused_with_a_plain_message(world):
    trader = world.trader()
    for bad in (499, 1_000_001, "abc", "", True, float("nan"), -5000):
        with pytest.raises(TraderError) as err:
            trader.start("nvda", "CALL", bad)
        assert err.value.status == 400 and "Account size" in err.value.message
    assert trader.snapshot()["runs"] == []
    trader.start("nvda", "CALL", 500)
    trader.reset("NVDA-CALL", 1_000_000)
    with pytest.raises(TraderError) as err:
        trader.reset("NVDA-CALL", 100)
    assert err.value.status == 400 and run_of(trader)["startBalance"] == 1_000_000.0


def test_reset_starts_fresh_at_the_new_size_and_net_follows_it(world):
    trader = bought(world)
    trader.reset("NVDA-CALL", 2000)
    run = run_of(trader)
    assert run["balance"] == 2000.0 and run["startBalance"] == 2000.0 and run["net"] == 0
    assert run["open"] is None and run["trades"] == [] and run["status"] == "idle"
    assert "fresh $2,000" in run["note"]
    trader.reset("NVDA-CALL")                       # no size: keeps the account's own starting size
    assert run_of(trader)["startBalance"] == 2000.0


def test_a_state_file_from_before_sizes_still_loads_at_10000(world):
    world.dir.mkdir(parents=True)
    won = {"id": "NVDA-CALL-1-1", "day": "2026-11-12", "status": "closed", "pl": 250.0, "qty": 3, "entry": 1.0}
    old = {"id": "NVDA-CALL", "ticker": "NVDA", "side": "CALL", "status": "idle", "balance": 10250.0,
           "open": None, "trades": [won], "pick": None}
    (world.dir / "state.json").write_text(json.dumps({"version": 1, "runs": {"NVDA-CALL": old}}), encoding="utf-8")
    run = run_of(world.trader())
    assert run["startBalance"] == 10000.0 and run["net"] == 250.0 and run["balance"] == 10250.0


def test_the_ten_percent_cap_follows_the_account_size(tmp_path):
    small = World(tmp_path / "a")
    trader = small.trader()
    small.set(100.0, bid=5.9, ask=6.0)
    trader.start("nvda", "CALL", 500)                # $600 a contract: more than the whole $500 account
    small.step(trader)
    small.step(trader)
    run = run_of(trader)
    assert run["open"] is None and run["status"] == "idle"
    assert "one contract costs $600, more than the $500 cash left in the account" in run["note"]
    fits = World(tmp_path / "b")
    trader2 = fits.trader()
    fits.set(100.0, bid=1.4, ask=1.5)
    trader2.start("nvda", "CALL", 2000)
    fits.step(trader2)
    fits.step(trader2)
    assert run_of(trader2)["open"]["qty"] == 1


# ---- one account for every ticker (2026-11-16: "why each trade 2k ?", "2k account reset but it will reduce
# amount so I know how my account size in real loss or profit") ----

AMD_CALL = "AMD   261120C00104000"


def amd_chain():
    doc = chain()
    for row in doc["selectedExpiryChainRows"]:
        row["symbol"] = row["symbol"].replace("NVDA ", "AMD  ")
    return doc


def two_tickers(tmp_path, size=2000, nvda_ask=0.72, amd_ask=4.4):
    world = World(tmp_path / "shared")
    world.chains["AMD"] = amd_chain()
    trader = world.trader()
    world.set(100.0, bid=nvda_ask - 0.02, ask=nvda_ask)
    world.quotes["AMD"] = {"last": 100.0}
    world.quotes[AMD_CALL] = {"bid": amd_ask - 0.1, "ask": amd_ask}
    trader.start("nvda", "CALL", size)
    world.step(trader)
    world.step(trader)
    trader.start("amd", "CALL")
    world.step(trader)
    world.step(trader)
    return world, trader


def test_every_ticker_spends_from_the_same_account_and_a_buy_takes_the_cash_down(tmp_path):
    world, trader = two_tickers(tmp_path)                    # his morning: a $144 trade, then a $440 one, on $2,000
    nvda, amd = run_of(trader), run_of(trader, "AMD-CALL")
    assert nvda["open"]["qty"] == 2 and nvda["open"]["cost"] == 144.0
    assert amd["open"]["qty"] == 1 and amd["open"]["cost"] == 440.0
    account = trader.snapshot()["account"]
    assert account["startBalance"] == 2000.0 and account["cash"] == 1416.0 and account["invested"] == 584.0
    assert account["openTrades"] == 2 and account["realized"] == 0.0
    # Both runs show the same account: its cash and its starting size.
    assert nvda["balance"] == amd["balance"] == 1416.0 and nvda["startBalance"] == amd["startBalance"] == 2000.0
    assert nvda["account"] == amd["account"] == account


def test_the_account_value_follows_the_open_trades_last_bid_and_a_sale_puts_the_money_back(tmp_path):
    world, trader = two_tickers(tmp_path)
    world.step(trader)                                       # holding: each open trade now carries its bid
    account = trader.snapshot()["account"]
    # 2 x $0.70 bid + 1 x $4.30 bid = $570 for what cost $584: the account is worth $14 less.
    assert account["openValue"] == 570.0 and account["value"] == 1986.0 and account["net"] == -14.0
    trader.stop("AMD-CALL")
    world.step(trader)                                       # sold 1 at the $4.30 bid: -$10
    account = trader.snapshot()["account"]
    amd = run_of(trader, "AMD-CALL")
    assert amd["trades"][0]["pl"] == -10.0 and amd["net"] == -10.0 and run_of(trader)["net"] == 0
    assert account["cash"] == 1846.0 and account["invested"] == 144.0 and account["realized"] == -10.0
    assert amd["trades"][0]["balanceAfter"] == 1990.0        # the account after that trade, open trades at cost


def test_two_trades_cannot_spend_the_same_cash(tmp_path):
    # $600 account: the first trade takes $440, so the second ($440 again) no longer fits the $160 left.
    world, trader = two_tickers(tmp_path, size=600, nvda_ask=4.4, amd_ask=4.4)
    assert run_of(trader)["open"]["cost"] == 440.0
    amd = run_of(trader, "AMD-CALL")
    assert amd["open"] is None and amd["status"] == "idle"
    assert amd["note"] == "Not bought: one contract costs $440, more than the $160 cash left in the account."
    assert trader.snapshot()["account"]["cash"] == 160.0


def test_fewer_contracts_when_the_cash_left_is_short_and_the_trade_says_so(tmp_path):
    # $1,000 account: $920 is tied up. 10% would allow 2 x $0.45, but only $80 cash is left: one.
    world = World(tmp_path / "short")
    world.chains["AMD"] = amd_chain()
    trader = world.trader()
    world.set(100.0, bid=9.1, ask=9.2)                       # one $920 contract (over 10%: bought once)
    world.quotes["AMD"] = {"last": 100.0}
    world.quotes[AMD_CALL] = {"bid": 0.4, "ask": 0.45}
    trader.start("nvda", "CALL", 1000)
    world.step(trader)
    world.step(trader)
    trader.start("amd", "CALL")
    world.step(trader)
    world.step(trader)
    amd = run_of(trader, "AMD-CALL")["open"]
    assert amd["qty"] == 1 and "1 not 3: that is all the cash left in the account buys." in amd["why"]
    assert run_of(trader)["open"]["cost"] == 920.0 and trader.snapshot()["account"]["cash"] == 35.0


def test_reset_starts_the_whole_account_fresh_for_every_ticker(tmp_path):
    world, trader = two_tickers(tmp_path)
    out = trader.reset("AMD-CALL", 5000)
    assert out["id"] == "AMD-CALL" and out["balance"] == 5000.0
    for run in trader.snapshot()["runs"]:
        assert run["open"] is None and run["trades"] == [] and run["status"] == "idle" and "fresh $5,000" in run["note"]
    account = trader.snapshot()["account"]
    assert account["startBalance"] == account["cash"] == account["value"] == 5000.0 and account["net"] == 0
    assert trader.reset(None, 2000) is None and trader.snapshot()["account"]["cash"] == 2000.0   # from the list page
    with pytest.raises(TraderError) as err:
        trader.reset("ZZZ-CALL", 2000)
    assert err.value.status == 404
    again = world.trader()                                   # saved and reloaded
    assert again.snapshot()["account"]["startBalance"] == 2000.0


def test_his_file_from_this_morning_becomes_one_2000_account(world):
    """Two runs saved with $2,000 each (SPY Put open at $144, LLY idle): one $2,000 account, $1,856 cash."""
    world.dir.mkdir(parents=True)
    held = {"id": "NVDA-PUT-1-1", "day": "2026-11-13", "ticker": "NVDA", "side": "PUT", "contract": PUT, "strike": 96, "expiry": EXPIRY,
            "qty": 2, "held": 2, "split": [1, 1, 0], "entry": 0.72, "entrySpot": 100.0, "cost": 144.0, "pl": 0.0, "status": "open",
            "stopStock": 104.0, "stopStart": 104.0, "stopKind": "stop", "t1": 98.0, "t2": 96.0, "t3": 94.0,
            "hits": [], "sells": [], "events": [], "targets": []}
    runs = {
        "NVDA-PUT": {"id": "NVDA-PUT", "ticker": "NVDA", "side": "PUT", "status": "open", "balance": 2000.0, "startBalance": 2000.0,
                     "createdAt": "2026-11-13T09:30:00-05:00", "open": held, "trades": [], "pick": None},
        "AMD-CALL": {"id": "AMD-CALL", "ticker": "AMD", "side": "CALL", "status": "idle", "balance": 2000.0, "startBalance": 2000.0,
                     "createdAt": "2026-11-13T09:36:00-05:00", "open": None, "trades": [], "pick": None},
    }
    (world.dir / "state.json").write_text(json.dumps({"version": 1, "runs": runs}), encoding="utf-8")
    account = world.trader().snapshot()["account"]
    assert account["startBalance"] == 2000.0 and account["cash"] == 1856.0 and account["invested"] == 144.0
