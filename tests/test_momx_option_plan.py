"""The Bot's own tracked trades on the chart's Entry / Target / Stop plan (momx/option_track.py, 2026-11-12).

His "Do the paper trades and all trades also changes": every Level trade the Bot records
carries the plan the chart would draw for its 0.20-delta contract, and three contracts are
followed through it. Fake chains and quotes only.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from momx import chart_plan as cp
from momx import option_track as ot
from momx import paper_mirror as pm

ET = ZoneInfo("America/New_York")
E_CONTRACT = "X_C104_1120"
#: Bought at the ask $1.25 with the stock at 100, delta 0.19, gamma 0.03, strike 104.
STOP = cp.stop_stock("CALL", 100.0, 1.25, 0.19, 0.03, 104.0, 0.63)


@pytest.fixture(autouse=True)
def _rows_are_live(monkeypatch):
    monkeypatch.setattr(ot, "live_today", lambda row, now: True)


def _at(h, m, s=0):
    return datetime(2026, 11, 13, h, m, s, tzinfo=ET)          # a Friday


def chain(spot=100.0):
    """Next week's calls around a $100 stock: the 104 call is the 0.20-delta pick ($1.15 x $1.25),
    the expected move is $4, and the biggest open interest past it (inside two moves) sits at 108."""
    rows = []
    for strike, delta, bid, ask, oi in ((101, 0.45, 2.9, 3.1, 50), (102, 0.35, 2.2, 2.4, 60), (103, 0.25, 1.6, 1.8, 70),
                                        (104, 0.19, 1.15, 1.25, 80), (105, 0.12, 0.7, 0.8, 90), (106, 0.08, 0.4, 0.5, 100),
                                        (108, 0.04, 0.15, 0.25, 3000), (110, 0.02, 0.05, 0.15, 9000)):
        rows.append({"symbol": f"X_C{strike}_1120", "side": "CALL", "strike": float(strike), "expiry": "2026-11-20",
                     "days_to_expiration": 7, "bid": bid, "ask": ask, "mark": round((bid + ask) / 2, 3), "delta": delta,
                     "gamma": 0.03, "open_interest": oi, "expected_move": 4.0})
    return {"underlyingPrice": spot, "selectedExpiryChainRows": rows,
            "currentAtm": {"expiry": "2026-11-20", "expectedMove": 4.0}, "scannedAt": None}


def row(last, close=None, bar=None, e21=99.0):
    """A board row: the stock's price now, and its last completed 5m candle against the 21 EMA."""
    m5 = {"ema21": e21}
    if bar is not None:
        m5["lastCompleted"] = {"time": bar.timestamp(), "close": close if close is not None else last}
    return {"symbol": "X", "last": last, "m5": m5}


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Quotes:
    """The batched live quote route: one bid/ask for the 0.20-delta contract, moved by the test."""

    def __init__(self):
        self.bid, self.ask, self.asked = 1.15, 1.25, []

    def __call__(self, contracts):
        self.asked.append(list(contracts))
        return {"quotes": {c: {"bid": self.bid, "ask": self.ask} for c in contracts if c == E_CONTRACT}}

    def move(self, bid, ask):
        self.bid, self.ask = bid, ask


def _book(tmp_path, monkeypatch, start, quotes=None, **kw):
    clock = Clock(start.timestamp())
    b = ot.OptionTrackBook(tmp_path, direction="bull", fetch=lambda s: chain(), healthy=lambda: True, background=False,
                           clock=clock, sleep=lambda s: None, quotes_fetch=quotes or Quotes(), **kw)
    monkeypatch.setattr(ot, "fired", lambda *args: [])
    b._levels.evaluate = lambda *a, **k: {"X": {"version": 1, "highestLevel": 3, "setupCount": 3, "virtualOnly": True}}
    return b, clock


def _scan(b, clock, when, board_row):
    clock.t = when.timestamp()
    b.apply("Mag7", {"rows": [board_row]}, when)


def _level3(b):
    return next(t for t in b.trades() if t["rule"] == "level3")


