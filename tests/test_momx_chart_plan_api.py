"""Integration: the chart's Entry / Target / Stop plan from the signal to the three screens (2026-11-12).

One real Bot book (momx/option_track.py) and one real paper bot (momx/paper_mirror.py) run a
morning together, with only what leaves the PC faked (the option chain, the live quotes, the
broker). Then the real routes are read over HTTP:

  GET /api/momx-scanner/paper-mirror   (scanner worker)  the paper order: entry at the ask, stop, T1-T3, what happened
  GET /api/momx-scanner/bot            (scanner worker)  the Bot's tracked trade on the same plan
  GET /api/momx-scanner/bot-desk       (api_server)      the Scorecard's All trades with the plan and the new evaluation

Nothing here can place an order: the broker is a fake and the harness guards refuse the network.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from api_harness import api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import bot_desk, chart_plan, option_track, paper_mirror, service
from worker_harness import running_worker

OWNER = "owner-chart-plan@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
ET = paper_mirror.ET
E_CONTRACT = "X_C104_1120"
STOP = chart_plan.stop_stock("CALL", 100.0, 1.25, 0.19, 0.03, 104.0, 0.63)


def _day() -> datetime:
    """Today (or the Friday before a weekend) at midnight ET: inside every route's look-back."""
    at = datetime.now(ET).replace(hour=0, minute=0, second=0, microsecond=0)
    while at.weekday() >= 5:
        at -= timedelta(days=1)
    return at


DAY = _day()
EXPIRY = (DAY + timedelta(days=7)).date().isoformat()


def at(h, m, s=0):
    return DAY.replace(hour=h, minute=m, second=s)


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")


def chain():
    rows = []
    for strike, delta, bid, ask, oi in ((101, 0.45, 2.9, 3.1, 50), (102, 0.35, 2.2, 2.4, 60), (103, 0.25, 1.6, 1.8, 70),
                                        (104, 0.19, 1.15, 1.25, 80), (105, 0.12, 0.7, 0.8, 90), (106, 0.08, 0.4, 0.5, 100),
                                        (108, 0.04, 0.15, 0.25, 3000), (110, 0.02, 0.05, 0.15, 9000)):
        rows.append({"symbol": f"X_C{strike}_1120", "side": "CALL", "strike": float(strike), "expiry": EXPIRY,
                     "days_to_expiration": 7, "bid": bid, "ask": ask, "mark": round((bid + ask) / 2, 3), "delta": delta,
                     "gamma": 0.03, "open_interest": oi, "expected_move": 4.0})
    return {"underlyingPrice": 100.0, "selectedExpiryChainRows": rows, "currentAtm": {"expiry": EXPIRY, "expectedMove": 4.0}}


class Feed:
    """The batched live quote route: the 0.20-delta option and the stock, moved by the test."""

    def __init__(self, clock):
        self.clock, self.option, self.stock = clock, (1.15, 1.25), 100.0

    def __call__(self, symbols):
        out = {}
        for s in symbols:
            if s == E_CONTRACT:
                out[s] = {"bid": self.option[0], "ask": self.option[1], "mid": sum(self.option) / 2}
            elif s in ("X", "Y"):
                out[s] = {"bid": self.stock - 0.01, "ask": self.stock + 0.01, "mid": self.stock, "last": self.stock}
        return {"asOf": self.clock.t, "quotes": out}


class Broker:
    """A paper broker that fills every limit at its limit. It only ever sees limit orders."""

    def __init__(self):
        self.credentials = SimpleNamespace(paper=True)
        self.orders, self.calls = {}, []

    def submit_option_limit_order(self, symbol, qty, limit, cid, intent):
        self.calls.append((intent, symbol, qty, limit))
        self.orders[cid] = SimpleNamespace(id=cid, status="filled", filled_avg_price=limit, filled_qty=qty)

    def get_order_by_client_id(self, cid):
        return self.orders[cid]

    def cancel_order_by_id(self, oid):
        self.orders[oid].status = "canceled"


