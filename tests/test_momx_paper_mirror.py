"""Alpaca paper mirror of the Bot (momx/paper_mirror.py) - fake client only."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from momx import paper_mirror as pm

ET = pm.ET
NOW = datetime(2026, 10, 19, 10, 2, tzinfo=ET)      # a Monday; the default signal (2 min old) fired at 10:00


class FakeClient:
    def __init__(self, paper=True, fill=True):
        self.credentials = SimpleNamespace(paper=paper)
        self.fill = fill
        self.orders = {}
        self.calls = []

    def submit_option_limit_order(self, symbol, qty, limit, cid, intent):
        self.calls.append(("limit", symbol, qty, limit, intent))
        self.orders[cid] = SimpleNamespace(id=cid, status="filled" if self.fill else "new",
                                           filled_avg_price=limit if self.fill else None,
                                           filled_qty=qty if self.fill else 0)

    def submit_option_market_order(self, symbol, qty, cid, intent):
        self.calls.append(("market", symbol, qty, None, intent))
        self.orders[cid] = SimpleNamespace(id=cid, status="filled", filled_avg_price=0.5, filled_qty=qty)

    def get_order_by_client_id(self, cid):
        return self.orders[cid]

    def cancel_order_by_id(self, oid):
        self.orders[oid].status = "canceled"


class Book:
    def __init__(self, trades):
        self._t = trades

    def trades(self):
        return [dict(t) for t in self._t]


def _trade(**kw):
    t = {"rule": "call2h", "symbol": "AAPL", "at": (NOW - timedelta(minutes=2)).isoformat(),
         "entryAt": (NOW - timedelta(minutes=2)).isoformat(), "contract": "AAPL261023C00250000",
         "status": "open", "entry": 1.20, "jevP": 0.25,
         "lastAt": (NOW - timedelta(seconds=20)).isoformat(), "lastBid": 1.15, "lastAsk": 1.25}   # 8% spread (v2 skips >15%)
    t.update(kw)
    # A Level signal is bought only when the Bot gave it a chart plan (paper_mirror.has_plan,
    # 2026-11-13); plan=None in a test = the Bot gave it none.
    if str(t.get("rule")).startswith("level"):
        t.setdefault("plan", {"strike": 250.0})
    return t


def _mirror(tmp_path, trades, client):
    return pm.PaperMirror({"bull": Book(trades)}, tmp_path, client_factory=lambda: client, off=lambda: False)


def _old_plan(m):
    """Turn the orders just sent into positions from BEFORE the chart plan (2026-11-13): no
    exit-plan marker, so they keep the older +100% / high-OI / stop-ladder exits until closed."""
    for row in m._rows.values():
        row.pop("exitPlan", None)


def test_expired_position_is_reconciled_from_broker_after_hours_without_orders(tmp_path):
    c = FakeClient()
    c.get_option_expirations = lambda day: [{"id":"expiry-proof", "activity_type":"OPEXP", "status":"executed",
        "symbol":"BTBT261023C00001000", "qty":"-300", "net_amount":"0", "date":day}]
    m = _mirror(tmp_path, [], c)
    now = datetime(2026,10,30,18,tzinfo=ET)
    m._day = now.date().isoformat()
    row = {"buyId":"old", "buyAt":"2026-10-22T10:29:00-04:00", "contract":"BTBT  261023C00001000",
           "status":"exit_due", "held":300, "buyFill":.01, "buyFilledQty":300, "sells":[]}
    m._rows = {"old":row}
    m.tick(now)
    assert row["status"] == "closed" and row["pl"] == -300 and row["held"] == 0
    assert row["sells"][0]["kind"] == "expiration" and row["sells"][0]["activityId"] == "expiry-proof"
    assert row["closedAt"].startswith("2026-10-23") and not c.calls
    m.tick(now+timedelta(minutes=10))
    assert len(row["sells"]) == 1 and not c.calls
    restored = _mirror(tmp_path, [], c)
    restored._load(m._day)
    assert restored._rows["old"]["pl"] == -300


def test_expired_missing_or_exercise_evidence_does_not_invent_loss_or_retry_sell(tmp_path):
    c = FakeClient()
    c.get_option_expirations = lambda day: [{"id":"exercise", "activity_type":"OPEXC", "status":"executed",
        "symbol":"BTBT261023C00001000", "qty":"-300", "net_amount":"0", "date":day}]
    m = _mirror(tmp_path, [], c)
    now = datetime(2026,10,30,10,tzinfo=ET)
    m._day = now.date().isoformat()
    row = {"buyId":"old", "contract":"BTBT261023C00001000", "status":"exit_due", "held":300, "buyFill":.01, "sells":[]}
    m._rows = {"old":row}
    m.tick(now)
    assert row["status"] == "exit_due" and row["held"] == 300 and not row.get("pl")
    assert not c.calls and "broker expiration evidence" in row["note"]


def test_missing_sell_fill_does_not_create_partial_total_profit():
    row = {"buyFill":1, "sells":[{"qty":1,"price":2},{"qty":1,"price":None}]}
    pm.PaperMirror._close_out(row, NOW.isoformat())
    assert row["status"] == "closed" and row["pl"] is None


def test_buys_an_allowed_paper_trade_at_the_ask(tmp_path):
    """2026-11-12, his decision: the chart plan's entry, a limit at the ASK (it was the mid)."""
    c = FakeClient()
    m = _mirror(tmp_path, [_trade()], c)
    m.tick(NOW)
    assert c.calls == [("limit", "AAPL261023C00250000", 3, 1.25, "buy_to_open")]     # ask $1.25 -> min 3
    row = m.status()["rows"][0]
    assert row["exitPlan"] == pm.EXIT_PLAN and (row["entryBid"], row["entryAsk"], row["entryMid"]) == (1.15, 1.25, 1.2)
    m.tick(NOW + timedelta(seconds=10))
    assert m.status()["totals"]["filled"] == 1


def test_research_snapshot_uses_order_quote_and_is_saved_before_fill(tmp_path):
    c=FakeClient(fill=False)
    seen=[]
    def policy(side,trade,now):
        seen.append(trade)
        return {"approved":True,"enabled":True}
    m=pm.PaperMirror({"bull":Book([_trade(entry=.5,entryBid=.4,entryAsk=.6)])},tmp_path,
                     client_factory=lambda:c,off=lambda:False,ai_policy=policy)
    m.tick(NOW)
    assert seen[0]["entry"] == 1.25 and seen[0]["entryBid"] == 1.15 and seen[0]["entryAsk"] == 1.25
    row=m.status()["rows"][0]
    assert row["entryInputs"]["premium"] == 1.25 and row["entryInputs"]["minute"] == 602
    assert row["status"] == "buying" and row.get("buyFill") is None
    restored=_mirror(tmp_path,[],c); restored._load(NOW.date().isoformat())
    assert restored.status()["rows"][0]["entryInputs"] == row["entryInputs"]


def test_an_ai_no_never_stops_the_buy_and_its_advice_is_kept_on_the_order(tmp_path):
    """2026-11-09, his "Don't block any trade using AI": a "no" is recorded, the buy still goes out."""
    client = FakeClient()
    trades = [_trade()]
    verdict = {"approved": False, "enabled": True, "provider": "ollama", "reason": "weak setup"}
    mirror = pm.PaperMirror({"bull": Book(trades)}, tmp_path, client_factory=lambda: client,
                            off=lambda: False, ai_policy=lambda *args: dict(verdict))
    mirror.tick(NOW)
    assert client.calls == [("limit", trades[0]["contract"], 3, 1.25, "buy_to_open")]
    row = mirror.status()["rows"][0]
    assert row["aiDecision"]["approved"] is False and row["aiDecision"]["reason"] == "weak setup"
    assert row["aiWouldSkip"] is True
    mirror.tick(NOW + timedelta(seconds=20))
    trades[0].update(exitPrice=.6, exitBid=.5, exitAsk=.7, exitReason="strategy exit")
    mirror.tick(NOW + timedelta(seconds=30))
    assert client.calls[-1] == ("limit", trades[0]["contract"], 3, .6, "sell_to_close")


def test_an_ai_error_never_stops_the_buy_but_a_stale_quote_still_does(tmp_path):
    client = FakeClient()
    def failed(*args):
        raise RuntimeError("provider down")
    mirror = pm.PaperMirror({"bull": Book([_trade()])}, tmp_path / "a", client_factory=lambda: client,
                            off=lambda: False, ai_policy=failed)
    mirror.tick(NOW)
    assert len(client.calls) == 1 and client.calls[0][4] == "buy_to_open"
    assert mirror.status()["ai"]["approved"] is True and "taken anyway" in mirror.status()["ai"]["reason"]
    assert "aiWouldSkip" not in mirror.status()["rows"][0]
    for answer in (None, "yes", 1):                                   # an answer that is not a verdict
        other = FakeClient()
        odd = pm.PaperMirror({"bull": Book([_trade()])}, tmp_path / ("odd-%s" % answer), client_factory=lambda: other,
                             off=lambda: False, ai_policy=lambda *args: answer)
        odd.tick(NOW)
        assert len(other.calls) == 1
    stale = FakeClient()                                               # the quote rule is not AI and still applies
    late = pm.PaperMirror({"bull": Book([_trade()])}, tmp_path / "b", client_factory=lambda: stale,
                          off=lambda: False, ai_policy=lambda *args: {"approved": True, "enabled": True})
    late.tick(NOW + timedelta(seconds=121))
    assert not stale.calls


def test_skips_low_jev_stale_expensive_and_non_paper(tmp_path):
    for t in (_trade(jevP=0.05), _trade(jevP=None),
              _trade(at=(NOW - timedelta(minutes=30)).isoformat(), entryAt=(NOW - timedelta(minutes=30)).isoformat()),
              _trade(lastAt=(NOW - timedelta(minutes=10)).isoformat()),
              _trade(lastBid=8.4, lastAsk=8.8), _trade(exitPrice=1.0)):
        c = FakeClient()
        _mirror(tmp_path / str(id(t)), [t], c).tick(NOW)
        assert c.calls == []
    c = FakeClient(paper=False)
    m = _mirror(tmp_path / "live", [_trade()], c)
    m.tick(NOW)
    assert c.calls == [] and "not a paper account" in m.status()["refused"]