def test_the_windows_are_the_paper_bots_windows():
    """9:30-11:00 and 1:00-3:30. Between 2026-11-12 and 11-13 this asserted a 10:00 start and the
    note "before 10:00"; he removed that rule ("if you see the setup then trade it")."""
    assert ot.PLAN_WINDOWS == pm.ENTRY_WINDOWS == ((9, 30, 11, 0), (13, 0, 15, 30))
    assert ot.plan_window_note(_at(9, 30).isoformat()) is None
    assert ot.plan_window_note(_at(9, 59, 59).isoformat()) is None
    assert ot.plan_window_note(_at(10, 0).isoformat()) is None and ot.plan_window_note(_at(10, 59).isoformat()) is None
    assert ot.plan_window_note(_at(11, 0).isoformat()) == "outside the buy windows (9:30-11:00, 1:00-3:30)" == ot.PLAN_OUTSIDE_WHY
    assert ot.plan_window_note(_at(13, 0).isoformat()) is None and ot.plan_window_note(_at(15, 29).isoformat()) is None
    assert ot.plan_window_note(_at(15, 30).isoformat()).startswith("outside the buy windows")
    assert ot.plan_window_note("not a time") is None


def test_a_signal_just_before_a_window_opens_is_still_bought_so_it_gets_its_plan():
    """The paper bots buy a signal up to 10 minutes old. On 2026-11-12 both bought AMD at 13:00 on
    a 12:56:22 signal; with "outside the buy windows" for it the order had no stop and no targets."""
    assert ot.PLAN_LEAD_SECONDS == pm.FRESH_SECONDS == 600.0
    assert ot.plan_window_note(_at(12, 56, 22).isoformat()) is None
    assert ot.plan_window_note(_at(12, 50, 0).isoformat()) is None
    assert ot.plan_window_note(_at(12, 49, 59).isoformat()).startswith("outside the buy windows")
    # (Was: "10:00 stays a hard line". That rule was removed 2026-11-13: a 9:55 signal has its plan.)
    assert ot.plan_window_note(_at(9, 55).isoformat()) is None
    # ONE clock with the paper bots (2026-11-13 review): they count the 10 fresh minutes from the
    # time the Bot OPENED the trade, so the plan is judged on the later of the two times.
    assert ot.plan_window_note(_at(12, 49, 55).isoformat(), _at(12, 50, 20).isoformat()) is None
    assert ot.plan_window_note(_at(12, 49, 55).isoformat(), _at(12, 49, 58).isoformat()) is not None
    assert ot.plan_window_note(_at(10, 59, 50).isoformat(), _at(11, 0, 10).isoformat()) == ot.PLAN_OUTSIDE_WHY
    assert ot.plan_window_note(_at(12, 55).isoformat(), "not a time") is None
    # The end of a window is not stretched: 11:00 and 15:30 signals are not bought.
    assert ot.plan_window_note(_at(11, 0).isoformat()) is not None and ot.plan_window_note(_at(15, 30).isoformat()) is not None


def test_a_1256_signal_carries_the_charts_plan(tmp_path, monkeypatch):
    b, clock = _book(tmp_path, monkeypatch, _at(12, 56, 22))
    _scan(b, clock, _at(12, 56, 22), row(100.0))
    t = _level3(b)
    assert "planNote" not in t and t["plan"]["stopStock"] == STOP
    assert (t["plan"]["t1"], t["plan"]["t2"], t["plan"]["t3"]) == (102.0, 104.0, 108.0)
    assert t["planState"]["status"] == "open" and t["planState"]["held"] == 3
    # The open-interest strikes ride on the plan so the paper bot can re-base T3 at its order.
    assert t["plan"]["walls"] == [[101.0, 50.0], [102.0, 60.0], [103.0, 70.0], [104.0, 80.0], [105.0, 90.0], [106.0, 100.0],
                                  [108.0, 3000.0], [110.0, 9000.0]]