def run_the_morning(base, monkeypatch):
    """10:05 a Level signal on X (bought at the ask); 10:10 X reaches T1; 10:20 X falls back to its
    entry price (the rest stopped at break-even); 11:40 a Level signal on Y (midday: outside the buy
    windows, never ordered). (Until 2026-11-13 Y fired at 09:40 and was "before 10:00"; that rule
    was removed, a 9:40 signal is bought like any other.)"""
    monkeypatch.setattr(option_track, "live_today", lambda row, now: True)
    monkeypatch.setattr(option_track, "fired", lambda *args: [])
    clock = SimpleNamespace(t=at(9, 40).timestamp())
    feed = Feed(clock)
    book = option_track.OptionTrackBook(base, direction="bull", fetch=lambda s: chain(), healthy=lambda: True,
                                        background=False, clock=lambda: clock.t, sleep=lambda s: None, quotes_fetch=feed)
    signals = {}
    book._levels.evaluate = lambda *a, **k: {s: {"version": 1, "highestLevel": 3, "setupCount": 3, "virtualOnly": True}
                                             for s in signals}
    broker = Broker()
    mirror = paper_mirror.PaperMirror({"bull": book}, base / "momx_paper_mirror" / "paper3", client_factory=lambda: broker,
                                      off=lambda: False, account="paper3", jev_cut=None,
                                      rules=paper_mirror.ACCOUNT_RULES["paper3"], leg=paper_mirror.PAPER_LEG,
                                      book=paper_mirror.ACCOUNTS["paper3"][0], quotes_fetch=feed)

    def step(when, stock, option=None, ticks=(5, 15)):
        clock.t = when.timestamp()
        feed.stock = stock
        if option:
            feed.option = option
        book.apply("Test board", {"rows": [{"symbol": s, "last": stock, "m5": {"ema21": 90.0}} for s in sorted(signals)]}, when)
        for seconds in ticks:
            clock.t = (when + timedelta(seconds=seconds)).timestamp()
            mirror.tick(when + timedelta(seconds=seconds))

    signals["X"] = True
    step(at(10, 5), 100.0)
    step(at(10, 10), 102.1, (1.70, 1.80))
    step(at(10, 20), 99.9, (1.00, 1.10))
    signals.pop("X")
    signals["Y"] = True
    step(at(11, 40), 100.0)
    mirror._save()
    book._flush()
    return SimpleNamespace(book=book, mirror=mirror, broker=broker)