def test_sells_at_the_mid_when_the_bot_exits_and_records_pl(tmp_path):
    c = FakeClient()
    trades = [_trade()]
    m = _mirror(tmp_path, trades, c)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=10))
    trades[0].update(exitPrice=1.6, exitBid=1.5, exitAsk=1.7, exitReason="target")
    m.tick(NOW + timedelta(seconds=20))
    assert c.calls[-1] == ("limit", "AAPL261023C00250000", 3, 1.6, "sell_to_close")
    m.tick(NOW + timedelta(seconds=30))
    s = m.status()
    assert s["totals"]["closed"] == 1 and s["totals"]["pl"] == 105.0      # bought at the ask $1.25: 3 x $0.35


def test_unfilled_buy_is_cancelled_and_sell_retries_only_at_mid(tmp_path):
    c = FakeClient(fill=False)
    m = _mirror(tmp_path, [_trade()], c)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=pm.BUY_TTL_SECONDS + 5))
    assert m.status()["rows"][0]["status"] == "missed"

    c2 = FakeClient()
    trades = [_trade(symbol="MSFT", contract="MSFT261023C00500000")]
    m2 = _mirror(tmp_path / "b", trades, c2)
    m2.tick(NOW)
    m2.tick(NOW + timedelta(seconds=5))
    c2.fill = False
    trades[0].update(exitPrice=1.0, exitBid=0.9, exitAsk=1.1)
    t = NOW + timedelta(seconds=10)
    for _ in range(4):                       # every attempt remains at the fresh mid
        trades[0]["lastAt"] = t.isoformat()
        m2.tick(t)
        t += timedelta(seconds=pm.SELL_TTL_SECONDS + 5)
    assert len(c2.calls) == 5
    assert all(call[0] == "limit" and call[3] == 1.0 for call in c2.calls[1:])


def test_caps_one_per_ticker_and_max_open(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "MAX_OPEN", 8)            # the limit as built; in the app it is out of the way since 2026-11-10
    trades = [_trade(rule=f"r{i}", symbol=f"S{i}", contract=f"S{i}C") for i in range(pm.MAX_OPEN + 3)]
    trades.append(_trade(rule="other"))                 # AAPL again: same ticker
    trades.append(_trade(rule="dupe"))
    c = FakeClient(fill=False)
    m = _mirror(tmp_path, trades, c)
    m.tick(NOW)
    assert len(c.calls) == pm.MAX_OPEN


def test_off_outside_hours_and_weekends(tmp_path):
    c = FakeClient()
    m = _mirror(tmp_path, [_trade()], c)
    m.tick(datetime(2026, 10, 19, 9, 20, tzinfo=ET))
    m.tick(datetime(2026, 10, 18, 11, 0, tzinfo=ET))   # Sunday
    m.tick(NOW.replace(hour=15, minute=40))           # no new entries after 15:30
    assert c.calls == []


def test_all_trades_book_ignores_jev_and_accounts_get_distinct_order_ids(tmp_path):
    c3, c5 = FakeClient(), FakeClient()
    trades = [_trade(jevP=None)]
    m3 = pm.PaperMirror({"bull": Book(trades)}, tmp_path / "p3", client_factory=lambda: c3, off=lambda: False,
                        account="paper3", book="All trades", jev_cut=None)
    m5 = pm.PaperMirror({"bull": Book(trades)}, tmp_path / "p5", client_factory=lambda: c5, off=lambda: False,
                        account="paper5", book="Jev BOT", jev_cut=0.10)
    m3.tick(NOW)
    m5.tick(NOW)
    assert len(c3.calls) == 1 and c5.calls == []             # no Jev score: only All trades buys
    assert m3.status()["account"] == "paper3" and m3.status()["rules"]["book"] == "All trades"
    assert m3._cid("k", "b") != m5._cid("k", "b")


def test_both_accounts_trade_only_the_three_level_strategies_at_020_delta():
    """2026-11-09, his "use only 3 strategies trades and 0.2 delta from tomorrow"."""
    assert pm.ACCOUNTS["paper3"][1] is None and pm.ACCOUNTS["paper5"][1] is None        # no Jev cut
    for account in ("paper3", "paper5"):
        assert pm.ACCOUNT_RULES[account] == {"bull": frozenset({"level1", "level2", "level3"}),
                                             "bear": frozenset({"level1", "level2", "level3"})}
    assert pm.PAPER_LEG == "entry"


def test_neither_account_waits_for_ai_to_be_switched_on_or_for_a_jev_score(tmp_path):
    for account in ("paper3", "paper5"):
        client = FakeClient()
        mirror = pm.PaperMirror({"bull": Book([_trade(rule="level2", jevP=None, virtualOnly=True)])}, tmp_path / account,
                                client_factory=lambda: client, off=lambda: False, account=account,
                                jev_cut=pm.ACCOUNTS[account][1], rules=pm.ACCOUNT_RULES[account],
                                ai_policy=lambda *args: {"approved": True, "enabled": False})
        mirror.tick(NOW)
        assert len(client.calls) == 1 and client.calls[0][3] == 1.25
        assert mirror.status()["rows"][0]["aiDecision"]["enabled"] is False


def test_paper3_trades_only_the_picked_rules(tmp_path):
    c = FakeClient()
    trades = [_trade(rule="zs2", symbol="A", contract="AC"), _trade(rule="mom", symbol="B", contract="BC")]
    m = pm.PaperMirror({"bull": Book(trades)}, tmp_path, client_factory=lambda: c, off=lambda: False,
                       account="paper3", book="All trades - picked rules", jev_cut=None, rules=pm.PICKED_RULES)
    m.tick(NOW)
    assert [call[1] for call in c.calls] == ["AC"]
    assert len(pm.PICKED_RULES["bull"]) == 13 and len(pm.PICKED_RULES["bear"]) == 16
    assert "mom" in pm.PICKED_RULES["bear"] and "zs2" not in pm.PICKED_RULES["bear"]
    assert "mom" not in pm.PICKED_RULES["bull"] and "rvol" in pm.PICKED_RULES["bull"]


def test_paper5_trades_without_any_ai_policy(tmp_path):
    """It used to place nothing unless an AI policy was wired; AI is no longer a condition."""
    c = FakeClient()
    bull = [_trade(rule="adx", symbol="A", contract="AC"), _trade(rule="go", symbol="B", contract="BC")]
    bear = [_trade(rule="go", symbol="C", contract="CC"), _trade(rule="star1", symbol="D", contract="DC")]
    m = pm.PaperMirror({"bull": Book(bull), "bear": Book(bear)}, tmp_path, client_factory=lambda: c,
                       off=lambda: False, account="paper5", jev_cut=0.10, rules=pm.PAPER5_RULES)
    m.tick(NOW)
    assert sorted(call[1] for call in c.calls) == ["AC", "CC"]          # the rules it is given, nothing else


def test_his_sizing_by_price():
    assert pm.size_for(0.25) == 12 and pm.size_for(0.30) == 10 and pm.size_for(0.50) == 6
    assert pm.size_for(1.00) == 3 and pm.size_for(2.50) == 3 and pm.size_for(4.00) == 3
    # 2026-11-10, his rule: 3 contracts up to $8 so 2 can be sold and 1 kept (was 1 over $4).
    assert pm.size_for(4.01) == 3 and pm.size_for(5.00) == 3 and pm.size_for(8.00) == 3 and pm.size_for(8.01) is None


def test_his_stop_ladder():
    assert pm.stop_for(1.0, 0.9) is None
    assert pm.stop_for(1.0, 1.0) == 1.0 and pm.stop_for(1.0, 1.7) == 1.0      # +100%: break-even
    assert pm.stop_for(1.0, 2.0) == 2.0 and pm.stop_for(1.0, 3.4) == 3.0      # +200% -> +100%, +300% -> +200%


def test_an_older_position_keeps_plus_100_sells_two_thirds_then_break_even_stop_sells_the_rest(tmp_path):
    """2026-11-10, how he traded by hand: "took 3 cons, 2 cons exit at 100% ... 1 cons move to BE".
    Since the chart plan (2026-11-13) this is only the plan of positions opened BEFORE the switch."""
    c = FakeClient()
    trades = [_trade(lastBid=0.25, lastAsk=0.25)]            # 0.25 -> 12 contracts
    m = _mirror(tmp_path, trades, c)
    m.tick(NOW)
    _old_plan(m)
    m.tick(NOW + timedelta(seconds=10))
    row = m.status()["rows"][0]
    assert row["held"] == 12 and row["buyFill"] == 0.25
    trades[0].update(lastBid=0.49, lastAsk=0.51, lastAt=(NOW + timedelta(seconds=15)).isoformat())   # +100%
    m.tick(NOW + timedelta(seconds=20))
    assert c.calls[-1] == ("limit", "AAPL261023C00250000", 8, 0.5, "sell_to_close")                  # two thirds of 12
    m.tick(NOW + timedelta(seconds=30))
    row = m.status()["rows"][0]
    assert row["held"] == 4 and row["partialDone"] and row["stop"] == 0.25
    trades[0].update(lastBid=0.24, lastAsk=0.26, lastAt=(NOW + timedelta(seconds=35)).isoformat())   # back to BE
    m.tick(NOW + timedelta(seconds=40))
    assert c.calls[-1] == ("limit", "AAPL261023C00250000", 4, 0.25, "sell_to_close")
    m.tick(NOW + timedelta(seconds=50))
    s = m.status()
    assert s["rows"][0]["status"] == "closed" and s["totals"]["pl"] == 200.0          # 8 x $0.25 profit, 4 at break-even


def test_an_older_positions_ladder_moves_stop_to_plus_100_after_plus_200(tmp_path):
    c = FakeClient()
    trades = [_trade(lastBid=0.25, lastAsk=0.25)]
    m = _mirror(tmp_path, trades, c)
    m.tick(NOW)
    _old_plan(m)
    m.tick(NOW + timedelta(seconds=10))
    trades[0].update(lastBid=0.74, lastAsk=0.76, lastAt=(NOW + timedelta(seconds=15)).isoformat())   # +200%
    m.tick(NOW + timedelta(seconds=20))
    m.tick(NOW + timedelta(seconds=30))
    assert m.status()["rows"][0]["stop"] == 0.5
    trades[0].update(lastBid=0.49, lastAsk=0.51, lastAt=(NOW + timedelta(seconds=35)).isoformat())   # falls to +100%
    m.tick(NOW + timedelta(seconds=40))
    assert c.calls[-1][2] == 4 and c.calls[-1][4] == "sell_to_close"                  # the third left after 8 of 12 came off