def test_a_level_trade_opened_after_ten_carries_the_charts_plan(tmp_path, monkeypatch):
    b, clock = _book(tmp_path, monkeypatch, _at(10, 5))
    _scan(b, clock, _at(10, 5), row(100.0))
    trades = b.trades()
    assert sorted(t["rule"] for t in trades) == ["level1", "level2", "level3"]
    for t in trades:
        assert t["status"] == "open" and t["eContract"] == E_CONTRACT and t["eEntryAsk"] == 1.25
        plan, st = t["plan"], t["planState"]
        # The same numbers the chart draws for this contract bought at its ask with the stock at 100.
        want = cp.from_chain(chain(), bear=False, expiry="2026-11-20", strike=104.0, entry=1.25, spot=100.0, today="2026-11-13")
        assert plan == {k: want.get(k) for k in ot.PLAN_KEYS}
        assert (plan["entry"], plan["entrySpot"], plan["delta"], plan["gamma"]) == (1.25, 100.0, 0.19, 0.03)
        assert (plan["t1"], plan["t2"], plan["t3"]) == (102.0, 104.0, 108.0) and plan["t3FromOi"] is True
        assert plan["stopStock"] == STOP and plan["stopPremium"] == 0.63
        assert plan["chartExpiry"] == "2026-11-20" and plan["chartStrike"] == 104.0     # the chart would pick the same
        assert st == {"status": "open", "qty": 3, "held": 3, "split": [1, 1, 1], "entry": 1.25, "stopStock": STOP,
                      "stopKind": "stop", "hits": [], "sells": []}
        assert "planNote" not in t
    assert {"plan", "planState", "planNote"} <= set(ot.OptionTrackBook.SUMMARY_KEYS)
    assert b.summary()[0]["plan"]["t2"] == 104.0


def test_a_signal_before_ten_gets_the_charts_plan_like_any_other(tmp_path, monkeypatch):
    """Was test_a_signal_before_ten_is_still_recorded_with_the_note_not_ordered_before_ten (the
    10:00 rule, removed 2026-11-13: "Start trading 9:30 if you see the setup")."""
    b, clock = _book(tmp_path, monkeypatch, _at(9, 40))
    _scan(b, clock, _at(9, 40), row(100.0))
    t = _level3(b)
    assert t["status"] == "open" and t["eEntry"] > 0
    assert "planNote" not in t and t["plan"]["stopStock"] is not None and t["plan"]["t1"] is not None
    assert t["planState"]["status"] == "open" and t["planState"]["held"] == 3


def test_a_midday_signal_is_noted_as_outside_the_buy_windows(tmp_path, monkeypatch):
    b, clock = _book(tmp_path, monkeypatch, _at(11, 30))
    _scan(b, clock, _at(11, 30), row(100.0))
    assert _level3(b)["planNote"] == "Not ordered: outside the buy windows (9:30-11:00, 1:00-3:30)"
    assert "plan" not in _level3(b)


def test_a_signal_opened_inside_the_lead_gets_its_plan_from_the_later_time(tmp_path, monkeypatch):
    """plan_open judges the later of the signal and entry times (the paper bots' clock)."""
    t = {"rule": "level3", "symbol": "AAPL", "at": _at(12, 49, 55).isoformat(), "entryAt": _at(12, 50, 20).isoformat(),
         "eContract": "X"}
    assert ot.plan_open(t, None, False, "2026-11-13") is True
    assert t["planNote"].startswith("No chart plan")            # inside the clock; only the chain is missing here
    late = {"rule": "level3", "symbol": "AAPL", "at": _at(10, 59, 50).isoformat(), "entryAt": _at(11, 0, 10).isoformat(),
            "eContract": "X"}
    ot.plan_open(late, None, False, "2026-11-13")
    assert late["planNote"] == "Not ordered: " + ot.PLAN_OUTSIDE_WHY


