"""The paper bots on the chart's Entry / Target / Stop plan (momx/paper_mirror.py, 2026-11-12).

His decision after three losing days: "BOT we can do the entry and exit and profit target, we
have in chart 'entry, exit and target' use that entry the position and target 1,2,3 ... and stop
loss". Fake broker only; nothing here can reach Alpaca or Schwab.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from momx import chart_plan as cp
from momx import paper_mirror as pm

ET = pm.ET
NOW = datetime(2026, 11, 13, 10, 5, tzinfo=ET)          # a Friday, inside the first buy window
CONTRACT = "AAPL261120C00104000"
#: The signal's chart plan as momx/option_track.py stores it: stock 100, expected move 4.
PLAN = {"side": "CALL", "expiry": "2026-11-20", "strike": 104.0, "entry": 1.25, "entrySpot": 100.0, "expectedMove": 4.0,
        "delta": 0.2, "gamma": 0.03, "t1": 102.0, "t2": 104.0, "t3": 108.0, "t3FromOi": True,
        "chartExpiry": "2026-11-13", "chartStrike": 101.0, "chainAt": "2026-11-13T10:01:00-05:00"}
#: Bought at the ask $1.25 with the stock at 100: the chart puts the stop where the option is worth $0.63.
STOP = cp.stop_stock("CALL", 100.0, 1.25, 0.2, 0.03, 104.0, 0.63)


class FakeClient:
    def __init__(self, fill=True):
        self.credentials = SimpleNamespace(paper=True)
        self.fill = fill
        self.orders = {}
        self.calls = []

    def submit_option_limit_order(self, symbol, qty, limit, cid, intent):
        self.calls.append(("limit", symbol, qty, limit, intent))
        self.orders[cid] = SimpleNamespace(id=cid, status="filled" if self.fill else "new",
                                           filled_avg_price=limit if self.fill else None,
                                           filled_qty=qty if self.fill else 0)

    def get_order_by_client_id(self, cid):
        return self.orders[cid]

    def cancel_order_by_id(self, oid):
        self.orders[oid].status = "canceled"

    def sells(self):
        return [c for c in self.calls if c[4] == "sell_to_close"]


class Book:
    def __init__(self, trades):
        self._t = trades

    def trades(self):
        return [dict(t) for t in self._t]


def _trade(**kw):
    t = {"rule": "level2", "symbol": "AAPL", "at": (NOW - timedelta(minutes=2)).isoformat(),
         "entryAt": (NOW - timedelta(minutes=2)).isoformat(), "contract": CONTRACT, "strike": 104.0, "delta": 0.2,
         "status": "open", "entry": 1.20, "virtualOnly": True, "spot": 100.0,
         "lastAt": (NOW - timedelta(seconds=5)).isoformat(), "lastBid": 1.15, "lastAsk": 1.25,
         "stockLast": 100.0, "stockLastAt": (NOW - timedelta(seconds=5)).isoformat(), "plan": dict(PLAN)}
    t.update(kw)
    return t


def _mirror(tmp_path, trades, client, **kw):
    return pm.PaperMirror({"bull": Book(trades)}, tmp_path, client_factory=lambda: client, off=lambda: False,
                          jev_cut=None, **kw)


def _bought(tmp_path, client=None, **trade):
    """Three contracts bought at the ask $1.25 and filled."""
    c = client or FakeClient()
    trades = [_trade(**trade)]
    m = _mirror(tmp_path, trades, c)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=10))
    return m, trades, c


def _move(trades, seconds, stock=None, bid=None, ask=None):
    """The Bot sees a new stock price and option quote `seconds` after NOW (fresh for the next tick)."""
    at = (NOW + timedelta(seconds=seconds)).isoformat()
    trades[0].update(lastAt=at)
    if stock is not None:
        trades[0].update(stockLast=stock, stockLastAt=at)
    if bid is not None:
        trades[0].update(lastBid=bid, lastAsk=ask)


def test_the_order_goes_out_at_the_ask_with_the_charts_stop_and_three_targets(tmp_path):
    m, _, c = _bought(tmp_path)
    assert c.calls == [("limit", CONTRACT, 3, 1.25, "buy_to_open")]
    row = m.status()["rows"][0]
    assert row["status"] == "open" and row["exitPlan"] == "chart" and row["buyFill"] == 1.25
    assert row["entryAsk"] == 1.25 and row["entryBid"] == 1.15 and row["entryStock"] == 100.0
    assert (row["plan"]["t1"], row["plan"]["t2"], row["plan"]["t3"]) == (102.0, 104.0, 108.0)
    assert row["plan"]["delta"] == 0.2 and row["plan"]["gamma"] == 0.03 and row["plan"]["chartStrike"] == 101.0
    assert row["stopPremium"] == 0.63 and row["stopStock"] == STOP and row["stopStock0"] == STOP and STOP < 100.0
    assert row["planSplit"] == [1, 1, 1] and "targetStrike" not in row


def test_the_stock_trading_through_the_stop_sells_everything_at_once_at_the_bid(tmp_path):
    m, trades, c = _bought(tmp_path)
    _move(trades, 15, stock=STOP + 0.01, bid=0.70, ask=0.80)            # a cent above the stop: held
    m.tick(NOW + timedelta(seconds=20))
    assert c.sells() == []
    _move(trades, 25, stock=STOP - 0.01, bid=0.60, ask=0.70)            # through it: this very tick
    m.tick(NOW + timedelta(seconds=30))
    assert c.sells() == [("limit", CONTRACT, 3, 0.60, "sell_to_close")]     # all three, at the bid
    m.tick(NOW + timedelta(seconds=40))
    row = m.status()["rows"][0]
    assert row["status"] == "closed" and row["sells"][0]["why"] == f"Stop: stock traded through {STOP:.2f}"
    assert row["pl"] == round((0.60 - 1.25) * 300, 2)


def test_t1_t2_t3_take_one_contract_each_and_move_the_stop(tmp_path):
    m, trades, c = _bought(tmp_path)
    _move(trades, 15, stock=101.99, bid=1.50, ask=1.60)                 # a cent short of T1
    m.tick(NOW + timedelta(seconds=20))
    assert c.sells() == []
    _move(trades, 25, stock=102.0, bid=1.70, ask=1.80)                  # T1
    m.tick(NOW + timedelta(seconds=30))
    assert c.sells()[-1] == ("limit", CONTRACT, 1, 1.75, "sell_to_close")   # one lot, at the mid
    m.tick(NOW + timedelta(seconds=40))
    row = m.status()["rows"][0]
    assert row["held"] == 2 and row["sells"][0]["why"] == "T1 hit: sold 1, stop to break-even"
    assert row["stopStock"] == 100.0 and row["stopKind"] == "break-even" and row["stopStock0"] == STOP
    assert [h["target"] for h in row["hits"]] == ["T1"] and row["hits"][0]["stock"] == 102.0
    _move(trades, 45, stock=104.2, bid=2.40, ask=2.60)                  # T2
    m.tick(NOW + timedelta(seconds=50))
    assert c.sells()[-1] == ("limit", CONTRACT, 1, 2.5, "sell_to_close")
    m.tick(NOW + timedelta(seconds=60))
    row = m.status()["rows"][0]
    assert row["held"] == 1 and row["sells"][1]["why"] == "T2 hit: sold 1, stop to T1"
    assert row["stopStock"] == 102.0 and row["stopKind"] == "T1"
    _move(trades, 65, stock=108.0, bid=4.90, ask=5.10)                  # T3
    m.tick(NOW + timedelta(seconds=70))
    assert c.sells()[-1] == ("limit", CONTRACT, 1, 5.0, "sell_to_close")
    m.tick(NOW + timedelta(seconds=80))
    row = m.status()["rows"][0]
    assert row["status"] == "closed" and row["sells"][2]["why"] == "T3 hit: sold the last 1"
    assert [h["target"] for h in row["hits"]] == ["T1", "T2", "T3"] and all(h["at"] for h in row["hits"])
    assert row["pl"] == round((1.75 + 2.5 + 5.0 - 3 * 1.25) * 100, 2)


def test_after_t1_the_rest_is_stopped_at_break_even_on_the_stock(tmp_path):
    m, trades, c = _bought(tmp_path)
    _move(trades, 15, stock=102.3, bid=1.70, ask=1.80)
    m.tick(NOW + timedelta(seconds=20))
    m.tick(NOW + timedelta(seconds=30))
    _move(trades, 35, stock=100.4, bid=1.20, ask=1.30)                  # above the entry price: held
    m.tick(NOW + timedelta(seconds=40))
    assert len(c.sells()) == 1
    _move(trades, 45, stock=100.0, bid=1.10, ask=1.20)                  # back to the stock price at entry
    m.tick(NOW + timedelta(seconds=50))
    assert c.sells()[-1] == ("limit", CONTRACT, 2, 1.10, "sell_to_close")   # the rest, at the bid
    m.tick(NOW + timedelta(seconds=60))
    assert m.status()["rows"][0]["sells"][-1]["why"] == "Stop at break-even: stock traded through 100.00"


def test_after_t2_the_last_contract_is_stopped_at_t1(tmp_path):
    m, trades, c = _bought(tmp_path)
    _move(trades, 15, stock=104.5, bid=2.40, ask=2.60)                  # straight through T1 and T2
    m.tick(NOW + timedelta(seconds=20))
    assert c.sells()[-1] == ("limit", CONTRACT, 2, 2.5, "sell_to_close")    # both lots in one sale
    m.tick(NOW + timedelta(seconds=30))
    row = m.status()["rows"][0]
    assert row["held"] == 1 and row["sells"][0]["why"] == "T2 hit: sold 2, stop to T1"
    assert [h["target"] for h in row["hits"]] == ["T1", "T2"] and row["stopStock"] == 102.0
    _move(trades, 35, stock=101.9, bid=1.60, ask=1.70)
    m.tick(NOW + timedelta(seconds=40))
    assert c.sells()[-1] == ("limit", CONTRACT, 1, 1.60, "sell_to_close")
    m.tick(NOW + timedelta(seconds=50))
    assert m.status()["rows"][0]["sells"][-1]["why"] == "Stop at T1: stock traded through 102.00"


def test_two_contracts_sell_at_t1_and_t2_and_one_contract_sells_at_t2(tmp_path):
    m, trades, c = _bought(tmp_path / "two")
    row = next(iter(m._rows.values()))
    row.update(held=2, buyFilledQty=2, planSplit=cp.split_contracts(2))      # only two filled
    _move(trades, 15, stock=102.0, bid=1.70, ask=1.80)
    m.tick(NOW + timedelta(seconds=20))
    assert c.sells()[-1][2] == 1
    m.tick(NOW + timedelta(seconds=30))
    _move(trades, 35, stock=104.0, bid=2.40, ask=2.60)
    m.tick(NOW + timedelta(seconds=40))
    assert c.sells()[-1][2] == 1
    m.tick(NOW + timedelta(seconds=50))
    row = m.status()["rows"][0]
    assert row["status"] == "closed" and row["sells"][-1]["why"] == "T2 hit: sold the last 1"

    m1, trades1, c1 = _bought(tmp_path / "one")
    row = next(iter(m1._rows.values()))
    row.update(held=1, buyFilledQty=1, planSplit=cp.split_contracts(1))      # only one filled
    _move(trades1, 15, stock=102.0, bid=1.70, ask=1.80)
    m1.tick(NOW + timedelta(seconds=20))
    assert c1.sells() == []                                                  # T1: nothing to sell, the stop still moves
    row = m1.status()["rows"][0]
    assert row["note"] == "T1 hit: stop to break-even" and row["stopStock"] == 100.0 and row["held"] == 1
    _move(trades1, 25, stock=104.0, bid=2.40, ask=2.60)
    m1.tick(NOW + timedelta(seconds=30))
    assert c1.sells() == [("limit", CONTRACT, 1, 2.5, "sell_to_close")]
    m1.tick(NOW + timedelta(seconds=40))
    assert m1.status()["rows"][0]["sells"][0]["why"] == "T2 hit: sold the last 1"


def test_twelve_cheap_contracts_come_off_in_thirds(tmp_path):
    m, trades, c = _bought(tmp_path, lastBid=0.23, lastAsk=0.25)
    row = m.status()["rows"][0]
    assert row["qty"] == 12 and row["planSplit"] == [4, 4, 4]
    _move(trades, 15, stock=102.0, bid=0.39, ask=0.41)
    m.tick(NOW + timedelta(seconds=20))
    assert c.sells()[-1] == ("limit", CONTRACT, 4, 0.4, "sell_to_close")


def test_the_option_backstop_fires_on_the_first_tick_with_a_fresh_quote(tmp_path):
    """The stock has not reached the stop price, but the option itself is down 50% on a quote
    under 15 s old: sold at once, no second tick (the older plan waited for two)."""
    m, trades, c = _bought(tmp_path)
    _move(trades, 15, stock=99.5, bid=0.58, ask=0.64)                   # mid 0.61 <= 0.625, quote 5 s old
    m.tick(NOW + timedelta(seconds=20))
    assert c.sells() == [("limit", CONTRACT, 3, 0.58, "sell_to_close")]
    m.tick(NOW + timedelta(seconds=30))
    assert m.status()["rows"][0]["sells"][0]["why"] == pm.BACKSTOP_WHY == "Stop: option down 50% (backstop)"


def test_the_option_backstop_ignores_an_old_quote_and_a_small_loss(tmp_path):
    m, trades, c = _bought(tmp_path / "old")
    _move(trades, 15, stock=99.5, bid=0.58, ask=0.64)
    m.tick(NOW + timedelta(seconds=15 + pm.STOP_QUOTE_SECONDS + 1))     # the same quote, now 16 s old
    assert c.sells() == []
    m2, trades2, c2 = _bought(tmp_path / "small")
    _move(trades2, 15, stock=99.5, bid=0.62, ask=0.66)                  # mid 0.64: -49%
    m2.tick(NOW + timedelta(seconds=20))
    assert c2.sells() == []


def test_an_old_stock_price_never_stops_or_takes_a_target(tmp_path):
    m, trades, c = _bought(tmp_path)
    stale = (NOW - timedelta(seconds=pm.STOCK_FRESH_SECONDS + 60)).isoformat()
    trades[0].update(stockLast=STOP - 1, stockLastAt=stale, lastAt=(NOW + timedelta(seconds=15)).isoformat())
    m.tick(NOW + timedelta(seconds=20))
    trades[0].update(stockLast=109.0, stockLastAt=stale, lastAt=(NOW + timedelta(seconds=25)).isoformat())
    m.tick(NOW + timedelta(seconds=30))
    assert c.sells() == [] and m.status()["rows"][0]["status"] == "open"


def test_no_order_goes_out_before_the_plan_and_the_plan_that_arrives_is_used(tmp_path):
    """Was test_a_plan_that_arrives_after_the_order_is_attached_then, which let the order go out
    BEFORE the plan (a position with no stop and no targets until the plan came). 2026-11-13
    review: a paper order never goes out without a chart plan. No plan = no order; once the Bot
    has it (while the signal is fresh) the order goes out with the stop and the targets."""
    m, trades, c = _bought(tmp_path, plan=None)
    assert c.calls == [] and m.status()["rows"] == []
    trades[0]["plan"] = dict(PLAN)
    _move(trades, 25, stock=100.2, bid=1.15, ask=1.25)
    m.tick(NOW + timedelta(seconds=30))
    m.tick(NOW + timedelta(seconds=40))
    row = m.status()["rows"][0]
    assert len(c.calls) == 1 and row["status"] == "open" and row["exitPlan"] == "chart"
    assert row["plan"]["t2"] is not None and row["stopStock"] is not None and row["stopStock"] < 100.2 and c.sells() == []


def test_a_plan_for_another_contract_means_no_order(tmp_path):
    """Was test_a_plan_for_another_contract_is_not_used (the order went out with no plan). A plan
    for another strike is not a plan for this contract: no stop for it, so no order."""
    m, _, c = _bought(tmp_path, plan={**PLAN, "strike": 105.0})
    assert c.calls == [] and m.status()["rows"] == []


class _SlowCancelClient(FakeClient):
    """Alpaca: a cancel is accepted, the order shows pending_cancel, and is cancelled a moment later."""
    def cancel_order_by_id(self, oid):
        self.orders[oid].status = "pending_cancel"

    def settle(self):
        for o in self.orders.values():
            if o.status == "pending_cancel":
                o.status = "canceled"


def test_a_stop_seen_while_a_target_sale_is_working_is_remembered_and_sells_everything(tmp_path):
    """2026-11-13 review (scratchpad rv14/latch.py). T1 lot working at the mid; the stock trades
    through the break-even stop; the cancel is pending for a tick; 10 s later the stock is 5 cents
    back above the stop. Before the latch the stop was forgotten: the T1 lot was re-priced at the
    mid and two contracts were held with no stop. Now row["stopDue"] remembers it and everything
    is sold at the bid as soon as the working order is out of the way."""
    c = _SlowCancelClient()
    m, trades, c = _bought(tmp_path, client=c)
    c.fill = False                                                      # sales rest from here on
    _move(trades, 15, stock=102.1, bid=1.70, ask=1.80)
    m.tick(NOW + timedelta(seconds=20))                                 # T1: one lot at the mid, stop -> break-even
    row = m.status()["rows"][0]
    assert c.sells() == [("limit", CONTRACT, 1, 1.75, "sell_to_close")] and row["stopStock"] == 100.0
    assert row["status"] == "selling" and not row.get("stopDue")
    _move(trades, 25, stock=99.60, bid=1.00, ask=1.10)
    m.tick(NOW + timedelta(seconds=30))                                 # through the stop; the cancel is pending
    row = m.status()["rows"][0]
    assert row["status"] == "selling" and len(c.sells()) == 1
    assert row["stopDue"]["why"] == "Stop at break-even: stock traded through 100.00" and row["stopDue"]["stock"] == 99.6
    c.settle()
    _move(trades, 35, stock=100.05, bid=1.10, ask=1.20)                 # the stock is back above the stop
    m.tick(NOW + timedelta(seconds=40))
    row = m.status()["rows"][0]
    assert c.sells()[-1] == ("limit", CONTRACT, 3, 1.10, "sell_to_close")   # everything, at the bid
    assert row["sellWhy"] == "Stop at break-even: stock traded through 100.00" and row["sellUrgent"] is True
    # It stays a stop sale while it is re-priced: never back to the one-lot target sale at the mid.
    for k in range(5, 20):
        _move(trades, 10 * k - 5, stock=100.05, bid=1.10, ask=1.20)
        c.settle()
        m.tick(NOW + timedelta(seconds=10 * k))
    assert all(s[2] == 3 and s[3] == 1.10 for s in c.sells()[1:])
    c.fill = True
    _move(trades, 205, stock=100.05, bid=1.10, ask=1.20)
    for k in range(21, 30):
        c.settle()
        m.tick(NOW + timedelta(seconds=10 * k))
        _move(trades, 10 * k + 5, stock=100.05, bid=1.10, ask=1.20)
    row = m.status()["rows"][0]
    assert row["status"] == "closed" and row["held"] == 0 and row["sells"][-1]["why"].startswith("Stop at break-even")


def test_the_options_own_backstop_is_latched_too(tmp_path):
    """The option at -50% on a fresh quote while a target lot is working, then back above the
    line before the cancel goes through: still sold in full."""
    c = _SlowCancelClient()
    m, trades, c = _bought(tmp_path, client=c, plan={**PLAN, "delta": None, "gamma": None})
    c.fill = False
    _move(trades, 15, stock=102.1, bid=1.70, ask=1.80)
    m.tick(NOW + timedelta(seconds=20))
    assert len(c.sells()) == 1
    _move(trades, 25, stock=101.0, bid=0.55, ask=0.65)                  # the option itself is down 50%
    m.tick(NOW + timedelta(seconds=30))
    assert m.status()["rows"][0]["stopDue"]["why"] == pm.BACKSTOP_WHY
    c.settle()
    _move(trades, 35, stock=101.5, bid=1.00, ask=1.10)
    m.tick(NOW + timedelta(seconds=40))
    assert c.sells()[-1] == ("limit", CONTRACT, 3, 1.00, "sell_to_close")
    assert m.status()["rows"][0]["sellWhy"] == pm.BACKSTOP_WHY


def test_a_signal_the_paper_clock_would_buy_always_has_the_bots_plan_clock(tmp_path):
    """ONE clock (2026-11-13 review, scratchpad rv14/scen.py). For every minute of the day and
    every fresh age: when the paper bot's own time rules would buy a signal (a buy window is open
    and the signal was opened at most 10 minutes ago), the Bot's rule gives that signal a plan.
    The reviewer's case: signal 12:49:55, opened 12:50:20, tick 13:00:05."""
    from momx import option_track as ot
    day = NOW.replace(hour=0, minute=0, second=0)
    odd = []
    for minute in range(9 * 60 + 30, 16 * 60):
        for second in (0, 5, 59):
            now = day + timedelta(minutes=minute, seconds=second)
            for age in (0, 1, 60, 299, 599, 600):
                for lag in (0, 25, 120):
                    opened = now - timedelta(seconds=age)
                    fired = opened - timedelta(seconds=lag)
                    t = {"rule": "level2", "status": "open", "contract": "X", "at": fired.isoformat(), "entryAt": opened.isoformat()}
                    buys = pm.entry_open(now) and pm.eligible(t, now.timestamp(), None)
                    if buys and ot.plan_window_note(t["at"], t["entryAt"]) is not None:
                        odd.append((now.time(), fired.time(), opened.time()))
    assert odd == []
    d = lambda h, mi, s: day + timedelta(hours=h, minutes=mi, seconds=s)
    assert ot.plan_window_note(d(12, 49, 55).isoformat(), d(12, 50, 20).isoformat()) is None
    assert ot.plan_window_note(d(12, 49, 55).isoformat()) is not None            # on the signal time alone it had none
    # And the other way round: whatever the clocks say, no plan on the trade = no order.
    now = d(13, 0, 5)
    c = FakeClient()
    t = _trade(at=d(12, 49, 55).isoformat(), entryAt=d(12, 50, 20).isoformat(), plan=None,
               planNote="Not ordered: outside the buy windows (9:30-11:00, 1:00-3:30)",
               lastAt=(now - timedelta(seconds=5)).isoformat(), stockLastAt=(now - timedelta(seconds=5)).isoformat())
    m = _mirror(tmp_path, [t], c)
    m.tick(now)
    assert c.calls == []