def test_the_plan_from_the_signal_to_the_paper_page_the_bot_page_and_the_scorecard(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK", {})
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-01-01")
    monkeypatch.setattr(bot_desk, "CHART_PLAN_DAY", "2026-01-01")
    monkeypatch.setattr(bot_desk, "_SIGNALS", {})
    with running_worker(tmp_path, monkeypatch) as worker:
        base = service.grade_dir()
        ran = run_the_morning(base, monkeypatch)
        monkeypatch.setattr(paper_mirror, "_MIRRORS", [ran.mirror])
        admin = worker.as_admin(monkeypatch, email=OWNER)
        status, paper = worker.get("/api/momx-scanner/paper-mirror", headers=admin)
        refused, _ = worker.get("/api/momx-scanner/paper-mirror", headers={"X-AGX-User": "someone@example.com"})
        bot_status, bot = worker.get("/api/momx-scanner/bot?days=1", headers=admin)
        with running_api(worker_port=worker.port) as api:
            desk_status, desk = api.get("/api/momx-scanner/bot-desk?days=30", headers=ADMIN)

    # The broker saw limit orders only: the buy at the ASK, one contract at T1 at the mid, the rest at the bid.
    assert ran.broker.calls == [("buy_to_open", E_CONTRACT, 3, 1.25), ("sell_to_close", E_CONTRACT, 1, 1.75),
                                ("sell_to_close", E_CONTRACT, 2, 1.0)]

    # --- the Paper page (scanner worker)
    assert status == 200 and refused == 403
    doc = paper["mirrors"][0]
    assert "limit at the live ask" in doc["rules"]["orders"] and "9:35-11:30" in doc["rules"]["entryTimes"]
    assert "10:00" not in doc["rules"]["entryTimes"] and doc["rules"]["plan"] == "chart"
    assert "T1 = half the expected move" in doc["rules"]["exitPlan"]
    assert len(doc["rows"]) == 1                                         # Y fired at 11:40 (midday): never ordered
    row = doc["rows"][0]
    assert (row["symbol"], row["rule"], row["status"], row["exitPlan"]) == ("X", "level3", "closed", "chart")
    assert (row["buyLimit"], row["buyFill"], row["entryAsk"], row["entryBid"], row["entryStock"]) == (1.25, 1.25, 1.25, 1.15, 100.0)
    assert (row["plan"]["t1"], row["plan"]["t2"], row["plan"]["t3"]) == (102.0, 104.0, 108.0)
    assert (row["plan"]["delta"], row["plan"]["gamma"]) == (0.19, 0.03)
    assert row["stopStock0"] == STOP and row["stopStock"] == 100.0 and row["stopKind"] == "break-even"
    assert [h["target"] for h in row["hits"]] == ["T1"] and row["hits"][0]["at"].startswith(DAY.date().isoformat() + "T10:10")
    assert [s["why"] for s in row["sells"]] == ["T1 hit: sold 1, stop to break-even",
                                               "Stop at break-even: stock traded through 100.00"]
    assert row["pl"] == round((1.75 - 1.25 + 2 * (1.0 - 1.25)) * 100, 2) == 0.0

    # --- the Bot page's tracked trades (scanner worker)
    assert bot_status == 200
    trades = bot["bull"][0]["trades"]
    x3 = next(t for t in trades if t["symbol"] == "X" and t["rule"] == "level3")
    assert x3["plan"]["entry"] == 1.25 and x3["plan"]["stopStock"] == STOP and x3["plan"]["t3"] == 108.0
    assert x3["planState"]["status"] == "closed"
    assert [s["why"] for s in x3["planState"]["sells"]] == ["T1 hit: sold 1, stop to break-even",
                                                            "Stop at break-even: stock traded through 100.00"]
    assert [s["price"] for s in x3["planState"]["sells"]] == [1.75, 1.05]         # the Bot scores at the mid
    y3 = next(t for t in trades if t["symbol"] == "Y" and t["rule"] == "level3")
    assert y3["planNote"] == "Not ordered: outside the buy windows (9:35-11:30, 1:00-3:30)" and "plan" not in y3

    # --- the Scorecard (api_server)
    assert desk_status == 200 and desk["startDay"] == "2026-01-01"
    assert "Entry / Target / Stop plan" in desk["strategy"] and "at the ask from 9:30" in desk["strategy"]
    # The running paper bot wrote its chart-plan marker, so the Scorecard says the chart plan runs.
    assert desk["plan"] == "chart" and desk["planSince"] and "+100%" in desk["oldStrategy"]
    log = desk["accounts"]["paper3"]["tradeLog"]["rows"]
    order = next(r for r in log if r["symbol"] == "X" and r["status"] == "closed")
    assert order["plan"] == {"entryAsk": 1.25, "entryStock": 100.0, "stop": STOP, "stopNow": 100.0, "stopKind": "break-even",
                             "t1": 102.0, "t2": 104.0, "t3": 108.0, "ready": True,
                             "hits": [{"target": "T1", "at": order["plan"]["hits"][0]["at"]}]}
    assert order["note"] == "Stop at break-even: stock traded through 100.00" and order["buy"] == 1.25
    journal = desk["accounts"]["paper3"]["journal"]["trades"][0]
    assert journal["exit"] == "Stop at break-even: stock traded through 100.00" and journal["plan"]["t2"] == 104.0
    if DAY.date() == datetime.now(ET).date():                            # today's signals are listed (not on a weekend run)
        early = next(r for r in log if r["symbol"] == "Y")
        assert early["status"] == "signal" and early["note"].startswith("Not ordered: outside the buy windows (9:35-11:30, 1:00-3:30)")
        assert early["plan"] is None


def test_the_new_evaluation_starts_on_the_next_session_and_the_old_plan_stays_as_the_earlier_period(tmp_path, monkeypatch):
    """The real start day, over the real route: 11-10..11-12 orders are not judged with the chart
    plan's, and are still served as the earlier period with their own totals."""
    import json
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK", {})
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)
    old = {"side": "bull", "rule": "level2", "symbol": "OLD", "at": "2026-11-11T09:41:00-05:00", "qty": 3, "buyLimit": 1.0,
           "status": "closed", "buyFill": 1.0, "buyFilledQty": 3, "pl": -240.0, "closedAt": "2026-11-11T10:02:00-05:00",
           "sells": [{"qty": 3, "price": 0.2, "why": "stop loss -50%", "at": "2026-11-11T10:02:00-05:00"}]}
    (folder / "2026-11-11.json").write_text(json.dumps({"rows": {"k": old}}), encoding="utf-8")
    assert bot_desk.EVALUATION_START == "2026-11-13" and bot_desk.CHART_PLAN_DAY == "2026-11-13"
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-desk?days=90", headers=ADMIN)
    assert status == 200 and body["startDay"] == "2026-11-13"
    acct = body["accounts"]["paper3"]
    assert acct["stats"]["n"] == 0 and acct["balance"] == 10000.0        # nothing from the old plan is judged
    earlier = acct["earlier"]
    assert len(earlier) == 1 and (earlier[0]["startDay"], earlier[0]["endDay"]) == ("2026-11-10", "2026-11-12")
    assert (earlier[0]["trades"], earlier[0]["wins"], earlier[0]["netPnl"], earlier[0]["worstTrade"]) == (1, 0, -240.0, -240.0)
    assert earlier[0]["rows"][0]["symbol"] == "OLD" and earlier[0]["rows"][0]["exit"] == "stop loss -50%"
    assert earlier[0]["rows"][0]["plan"] is None                         # an order from before the chart plan