def test_targets_come_off_one_by_one_and_the_stop_follows(tmp_path, monkeypatch):
    q = Quotes()
    b, clock = _book(tmp_path, monkeypatch, _at(10, 5), q)
    _scan(b, clock, _at(10, 5), row(100.0))
    q.move(1.70, 1.80)
    _scan(b, clock, _at(10, 10), row(102.1))                            # T1
    st = _level3(b)["planState"]
    assert [h["target"] for h in st["hits"]] == ["T1"] and st["hits"][0]["at"] == _at(10, 10).isoformat()
    assert st["held"] == 2 and st["stopStock"] == 100.0 and st["stopKind"] == "break-even"
    sale = st["sells"][0]
    assert (sale["qty"], sale["why"], sale["stock"]) == (1, "T1 hit: sold 1, stop to break-even", 102.1)
    assert (sale["price"], sale["bid"], sale["ask"]) == (1.75, 1.70, 1.80)   # priced from a live quote right after
    q.move(2.40, 2.60)
    _scan(b, clock, _at(10, 15), row(104.3))                            # T2
    st = _level3(b)["planState"]
    assert st["held"] == 1 and st["stopStock"] == 102.0 and st["stopKind"] == "T1"
    assert st["sells"][1]["why"] == "T2 hit: sold 1, stop to T1" and st["sells"][1]["price"] == 2.5
    q.move(1.55, 1.65)
    _scan(b, clock, _at(10, 20), row(101.9))                            # back through T1
    st = _level3(b)["planState"]
    assert st["sells"][2]["why"] == "Stop at T1: stock traded through 102.00" and st["sells"][2]["price"] == 1.6
    assert st["status"] == "closed" and st["held"] == 0 and st["reason"] == "Stop at T1: stock traded through 102.00"
    assert st["pnl"] == 210.0 and st["pct"] == 56.0                     # (0.50 + 1.25 + 0.35) x 100 on 3 x $1.25


def test_all_three_targets(tmp_path, monkeypatch):
    q = Quotes()
    b, clock = _book(tmp_path, monkeypatch, _at(10, 5), q)
    _scan(b, clock, _at(10, 5), row(100.0))
    for when, stock, bid, ask in ((_at(10, 10), 102.0, 1.7, 1.8), (_at(10, 15), 104.0, 2.4, 2.6), (_at(10, 20), 108.2, 4.9, 5.1)):
        q.move(bid, ask)
        _scan(b, clock, when, row(stock))
    st = _level3(b)["planState"]
    assert [s["why"] for s in st["sells"]] == ["T1 hit: sold 1, stop to break-even", "T2 hit: sold 1, stop to T1",
                                                "T3 hit: sold the last 1"]
    assert [h["target"] for h in st["hits"]] == ["T1", "T2", "T3"] and st["status"] == "closed"
    assert st["pnl"] == round((1.75 + 2.5 + 5.0 - 3.75) * 100, 2)


def test_the_stock_trading_through_the_stop_sells_all_three(tmp_path, monkeypatch):
    q = Quotes()
    b, clock = _book(tmp_path, monkeypatch, _at(10, 5), q)
    _scan(b, clock, _at(10, 5), row(100.0))
    _scan(b, clock, _at(10, 8), row(STOP + 0.05))
    assert _level3(b)["planState"]["sells"] == []
    q.move(0.60, 0.70)
    _scan(b, clock, _at(10, 12), row(STOP - 0.05))
    st = _level3(b)["planState"]
    assert len(st["sells"]) == 1 and st["sells"][0]["qty"] == 3
    assert st["sells"][0]["why"] == f"Stop: stock traded through {STOP:.2f}" and st["sells"][0]["price"] == 0.65
    assert st["status"] == "closed" and st["pnl"] == round((0.65 - 1.25) * 300, 2) and st["pct"] == -48.0


def test_the_five_minute_close_past_the_21_ema_sells_what_is_left(tmp_path, monkeypatch):
    q = Quotes()
    b, clock = _book(tmp_path, monkeypatch, _at(10, 5), q)
    _scan(b, clock, _at(10, 5), row(100.0, bar=_at(9, 55), close=100.0))
    q.move(1.70, 1.80)
    _scan(b, clock, _at(10, 10), row(102.0, bar=_at(10, 5), close=102.0))
    q.move(1.30, 1.40)
    _scan(b, clock, _at(10, 20), row(100.6, bar=_at(10, 15), close=98.5))    # the candle closed under the 21 EMA (99)
    t = _level3(b)
    assert t["exitReason"] == "below EMA21" and t["eExit"] == 1.35
    st = t["planState"]
    assert [(s["qty"], s["why"]) for s in st["sells"]] == [(1, "T1 hit: sold 1, stop to break-even"),
                                                           (2, "5-minute candle closed below the 21 EMA")]
    assert st["status"] == "closed" and st["pnl"] == round((1.75 - 1.25 + 2 * (1.35 - 1.25)) * 100, 2)