def test_without_targets_only_the_stop_and_the_other_exits_apply(tmp_path):
    """No expected move in the chain: the chart shows no targets, so the bot invents none."""
    m, trades, c = _bought(tmp_path, plan={**PLAN, "expectedMove": None, "t1": None, "t2": None, "t3": None})
    _move(trades, 15, stock=120.0, bid=9.0, ask=9.2)
    m.tick(NOW + timedelta(seconds=20))
    assert c.sells() == [] and not m.status()["rows"][0].get("hits")


def test_the_bots_exit_is_said_in_plain_words_and_sells_at_the_mid(tmp_path):
    m, trades, c = _bought(tmp_path)
    _move(trades, 15, stock=100.5, bid=1.30, ask=1.40)
    trades[0].update(exitPrice=1.35, exitBid=1.30, exitAsk=1.40, exitReason="below EMA21")
    m.tick(NOW + timedelta(seconds=20))
    assert c.sells() == [("limit", CONTRACT, 3, 1.35, "sell_to_close")]
    m.tick(NOW + timedelta(seconds=30))
    assert m.status()["rows"][0]["sells"][0]["why"] == "5-minute candle closed below the 21 EMA"


def test_the_1552_close_still_sells_what_is_left(tmp_path):
    m, trades, c = _bought(tmp_path)
    late = NOW.replace(hour=15, minute=52)
    trades[0].update(lastAt=(late - timedelta(seconds=30)).isoformat(), lastBid=1.30, lastAsk=1.40,
                     stockLast=100.5, stockLastAt=(late - timedelta(seconds=30)).isoformat())
    m.tick(late)
    assert c.sells() == [("limit", CONTRACT, 3, 1.35, "sell_to_close")]
    m.tick(late + timedelta(seconds=10))
    assert m.status()["rows"][0]["sells"][0]["why"] == "15:52 close"