def run_a_signal_just_before_one(base, monkeypatch):
    """12:56 a Level signal on X with the stock at 100 (the buy window opens at 13:00); 13:00 the
    stock is at 101 and the paper bot buys at the ask; 13:05 the stock reaches 103.10."""
    monkeypatch.setattr(option_track, "live_today", lambda row, now: True)
    monkeypatch.setattr(option_track, "fired", lambda *args: [])
    clock = SimpleNamespace(t=at(12, 56).timestamp())
    feed = Feed(clock)
    book = option_track.OptionTrackBook(base, direction="bull", fetch=lambda s: chain(), healthy=lambda: True,
                                        background=False, clock=lambda: clock.t, sleep=lambda s: None, quotes_fetch=feed)
    book._levels.evaluate = lambda *a, **k: {"X": {"version": 1, "highestLevel": 3, "setupCount": 3, "virtualOnly": True}}
    broker = Broker()
    mirror = paper_mirror.PaperMirror({"bull": book}, base / "momx_paper_mirror" / "paper3", client_factory=lambda: broker,
                                      off=lambda: False, account="paper3", jev_cut=None,
                                      rules=paper_mirror.ACCOUNT_RULES["paper3"], leg=paper_mirror.PAPER_LEG,
                                      book=paper_mirror.ACCOUNTS["paper3"][0], quotes_fetch=feed)

    def step(when, stock, option=None, ticks=(5, 15)):
        clock.t = when.timestamp()
        feed.stock = stock
        if option:
            feed.option = option
        book.apply("Test board", {"rows": [{"symbol": "X", "last": stock, "m5": {"ema21": 90.0}}]}, when)
        for seconds in ticks:
            clock.t = (when + timedelta(seconds=seconds)).timestamp()
            mirror.tick(when + timedelta(seconds=seconds))

    step(at(12, 56), 100.0)
    before_one = list(broker.calls)
    step(at(13, 0), 101.0)
    step(at(13, 5), 103.1, (1.70, 1.80))
    mirror._save()
    book._flush()
    return SimpleNamespace(book=book, mirror=mirror, broker=broker, before_one=before_one)