def test_the_1550_close_sells_what_is_left(tmp_path, monkeypatch):
    q = Quotes()
    b, clock = _book(tmp_path, monkeypatch, _at(13, 5), q)
    _scan(b, clock, _at(13, 5), row(100.0))
    q.move(1.40, 1.50)
    _scan(b, clock, _at(15, 50), row(100.8))
    st = _level3(b)["planState"]
    assert st["status"] == "closed" and st["sells"][-1]["why"] == "15:50 close" and st["sells"][-1]["qty"] == 3
    assert st["sells"][-1]["price"] == 1.45


def test_the_option_backstop_on_the_tracked_trade():
    t = {"rule": "level2", "plan": {"entrySpot": 100.0, "t1": 102.0, "t2": 104.0, "t3": 108.0},
         "planState": {"status": "open", "qty": 3, "held": 3, "split": [1, 1, 1], "entry": 1.25, "stopStock": 99.0,
                       "stopKind": "stop", "hits": [], "sells": []}}
    assert ot.plan_price(t, 0.70, 0.65, 0.75, "10:10") is False              # -44%: held
    assert ot.plan_price(t, 0.62, 0.58, 0.66, "10:11") is True               # -50.4%
    st = t["planState"]
    assert st["sells"] == [{"qty": 3, "why": "Stop: option down 50% (backstop)", "at": "10:11", "price": 0.62, "bid": 0.58,
                            "ask": 0.66, "priceAt": "10:11", "kind": "stop"}]
    assert ot.plan_settle(t) is True and st["status"] == "closed" and st["pct"] == -50.4
    assert ot.plan_step(t, 110.0, "10:12", False) is False                   # closed: nothing more happens


def test_a_gap_through_two_targets_and_the_two_and_one_contract_plans():
    def trade(qty):
        return {"rule": "level2", "plan": {"entrySpot": 100.0, "t1": 102.0, "t2": 104.0, "t3": 108.0},
                "planState": {"status": "open", "qty": qty, "held": qty, "split": cp.split_contracts(qty), "entry": 1.0,
                              "stopStock": 99.0, "stopKind": "stop", "hits": [], "sells": []}}
    t = trade(3)
    assert ot.plan_step(t, 104.5, "a", False)
    assert t["planState"]["sells"][0]["qty"] == 2 and t["planState"]["sells"][0]["why"] == "T2 hit: sold 2, stop to T1"
    assert t["planState"]["stopStock"] == 102.0 and len(t["planState"]["hits"]) == 2
    two = trade(2)
    ot.plan_step(two, 102.0, "a", False)
    ot.plan_step(two, 104.0, "b", False)
    assert [s["why"] for s in two["planState"]["sells"]] == ["T1 hit: sold 1, stop to break-even", "T2 hit: sold the last 1"]
    assert two["planState"]["status"] == "closing"
    one = trade(1)
    assert ot.plan_step(one, 102.0, "a", False)
    assert one["planState"]["sells"] == [] and one["planState"]["note"] == "T1 hit: stop to break-even"
    assert one["planState"]["stopStock"] == 100.0
    ot.plan_step(one, 104.0, "b", False)
    assert one["planState"]["sells"][0]["why"] == "T2 hit: sold the last 1"
    put = {"rule": "level2", "plan": {"entrySpot": 100.0, "t1": 98.0, "t2": 96.0, "t3": 92.0},
           "planState": {"status": "open", "qty": 3, "held": 3, "split": [1, 1, 1], "entry": 1.0, "stopStock": 101.0,
                         "stopKind": "stop", "hits": [], "sells": []}}
    assert not ot.plan_step(put, 99.0, "a", True)
    assert ot.plan_step(put, 101.0, "b", True) and put["planState"]["sells"][0]["why"] == "Stop: stock traded through 101.00"