def test_history_reads_saved_days_newest_first(tmp_path):
    import json as _json
    (tmp_path / "2026-10-15.json").write_text(_json.dumps({"day": "2026-10-15", "rows": {"k": {"symbol": "OLD"}}}))
    c = FakeClient()
    m = _mirror(tmp_path, [_trade()], c)
    m.tick(NOW)
    hist = m.history(5)
    assert [h["day"] for h in hist] == ["2026-10-19", "2026-10-15"]
    assert hist[0]["rows"][0]["symbol"] == "AAPL" and hist[1]["rows"][0]["symbol"] == "OLD"


def test_new_buys_only_in_his_windows():
    """His windows: 9:30-11:00 and 1:00-3:30 ET. (2026-11-12 the chart-plan build moved the start
    to 10:00; 2026-11-13 he took that back: "Remove 10 am rule on bot also if you see the setup
    then trade it". This test asserted the 10:00 start in between.)"""
    d = NOW.replace
    assert pm.ENTRY_WINDOWS == ((9, 30, 11, 0), (13, 0, 15, 30))
    assert not pm.entry_open(d(hour=9, minute=29))
    assert pm.entry_open(d(hour=9, minute=30)) and pm.entry_open(d(hour=9, minute=59))
    assert pm.entry_open(d(hour=10, minute=0)) and pm.entry_open(d(hour=10, minute=59))
    assert not pm.entry_open(d(hour=11, minute=0)) and not pm.entry_open(d(hour=12, minute=30))
    assert pm.entry_open(d(hour=13, minute=0)) and not pm.entry_open(d(hour=15, minute=30))
    assert not hasattr(pm, "NO_BUY_BEFORE") and not hasattr(pm, "signal_too_early")


def test_a_setup_between_930_and_1000_is_bought(tmp_path):
    """Was test_no_buy_between_930_and_1000 (the 10:00 rule, removed 2026-11-13): "Start trading
    9:30 if you see the setup"."""
    for minute in (30, 45, 59):
        c = FakeClient()
        early = NOW.replace(hour=9, minute=minute)
        m = _mirror(tmp_path / str(minute), [_trade(at=(early - timedelta(seconds=20)).isoformat(),
                                                    entryAt=(early - timedelta(seconds=20)).isoformat(),
                                                    lastAt=(early - timedelta(seconds=20)).isoformat())], c)
        m.tick(early)
        assert len(c.calls) == 1 and c.calls[0][4] == "buy_to_open", minute


def test_a_signal_that_fired_before_ten_is_bought_like_any_other(tmp_path):
    """Was test_a_signal_that_fired_before_ten_is_never_bought_even_when_still_fresh_at_ten (the
    10:00 rule, removed 2026-11-13): a 9:58 signal still fresh at 10:02 gets its order, and the
    AI picker is offered it."""
    ten = NOW.replace(hour=10, minute=0)
    c = FakeClient()
    early = (ten - timedelta(minutes=2)).isoformat()
    m = _mirror(tmp_path / "early", [_trade(at=early, entryAt=early)], c)
    m.tick(NOW)
    assert len(c.calls) == 1 and m.status()["rows"][0]["at"] == early
    m3 = _mirror(tmp_path / "pick", [_trade(at=early, entryAt=early)], FakeClient())
    m3._day = NOW.date().isoformat()
    assert [k for k, _, _ in m3.candidates(NOW)] == [pm.trade_key("bull", _trade(at=early, entryAt=early))]


def test_a_level_signal_with_no_chart_plan_is_never_bought_and_not_offered_to_the_ai(tmp_path):
    """2026-11-13 review: a paper order must never go out without a chart plan (the stock stop
    and T1 / T2 / T3). The Bot gives the plan (option_track.plan_open); no plan = no order,
    checked again while the signal is fresh."""
    c = FakeClient()
    trades = [_level("level3", plan=None, planNote="No chart plan: the option chain was not available at the signal")]
    m = _level_mirror(tmp_path, trades, c)
    for k in range(4):
        m.tick(NOW + timedelta(seconds=10 * k))
    assert c.calls == [] and m.status()["rows"] == []
    assert m.candidates(NOW + timedelta(seconds=30)) == []
    assert pm.has_plan({"plan": {"strike": 250.0}}) and not pm.has_plan({}) and not pm.has_plan({"plan": {}})
    assert not pm.has_plan({"plan": {"strike": 250.0}, "eStrike": 255.0}, "entry")
    # The Bot's plan arrives while the signal is still fresh: now it is bought.
    trades[0]["plan"] = {"strike": 250.0}
    m.tick(NOW + timedelta(seconds=40))
    assert len(c.calls) == 1 and m.status()["rows"][0]["exitPlan"] == "chart"


def test_no_buy_at_midday(tmp_path):
    c = FakeClient()
    noon = NOW.replace(hour=12, minute=0)
    m = _mirror(tmp_path, [_trade(at=(noon - timedelta(minutes=2)).isoformat(), entryAt=(noon - timedelta(minutes=2)).isoformat(),
                                  lastAt=(noon - timedelta(seconds=20)).isoformat())], c)
    m.tick(noon)
    assert c.calls == []


def test_pending_cancel_keeps_order_live_and_prevents_duplicate_sell(tmp_path):
    class SlowCancel(FakeClient):
        def cancel_order_by_id(self, oid):
            self.orders[oid].status = "pending_cancel"
    c = SlowCancel()
    trades = [_trade()]
    m = _mirror(tmp_path, trades, c)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=5))
    c.fill = False
    trades[0].update(exitPrice=1, exitBid=0.9, exitAsk=1.1)
    m.tick(NOW + timedelta(seconds=10))
    m.tick(NOW + timedelta(seconds=80))
    m.tick(NOW + timedelta(seconds=90))
    assert len(c.calls) == 2
    assert m.status()["rows"][0]["status"] == "selling"


def test_stale_exit_waits_without_market_order_and_recovers(tmp_path):
    c = FakeClient()
    trades = [_trade()]
    m = _mirror(tmp_path, trades, c)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=5))
    trades[0].update(exitPrice=1, exitBid=0.9, exitAsk=1.1)
    m.tick(NOW + timedelta(seconds=200))
    row = m.status()["rows"][0]
    assert row["status"] == "exit_due" and "Waiting" in row["note"]
    assert len(c.calls) == 1
    trades[0]["lastAt"] = (NOW + timedelta(seconds=210)).isoformat()
    m.tick(NOW + timedelta(seconds=210))
    assert c.calls[-1][0] == "limit" and c.calls[-1][3] == 1


def test_invalid_quotes_and_future_signals_cannot_enter(tmp_path):
    for changes in ({"lastAsk": float("inf")}, {"lastBid": 2, "lastAsk": 1},
                    {"lastBid": None}, {"entryAt": (NOW + timedelta(seconds=20)).isoformat()}):
        c = FakeClient()
        _mirror(tmp_path / str(len(str(changes))), [_trade(**changes)], c).tick(NOW)
        assert not c.calls


def test_unfilled_position_survives_rollover_and_restart(tmp_path):
    c = FakeClient()
    m = _mirror(tmp_path, [_trade()], c)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=5))
    m2 = _mirror(tmp_path, [], c)
    m2.tick(NOW + timedelta(days=1))
    row = m2.status()["rows"][0]
    assert row["held"] == 3 and row["carried"]
    assert row["status"] == "exit_due"
    assert len(c.calls) == 1  # no quote, no fabricated market exit


def test_one_unavailable_order_does_not_block_other_positions(tmp_path):
    c = FakeClient()
    trades = [_trade(), _trade(symbol="MSFT", contract="MSFTC")]
    m = _mirror(tmp_path, trades, c)
    m.tick(NOW)
    first = next(iter(c.orders))
    del c.orders[first]
    m.tick(NOW + timedelta(seconds=5))
    states = [r["status"] for r in m.status()["rows"]]
    assert sorted(states) == ["buying", "open"]
    assert len(c.calls) == 2


def test_carried_history_counts_each_broker_order_once(tmp_path):
    c = FakeClient()
    m = _mirror(tmp_path, [_trade()], c)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=5))
    m.tick(NOW + timedelta(days=1))
    history = m.history(5)
    assert sum(len(d["rows"]) for d in history) == 1
    assert history[0]["day"] == NOW.date().isoformat()
    assert history[0]["rows"][0]["status"] == "exit_due"


def test_display_records_partial_filled_quantity_broker_time_and_midpoint(tmp_path):
    c = FakeClient(fill=False)
    m = _mirror(tmp_path, [_trade()], c)
    m.tick(NOW)
    order = next(iter(c.orders.values()))
    order.status = "canceled"
    order.filled_qty = 1
    order.filled_avg_price = 1.2
    order.filled_at = NOW.isoformat()
    m.tick(NOW + timedelta(seconds=10))
    row = m.status()["rows"][0]
    assert row["qty"] == 3 and row["buyFilledQty"] == row["held"] == 1
    assert row["buyFilledAt"] == NOW.isoformat()
    assert row["markMid"] == 1.2 and row["markAt"] == _trade()["lastAt"]
    m.tick(NOW + timedelta(seconds=140))
    assert m.status()["rows"][0]["markMid"] is None
    assert len(c.calls) == 1


def test_retired_jev_rule_cannot_open_a_new_paper_position(tmp_path):
    for account in ('paper3','paper5'):
        for side,rules in pm.ACCOUNT_RULES[account].items():
            assert 'jev' not in rules
        client=FakeClient()
        mirror=pm.PaperMirror({'bull':Book([_trade(rule='jev',jevP=.99)])},tmp_path/account,
            client_factory=lambda:client,off=lambda:False,account=account,jev_cut=None,rules=None)
        mirror.tick(NOW)
        assert not client.calls
        assert not pm.eligible(_trade(rule='jev',jevP=.99),NOW.timestamp(),None)