def test_a_signal_minutes_before_one_is_bought_at_one_with_its_stop_and_targets(tmp_path, monkeypatch):
    """2026-11-12: both accounts bought AMD at 13:00 on a 12:56 signal. That order must carry the
    plan (it had none: no stock stop, no T1-T3), and its targets start from the stock price when
    the order went out (101), not from the signal's (100)."""
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK", {})
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-01-01")
    monkeypatch.setattr(bot_desk, "CHART_PLAN_DAY", "2026-01-01")
    monkeypatch.setattr(bot_desk, "_SIGNALS", {})
    with running_worker(tmp_path, monkeypatch) as worker:
        ran = run_a_signal_just_before_one(service.grade_dir(), monkeypatch)
        monkeypatch.setattr(paper_mirror, "_MIRRORS", [ran.mirror])
        admin = worker.as_admin(monkeypatch, email=OWNER)
        status, paper = worker.get("/api/momx-scanner/paper-mirror", headers=admin)
        bot_status, bot = worker.get("/api/momx-scanner/bot?days=1", headers=admin)
        with running_api(worker_port=worker.port) as api:
            desk_status, desk = api.get("/api/momx-scanner/bot-desk?days=30", headers=ADMIN)

    assert ran.before_one == []                                          # nothing is bought before the window opens
    assert ran.broker.calls == [("buy_to_open", E_CONTRACT, 3, 1.25), ("sell_to_close", E_CONTRACT, 1, 1.75)]

    assert status == 200
    (row,) = paper["mirrors"][0]["rows"]
    assert row["at"].startswith(DAY.date().isoformat() + "T12:56") and row["buyAt"].startswith(DAY.date().isoformat() + "T13:00")
    assert (row["exitPlan"], row["buyFill"], row["entryStock"]) == ("chart", 1.25, 101.0)
    # Stop and targets from 101: the same numbers the chart's formulas give from the chain at 101.
    chart = chart_plan.from_chain(chain(), bear=False, expiry=EXPIRY, strike=104.0, entry=1.25, spot=101.0)
    assert (row["plan"]["t1"], row["plan"]["t2"], row["plan"]["t3"]) == (chart["t1"], chart["t2"], chart["t3"]) == (103.0, 105.0, 108.0)
    assert row["stopStock0"] == chart["stopStock"] and row["stopStock0"] < 101.0
    assert row["plan"]["signalSpot"] == 100.0 and row["plan"]["signalTargets"] == [102.0, 104.0, 108.0]
    assert [h["target"] for h in row["hits"]] == ["T1"] and row["hits"][0]["level"] == 103.0
    assert [s["why"] for s in row["sells"]] == ["T1 hit: sold 1, stop to break-even"]
    assert row["stopStock"] == 101.0 and row["stopKind"] == "break-even" and row["held"] == 2

    # The Bot's own tracked trade for the 12:56 signal is on the plan too (from the signal's price).
    assert bot_status == 200
    x3 = next(t for t in bot["bull"][0]["trades"] if t["symbol"] == "X" and t["rule"] == "level3")
    assert "planNote" not in x3 and x3["plan"]["entrySpot"] == 100.0 and x3["plan"]["t1"] == 102.0
    assert x3["plan"]["stopStock"] == STOP and x3["planState"]["hits"][0]["target"] == "T1"

    # The Scorecard shows the order with the re-based plan.
    assert desk_status == 200
    order = next(r for r in desk["accounts"]["paper3"]["tradeLog"]["rows"] if r["symbol"] == "X" and r["status"] == "open")
    assert (order["plan"]["entryStock"], order["plan"]["t1"], order["plan"]["t2"], order["plan"]["t3"]) == (101.0, 103.0, 105.0, 108.0)
    assert order["plan"]["stopNow"] == 101.0 and order["note"] == "part sold: T1 hit: sold 1, stop to break-even"