def test_a_target_lot_still_unfilled_is_replaced_when_the_stop_is_hit(tmp_path):
    """A target sale that has not filled must not hide the stop: everything left is sold at the bid."""
    m, trades, c = _bought(tmp_path)
    c.fill = False                                                       # sells now sit unfilled
    _move(trades, 15, stock=102.0, bid=1.70, ask=1.80)
    m.tick(NOW + timedelta(seconds=20))
    assert c.sells()[-1][2] == 1 and m.status()["rows"][0]["status"] == "selling"
    late = 20 + pm.SELL_TTL_SECONDS + 5
    _move(trades, late - 2, stock=99.9, bid=1.00, ask=1.10)             # reverses through break-even
    m.tick(NOW + timedelta(seconds=late))                               # the T1 lot is cancelled, and in the same tick...
    assert c.sells()[-1] == ("limit", CONTRACT, 3, 1.00, "sell_to_close")
    row = m.status()["rows"][0]
    assert row["status"] == "selling" and row["sellWhy"] == "Stop at break-even: stock traded through 100.00"


def test_a_stop_sale_that_does_not_fill_is_sent_again_at_the_new_bid(tmp_path):
    m, trades, c = _bought(tmp_path)
    c.fill = False
    _move(trades, 15, stock=STOP - 0.5, bid=0.60, ask=0.70)
    m.tick(NOW + timedelta(seconds=20))
    assert c.sells()[-1][3] == 0.60
    late = 20 + pm.SELL_TTL_SECONDS + 5
    _move(trades, late - 2, stock=STOP - 0.8, bid=0.45, ask=0.55)
    m.tick(NOW + timedelta(seconds=late))
    assert c.sells()[-1] == ("limit", CONTRACT, 3, 0.45, "sell_to_close")    # the bid again, never a market order
    assert all(call[0] == "limit" for call in c.calls)