# ---- order rules v2 (2026-11-04/05: "Fix all", then "dont miss any trade") ----

CONTRACT = "AAPL261023C00250000"


class Quotes:
    """A live broker quote feed (the /api/option-quotes shape) the test can move."""
    def __init__(self, bid, ask):
        self.bid, self.ask, self.asof, self.asked = bid, ask, NOW.timestamp(), []

    def __call__(self, contracts):
        self.asked.append(list(contracts))
        return {"asOf": self.asof, "quotes": {c: {"bid": self.bid, "ask": self.ask} for c in contracts}}

    def move(self, bid, ask, at):
        self.bid, self.ask, self.asof = bid, ask, at.timestamp()


def _mirror_q(tmp_path, trades, client, quotes):
    return pm.PaperMirror({"bull": Book(trades)}, tmp_path, client_factory=lambda: client, off=lambda: False,
                          quotes_fetch=quotes)


def test_wide_spread_is_still_bought_and_its_spread_recorded(tmp_path):
    # His "dont think about spready just take the trade" (2026-11-05): no spread filter.
    c = FakeClient()
    m = _mirror(tmp_path, [_trade(lastBid=1.0, lastAsk=1.4)], c)          # 33% of the mid
    m.tick(NOW)
    assert c.calls == [("limit", CONTRACT, 3, 1.4, "buy_to_open")]          # at the ask, however wide
    row = m.status()["rows"][0]
    assert row["exec"] == pm.EXEC_VERSION and round(row["spreadAtOrder"], 3) == 0.333


def test_spread_share_edges():
    assert round(pm.spread_share(1.15, 1.25), 4) == 0.0833
    assert pm.spread_share(None, 1) is None and pm.spread_share(2, 1) is None


def test_live_quote_is_used_before_the_bots_older_quote(tmp_path):
    c = FakeClient()
    q = Quotes(1.35, 1.45)
    m = _mirror_q(tmp_path, [_trade()], c, q)                               # Bot's own quote: ask 1.25
    m.tick(NOW)
    # The live ask; the stock's price rides in the same call (the chart plan's stock price at entry).
    assert c.calls[0][3] == 1.45 and q.asked == [[CONTRACT, "AAPL"]]
    row = m.status()["rows"][0]
    assert row["quoteVia"] == "live" and row["exec"] == pm.EXEC_VERSION and row["buyTries"] == 0


def test_stale_live_quote_falls_back_to_the_bots_fresh_quote(tmp_path):
    c = FakeClient()
    q = Quotes(1.35, 1.45)
    q.asof = NOW.timestamp() - pm.QUOTE_FRESH_SECONDS - 30
    m = _mirror_q(tmp_path, [_trade()], c, q)
    m.tick(NOW)
    assert c.calls[0][3] == 1.25 and m.status()["rows"][0]["quoteVia"] == "bot"


def test_an_unfilled_buy_at_the_ask_is_resent_at_the_new_ask_twice_then_missed(tmp_path):
    c = FakeClient(fill=False)
    q = Quotes(1.16, 1.24)                                                  # ask 1.24
    m = _mirror_q(tmp_path, [_trade()], c, q)
    m.tick(NOW)
    t1 = NOW + timedelta(seconds=pm.BUY_TTL_SECONDS + 5)
    q.move(1.30, 1.40, t1)                                                  # the call ran
    m.tick(t1)
    t2 = t1 + timedelta(seconds=pm.BUY_TTL_SECONDS + 5)
    q.move(1.45, 1.55, t2)
    m.tick(t2)
    buys = [call[3] for call in c.calls if call[4] == "buy_to_open"]
    assert buys == [1.24, 1.4, 1.55]                                        # the ask each time, as it moves
    row = m.status()["rows"][0]
    assert row["status"] == "buying" and row["buyTries"] == 2 and len(row["buyHistory"]) == 2
    assert "ask $1.55" in row["note"]
    assert len({h["id"] for h in row["buyHistory"]} | {row["buyId"]}) == 3      # a new order id each time
    m.tick(t2 + timedelta(seconds=pm.BUY_TTL_SECONDS + 5))
    assert m.status()["rows"][0]["status"] == "missed" and len(c.calls) == 3


def test_an_ask_resend_that_fills_opens_the_position(tmp_path):
    c = FakeClient(fill=False)
    q = Quotes(1.16, 1.24)
    m = _mirror_q(tmp_path, [_trade()], c, q)
    m.tick(NOW)
    c.fill = True
    t1 = NOW + timedelta(seconds=pm.BUY_TTL_SECONDS + 5)
    q.move(1.30, 1.40, t1)
    m.tick(t1)
    m.tick(t1 + timedelta(seconds=10))
    row = m.status()["rows"][0]
    assert row["status"] == "open" and row["buyFill"] == 1.4 and row["buyTries"] == 1


def test_no_fresh_quote_means_no_resend(tmp_path):
    c = FakeClient(fill=False)
    q = Quotes(1.16, 1.24)
    m = _mirror_q(tmp_path, [_trade()], c, q)
    m.tick(NOW)
    t1 = NOW + timedelta(seconds=pm.BUY_TTL_SECONDS + 5)
    q.asof = t1.timestamp() - pm.QUOTE_FRESH_SECONDS - 30                    # live feed stale; Bot quote stale too
    m.tick(t1)
    row = m.status()["rows"][0]
    assert row["status"] == "missed" and "No fresh quote" in row["note"] and len(c.calls) == 1


def test_no_resend_once_the_bot_trade_is_no_longer_eligible(tmp_path):
    c = FakeClient(fill=False)
    q = Quotes(1.16, 1.24)
    trades = [_trade()]
    m = _mirror_q(tmp_path, trades, c, q)
    m.tick(NOW)
    trades[0].update(exitPrice=1.0, exitBid=0.95, exitAsk=1.05)            # the Bot already sold
    t1 = NOW + timedelta(seconds=pm.BUY_TTL_SECONDS + 5)
    q.move(1.17, 1.25, t1)
    m.tick(t1)
    assert m.status()["rows"][0]["status"] == "missed" and len(c.calls) == 1


def test_legacy_rows_without_exec_are_never_resent(tmp_path):
    c = FakeClient(fill=False)
    q = Quotes(1.16, 1.24)
    m = _mirror_q(tmp_path, [_trade()], c, q)
    m._day = NOW.date().isoformat()
    key = pm.trade_key("bull", _trade())
    cid = m._cid(key, "b")
    c.submit_option_limit_order(CONTRACT, 3, 1.2, cid, "buy_to_open")
    m._rows = {key: {"side": "bull", "rule": "call2h", "symbol": "AAPL", "at": _trade()["at"], "contract": CONTRACT,
                     "qty": 3, "buyLimit": 1.2, "buyId": cid, "buyAt": NOW.isoformat(), "status": "buying"}}
    t1 = NOW + timedelta(seconds=pm.BUY_TTL_SECONDS + 5)
    q.move(1.17, 1.25, t1)
    m.tick(t1)
    assert m.status()["rows"][0]["status"] == "missed" and len(c.calls) == 1


# ---- his manual plan, 2026-11-10: 2 of 3 off at +100% or the high OI level; -50% stop
def test_three_contracts_two_come_off_and_one_runs():
    assert [pm.partial_qty(q, q) for q in (3, 5, 12, 60)] == [2, 3, 8, 40]
    assert pm.partial_qty(3, 2) == 1 and pm.partial_qty(3, 1) == 0 and pm.partial_qty(1, 1) == 0     # always one left to run


def test_the_target_is_the_nearest_high_oi_wall_ahead_of_the_stock():
    walls = {"spot": 100.0, "callWalls": [{"strike": 110.0, "oi": 9}, {"strike": 103.0, "oi": 5}, {"strike": 95.0, "oi": 7}],
             "putWalls": [{"strike": 90.0, "oi": 9}, {"strike": 97.0, "oi": 5}, {"strike": 104.0, "oi": 1}]}
    assert pm.oi_target("bull", walls) == 103.0 and pm.oi_target("bear", walls) == 97.0
    assert pm.oi_target("bull", {"spot": 100.0, "callWalls": [{"strike": 99.0}]}) is None            # no wall ahead
    assert pm.oi_target("bull", {"callWalls": [{"strike": 103.0}]}) is None                          # no stock price


def _held(tmp_path, client, **trade):
    """Three contracts at $1.00 held under the OLDER plan (a position from before 2026-11-13):
    no chart-plan marker, and the high-OI wall ahead as its target."""
    trades = [_trade(lastBid=1.0, lastAsk=1.0, spot=100.0, callWalls=[{"strike": 103.0, "oi": 5000}], **trade)]   # $1.00 -> 3 contracts
    m = _mirror(tmp_path, trades, client)
    m.tick(NOW)
    _old_plan(m)
    for r in m._rows.values():
        r["targetStrike"] = pm.oi_target("bull", trades[0])
    m.tick(NOW + timedelta(seconds=10))
    row = m.status()["rows"][0]
    assert row["held"] == 3 and row["targetStrike"] == 103.0 and "exitPlan" not in row
    return m, trades


def test_the_stock_reaching_the_high_oi_level_sells_two_of_three_and_moves_the_rest_to_break_even(tmp_path):
    c = FakeClient()
    m, trades = _held(tmp_path, c)
    at = NOW + timedelta(seconds=15)
    trades[0].update(lastBid=1.39, lastAsk=1.41, lastAt=at.isoformat(), stockLast=102.9, stockLastAt=at.isoformat())   # +40%, not there yet
    m.tick(NOW + timedelta(seconds=20))
    assert not [x for x in c.calls if x[4] == "sell_to_close"]
    at = NOW + timedelta(seconds=25)
    trades[0].update(lastAt=at.isoformat(), stockLast=103.05, stockLastAt=at.isoformat())                              # the wall is reached
    m.tick(NOW + timedelta(seconds=30))
    assert c.calls[-1] == ("limit", "AAPL261023C00250000", 2, 1.4, "sell_to_close")
    m.tick(NOW + timedelta(seconds=40))
    row = m.status()["rows"][0]
    assert row["held"] == 1 and row["partialDone"] and row["stop"] == 1.0 and row["sells"][0]["why"] == pm.TARGET_WHY
    at = NOW + timedelta(seconds=45)
    trades[0].update(lastBid=0.99, lastAsk=1.01, lastAt=at.isoformat())                                                # back to the buy price
    m.tick(NOW + timedelta(seconds=50))
    assert c.calls[-1] == ("limit", "AAPL261023C00250000", 1, 1.0, "sell_to_close")                                    # the runner is not allowed to lose