# ---------------------------------------------------------------------------
# 2026-11-13 second review: a sale the broker refuses AT SUBMIT must not strand the position.

class RefusingBroker(Broker):
    """Like Alpaca: the next `refuse` sales get the broker's own 403 at submit (no order is
    created), and an id it never took is "order not found" (40410000)."""

    def __init__(self, refuse=1):
        super().__init__()
        self.refuse = refuse

    def submit_option_limit_order(self, symbol, qty, limit, cid, intent):
        if intent == "sell_to_close" and self.refuse > 0:
            self.refuse -= 1
            self.calls.append((intent, symbol, qty, limit))
            raise RuntimeError('403 {"code":40310000,"message":"insufficient qty available for order"}')
        super().submit_option_limit_order(symbol, qty, limit, cid, intent)

    def get_order_by_client_id(self, cid):
        if cid not in self.orders:
            raise RuntimeError('404 {"code":40410000,"message":"order not found for %s"}' % cid)
        return self.orders[cid]


def run_a_refused_target_sale(base, monkeypatch):
    """10:05 a Level signal on X, bought at the ask; 10:10 X reaches T1 and the one-lot sale is
    refused at submit; 30 s later X is back under its entry price."""
    monkeypatch.setattr(option_track, "live_today", lambda row, now: True)
    monkeypatch.setattr(option_track, "fired", lambda *args: [])
    clock = SimpleNamespace(t=at(10, 5).timestamp())
    feed = Feed(clock)
    book = option_track.OptionTrackBook(base, direction="bull", fetch=lambda s: chain(), healthy=lambda: True,
                                        background=False, clock=lambda: clock.t, sleep=lambda s: None, quotes_fetch=feed)
    book._levels.evaluate = lambda *a, **k: {"X": {"version": 1, "highestLevel": 3, "setupCount": 3, "virtualOnly": True}}
    broker = RefusingBroker(refuse=1)
    mirror = paper_mirror.PaperMirror({"bull": book}, base / "momx_paper_mirror" / "paper3", client_factory=lambda: broker,
                                      off=lambda: False, account="paper3", jev_cut=None,
                                      rules=paper_mirror.ACCOUNT_RULES["paper3"], leg=paper_mirror.PAPER_LEG,
                                      book=paper_mirror.ACCOUNTS["paper3"][0], quotes_fetch=feed)

    def step(when, stock, option=None, ticks=(5, 15)):
        clock.t = when.timestamp()
        feed.stock = stock
        if option:
            feed.option = option
        book.apply("Test board", {"rows": [{"symbol": "X", "last": stock, "m5": {"ema21": 90.0}}]}, when)
        for seconds in ticks:
            clock.t = (when + timedelta(seconds=seconds)).timestamp()
            mirror.tick(when + timedelta(seconds=seconds))

    step(at(10, 5), 100.0)
    step(at(10, 10), 102.1, (1.70, 1.80))
    after_refusal = dict(next(iter(mirror._rows.values())))
    step(at(10, 10, 30), 99.9, (1.00, 1.10))
    mirror._save()
    book._flush()
    return SimpleNamespace(book=book, mirror=mirror, broker=broker, after_refusal=after_refusal)