class Feed:
    """The /api/option-quotes shape: option contracts and stocks in one batched call."""

    def __init__(self):
        self.option, self.stock, self.asof, self.asked = (1.15, 1.25), 100.0, NOW.timestamp(), []

    def __call__(self, symbols):
        self.asked.append(list(symbols))
        out = {}
        for s in symbols:
            if s == CONTRACT:
                out[s] = {"bid": self.option[0], "ask": self.option[1], "mid": sum(self.option) / 2}
            elif self.stock is not None:
                out[s] = {"bid": self.stock - 0.01, "ask": self.stock + 0.01, "mid": self.stock, "last": self.stock}
        return {"asOf": self.asof, "quotes": out}

    def move(self, seconds, stock, bid, ask):
        self.stock, self.option, self.asof = stock, (bid, ask), (NOW + timedelta(seconds=seconds)).timestamp()


def test_the_live_stock_price_is_read_every_tick_with_the_option_quotes(tmp_path):
    """The stop is checked on the stock's own live quote each tick, not on the scanner's last board price."""
    c, feed = FakeClient(), Feed()
    trades = [_trade(stockLast=None, stockLastAt=None)]                 # the Bot has no stock price at all
    m = _mirror(tmp_path, trades, c, quotes_fetch=feed)
    m.tick(NOW)
    assert feed.asked == [[CONTRACT, "AAPL"]] and c.calls == [("limit", CONTRACT, 3, 1.25, "buy_to_open")]
    m.tick(NOW + timedelta(seconds=10))
    row = m.status()["rows"][0]
    assert row["entryStock"] == 100.0 and row["stopStock"] == STOP
    feed.move(20, 101.0, 1.4, 1.5)
    m.tick(NOW + timedelta(seconds=20))
    assert feed.asked[-1] == [CONTRACT, "AAPL"] and m.status()["rows"][0]["stockLast"] == 101.0 and c.sells() == []
    feed.move(30, STOP - 0.02, 0.66, 0.74)
    m.tick(NOW + timedelta(seconds=30))
    assert c.sells() == [("limit", CONTRACT, 3, 0.66, "sell_to_close")]
    m.tick(NOW + timedelta(seconds=40))
    assert m.status()["rows"][0]["sells"][0]["why"].startswith("Stop: stock traded through")


def test_a_live_feed_with_no_stock_quote_falls_back_to_the_bots_board_price(tmp_path):
    c, feed = FakeClient(), Feed()
    trades = [_trade()]
    m = _mirror(tmp_path, trades, c, quotes_fetch=feed)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=10))
    feed.stock = None
    feed.move(20, None, 1.7, 1.8)
    _move(trades, 15, stock=102.5)
    m.tick(NOW + timedelta(seconds=20))
    assert c.sells() == [("limit", CONTRACT, 1, 1.75, "sell_to_close")]


def test_a_put_mirrors_the_plan(tmp_path):
    plan = {"side": "PUT", "expiry": "2026-11-20", "strike": 96.0, "entrySpot": 100.0, "expectedMove": 4.0,
            "delta": -0.2, "gamma": 0.03, "t1": 98.0, "t2": 96.0, "t3": 92.0}
    c = FakeClient()
    trades = [_trade(contract="AAPL261120P00096000", strike=96.0, delta=-0.2, plan=plan)]
    m = pm.PaperMirror({"bear": Book(trades)}, tmp_path, client_factory=lambda: c, off=lambda: False, jev_cut=None)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=10))
    row = m.status()["rows"][0]
    stop = cp.stop_stock("PUT", 100.0, 1.25, -0.2, 0.03, 96.0, 0.63)
    assert row["stopStock"] == stop and stop > 100.0
    _move(trades, 15, stock=98.0, bid=1.70, ask=1.80)                   # T1 is below for a put
    m.tick(NOW + timedelta(seconds=20))
    assert c.sells()[-1][2] == 1
    m.tick(NOW + timedelta(seconds=30))
    _move(trades, 35, stock=100.0, bid=1.20, ask=1.30)                  # back up to the entry price
    m.tick(NOW + timedelta(seconds=40))
    assert c.sells()[-1] == ("limit", "AAPL261120P00096000", 2, 1.20, "sell_to_close")