def test_an_old_stock_price_does_not_count_as_reaching_the_level(tmp_path):
    c = FakeClient()
    m, trades = _held(tmp_path, c)
    stale = NOW - timedelta(seconds=pm.STOCK_FRESH_SECONDS + 60)
    trades[0].update(lastAt=(NOW + timedelta(seconds=15)).isoformat(), stockLast=104.0, stockLastAt=stale.isoformat())
    m.tick(NOW + timedelta(seconds=20))
    assert not [x for x in c.calls if x[4] == "sell_to_close"]


def test_down_fifty_percent_for_two_ticks_sells_everything(tmp_path):
    """His "Exit -50%". One bad quote is not enough; the second tick in a row sells."""
    c = FakeClient()
    m, trades = _held(tmp_path, c)
    trades[0].update(lastBid=0.48, lastAsk=0.50, lastAt=(NOW + timedelta(seconds=15)).isoformat())     # -51%
    m.tick(NOW + timedelta(seconds=20))
    assert not [x for x in c.calls if x[4] == "sell_to_close"] and m.status()["rows"][0]["lossTicks"] == 1
    trades[0].update(lastAt=(NOW + timedelta(seconds=25)).isoformat())
    m.tick(NOW + timedelta(seconds=30))
    assert c.calls[-1] == ("limit", "AAPL261023C00250000", 3, 0.49, "sell_to_close")
    m.tick(NOW + timedelta(seconds=40))
    row = m.status()["rows"][0]
    assert row["status"] == "closed" and row["sells"][0]["why"] == pm.STOP_LOSS_WHY and row["pl"] == -153.0


def test_one_bad_quote_does_not_stop_a_position_out(tmp_path):
    c = FakeClient()
    m, trades = _held(tmp_path, c)
    trades[0].update(lastBid=0.40, lastAsk=0.44, lastAt=(NOW + timedelta(seconds=15)).isoformat())     # -58% for one tick
    m.tick(NOW + timedelta(seconds=20))
    trades[0].update(lastBid=0.94, lastAsk=0.96, lastAt=(NOW + timedelta(seconds=25)).isoformat())     # the quote comes back
    m.tick(NOW + timedelta(seconds=30))
    row = m.status()["rows"][0]
    assert row["status"] == "open" and row["lossTicks"] == 0 and not [x for x in c.calls if x[4] == "sell_to_close"]


def test_down_forty_nine_percent_is_held(tmp_path):
    c = FakeClient()
    m, trades = _held(tmp_path, c)
    for i in range(3):
        trades[0].update(lastBid=0.50, lastAsk=0.52, lastAt=(NOW + timedelta(seconds=15 + 10 * i)).isoformat())   # -49%
        m.tick(NOW + timedelta(seconds=20 + 10 * i))
    assert m.status()["rows"][0]["status"] == "open" and not m.status()["rows"][0].get("lossTicks")


def test_with_no_open_limit_every_signal_gets_an_order(tmp_path):
    """2026-11-10, his "No limit for open": 20 different tickers waiting -> 20 orders, not 8."""
    assert pm.MAX_OPEN >= 500 and pm.MAX_ENTRIES_PER_DAY >= 500
    trades = [_trade(rule=f"r{i}", symbol=f"S{i}", contract=f"S{i}C") for i in range(20)]
    c = FakeClient(fill=False)
    m = _mirror(tmp_path, trades, c)
    m.tick(NOW)
    assert len(c.calls) == 20


def test_each_buy_carries_a_robinhood_style_fill_label_and_it_never_changes_the_price(tmp_path):
    """2026-11-10, "yes same like robinhood": a label on the order; it never changes the limit."""
    t = _trade(rule="rv", symbol="S1", contract="S1C")
    c = FakeClient(fill=False)
    m = _mirror(tmp_path, [t], c)
    m.tick(NOW)
    row = next(iter(m._rows.values()))
    assert row["fillChance"]["label"] in ("high", "medium", "low") and row["fillChance"]["by"] == "spread"
    assert row["fillChance"]["spreadPct"] == round(row["spreadAtOrder"] * 100, 1)
    assert c.calls[0][3] == row["buyLimit"]


# ---- his rule 2026-11-10: the 5m chart check before a Level buy, and 3 contracts up to $8
def _chart(symbol="AAPL", price=100.0, **levels):
    """A frozen completed-5m chart on the signal, as option_track stores it (setup.chartContext)."""
    at = (NOW - timedelta(minutes=2)).timestamp()
    return {"version": 1, "timeframe": "5m", "symbol": symbol, "barAt": at - 360, "availableAt": at - 60,
            "capturedAt": at - 30, "referencePrice": price, "levels": {"ema21": 99.0, **levels}}


def _level_mirror(tmp_path, trades, client, account="paper3"):
    return pm.PaperMirror({"bull": Book(trades)}, tmp_path, client_factory=lambda: client, off=lambda: False,
                          account=account, jev_cut=None, rules=pm.ACCOUNT_RULES[account])


def _level(rule="level2", **kw):
    return _trade(rule=rule, jevP=None, virtualOnly=True, **kw)


def test_a_failing_chart_check_skips_the_buy_and_the_reason_is_saved_and_restored(tmp_path):
    for account in ("paper3", "paper5"):
        c = FakeClient()
        chart = _chart(price=100.0, ema21=101.0)                     # a call under the 21 EMA
        trades = [_level("level3", setup={"chartContext": chart}), _level("level2", setup={"chartContext": chart})]
        m = _level_mirror(tmp_path / account, trades, c, account)
        m.tick(NOW)
        assert c.calls == [] and m.status()["rows"] == [] and m.status()["chartSkipped"] == 1   # one signal, one skip
        skip = next(iter(m._chart_skips.values()))
        assert skip["symbol"] == "AAPL" and skip["side"] == "bull" and skip["rule"] == "level3"
        assert skip["why"] == "price under the 21 EMA" and skip["at"] == trades[0]["at"]
        m.tick(NOW + timedelta(seconds=10))
        assert c.calls == []                                          # still no order on the next tick
        restored = _level_mirror(tmp_path / account, [], c, account)
        restored._load(NOW.date().isoformat())
        assert restored._chart_skips == m._chart_skips                # a restart keeps the reason


def test_no_room_before_the_next_level_also_skips(tmp_path):
    c = FakeClient()
    m = _level_mirror(tmp_path, [_level(setup={"chartContext": _chart(price=100.0, R1=100.3)})], c)
    m.tick(NOW)
    assert c.calls == [] and next(iter(m._chart_skips.values()))["why"] == "R1 $100.30 is only 0.3% ahead"


def test_a_passing_chart_buys_and_says_passed_and_a_missing_chart_buys_as_not_checked(tmp_path):
    c = FakeClient()
    good = _level(setup={"chartContext": _chart(price=100.0, R1=100.8)})
    m = _level_mirror(tmp_path / "a", [good], c)
    m.tick(NOW)
    assert len(c.calls) == 1 and m.status()["rows"][0]["chartCheck"] == "passed" and not m._chart_skips
    c2 = FakeClient()
    m2 = _level_mirror(tmp_path / "b", [_level()], c2)                # no chart on the signal: never blocked
    m2.tick(NOW)
    assert len(c2.calls) == 1 and m2.status()["rows"][0]["chartCheck"] == "not checked"


def test_the_ai_picker_is_not_offered_a_signal_the_chart_check_turns_down(tmp_path):
    c = FakeClient()
    bad = _level(symbol="BAD", contract="BADC", setup={"chartContext": _chart("BAD", price=100.0, ema21=101.0)})
    good = _level(symbol="AAPL", setup={"chartContext": _chart(price=100.0)})
    m = _level_mirror(tmp_path, [bad, good], c, "paper5")
    m._day = NOW.date().isoformat()
    assert [t["symbol"] for _, _, t in m.candidates(NOW)] == ["AAPL"]


def test_a_five_dollar_option_buys_three_so_one_can_come_off_at_each_target(tmp_path):
    c = FakeClient()
    m = _level_mirror(tmp_path, [_level(lastBid=4.95, lastAsk=5.05)], c)
    m.tick(NOW)
    assert c.calls == [("limit", "AAPL261023C00250000", 3, 5.05, "buy_to_open")]
    rules = m.status()["rules"]
    assert "at least 3 contracts" in rules["sizing"] and "$8.00" in rules["sizing"]
    assert "21 EMA" in rules["entry"] and "RVOL" in rules["entry"]


# ---- review 2026-11-10: buying power, Level re-entries, and the put (bear) book's chart check
class FundedClient(FakeClient):
    """A paper account at Alpaca with this much options buying power."""

    def __init__(self, power, **kw):
        super().__init__(**kw)
        self.power = power

    def get_account(self):
        return {"options_buying_power": str(self.power), "cash": str(self.power), "buying_power": str(self.power),
                "equity": "1000", "last_equity": "1000"}


def test_a_buy_the_paper_account_cannot_pay_for_is_skipped_with_the_reason_and_never_cut_to_fewer_contracts(tmp_path):
    c = FundedClient(1000, fill=False)
    first = _level("level2", symbol="NEW", contract="NEWC", lastBid=1.95, lastAsk=2.05, at=(NOW - timedelta(minutes=1)).isoformat())
    second = _level("level2", symbol="OLD", contract="OLDC", lastBid=1.95, lastAsk=2.05)
    m = _level_mirror(tmp_path, [first, second], c)
    m.tick(NOW)
    # Ask $2.05 x 3 = $615: the newest buys; the next would need $615 with $385 left (the waiting buy holds the rest).
    assert c.calls == [("limit", "NEWC", 3, 2.05, "buy_to_open")]
    skip = next(iter(m._fund_skips.values()))
    assert (skip["symbol"], skip["side"], skip["at"]) == ("OLD", "bull", second["at"])
    assert skip["why"] == "not enough buying power ($615 needed, $385 free)"
    assert m.status()["fundSkipped"] == 1 and m.status()["broker"]["optionsBuyingPower"] == 1000.0
    restored = _level_mirror(tmp_path, [], c)
    restored._load(NOW.date().isoformat())
    assert restored._fund_skips == m._fund_skips                     # a restart keeps the reason
    # Money freed up (the next account read): the same signal is bought - still 3 contracts - and its reason is gone.
    c.power = 5000
    m.tick(NOW + timedelta(seconds=61))
    assert c.calls[-1] == ("limit", "OLDC", 3, 2.05, "buy_to_open") and not m._fund_skips