def test_a_target_sale_refused_at_submit_still_leaves_the_stop_working_over_the_routes(tmp_path, monkeypatch):
    """Before: the row stayed "selling" under an id the broker never had - no stop, no targets, no
    15:52 close (paper3 CLS sat that way from 2026-11-10). Now the stop sells everything at the
    bid on the tick the stock trades through it, and the pages show the closed trade."""
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK", {})
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-01-01")
    monkeypatch.setattr(bot_desk, "CHART_PLAN_DAY", "2026-01-01")
    monkeypatch.setattr(bot_desk, "_SIGNALS", {})
    with running_worker(tmp_path, monkeypatch) as worker:
        ran = run_a_refused_target_sale(service.grade_dir(), monkeypatch)
        monkeypatch.setattr(paper_mirror, "_MIRRORS", [ran.mirror])
        admin = worker.as_admin(monkeypatch, email=OWNER)
        status, paper = worker.get("/api/momx-scanner/paper-mirror", headers=admin)
        with running_api(worker_port=worker.port) as api:
            desk_status, desk = api.get("/api/momx-scanner/bot-desk?days=30", headers=ADMIN)

    # One buy, the refused T1 lot, then everything at the bid. Limit orders only.
    assert ran.broker.calls == [("buy_to_open", E_CONTRACT, 3, 1.25), ("sell_to_close", E_CONTRACT, 1, 1.75),
                                ("sell_to_close", E_CONTRACT, 3, 1.00)]
    # Straight after the refusal the row was waiting to sell again, with the broker's reason.
    assert ran.after_refusal["status"] == "exit_due" and ran.after_refusal["sellRejects"] == 1
    assert ran.after_refusal["note"].startswith("The broker refused this sale") and ran.after_refusal["held"] == 3

    assert status == 200
    (row,) = paper["mirrors"][0]["rows"]
    assert row["status"] == "closed" and row["held"] == 0 and row["exitPlan"] == "chart"
    assert [(s["qty"], s["price"], s["why"]) for s in row["sells"]] == [(3, 1.00, "Stop at break-even: stock traded through 100.00")]
    assert row["pl"] == round((1.00 - 1.25) * 300, 2) and "sellRejects" not in row

    assert desk_status == 200
    acct = desk["accounts"]["paper3"]
    assert acct["stats"]["n"] == 1 and acct["stats"]["netPnl"] == -75.0
    log = next(r for r in acct["tradeLog"]["rows"] if r["symbol"] == "X" and r["status"] != "signal")
    assert log["status"] == "closed" and log["note"] == "Stop at break-even: stock traded through 100.00"


def test_an_older_plan_order_sent_on_the_first_chart_plan_day_is_not_judged_over_the_route(tmp_path, monkeypatch):
    """The real start day over the real route: the worker still trades the older plan until it is
    restarted, so an order it sends on 11-13 has no chart-plan marker. It must not open the new
    plan's record at a loss; it is served with the earlier period."""
    import json
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK", {})
    monkeypatch.setattr(bot_desk, "_SIGNALS", {})
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)
    old = {"side": "bull", "rule": "level2", "symbol": "OLD", "at": "2026-11-13T09:37:00-05:00", "qty": 3, "buyLimit": 1.97,
           "status": "closed", "buyFill": 1.97, "buyFilledQty": 3, "pl": -591.0, "closedAt": "2026-11-13T09:58:00-05:00",
           "sells": [{"qty": 3, "price": 0.0, "why": "stop loss -50%", "at": "2026-11-13T09:58:00-05:00"}]}
    new = {**old, "symbol": "NEW", "at": "2026-11-13T10:05:00-05:00", "exitPlan": "chart", "pl": 90.0, "buyLimit": 1.25,
           "buyFill": 1.25, "closedAt": "2026-11-13T10:40:00-05:00",
           "sells": [{"qty": 3, "price": 1.55, "why": "T2 hit: sold the last 1", "at": "2026-11-13T10:40:00-05:00"}]}
    (folder / "2026-11-13.json").write_text(json.dumps({"rows": {"o": old, "n": new}}), encoding="utf-8")
    assert bot_desk.EVALUATION_START == "2026-11-13" == bot_desk.CHART_PLAN_DAY
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-desk?days=90", headers=ADMIN)
    assert status == 200
    acct = body["accounts"]["paper3"]
    assert acct["stats"]["n"] == 1 and acct["stats"]["netPnl"] == 90.0 and acct["balance"] == 10090.0
    (earlier,) = acct["earlier"]
    assert (earlier["trades"], earlier["netPnl"], earlier["afterSwitch"]) == (1, -591.0, 1)
    assert [r["symbol"] for r in earlier["rows"]] == ["OLD"]
    assert [r["symbol"] for r in acct["tradeLog"]["rows"] if r["status"] == "closed"] == ["NEW"]