def test_the_rules_text_describes_the_chart_plan(tmp_path):
    rules = _mirror(tmp_path, [], FakeClient()).status()["rules"]
    assert "limit at the live ask" in rules["orders"] and "stop sells: limit at the bid" in rules["orders"]
    # Was: "10:00-11:00" and "no buys 9:30-10:00" (the 10:00 rule, removed 2026-11-13).
    assert "9:30-11:00 AM and 1:00-3:30 PM" in rules["entryTimes"] and "10:00" not in rules["entryTimes"]
    assert "no chart plan" in rules["entryTimes"] and rules["plan"] == "chart" == pm.EXIT_PLAN
    for words in ("T1 = half the expected move", "T2 = the full expected move", "open-interest strike", "break-even",
                  "stock trades through", "21 EMA", "15:52"):
        assert words in rules["exitPlan"], words
    assert "+100%" not in rules["exitPlan"] and "older exit plan" in rules["planSince"]


def test_a_restart_keeps_the_plan_the_stop_and_the_hits(tmp_path):
    m, trades, c = _bought(tmp_path)
    _move(trades, 15, stock=102.0, bid=1.70, ask=1.80)
    m.tick(NOW + timedelta(seconds=20))
    m.tick(NOW + timedelta(seconds=30))
    again = _mirror(tmp_path, trades, c)
    again._load(NOW.date().isoformat())
    row = next(iter(again._rows.values()))
    assert row["exitPlan"] == "chart" and row["stopStock"] == 100.0 and [h["target"] for h in row["hits"]] == ["T1"]
    _move(trades, 35, stock=104.0, bid=2.40, ask=2.60)
    again.tick(NOW + timedelta(seconds=40))
    assert c.sells()[-1] == ("limit", CONTRACT, 1, 2.5, "sell_to_close")     # T2 next, T1 is not taken twice


# ---------------------------------------------------------------------------
# 2026-11-13 review fixes. The fake broker above accepts a repeated order id and never
# rejects; Alpaca does neither, so these use brokers that behave like it.

class StrictClient(FakeClient):
    """Like Alpaca: a client order id can be used once (422 "client_order_id must be unique")."""

    def __init__(self, fill=True):
        super().__init__(fill)
        self.refused = []

    def submit_option_limit_order(self, symbol, qty, limit, cid, intent):
        if cid in self.orders:
            self.refused.append((cid, qty, limit, intent))
            raise RuntimeError('422 {"code":40010001,"message":"client_order_id must be unique"}')
        super().submit_option_limit_order(symbol, qty, limit, cid, intent)


class RejectingClient(StrictClient):
    """A broker that takes the sale and then REJECTS it (status "rejected", nothing filled)."""

    def __init__(self):
        super().__init__(True)
        self.reject = True

    def submit_option_limit_order(self, symbol, qty, limit, cid, intent):
        super().submit_option_limit_order(symbol, qty, limit, cid, intent)
        if intent == "sell_to_close" and self.reject:
            self.orders[cid].status, self.orders[cid].filled_qty, self.orders[cid].filled_avg_price = "rejected", 0, None


def test_a_stop_after_a_repriced_target_lot_reaches_the_broker_on_the_same_tick(tmp_path):
    """The T1 lot did not fill, was cancelled after 60 s and re-sent. The stock then trades through
    break-even. The stop's order id must be a NEW one: with the try counter reset it re-used the
    cancelled lot's id, Alpaca refused it, and the stop went out a tick later for every re-price."""
    c = StrictClient()
    m, trades, _ = _bought(tmp_path, client=c)
    c.fill = False
    _move(trades, 15, stock=102.0, bid=1.70, ask=1.80)
    m.tick(NOW + timedelta(seconds=20))                                  # the T1 lot, at the mid
    late = 20 + pm.SELL_TTL_SECONDS + 5
    _move(trades, late - 2, stock=101.0, bid=1.50, ask=1.60)             # still above break-even
    m.tick(NOW + timedelta(seconds=late))                                # cancelled and re-priced
    assert [s[2:4] for s in c.sells()] == [(1, 1.75), (1, 1.55)]
    _move(trades, late + 8, stock=99.9, bid=1.00, ask=1.10)              # through break-even
    m.tick(NOW + timedelta(seconds=late + 10))
    assert c.refused == []
    assert c.sells()[-1] == ("limit", CONTRACT, 3, 1.00, "sell_to_close")   # all three at the bid, THIS tick
    assert len(set(c.orders)) == len(c.calls)                             # every order had its own id
    assert m.status()["rows"][0]["sellWhy"] == "Stop at break-even: stock traded through 100.00"


def test_the_backstop_after_many_mid_reprices_reaches_the_broker_on_the_same_tick(tmp_path):
    """The 21-EMA exit re-priced at the mid 30 times without a fill, then the option collapses.
    Before the fix: 30 refused order ids, the bid-priced stop reached the broker 310 s late."""
    c = StrictClient()
    m, trades, _ = _bought(tmp_path, client=c)
    c.fill = False
    trades[0].update(exitPrice=1.2, exitReason="below EMA21", status="closed")
    t = 10
    while len(c.sells()) < 30:
        t += 10
        _move(trades, t - 5, stock=100.0, bid=1.00, ask=1.40)
        m.tick(NOW + timedelta(seconds=t))
    assert c.refused == [] and all(s[3] == 1.20 for s in c.sells())
    _move(trades, t + 5, stock=100.0, bid=0.30, ask=0.40)                # 1.25 -> 0.35: far past -50%
    m.tick(NOW + timedelta(seconds=t + 10))
    assert c.refused == []
    assert c.sells()[-1] == ("limit", CONTRACT, 3, 0.30, "sell_to_close")
    assert m.status()["rows"][0]["sellWhy"] == pm.BACKSTOP_WHY


def test_the_stop_is_checked_while_a_target_lot_is_still_working(tmp_path):
    """T1 lot sent at the mid and not filled; on the very next tick the stock is through the stop.
    The lot is cancelled and everything is sold at the bid in that tick, not after the 60 s wait."""
    c = StrictClient()
    m, trades, _ = _bought(tmp_path, client=c)
    c.fill = False
    _move(trades, 15, stock=102.0, bid=1.70, ask=1.80)
    m.tick(NOW + timedelta(seconds=20))
    assert m.status()["rows"][0]["status"] == "selling" and c.sells()[-1][2] == 1
    lot = m.status()["rows"][0]["sellId"]
    _move(trades, 25, stock=99.9, bid=1.00, ask=1.10)
    m.tick(NOW + timedelta(seconds=30))                                  # 10 s later, well inside the 60 s
    assert c.orders[lot].status == "canceled" and c.refused == []
    assert c.sells()[-1] == ("limit", CONTRACT, 3, 1.00, "sell_to_close")
    row = m.status()["rows"][0]
    assert row["status"] == "selling" and row["sellUrgent"] is True
    assert row["sellWhy"] == "Stop at break-even: stock traded through 100.00"
    # The option's own -50% does the same while the 21-EMA sale sits at the mid.
    c2 = StrictClient()
    m2, trades2, _ = _bought(tmp_path / "backstop", client=c2)
    c2.fill = False
    trades2[0].update(exitPrice=1.2, exitReason="below EMA21", status="closed")
    _move(trades2, 15, stock=100.0, bid=1.00, ask=1.40)
    m2.tick(NOW + timedelta(seconds=20))
    assert c2.sells() == [("limit", CONTRACT, 3, 1.20, "sell_to_close")]
    _move(trades2, 25, stock=100.0, bid=0.30, ask=0.40)
    m2.tick(NOW + timedelta(seconds=30))
    assert c2.sells()[-1] == ("limit", CONTRACT, 3, 0.30, "sell_to_close") and c2.refused == []