def test_unknown_or_stale_buying_power_never_blocks_a_buy(tmp_path):
    c = FakeClient()                                                 # no account read at all
    m = _level_mirror(tmp_path / "a", [_level(lastBid=7.9, lastAsk=8.0)], c)
    m.tick(NOW)
    assert len(c.calls) == 1 and c.calls[0][2] == 3 and not m._fund_skips

    class Broken(FakeClient):
        def get_account(self):
            raise RuntimeError("alpaca down")
    c2 = Broken()
    m2 = _level_mirror(tmp_path / "b", [_level()], c2)
    m2.tick(NOW)
    assert len(c2.calls) == 1
    now_ts = NOW.timestamp()
    m2._broker = {"at": (NOW - timedelta(minutes=10)).isoformat(), "optionsBuyingPower": 0.0}      # an old read
    assert m2._free_buying_power(now_ts) is None
    m2._broker = {"at": NOW.isoformat(), "optionsBuyingPower": None}
    assert m2._free_buying_power(now_ts) is None
    m2._broker = {"at": NOW.isoformat(), "optionsBuyingPower": 900.0}
    assert m2._free_buying_power(now_ts) is not None


def test_every_live_order_sent_since_the_account_read_is_taken_off_the_buying_power(tmp_path):
    """Review 2026-11-10: a buy that FILLED since the read is not in the broker's figure yet either."""
    m = _level_mirror(tmp_path, [], FakeClient())
    read = NOW - timedelta(seconds=30)
    after = NOW.isoformat()
    m._broker = {"at": read.isoformat(), "optionsBuyingPower": 2000.0}
    m._rows = {"before": {"status": "buying", "qty": 3, "buyLimit": 2.0, "buyAt": (read - timedelta(seconds=5)).isoformat()},
               "waiting": {"status": "buying", "qty": 3, "buyLimit": 1.0, "buyAt": after},                     # $300
               "filled": {"status": "open", "qty": 3, "buyLimit": 1.6, "buyFill": 1.5, "buyFilledQty": 3,
                          "buyAt": after},                                                                  # $450 at its fill
               "selling": {"status": "selling", "qty": 2, "buyLimit": 2.0, "buyAt": after},                    # $400, no fill info
               "due": {"status": "exit_due", "qty": 1, "buyLimit": 1.0, "buyFill": 0.5, "buyFilledQty": 1,
                       "buyAt": after},                                                                     # $50
               "closed": {"status": "closed", "qty": 3, "buyLimit": 5.0, "buyFill": 5.0, "buyFilledQty": 3, "buyAt": after},
               "missed": {"status": "missed", "qty": 3, "buyLimit": 5.0, "buyAt": after}}
    assert m._free_buying_power(NOW.timestamp()) == 2000.0 - 300 - 450 - 400 - 50


def test_a_fund_skipped_signal_is_not_quoted_again_until_a_new_read_or_an_order_closes(tmp_path):
    """Review 2026-11-10: each live quote is a synchronous broker call; a signal that cannot be paid
    for must not be re-quoted every 10 s tick."""
    c = FundedClient(500, fill=False)
    q = Quotes(1.95, 2.05)                                           # live ask $2.05 x 3 = $615; the Bot's ask $1.25 x 3 = $375
    m = _mirror_q(tmp_path, [_trade()], c, q)
    m.tick(NOW)
    assert c.calls == [] and len(q.asked) == 1
    assert next(iter(m._fund_skips.values()))["why"] == "not enough buying power ($615 needed, $500 free)"
    m.tick(NOW + timedelta(seconds=10))
    assert len(q.asked) == 1                                         # same read, nothing closed: no new quote
    m._rows["old"] = {"side": "bull", "rule": "call2h", "symbol": "MSFT", "at": (NOW - timedelta(minutes=30)).isoformat(),
                      "status": "closed", "buyId": "x", "buyFill": 1.0, "closedAt": (NOW + timedelta(seconds=15)).isoformat(),
                      "pl": 50.0}
    m.tick(NOW + timedelta(seconds=20))
    assert len(q.asked) == 2 and c.calls == []                       # an order closed: checked again (still short)
    m.tick(NOW + timedelta(seconds=30))
    assert len(q.asked) == 2
    c.power = 5000
    m.tick(NOW + timedelta(seconds=61))                              # the next account read: checked again, bought
    assert len(q.asked) == 3 and c.calls == [("limit", CONTRACT, 3, 2.05, "buy_to_open")] and not m._fund_skips


def test_a_stale_account_read_never_holds_a_fund_skip(tmp_path):
    c = FundedClient(500, fill=False)
    m = _mirror_q(tmp_path, [_trade()], c, Quotes(1.95, 2.05))
    m.tick(NOW)
    key = next(iter(m._fund_skips))
    done = sum(1 for r in m._rows.values() if r.get("status") not in pm.LIVE)
    assert m._fund_hold(key, NOW.timestamp() + 10, done)
    assert not m._fund_hold(key, NOW.timestamp() + pm.BROKER_FRESH_SECONDS + 30, done)   # read too old: unknown
    assert not m._fund_hold(key, NOW.timestamp() + 10, done + 1)                          # an order closed since


def test_the_bots_own_quote_alone_can_show_a_buy_cannot_be_paid_for_without_a_live_quote(tmp_path):
    c = FundedClient(300, fill=False)
    q = Quotes(1.15, 1.25)
    m = _mirror_q(tmp_path, [_trade()], c, q)                       # the Bot's ask $1.25 x 3 = $375 > $300
    m.tick(NOW)
    assert c.calls == [] and q.asked == []
    assert next(iter(m._fund_skips.values()))["why"] == "not enough buying power ($375 needed, $300 free)"


def test_a_bought_signal_is_not_left_skipped_at_its_other_levels(tmp_path):
    c = FundedClient(1000, fill=False)
    l3 = _level("level3", contract="AAPLC3", lastBid=4.95, lastAsk=5.05)    # $1,515: cannot be paid for
    l2 = _level("level2", contract="AAPLC2", lastBid=0.95, lastAsk=1.05)    # $315: bought
    m = _level_mirror(tmp_path, [l3, l2], c)
    m.tick(NOW)
    assert c.calls == [("limit", "AAPLC2", 3, 1.05, "buy_to_open")]
    assert m._fund_skips == {} and m.status()["fundSkipped"] == 0    # the Level 3 skip of the same signal is gone
    signal = ("bull", "AAPL", l3["at"])
    m._chart_skips = {"k1": {"side": "bull", "symbol": "AAPL", "at": l3["at"], "why": "x"},
                      "k2": {"side": "bull", "symbol": "MSFT", "at": l3["at"], "why": "y"}}
    m._fund_skips = {"k3": {"side": "bull", "symbol": "AAPL", "at": l3["at"], "why": "z"}}
    assert m._drop_skips_for(signal) is True
    assert list(m._chart_skips) == ["k2"] and m._fund_skips == {}
    assert m._drop_skips_for(signal) is False


def test_a_level_re_entry_after_the_earlier_order_closed_counts_for_every_level(tmp_path):
    trades = []
    c = FakeClient(fill=False)
    m = _level_mirror(tmp_path, trades, c)
    m.tick(NOW)
    m._rows["old"] = {"side": "bull", "rule": "level2", "symbol": "AAPL", "at": (NOW - timedelta(minutes=30)).isoformat(),
                      "status": "closed", "buyId": "x", "buyFill": 1.0, "closedAt": (NOW - timedelta(minutes=10)).isoformat(),
                      "levels": ["level1", "level2"], "pl": 50.0}
    trades.append(_level("level3", levelSignal={"highestLevel": 3}))
    m.tick(NOW + timedelta(seconds=10))
    new = [r for k, r in m._rows.items() if k != "old"]
    assert len(new) == 1 and new[0]["levels"] == ["level1", "level2", "level3"]   # a fresh signal, not a lone upgrade


def test_an_upgrade_while_the_earlier_order_is_still_live_keeps_its_own_level_only():
    """The live case cannot reach an order today (one position per ticker), but the rule must hold if it does."""
    rows = [{"side": "bull", "rule": "level2", "symbol": "AAPL", "status": "open"},
            {"side": "bull", "rule": "level1", "symbol": "MSFT", "status": "closed"},
            {"side": "bull", "rule": "level1", "symbol": "NVDA", "status": "missed"},
            {"side": "bull", "rule": "level2", "symbol": "TSLA", "status": "open", "carried": True},
            {"side": "bull", "rule": "call2h", "symbol": "AMD", "status": "open"}]
    seen = pm._live_level_tickers(rows)
    assert seen == {("bull", "AAPL")}                                # only a LIVE, non-carried Level order counts
    upgrade = {"rule": "level3", "symbol": "AAPL"}
    assert pm._levels_for("bull", upgrade, seen, 3) == ["level3"]    # earlier order still live: its own level only
    fresh = {"rule": "level3", "symbol": "MSFT"}
    assert pm._levels_for("bull", fresh, seen, 3) == ["level1", "level2", "level3"]   # earlier one closed: every level
    assert pm._levels_for("bear", upgrade, seen, 3) == ["level1", "level2", "level3"]  # the other side is its own book
    assert pm._levels_for("bull", {"rule": "call2h", "symbol": "AAPL"}, seen, 3) is None
    assert pm._levels_for("bull", fresh, seen, None) is None and pm._levels_for("bull", fresh, seen, 9) is None


def _bear_mirror(tmp_path, trades, client):
    return pm.PaperMirror({"bear": Book(trades)}, tmp_path, client_factory=lambda: client, off=lambda: False,
                          account="paper3", jev_cut=None, rules=pm.ACCOUNT_RULES["paper3"])


