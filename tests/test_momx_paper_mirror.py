"""Alpaca paper mirror of the Bot (momx/paper_mirror.py) - fake client only."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from momx import paper_mirror as pm

ET = pm.ET
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=ET)      # a Monday


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
         "lastAt": (NOW - timedelta(seconds=20)).isoformat(), "lastBid": 1.10, "lastAsk": 1.30}
    t.update(kw)
    return t


def _mirror(tmp_path, trades, client):
    return pm.PaperMirror({"bull": Book(trades)}, tmp_path, client_factory=lambda: client, off=lambda: False)


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


def test_buys_an_allowed_paper_trade_at_the_mid(tmp_path):
    c = FakeClient()
    m = _mirror(tmp_path, [_trade()], c)
    m.tick(NOW)
    assert c.calls == [("limit", "AAPL261023C00250000", 3, 1.2, "buy_to_open")]     # $1.20 -> min 3
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
    assert seen[0]["entry"] == 1.2 and seen[0]["entryBid"] == 1.1 and seen[0]["entryAsk"] == 1.3
    row=m.status()["rows"][0]
    assert row["entryInputs"]["premium"] == 1.2 and row["entryInputs"]["minute"] == 600
    assert row["status"] == "buying" and row.get("buyFill") is None
    restored=_mirror(tmp_path,[],c); restored._load(NOW.date().isoformat())
    assert restored.status()["rows"][0]["entryInputs"] == row["entryInputs"]


def test_ai_pending_blocks_entry_approval_preserves_mid_and_exits_never_wait(tmp_path):
    client = FakeClient()
    trades = [_trade()]
    verdict = {"approved": False, "enabled": True, "reason": "pending"}
    mirror = pm.PaperMirror({"bull": Book(trades)}, tmp_path, client_factory=lambda: client,
                            off=lambda: False, ai_policy=lambda *args: dict(verdict))
    mirror.tick(NOW)
    assert not client.calls
    verdict.update(approved=True, provider="ollama", probability=.65)
    mirror.tick(NOW + timedelta(seconds=10))
    assert client.calls == [("limit", trades[0]["contract"], 3, 1.2, "buy_to_open")]
    assert mirror.status()["rows"][0]["aiDecision"]["provider"] == "ollama"
    mirror.tick(NOW + timedelta(seconds=20))
    verdict.update(approved=False)
    trades[0].update(exitPrice=.6, exitBid=.5, exitAsk=.7, exitReason="strategy exit")
    mirror.tick(NOW + timedelta(seconds=30))
    assert client.calls[-1] == ("limit", trades[0]["contract"], 3, .6, "sell_to_close")


def test_ai_error_and_stale_quote_cannot_place_entry(tmp_path):
    client = FakeClient()
    def failed(*args):
        raise RuntimeError("provider down")
    mirror = pm.PaperMirror({"bull": Book([_trade()])}, tmp_path, client_factory=lambda: client,
                            off=lambda: False, ai_policy=failed)
    mirror.tick(NOW)
    assert not client.calls and mirror.status()["ai"]["approved"] is False
    mirror._ai_policy = lambda *args: {"approved": True, "enabled": True}
    mirror.tick(NOW + timedelta(seconds=121))
    assert not client.calls


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
    assert s["totals"]["closed"] == 1 and s["totals"]["pl"] == 120.0      # 3 x $0.40


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


def test_caps_one_per_ticker_and_max_open(tmp_path):
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


def test_accounts_map_is_his_split():
    assert pm.ACCOUNTS["paper3"][1] is None and pm.ACCOUNTS["paper5"][1] is None
    assert pm.ACCOUNT_RULES["paper5"] == pm.PICKED_RULES


def test_both_accounts_require_enabled_ai_and_paper5_no_longer_needs_jev(tmp_path):
    for account in ("paper3", "paper5"):
        client = FakeClient()
        verdict = {"approved": True, "enabled": False}
        mirror = pm.PaperMirror({"bull": Book([_trade(rule="zs2", jevP=None)])}, tmp_path / account,
                                client_factory=lambda: client, off=lambda: False, account=account,
                                jev_cut=pm.ACCOUNTS[account][1], rules=pm.ACCOUNT_RULES[account],
                                ai_policy=lambda *args: dict(verdict))
        mirror.tick(NOW)
        assert not client.calls
        verdict.update(enabled=True, provider="openclaw", probability=.7, reason="approved")
        mirror.tick(NOW + timedelta(seconds=10))
        assert len(client.calls) == 1 and client.calls[0][3] == 1.2
        assert mirror.status()["rows"][0]["aiDecision"]["provider"] == "openclaw"


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


def test_retired_paper5_takes_no_new_entries(tmp_path):
    c = FakeClient()
    bull = [_trade(rule="adx", symbol="A", contract="AC"), _trade(rule="go", symbol="B", contract="BC")]
    bear = [_trade(rule="go", symbol="C", contract="CC"), _trade(rule="star1", symbol="D", contract="DC")]
    m = pm.PaperMirror({"bull": Book(bull), "bear": Book(bear)}, tmp_path, client_factory=lambda: c,
                       off=lambda: False, account="paper5", jev_cut=0.10, rules=pm.PAPER5_RULES)
    m.tick(NOW)
    assert c.calls == []


def test_his_sizing_by_price():
    assert pm.size_for(0.25) == 12 and pm.size_for(0.30) == 10 and pm.size_for(0.50) == 6
    assert pm.size_for(1.00) == 3 and pm.size_for(2.50) == 3 and pm.size_for(4.00) == 3
    assert pm.size_for(4.01) == 1 and pm.size_for(8.00) == 1 and pm.size_for(8.01) is None


def test_his_stop_ladder():
    assert pm.stop_for(1.0, 0.9) is None
    assert pm.stop_for(1.0, 1.0) == 1.0 and pm.stop_for(1.0, 1.7) == 1.0      # +100%: break-even
    assert pm.stop_for(1.0, 2.0) == 2.0 and pm.stop_for(1.0, 3.4) == 3.0      # +200% -> +100%, +300% -> +200%


def test_plus_100_sells_20_percent_then_break_even_stop_sells_the_rest(tmp_path):
    c = FakeClient()
    trades = [_trade(lastBid=0.24, lastAsk=0.26)]            # mid 0.25 -> 12 contracts
    m = _mirror(tmp_path, trades, c)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=10))
    row = m.status()["rows"][0]
    assert row["held"] == 12 and row["buyFill"] == 0.25
    trades[0].update(lastBid=0.49, lastAsk=0.51, lastAt=(NOW + timedelta(seconds=15)).isoformat())   # +100%
    m.tick(NOW + timedelta(seconds=20))
    assert c.calls[-1] == ("limit", "AAPL261023C00250000", 2, 0.5, "sell_to_close")                  # 20% of 12
    m.tick(NOW + timedelta(seconds=30))
    row = m.status()["rows"][0]
    assert row["held"] == 10 and row["partialDone"] and row["stop"] == 0.25
    trades[0].update(lastBid=0.24, lastAsk=0.26, lastAt=(NOW + timedelta(seconds=35)).isoformat())   # back to BE
    m.tick(NOW + timedelta(seconds=40))
    assert c.calls[-1] == ("limit", "AAPL261023C00250000", 10, 0.25, "sell_to_close")
    m.tick(NOW + timedelta(seconds=50))
    s = m.status()
    assert s["rows"][0]["status"] == "closed" and s["totals"]["pl"] == 50.0           # 2 x $0.25 profit


def test_ladder_moves_stop_to_plus_100_after_plus_200(tmp_path):
    c = FakeClient()
    trades = [_trade(lastBid=0.24, lastAsk=0.26)]
    m = _mirror(tmp_path, trades, c)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=10))
    trades[0].update(lastBid=0.74, lastAsk=0.76, lastAt=(NOW + timedelta(seconds=15)).isoformat())   # +200%
    m.tick(NOW + timedelta(seconds=20))
    m.tick(NOW + timedelta(seconds=30))
    assert m.status()["rows"][0]["stop"] == 0.5
    trades[0].update(lastBid=0.49, lastAsk=0.51, lastAt=(NOW + timedelta(seconds=35)).isoformat())   # falls to +100%
    m.tick(NOW + timedelta(seconds=40))
    assert c.calls[-1][2] == 10 and c.calls[-1][4] == "sell_to_close"


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
    d = NOW.replace
    assert pm.entry_open(d(hour=9, minute=30)) and pm.entry_open(d(hour=10, minute=59))
    assert not pm.entry_open(d(hour=11, minute=0)) and not pm.entry_open(d(hour=12, minute=30))
    assert pm.entry_open(d(hour=13, minute=0)) and not pm.entry_open(d(hour=15, minute=30))


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