def test_a_target_lot_that_part_filled_is_booked_before_the_stop_sells_the_rest(tmp_path):
    c = StrictClient()
    m, trades, _ = _bought(tmp_path, client=c, lastBid=0.23, lastAsk=0.25)   # 12 contracts: lots of 4
    c.fill = False
    _move(trades, 15, stock=102.0, bid=0.39, ask=0.41)
    m.tick(NOW + timedelta(seconds=20))
    lot = c.orders[m.status()["rows"][0]["sellId"]]
    lot.filled_qty, lot.filled_avg_price = 2, 0.40                       # two of the four went
    _move(trades, 25, stock=99.9, bid=0.20, ask=0.22)
    m.tick(NOW + timedelta(seconds=30))
    row = m.status()["rows"][0]
    assert [(s["qty"], s["price"], s["why"]) for s in row["sells"]] == [(2, 0.40, "T1 hit: sold 4, stop to break-even")]
    assert row["held"] == 10 and c.sells()[-1] == ("limit", CONTRACT, 10, 0.20, "sell_to_close") and c.refused == []


def test_a_working_stop_sale_is_left_alone_and_a_healthy_position_keeps_its_lot(tmp_path):
    """Only a stop pulls a working sale: a lot whose stock is still above the stop waits its 60 s."""
    c = StrictClient()
    m, trades, _ = _bought(tmp_path, client=c)
    c.fill = False
    _move(trades, 15, stock=102.0, bid=1.70, ask=1.80)
    m.tick(NOW + timedelta(seconds=20))
    _move(trades, 25, stock=101.0, bid=1.50, ask=1.60)
    m.tick(NOW + timedelta(seconds=30))
    assert len(c.sells()) == 1 and m.status()["rows"][0]["status"] == "selling"


def test_a_sale_the_broker_rejects_is_not_sent_again_every_tick(tmp_path):
    """Before: a rejected sale became a brand-new order every 10 s (30 in 5 minutes, about 2,000
    a day) with no note. Now: once a minute, then every 30 minutes after five refusals in a row."""
    c = RejectingClient()
    m, trades, _ = _bought(tmp_path, client=c)
    t = 20
    for _ in range(30):                                                  # five minutes through the stop
        t += 10
        _move(trades, t - 5, stock=STOP - 1, bid=0.60, ask=0.70)
        m.tick(NOW + timedelta(seconds=t))
    assert len(c.sells()) == pm.SELL_REJECTS_MAX == 5 and c.refused == []
    row = m.status()["rows"][0]
    assert row["status"] == "exit_due" and row["held"] == 3 and row["sellRejects"] == 5
    assert row["note"] == "The broker refused this sale (5 times in a row; trying again every 30 minutes)"
    for _ in range(6 * 60):                                              # another hour
        t += 10
        _move(trades, t - 5, stock=STOP - 1, bid=0.60, ask=0.70)
        m.tick(NOW + timedelta(seconds=t))
    assert len(c.sells()) == 6                                           # one more in the hour, not 360
    assert all(call[0] == "limit" and call[3] == 0.60 for call in c.sells())
    # The broker takes sales again: the next try fills, the position closes, the count is gone.
    c.reject = False
    for _ in range(6 * 31):
        t += 10
        _move(trades, t - 5, stock=STOP - 1, bid=0.60, ask=0.70)
        m.tick(NOW + timedelta(seconds=t))
    row = m.status()["rows"][0]
    assert row["status"] == "closed" and len(c.sells()) == 7 and "sellRejects" not in row


def test_the_first_refusal_says_so_and_waits_a_minute(tmp_path):
    c = RejectingClient()
    m, trades, _ = _bought(tmp_path, client=c)
    _move(trades, 25, stock=STOP - 1, bid=0.60, ask=0.70)
    m.tick(NOW + timedelta(seconds=30))
    m.tick(NOW + timedelta(seconds=40))
    row = m.status()["rows"][0]
    assert row["note"] == pm.SELL_REFUSED_NOTE == "The broker refused this sale" and row["sellRejects"] == 1
    _move(trades, 85, stock=STOP - 1, bid=0.60, ask=0.70)
    m.tick(NOW + timedelta(seconds=90))                                  # 50 s after the refusal: not yet
    assert len(c.sells()) == 1
    _move(trades, 95, stock=STOP - 1, bid=0.55, ask=0.65)
    m.tick(NOW + timedelta(seconds=100))                                 # 60 s after: once more, at the new bid
    assert c.sells()[-1] == ("limit", CONTRACT, 3, 0.55, "sell_to_close") and len(c.sells()) == 2


def test_an_older_plan_position_whose_sale_is_rejected_is_bounded_too(tmp_path):
    c = RejectingClient()
    m, trades, _ = _bought(tmp_path, client=c)
    next(iter(m._rows.values())).pop("exitPlan")                         # opened under the older plan
    t = 20
    for _ in range(30):
        t += 10
        _move(trades, t - 5, stock=100.0, bid=0.30, ask=0.40)            # the option -50%
        m.tick(NOW + timedelta(seconds=t))
    m.tick(NOW + timedelta(seconds=t + 10))
    assert 1 <= len(c.sells()) <= pm.SELL_REJECTS_MAX
    assert m.status()["rows"][0]["note"].startswith("The broker refused this sale")


#: The open-interest strikes the Bot keeps on the plan (chart_plan.walls): [strike, open interest].
WALLS = [[100.0, 50.0], [102.0, 500.0], [104.0, 900.0], [105.0, 900.0], [106.0, 100.0], [108.0, 3000.0], [110.0, 9000.0]]


def test_targets_and_stop_start_from_the_stock_price_at_the_order_not_at_the_signal(tmp_path):
    """The signal fired with the stock at 100 (T1 102). The order went out minutes later with the
    stock already at 102.50. Before: bought at the ask, "T1 hit" on the first tick, stopped at
    break-even a cent later. Now entry, stop and T1-T3 all start from 102.50, as on the chart."""
    m, trades, c = _bought(tmp_path, plan={**PLAN, "walls": WALLS}, stockLast=102.5)
    row = m.status()["rows"][0]
    assert row["entryStock"] == 102.5 and row["plan"]["entrySpot"] == 102.5
    assert (row["plan"]["t1"], row["plan"]["t2"]) == (104.5, 106.5)       # half the move and the full move from 102.50
    assert row["plan"]["t3"] == 110.0 and row["plan"]["t3FromOi"] is True  # biggest open interest past 106.50, within two moves
    assert row["plan"]["signalSpot"] == 100.0 and row["plan"]["signalTargets"] == [102.0, 104.0, 108.0]
    assert "walls" not in row["plan"]
    assert row["stopStock"] == cp.stop_stock("CALL", 102.5, 1.25, 0.2, 0.03, 104.0, 0.63) < 102.5
    # The same numbers the chart's own code gives for this contract from 102.50.
    rows = [{"expiry": "2026-11-20", "side": "CALL", "strike": k, "open_interest": oi, "delta": 0.2, "gamma": 0.03}
            for k, oi in WALLS]
    chart = cp.build(rows, side="CALL", expiry="2026-11-20", strike=104.0, entry=1.25, spot=102.5,
                     expiry_expected_moves={"2026-11-20": 4.0})
    assert (chart["t1"], chart["t2"], chart["t3"], chart["stopStock"]) == (104.5, 106.5, 110.0, row["stopStock"])
    _move(trades, 15, stock=102.6, bid=1.30, ask=1.40)                    # past the SIGNAL's T1: nothing to sell
    m.tick(NOW + timedelta(seconds=20))
    _move(trades, 25, stock=102.4, bid=1.20, ask=1.30)                    # a dime under the entry: not a stop
    m.tick(NOW + timedelta(seconds=30))
    assert c.sells() == [] and not m.status()["rows"][0].get("hits")
    _move(trades, 35, stock=104.5, bid=1.90, ask=2.00)                    # the real T1
    m.tick(NOW + timedelta(seconds=40))
    assert c.sells() == [("limit", CONTRACT, 1, 1.95, "sell_to_close")]
    m.tick(NOW + timedelta(seconds=50))
    row = m.status()["rows"][0]
    assert row["stopStock"] == 102.5 and row["stopKind"] == "break-even" and row["hits"][0]["level"] == 104.5