def test_the_put_book_runs_the_mirrored_chart_check_through_the_order_loop(tmp_path):
    above = _level("level2", symbol="UPP", contract="UPPP", setup={"chartContext": _chart("UPP", price=100.0, ema21=99.0)})
    near = _level("level2", symbol="NEAR", contract="NEARP", setup={"chartContext": _chart("NEAR", price=100.0, ema21=101.0, S1=99.7)})
    clear = _level("level2", symbol="GOOD", contract="GOODP", setup={"chartContext": _chart("GOOD", price=100.0, ema21=101.0, S1=99.2)})
    c = FakeClient()
    m = _bear_mirror(tmp_path, [above, near, clear], c)
    m.tick(NOW)
    assert [call[1] for call in c.calls] == ["GOODP"]                # only the put with room below and price under the EMA
    why = {v["symbol"]: (v["side"], v["why"]) for v in m._chart_skips.values()}
    assert why == {"UPP": ("bear", "price above the 21 EMA"), "NEAR": ("bear", "S1 $99.70 is only 0.3% ahead")}
    assert m.status()["rows"][0]["chartCheck"] == "passed" and m.status()["rows"][0]["side"] == "bear"


# ---- his top-of-board rule 2026-11-10 ("Mag7 top 3", "use H/L column top"): the Bot's topHL stamp
def _top(board="Watchlist", rank=3, held=360, hl=0.8):
    from momx.option_track import TOP_N
    return {"board": board, "n": TOP_N.get(board), "rank": rank, "heldSeconds": held, "hl": hl}


def test_top_verdict_pass_fail_wait_and_missing_data():
    v, OK, WAIT, FAIL = pm.top_verdict, pm.TOP_OK, pm.TOP_WAIT, pm.TOP_FAIL
    t = _level("level3", board="Watchlist")
    assert v(t, _top(), NOW) == (OK, None)
    assert v(_level("level3", symbol="CMCSA"), _top(rank=9), NOW) == (
        FAIL, "CMCSA was #9 on the Watchlist by H/L (needs top 7 for 5 min from 9:45)")
    # A hold still building is "not yet", not a skip for the day.
    assert v(t, _top(rank=2, held=150), NOW) == (
        WAIT, "AAPL was #2 on the Watchlist by H/L for only 2 min of the 5 (needs top 7 for 5 min from 9:45)")
    assert v(t, _top(rank=2, held=40), NOW)[1].startswith("AAPL was #2 on the Watchlist by H/L for only 40 s of the 5")
    assert v(t, _top(rank=None, held=0), NOW) == (
        FAIL, "AAPL was not ranked on the Watchlist by H/L (needs top 7 for 5 min from 9:45)")
    # Mag7 is top 3, the Watchlist top 7.
    assert v(t, _top("Mag7", rank=3, held=300), NOW)[0] == OK
    assert v(t, _top("Mag7", rank=4, held=300), NOW) == (
        FAIL, "AAPL was #4 on the Mag7 board by H/L (needs top 3 for 5 min from 9:45)")
    assert v(t, _top("Watchlist", rank=7, held=300), NOW)[0] == OK
    # Never blocks on missing data: unknown run (not warming), another board, a non-Level rule.
    assert v(t, None, NOW) == (OK, None)
    other = _level("level3", board="21 EMA Touch Monthly")
    assert v(other, {"board": "21 EMA Touch Monthly", "rank": 40}, NOW) == (OK, None)
    assert v(_trade(), _top(rank=9), NOW) == (OK, None)
    # The record's own stamp, judged at the record's time; no stamp (older records) passes.
    assert pm.stamp_verdict(_level("level3", topHL=_top(rank=9)))[0] == FAIL
    assert pm.stamp_verdict(_level("level3")) == (OK, None)


def _et(h, m, s=0):
    return datetime(2026, 10, 19, h, m, s, tzinfo=ET)


def test_his_rule_9_30_to_9_45_needs_only_the_current_rank_then_the_5_min_hold():
    """"Start trading 9:30", "Don't start 9:45": 9:30-9:45 rank <= n is enough; from 9:45 the hold applies."""
    v, t = pm.top_verdict, _level("level3", board="Mag7")
    assert pm.OPEN_NO_HOLD_UNTIL == (9, 45)
    assert v(t, _top("Mag7", rank=1, held=0), _et(9, 31)) == (pm.TOP_OK, None)
    assert v(t, _top("Mag7", rank=3, held=0), _et(9, 44, 59)) == (pm.TOP_OK, None)
    assert v(t, _top("Mag7", rank=4, held=900), _et(9, 40)) == (
        pm.TOP_FAIL, "AAPL was #4 on the Mag7 board by H/L (needs top 3; no 5-min hold before 9:45)")
    assert v(t, _top("Mag7", rank=1, held=0), _et(9, 45))[0] == pm.TOP_WAIT
    assert v(t, _top("Mag7", rank=1, held=300), _et(9, 45))[0] == pm.TOP_OK
    # Unknown never blocks before 9:45, warming or not; from 9:45 it waits while warming.
    assert v(t, None, _et(9, 40), warming=True) == (pm.TOP_OK, None)
    state, why = v(t, None, _et(9, 50), warming=True)
    assert state == pm.TOP_WAIT and "just started" in why and "Mag7" in why
    assert v(t, None, _et(9, 50), warming=False) == (pm.TOP_OK, None)


class TopBook(Book):
    """A Bot book with the live run (OptionTrackBook.top_now): {symbol: top dict or None}."""

    def __init__(self, trades, tops, started_at=0.0):
        super().__init__(trades)
        self.tops, self.started_at, self.asked = tops, started_at, []

    def top_now(self, board, symbol, now_ts=None):
        self.asked.append((board, symbol))
        top = self.tops.get(symbol)
        return dict(top, board=board) if top else None


def _live_mirror(tmp_path, book, client, account="paper3"):
    return pm.PaperMirror({"bull": book}, tmp_path, client_factory=lambda: client, off=lambda: False,
                          account=account, jev_cut=None, rules=pm.ACCOUNT_RULES[account])


def _saved_top_skips(folder):
    import json
    return json.loads((folder / f"{NOW.date().isoformat()}.json").read_text(encoding="utf-8"))["topSkips"]


def test_the_current_run_decides_not_the_frozen_stamp(tmp_path):
    # Stamped "held 0 s" when it fired, but it has stayed #1 since: bought on the current run.
    c = FakeClient()
    t = _level("level3", board="Mag7", topHL=_top("Mag7", rank=1, held=0))
    book = TopBook([t], {"AAPL": _top("Mag7", rank=1, held=420)})
    m = _live_mirror(tmp_path / "a", book, c)
    m.tick(NOW)
    assert len(c.calls) == 1 and not m._top_skips and book.asked[0] == ("Mag7", "AAPL")
    # A stale PASSING stamp, but the ticker has since left the top: not bought.
    c2 = FakeClient()
    t2 = _level("level3", board="Mag7", topHL=_top("Mag7", rank=1, held=600))
    m2 = _live_mirror(tmp_path / "b", TopBook([t2], {"AAPL": _top("Mag7", rank=5, held=0)}), c2)
    m2.tick(NOW)
    skip = next(iter(m2._top_skips.values()))
    assert c2.calls == [] and skip["final"] is True
    assert skip["why"] == "AAPL was #5 on the Mag7 board by H/L (needs top 3 for 5 min from 9:45)"
    assert t2["topHL"]["heldSeconds"] == 600                              # the stamp stays as history


def test_a_hold_still_building_is_retried_and_its_saved_reason_kept_current(tmp_path):
    c = FakeClient()
    t3, t2 = _level("level3", board="Watchlist"), _level("level2", board="Watchlist")
    book = TopBook([t3, t2], {"AAPL": _top(rank=2, held=60)})
    m = _live_mirror(tmp_path, book, c)
    m.tick(NOW)
    assert c.calls == [] and m.status()["topSkipped"] == 1                 # one signal, one reason
    skip = next(iter(m._top_skips.values()))
    assert "final" not in skip and skip["why"].startswith("AAPL was #2 on the Watchlist by H/L for only 1 min of the 5")
    assert next(iter(_saved_top_skips(tmp_path).values()))["why"] == skip["why"]
    # The next scan: held 3 min. Still waiting; the saved reason says so (not the old 1 min).
    book.tops["AAPL"] = _top(rank=2, held=180)
    m.tick(NOW + timedelta(seconds=10))
    assert c.calls == [] and len(m._top_skips) == 1
    assert "for only 3 min of the 5" in next(iter(_saved_top_skips(tmp_path).values()))["why"]
    # Held 5 min: bought, and the reason is gone.
    book.tops["AAPL"] = _top(rank=2, held=310)
    m.tick(NOW + timedelta(seconds=20))
    assert len(c.calls) == 1 and m._top_skips == {}


def test_a_rank_fail_is_a_skip_for_the_day_even_if_the_ticker_climbs_later(tmp_path):
    c = FakeClient()
    book = TopBook([_level("level3", board="Watchlist")], {"AAPL": _top(rank=9, held=0)})
    m = _live_mirror(tmp_path, book, c)
    m.tick(NOW)
    book.tops["AAPL"] = _top(rank=1, held=600)
    m.tick(NOW + timedelta(seconds=10))
    assert c.calls == [] and m.status()["topSkipped"] == 1
    assert next(iter(m._top_skips.values()))["why"].startswith("AAPL was #9")