def test_no_chain_means_no_plan_and_it_says_so():
    t = {"rule": "level2", "at": _at(10, 5).isoformat(), "eContract": E_CONTRACT, "eStrike": 104.0, "eEntryAsk": 1.25,
         "expiry": "2026-11-20", "spot": 100.0}
    assert ot.plan_open(t, None, False, "2026-11-13") is True
    assert t["planNote"] == "No chart plan: the option chain was not available at the signal" and "plan" not in t
    other = {"rule": "go", "at": _at(10, 5).isoformat(), "eContract": E_CONTRACT}
    assert ot.plan_open(other, chain(), False, "2026-11-13") is False and "plan" not in other and "planNote" not in other
    none = {"rule": "level2", "at": _at(10, 5).isoformat()}
    ot.plan_open(none, chain(), False, "2026-11-13")
    assert none["planNote"] == "No chart plan: no 0.20-delta option was found"


def test_the_fast_quote_path_reads_the_saved_chain_for_the_plan(tmp_path, monkeypatch):
    """Nearly every Level trade opens on the fast quote reply, which has no gamma, expected move or
    open interest: the plan is worked out from the chain file the reply's contract came from."""
    asked = []

    def plan_chain(symbol):
        asked.append(symbol)
        return chain()

    reply = lambda q: {"results": [{"symbol": "X", "ok": True, "chainAt": "2026-11-13T10:01:00-05:00",
                                    "pick": {"contract": "X_C101_1120", "strike": 101.0, "expiry": "2026-11-20", "delta": 0.45,
                                             "spot": 100.0, "entry": 3.0, "entryBid": 2.9, "entryAsk": 3.1},
                                    "e": {"eContract": E_CONTRACT, "eStrike": 104.0, "eDelta": 0.19, "eEntry": 1.2, "eLast": 1.2,
                                          "eBest": 1.2, "eStatus": "open", "eEntryBid": 1.15, "eEntryAsk": 1.25},
                                    "z": None, "levels": {"expectedMove": 4.0}}]}
    clock = Clock(_at(10, 5).timestamp())
    b = ot.OptionTrackBook(tmp_path, direction="bull", fetch=lambda s: None, healthy=lambda: True, background=False,
                           clock=clock, sleep=lambda s: None, quote_fetch=reply, quotes_fetch=Quotes(), plan_chain=plan_chain)
    monkeypatch.setattr(ot, "fired", lambda *args: [])
    b._levels.evaluate = lambda *a, **k: {"X": {"version": 1, "highestLevel": 3, "setupCount": 3, "virtualOnly": True}}
    b.apply("Mag7", {"rows": [row(100.0)]}, _at(10, 5))
    t = _level3(b)
    assert t["entryVia"] == "quote" and asked == ["X"]                  # one file read for the ticker, not one per level
    assert t["plan"]["stopStock"] == STOP and (t["plan"]["t1"], t["plan"]["t2"], t["plan"]["t3"]) == (102.0, 104.0, 108.0)
    assert t["planState"]["held"] == 3

    # A chain file that cannot be read never costs the trade: it is recorded, with the reason.
    def broken(symbol):
        raise OSError("no file")
    b2 = ot.OptionTrackBook(tmp_path / "b2", direction="bull", fetch=lambda s: None, healthy=lambda: True, background=False,
                            clock=clock, sleep=lambda s: None, quote_fetch=reply, quotes_fetch=Quotes(), plan_chain=broken)
    b2._levels.evaluate = lambda *a, **k: {"X": {"version": 1, "highestLevel": 3, "setupCount": 3, "virtualOnly": True}}
    b2.apply("Mag7", {"rows": [row(100.0)]}, _at(10, 5))
    t2 = _level3(b2)
    assert t2["status"] == "open" and t2["planNote"].startswith("No chart plan") and "plan" not in t2


def test_the_plan_survives_a_reload(tmp_path, monkeypatch):
    q = Quotes()
    b, clock = _book(tmp_path, monkeypatch, _at(10, 5), q)
    _scan(b, clock, _at(10, 5), row(100.0))
    q.move(1.70, 1.80)
    _scan(b, clock, _at(10, 10), row(102.1))
    b._flush()
    again, clock2 = _book(tmp_path, monkeypatch, _at(10, 11), q)
    again._load("2026-11-13")
    st = _level3(again)["planState"]
    assert st["held"] == 2 and st["stopStock"] == 100.0 and st["sells"][0]["price"] == 1.75


def test_the_default_chain_reader_refuses_odd_symbols():
    assert ot._default_plan_chain("../x") is None and ot._default_plan_chain("") is None