def test_a_put_ordered_after_the_stock_fell_is_rebased_the_same_way(tmp_path):
    plan = {"side": "PUT", "expiry": "2026-11-20", "strike": 96.0, "entrySpot": 100.0, "expectedMove": 4.0, "delta": -0.2,
            "gamma": 0.03, "t1": 98.0, "t2": 96.0, "t3": 92.0, "t3FromOi": True, "walls": [[96.0, 400.0], [92.0, 700.0], [90.0, 9000.0]]}
    c = FakeClient()
    trades = [_trade(contract="AAPL261120P00096000", strike=96.0, delta=-0.2, plan=plan, stockLast=98.0)]
    m = pm.PaperMirror({"bear": Book(trades)}, tmp_path, client_factory=lambda: c, off=lambda: False, jev_cut=None)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=10))
    row = m.status()["rows"][0]
    assert (row["plan"]["t1"], row["plan"]["t2"], row["plan"]["t3"]) == (96.0, 94.0, 90.0) and row["stopStock"] > 98.0
    _move(trades, 15, stock=97.9, bid=1.30, ask=1.40)
    m.tick(NOW + timedelta(seconds=20))
    assert c.sells() == []


# ---------------------------------------------------------------------------
# 2026-11-13 second review. A SALE whose submit fails leaves no order at the broker: Alpaca
# answers most refusals at the submit itself (403 / 422), and a timeout may or may not have
# got through. Before, such a row stayed "selling" for good: no stop, no targets, no 15:52
# close (the live paper3 file had CLS stuck this way from 11-10 12:24, 89 tries).

class SubmitFailClient(FakeClient):
    """Like Alpaca: an unknown client id is "order not found" (40410000), and a sale can fail AT
    SUBMIT - `mode` "refuse" = the broker's own 403 answer (no order exists), "lost" = a network
    timeout that never got there, "late" = a timeout AFTER the broker took the order."""

    def __init__(self, fill=True):
        super().__init__(fill)
        self.mode, self.times = None, 0

    def submit_option_limit_order(self, symbol, qty, limit, cid, intent):
        if intent == "sell_to_close" and self.mode and self.times > 0:
            self.times -= 1
            if self.mode == "late":
                super().submit_option_limit_order(symbol, qty, limit, cid, intent)
                raise TimeoutError("HTTPSConnectionPool(host=paper-api.alpaca.markets): Read timed out.")
            self.calls.append(("limit", symbol, qty, limit, intent))
            if self.mode == "refuse":
                raise RuntimeError('403 {"code":40310000,"message":"account not eligible to trade uncovered option contracts"}')
            raise TimeoutError("HTTPSConnectionPool(host=paper-api.alpaca.markets): Read timed out.")
        if cid in self.orders:
            raise RuntimeError('422 {"code":40010001,"message":"client_order_id must be unique"}')
        super().submit_option_limit_order(symbol, qty, limit, cid, intent)

    def get_order_by_client_id(self, cid):
        if cid not in self.orders:
            raise RuntimeError('404 {"code":40410000,"message":"order not found for %s"}' % cid)
        return self.orders[cid]


def _run(m, trades, start, ticks, **quote):
    """`ticks` 10-second ticks after `start` seconds, the Bot's quote fresh on each. Returns the last second."""
    t = start
    for _ in range(ticks):
        t += 10
        _move(trades, t - 5, **quote)
        m.tick(NOW + timedelta(seconds=t))
    return t


def test_a_stop_sale_whose_submit_timed_out_is_sent_again_within_30_seconds(tmp_path):
    c = SubmitFailClient()
    m, trades, _ = _bought(tmp_path, client=c)
    c.mode, c.times = "lost", 1
    _run(m, trades, 20, 1, stock=STOP - 1, bid=0.60, ask=0.70)           # t=30: the stop sale, lost on the way
    row = m.status()["rows"][0]
    assert len(c.sells()) == 1 and row["status"] == "selling" and "sellError" not in row
    _run(m, trades, 30, 1, stock=STOP - 1, bid=0.58, ask=0.68)           # t=40: it may still appear: looked up, not re-sent
    assert len(c.sells()) == 1 and m.status()["rows"][0]["status"] == "selling"
    _run(m, trades, 40, 1, stock=STOP - 1, bid=0.55, ask=0.65)           # t=50: no record of it: sent again, at the bid
    assert c.sells()[-1] == ("limit", CONTRACT, 3, 0.55, "sell_to_close") and len(c.sells()) == 2
    _run(m, trades, 50, 1, stock=STOP - 1, bid=0.55, ask=0.65)
    row = m.status()["rows"][0]
    assert row["status"] == "closed" and row["held"] == 0 and row["sells"][0]["why"].startswith("Stop: stock traded through")
    assert row["pl"] == round((0.55 - 1.25) * 300, 2) and all(call[0] == "limit" for call in c.calls)


def test_a_submit_that_timed_out_but_reached_the_broker_is_not_sent_twice(tmp_path):
    c = SubmitFailClient()
    m, trades, _ = _bought(tmp_path, client=c)
    c.mode, c.times = "late", 1
    _run(m, trades, 20, 1, stock=STOP - 1, bid=0.60, ask=0.70)
    assert m.status()["rows"][0]["status"] == "selling" and len(c.sells()) == 1
    _run(m, trades, 30, 6, stock=STOP - 1, bid=0.60, ask=0.70)           # found by its id and booked: one sale only
    row = m.status()["rows"][0]
    assert row["status"] == "closed" and len(c.sells()) == 1 and row["sells"] and row["sells"][0]["qty"] == 3


def test_a_stop_sale_refused_at_submit_is_tried_again_but_not_every_tick(tmp_path):
    """The broker's own 403 at submit: no order exists. Tried again once a minute, and after
    five refusals in a row once every 30 minutes - never stuck, never a new order every tick."""
    c = SubmitFailClient()
    m, trades, _ = _bought(tmp_path, client=c)
    c.mode, c.times = "refuse", 10 ** 6
    t = _run(m, trades, 20, 30, stock=STOP - 1, bid=0.60, ask=0.70)      # five minutes below the stop
    assert len(c.sells()) == pm.SELL_REJECTS_MAX == 5
    row = m.status()["rows"][0]
    assert row["status"] == "exit_due" and row["held"] == 3 and row["sellRejects"] == 5 and row["sellUrgent"] is True
    assert row["note"].startswith("The broker refused this sale: ") and "trying again every 30 minutes" in row["note"]
    t = _run(m, trades, t, 6 * 60, stock=STOP - 1, bid=0.60, ask=0.70)   # another hour
    assert 6 <= len(c.sells()) <= 7                                      # one per 30 minutes, not 360
    assert all(call == ("limit", CONTRACT, 3, 0.60, "sell_to_close") for call in c.sells())
    c.mode = None                                                        # the broker takes sales again
    _run(m, trades, t, 6 * 31, stock=STOP - 1, bid=0.60, ask=0.70)
    row = m.status()["rows"][0]
    assert row["status"] == "closed" and "sellRejects" not in row and row["pl"] == round((0.60 - 1.25) * 300, 2)