def test_an_unknown_run_after_a_restart_warms_up_then_falls_back_to_the_stamp(tmp_path):
    # 9:40: unknown never blocks before 9:45. (Between 2026-11-12 and 11-13 this asserted "nothing
    # is bought before 10:00"; that rule was removed.)
    early = _et(9, 40)
    c = FakeClient()
    t = _level("level3", board="Mag7", at=(early - timedelta(minutes=1)).isoformat(),
               entryAt=(early - timedelta(minutes=1)).isoformat(), lastAt=(early - timedelta(seconds=20)).isoformat())
    m = _live_mirror(tmp_path / "early", TopBook([t], {}, started_at=early.timestamp() - 10), c)
    m.tick(early)
    assert len(c.calls) == 1
    # 10:00, the book started 1 minute ago and has not seen Mag7 scan: warming, not bought, retried.
    c = FakeClient()
    book = TopBook([_level("level3", board="Mag7")], {}, started_at=NOW.timestamp() - 60)
    m = _live_mirror(tmp_path / "warm", book, c)
    m.tick(NOW)
    skip = next(iter(m._top_skips.values()))
    assert c.calls == [] and "final" not in skip and "just started" in skip["why"]
    # Warm-up over (5 min after the book started) and still unknown: the record's own stamp
    # decides; no stamp = not blocked.
    book.started_at = NOW.timestamp() - 300
    m.tick(NOW + timedelta(seconds=10))
    assert len(c.calls) == 1 and m._top_skips == {}
    # Once the board has scanned, the warm-up does not matter: the run decides.
    c = FakeClient()
    book = TopBook([_level("level3", board="Mag7")], {"AAPL": _top("Mag7", rank=2, held=400)},
                   started_at=NOW.timestamp() - 60)
    m = _live_mirror(tmp_path / "known", book, c)
    m.tick(NOW)
    assert len(c.calls) == 1


def test_the_real_book_top_now_drives_the_paper_bot(tmp_path):
    """The two halves together: a real OptionTrackBook's live runs answer the mirror's check."""
    from momx import option_track as ot
    b = ot.OptionTrackBook(tmp_path / "book", fetch=lambda s: None, healthy=lambda: True, background=False,
                           clock=lambda: NOW.timestamp() - 3600, sleep=lambda s: None)
    b._load(NOW.date().isoformat())
    for ago in (420, 240, 60):
        b._top_update("Mag7", [("AAPL", 0.9), ("B", 0.5), ("C", 0.1)], NOW.timestamp() - ago)
    t = _level("level3", board="Mag7", topHL=_top("Mag7", rank=1, held=0))
    m = _live_mirror(tmp_path / "m", b, FakeClient())
    assert m._top_check("bull", t, NOW) == (pm.TOP_OK, None)
    assert m._top_check("bull", dict(t, symbol="ZZ"), NOW)[0] == pm.TOP_FAIL
    assert m._top_check("bull", dict(t, symbol="C"), NOW)[0] == pm.TOP_OK          # #3 of top 3, held 6 min


def _top_mirror(tmp_path, trades, client, quotes, account="paper3"):
    return pm.PaperMirror({"bull": Book(trades)}, tmp_path, client_factory=lambda: client, off=lambda: False,
                          account=account, jev_cut=None, rules=pm.ACCOUNT_RULES[account], quotes_fetch=quotes)


def test_a_signal_not_on_top_gets_no_order_no_quote_no_chart_check_and_the_reason_is_saved(tmp_path):
    for account in ("paper3", "paper5"):
        c, q = FakeClient(), Quotes(1.15, 1.25)
        bad_chart = {"chartContext": _chart(price=100.0, ema21=101.0)}          # would also fail the chart
        trades = [_level("level3", symbol="CMCSA", contract="CMCSAC", topHL=_top(rank=9), setup=bad_chart),
                  _level("level2", symbol="CMCSA", contract="CMCSAC", topHL=_top(rank=9), setup=bad_chart)]
        m = _top_mirror(tmp_path / account, trades, c, q, account)
        m.tick(NOW)
        assert c.calls == [] and q.asked == [] and m._chart_skips == {}         # checked first, for free
        assert m.status()["topSkipped"] == 1                                    # one signal, one skip
        skip = next(iter(m._top_skips.values()))
        assert (skip["symbol"], skip["side"], skip["rule"], skip["at"]) == ("CMCSA", "bull", "level3", trades[0]["at"])
        assert skip["why"] == "CMCSA was #9 on the Watchlist by H/L (needs top 7 for 5 min from 9:45)"
        m.tick(NOW + timedelta(seconds=10))
        assert c.calls == [] and m.status()["topSkipped"] == 1
        restored = _top_mirror(tmp_path / account, [], c, q, account)
        restored._load(NOW.date().isoformat())
        assert restored._top_skips == m._top_skips                              # a restart keeps the reason
        assert "top by H/L" in m.status()["rules"]["entry"] and "Mag7 top 3" in m.status()["rules"]["entry"]
        assert "last 5 min" in m.status()["rules"]["entry"] and "9:30-9:45" not in m.status()["rules"]["entry"]


def test_a_signal_on_top_for_five_minutes_or_with_no_stamp_is_bought(tmp_path):
    c = FakeClient()
    m = _level_mirror(tmp_path / "a", [_level(topHL=_top(rank=5, held=360))], c)
    m.tick(NOW)
    assert len(c.calls) == 1 and not m._top_skips
    c2 = FakeClient()
    m2 = _level_mirror(tmp_path / "b", [_level()], c2)                          # an older record: no stamp
    m2.tick(NOW)
    assert len(c2.calls) == 1 and not m2._top_skips


def test_a_top_skip_is_dropped_when_the_signal_is_bought(tmp_path):
    c = FakeClient()
    t = _level("level3", topHL=_top(rank=2, held=120))
    m = _level_mirror(tmp_path, [t], c)
    m.tick(NOW)
    assert c.calls == [] and m.status()["topSkipped"] == 1
    t["topHL"] = _top(rank=2, held=360)                                         # e.g. a corrected stamp
    m.tick(NOW + timedelta(seconds=10))
    assert len(c.calls) == 1 and m._top_skips == {} and m.status()["topSkipped"] == 0
    # And a bought signal drops its top skip at any other level too.
    m._top_skips = {"k": {"side": "bull", "symbol": "AAPL", "at": t["at"], "why": "x"}}
    assert m._drop_skips_for(("bull", "AAPL", t["at"])) is True and m._top_skips == {}


def test_the_ai_picker_is_not_offered_a_signal_that_is_not_on_top(tmp_path):
    c = FakeClient()
    off_top = _level(symbol="CMCSA", contract="CMCSAC", topHL=_top(rank=9))
    on_top = _level(symbol="AAPL", topHL=_top(rank=1))
    m = _level_mirror(tmp_path, [off_top, on_top], c, "paper5")
    m._day = NOW.date().isoformat()
    assert [t["symbol"] for _, _, t in m.candidates(NOW)] == ["AAPL"]


def test_a_scan_with_no_hl_keeps_the_signal_retryable(tmp_path):
    """Review fix: a candle gap (no numeric H/L on the last scan) is rank unknown - a wait with a plain
    reason, never the day's fail - and the next good scan lets the buy go once the run holds."""
    c = FakeClient()
    gap = {"rank": None, "heldSeconds": 0, "hl": None, "hlMissing": True}
    book = TopBook([_level("level3", board="Mag7")], {"AAPL": _top("Mag7", rank=1, held=0) | gap})
    m = _live_mirror(tmp_path, book, c)
    m.tick(NOW)
    skip = next(iter(m._top_skips.values()))
    assert c.calls == [] and "final" not in skip and "no H/L on the last scan" in skip["why"]
    assert m._top_final() == set() and m.status()["topSkipped"] == 1
    # The next good scan restarts the run: still building (waits), then held 5 min (bought).
    book.tops["AAPL"] = _top("Mag7", rank=1, held=60)
    m.tick(NOW + timedelta(seconds=10))
    assert c.calls == [] and "for only 1 min" in next(iter(m._top_skips.values()))["why"]
    book.tops["AAPL"] = _top("Mag7", rank=1, held=300)
    m.tick(NOW + timedelta(seconds=20))
    assert len(c.calls) == 1 and m._top_skips == {}


def test_a_passed_top_check_is_marked_passed_and_the_scorecard_shows_no_hold_reason(tmp_path):
    """Review fix: stamp "held 0" -> WAIT saved -> later passes (entry marked passed, not deleted) ->
    order abandoned at the quote step -> the Scorecard's Not ordered must not show the stale hold."""
    from momx import bot_desk as bd
    c = FakeClient()
    stale = (NOW - timedelta(minutes=10)).isoformat()            # no fresh Bot quote, no live feed
    t = _level("level3", board="Mag7", topHL=_top("Mag7", rank=1, held=0), lastAt=stale,
               eContract="AAPL261023C00250000", eEntry=1.20, eEntryBid=1.15, eEntryAsk=1.25)
    assert pm.stamp_verdict(t)[0] == pm.TOP_WAIT                  # the frozen stamp reads as a hold
    book = TopBook([t], {"AAPL": _top("Mag7", rank=1, held=120)})
    m = _live_mirror(tmp_path, book, c)
    m.tick(NOW)
    assert c.calls == [] and "for only 2 min" in next(iter(_saved_top_skips(tmp_path).values()))["why"]
    book.tops["AAPL"] = _top("Mag7", rank=1, held=320)
    m.tick(NOW + timedelta(seconds=10))
    assert c.calls == [] and m._rows == {}                       # passed, then abandoned: no quote
    saved = _saved_top_skips(tmp_path)
    entry = next(iter(saved.values()))
    assert entry["passed"] and "why" not in entry and "final" not in entry
    assert m.status()["topSkipped"] == 0                          # a passed entry is not a skip
    note = bd._not_ordered_why("bull", t, [], None, None, None, saved)
    assert note == "not ordered by this bot (the records do not say why)"
    # Held by the top check again later: the reason comes back and "passed" goes.
    book.tops["AAPL"] = _top("Mag7", rank=1, held=0)
    m.tick(NOW + timedelta(seconds=20))
    entry = next(iter(m._top_skips.values()))
    assert "passed" not in entry and entry["why"] and m.status()["topSkipped"] == 1
    # A signal that passes at once while its stamp reads as a hold gets a passed entry too, and an
    # order that goes out removes it (_drop_skips_for).
    c2 = FakeClient()
    t2 = _level("level3", board="Mag7", topHL=_top("Mag7", rank=1, held=0), lastAt=stale)
    b2 = TopBook([t2], {"AAPL": _top("Mag7", rank=1, held=400)})
    m2 = _live_mirror(tmp_path / "b", b2, c2)
    m2.tick(NOW)
    assert c2.calls == [] and next(iter(m2._top_skips.values()))["passed"]
    t2["lastAt"] = (NOW + timedelta(seconds=5)).isoformat()      # a fresh Bot quote arrives
    m2.tick(NOW + timedelta(seconds=10))
    assert len(c2.calls) == 1 and m2._top_skips == {}