def test_a_target_lot_refused_at_submit_does_not_hide_the_stop(tmp_path):
    c = SubmitFailClient()
    m, trades, _ = _bought(tmp_path, client=c)
    c.mode, c.times = "refuse", 1
    _run(m, trades, 20, 1, stock=102.0, bid=1.70, ask=1.80)              # T1: the one-lot sale gets the 403
    row = m.status()["rows"][0]
    assert c.sells() == [("limit", CONTRACT, 1, 1.75, "sell_to_close")] and row["status"] == "selling" and row["sellError"]
    _run(m, trades, 30, 1, stock=97.0, bid=0.80, ask=0.90)               # through break-even: everything, at the bid, now
    assert c.sells()[-1] == ("limit", CONTRACT, 3, 0.80, "sell_to_close") and len(c.sells()) == 2
    _run(m, trades, 40, 1, stock=97.0, bid=0.80, ask=0.90)
    row = m.status()["rows"][0]
    assert row["status"] == "closed" and row["sells"][0]["why"] == "Stop at break-even: stock traded through 100.00"
    assert "sellRejects" not in row


def test_the_1552_close_still_sells_a_position_whose_target_lot_was_refused(tmp_path):
    c = SubmitFailClient()
    m, trades, _ = _bought(tmp_path, client=c)
    c.mode, c.times = "refuse", 1
    _run(m, trades, 20, 1, stock=102.0, bid=1.70, ask=1.80)              # the T1 lot is refused at submit
    late = NOW.replace(hour=15, minute=52)
    trades[0].update(lastAt=(late - timedelta(seconds=5)).isoformat(), lastBid=1.30, lastAsk=1.40,
                     stockLast=101.0, stockLastAt=(late - timedelta(seconds=5)).isoformat())
    m.tick(late)
    assert c.sells()[-1] == ("limit", CONTRACT, 3, 1.35, "sell_to_close")
    m.tick(late + timedelta(seconds=10))
    row = m.status()["rows"][0]
    assert row["status"] == "closed" and row["sells"][0]["why"] == "15:52 close"


def test_a_sale_left_stuck_by_the_older_code_is_picked_up_again(tmp_path):
    """The live shape (paper3 CLS, 2026-11-10): an older-plan row "selling" under an id the
    broker never had, tried 89 times. It goes back to a normal sale at the mid."""
    c = SubmitFailClient()
    m, trades, _ = _bought(tmp_path, client=c)
    row = next(iter(m._rows.values()))
    row.pop("exitPlan")
    row.update(status="selling", sellId="mxp-s0-88-0123456789abcdef", sellQty=3, sellWhy="stop loss -50%",
               sellTries=89, sellAt=(NOW - timedelta(minutes=30)).isoformat(),
               note="Awaiting broker reconciliation: order not found")
    _run(m, trades, 20, 1, stock=100.0, bid=1.10, ask=1.20)
    assert c.sells() == [("limit", CONTRACT, 3, 1.15, "sell_to_close")]
    _run(m, trades, 30, 1, stock=100.0, bid=1.10, ask=1.20)
    row = m.status()["rows"][0]
    assert row["status"] == "closed" and row["sells"][0]["why"] == "stop loss -50%"


def test_the_stop_due_while_a_lost_target_lot_is_looked_up_waits_only_the_short_grace(tmp_path):
    """A T1 lot whose submit got no answer may still exist, so it is looked up for 15 s; a stop
    that comes due meanwhile goes out on the first tick after that, all of it, at the bid."""
    c = SubmitFailClient()
    m, trades, _ = _bought(tmp_path, client=c)
    c.mode, c.times = "lost", 1
    _run(m, trades, 20, 1, stock=102.0, bid=1.70, ask=1.80)              # t=30: the T1 lot, lost on the way
    _run(m, trades, 30, 1, stock=97.0, bid=0.80, ask=0.90)               # t=40: 10 s old: still looked up
    assert len(c.sells()) == 1
    _run(m, trades, 40, 1, stock=97.0, bid=0.78, ask=0.88)               # t=50: no record: the stop goes out
    assert c.sells()[-1] == ("limit", CONTRACT, 3, 0.78, "sell_to_close") and len(c.sells()) == 2


# A buy re-sent at the new ask must carry the stock price of the RE-SEND, not the first order's.

def _resent(tmp_path, feed=None):
    """First buy 3 @ $1.25 with the stock at 100 does not fill; 125 s later the stock is 102.60
    (past the signal's T1 = 102), the buy is re-sent at the ask $1.95 and fills."""
    c = FakeClient(fill=False)
    trades = [_trade(plan={**PLAN, "walls": WALLS})]
    m = _mirror(tmp_path, trades, c, **({"quotes_fetch": feed} if feed else {}))
    m.tick(NOW)
    assert c.calls == [("limit", CONTRACT, 3, 1.25, "buy_to_open")]
    c.fill = True
    if feed:
        feed.move(125, 102.6, 1.85, 1.95)
        _move(trades, 120)                                               # the Bot's own stock price is still 100, and old
    else:
        _move(trades, 120, stock=102.6, bid=1.85, ask=1.95)
    m.tick(NOW + timedelta(seconds=125))
    assert c.calls[-1] == ("limit", CONTRACT, 3, 1.95, "buy_to_open") and len(c.calls) == 2
    if feed:
        feed.move(135, 102.6, 1.85, 1.95)
        _move(trades, 130)
    else:
        _move(trades, 130, stock=102.6, bid=1.85, ask=1.95)
    m.tick(NOW + timedelta(seconds=135))                                 # the fill tick
    return m, trades, c


def _assert_rebased_from_the_resend(m, c):
    row = m.status()["rows"][0]
    want = cp.rebase({**PLAN, "walls": WALLS}, 1.95, 102.6)
    assert row["status"] == "open" and row["buyFill"] == 1.95 and row["held"] == 3
    assert c.sells() == [] and not row.get("hits")                       # no "T1 hit" on the fill tick
    assert row["entryStock"] == 102.6 and row["plan"]["entrySpot"] == 102.6
    assert (row["plan"]["t1"], row["plan"]["t2"], row["plan"]["t3"]) == (want["t1"], want["t2"], want["t3"]) == (104.6, 106.6, 110.0)
    assert row["stopStock"] == want["stopStock"] < 102.6 and row["stopPremium"] == want["stopPremium"]
    assert (row["entryBid"], row["entryAsk"], row["entryMid"]) == (1.85, 1.95, 1.9)
    first = row["buyHistory"][0]
    assert first["limit"] == 1.25 and first["entryStock"] == 100.0 and first["entryAsk"] == 1.25


def test_a_buy_resent_at_the_new_ask_starts_its_plan_from_the_stock_price_at_the_resend(tmp_path):
    m, trades, c = _resent(tmp_path)
    _assert_rebased_from_the_resend(m, c)
    _move(trades, 140, stock=102.2, bid=1.75, ask=1.85)                  # under the re-send price, far above the stop
    m.tick(NOW + timedelta(seconds=145))
    assert c.sells() == []
    _move(trades, 150, stock=104.6, bid=2.50, ask=2.60)                  # the real T1
    m.tick(NOW + timedelta(seconds=155))
    assert c.sells() == [("limit", CONTRACT, 1, 2.55, "sell_to_close")]
    m.tick(NOW + timedelta(seconds=165))
    row = m.status()["rows"][0]
    assert row["stopStock"] == 102.6 and row["stopKind"] == "break-even"  # break-even = the re-send's stock price


def test_a_resent_buy_reads_the_stock_price_live_with_the_option_quote(tmp_path):
    feed = Feed()
    m, _, c = _resent(tmp_path, feed)
    assert [CONTRACT, "AAPL"] in feed.asked
    _assert_rebased_from_the_resend(m, c)
