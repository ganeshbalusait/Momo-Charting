"""Paper accounts trade only the three Level strategies on the 0.20-delta contract (2026-11-09).

His "All trades: use only 3 strategies trades and 0.2 delta from tomorrow" and
"Don't block any trade using AI". Fake broker only.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

from momx import paper_mirror as pm

ET = pm.ET
NOW = datetime(2026, 11, 10, 10, 10, tzinfo=ET)     # Tuesday, inside the first buy window
MAIN = "AAPL261113C00270000"
E_LEG = "AAPL261113C00285000"


class FakeClient:
    def __init__(self, fill=True):
        self.credentials = SimpleNamespace(paper=True)
        self.fill = fill
        self.orders = {}
        self.calls = []

    def submit_option_limit_order(self, symbol, qty, limit, cid, intent):
        self.calls.append((intent, symbol, qty, limit))
        self.orders[cid] = SimpleNamespace(id=cid, status="filled" if self.fill else "new",
                                           filled_avg_price=limit if self.fill else None,
                                           filled_qty=qty if self.fill else 0)

    def get_order_by_client_id(self, cid):
        return self.orders[cid]

    def cancel_order_by_id(self, oid):
        self.orders[oid].status = "canceled"


class Book:
    def __init__(self, trades):
        self.items = list(trades)

    def trades(self):
        return [dict(t) for t in self.items]


def level(rule="level1", symbol="AAPL", now=NOW, **kw):
    """A Level row as the Bot records it: virtualOnly, a main contract and the 0.20-delta one."""
    at = (now - timedelta(minutes=2)).isoformat()
    n = int(rule[-1]) if rule.startswith("level") else None
    t = {"rule": rule, "symbol": symbol, "at": at, "entryAt": at, "status": "open", "virtualOnly": True,
         "levelSignal": {"highestLevel": n, "strategyLevel": n} if n else None,
         "contract": MAIN, "strike": 270.0, "delta": 0.36, "entry": 3.10, "lastBid": 3.00, "lastAsk": 3.20,
         "eContract": E_LEG, "eStrike": 285.0, "eDelta": 0.18, "eEntry": 0.60, "eLastBid": 0.58, "eLastAsk": 0.62,
         "eStatus": "open", "lastAt": (now - timedelta(seconds=20)).isoformat()}
    t.update(kw)
    # A Level signal is bought only when the Bot gave it a chart plan (paper_mirror.has_plan,
    # 2026-11-13); plan=None in a test = the Bot gave it none.
    if rule.startswith("level"):
        t.setdefault("plan", {"strike": t.get("eStrike")})
    return t


def account(tmp_path, trades, client, name="paper3", sides=("bull",), **kw):
    """A mirror set up the way start() sets up the real accounts."""
    books = {side: Book(trades if side == "bull" else kw.pop("bear", [])) for side in sides}
    m = pm.PaperMirror(books, tmp_path / name, client_factory=lambda: client, off=lambda: False, account=name,
                       jev_cut=pm.ACCOUNTS[name][1], rules=pm.ACCOUNT_RULES[name], leg=pm.PAPER_LEG, **kw)
    return m, books


def buys(client):
    return [c for c in client.calls if c[0] == "buy_to_open"]


def test_a_level_signal_is_bought_on_the_020_delta_contract_at_its_own_ask(tmp_path):
    """2026-11-12: the chart plan's entry is the ASK of the 0.20-delta contract (it was its mid)."""
    client = FakeClient()
    m, _ = account(tmp_path, [level()], client)
    m.tick(NOW)
    assert buys(client) == [("buy_to_open", E_LEG, 4, 0.62)]             # $0.62 ask -> 4 contracts fit in $300
    row = m.status()["rows"][0]
    assert (row["contract"], row["leg"], row["delta"], row["strike"], row["botEntry"]) == (E_LEG, "entry", 0.18, 285.0, 0.60)
    assert row["rule"] == "level1" and row["levelAtSignal"] == 1 and row["levels"] == ["level1"]
    assert m.status()["rules"]["contract"].startswith("0.20 delta")
    assert m.status()["rules"]["picked"] == {"bull": ["level1", "level2", "level3"], "bear": ["level1", "level2", "level3"]}


def test_every_other_rule_is_left_alone_on_both_accounts(tmp_path):
    for name in ("paper3", "paper5"):
        client = FakeClient()
        others = [level(rule=r, symbol=r.upper(), virtualOnly=False) for r in ("rvol", "call2h", "skit", "go", "news", "jev")]
        m, _ = account(tmp_path, others, client, name=name)
        m.tick(NOW)
        assert buys(client) == []


def test_a_signal_with_no_contract_under_020_delta_is_skipped_not_swapped_for_another(tmp_path):
    client = FakeClient()
    none = level(eContract=None, eStatus="none", eLastBid=None, eLastAsk=None)
    m, _ = account(tmp_path, [none], client)
    m.tick(NOW)
    assert buys(client) == [] and m.status()["rows"] == []


def test_the_020_delta_quote_decides_the_price_never_the_main_contracts(tmp_path):
    client = FakeClient()
    stale_e = level(eLastBid=0.0, eLastAsk=0.0)                          # no usable 0.20-delta quote, main is fine
    m, _ = account(tmp_path, [stale_e], client)
    m.tick(NOW)
    assert buys(client) == []


def test_a_ticker_that_qualified_for_several_levels_is_booked_once_under_the_highest(tmp_path):
    client = FakeClient()
    trio = [level("level1"), level("level2"), level("level3"),
            level("level1", symbol="MSFT", eContract="MSFT261113C00520000")]
    m, _ = account(tmp_path, trio, client)
    m.tick(NOW)
    rows = {r["symbol"]: r for r in m.status()["rows"]}
    assert len(buys(client)) == 2
    assert rows["AAPL"]["rule"] == "level3" and rows["MSFT"]["rule"] == "level1"
    # ... and that one order counts for every level it qualified for.
    assert rows["AAPL"]["levels"] == ["level1", "level2", "level3"] and rows["MSFT"]["levels"] == ["level1"]


def test_calls_and_puts_are_both_traded(tmp_path):
    client = FakeClient()
    put = level("level2", symbol="NVDA", eContract="NVDA261113P00180000")
    m, _ = account(tmp_path, [level()], client, sides=("bull", "bear"), bear=[put])
    m.tick(NOW)
    assert sorted(c[1] for c in buys(client)) == [E_LEG, "NVDA261113P00180000"]
    assert {r["side"] for r in m.status()["rows"]} == {"bull", "bear"}


def test_an_unfilled_buy_is_resent_at_the_ask_of_the_020_delta_contract(tmp_path):
    client = FakeClient(fill=False)
    m, books = account(tmp_path, [level()], client)
    m.tick(NOW)
    later = NOW + timedelta(seconds=pm.BUY_TTL_SECONDS + 10)
    books["bull"].items = [level(lastAt=(later - timedelta(seconds=5)).isoformat(), eLastBid=0.60, eLastAsk=0.66)]
    m.tick(later)
    assert buys(client) == [("buy_to_open", E_LEG, 4, 0.62), ("buy_to_open", E_LEG, 4, 0.66)]


def test_the_exit_uses_the_020_delta_contracts_exit_quote(tmp_path):
    client = FakeClient()
    m, books = account(tmp_path, [level()], client)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=10))                                  # the fill is seen
    later = NOW + timedelta(seconds=20)
    books["bull"].items = [level(status="closed", exitPrice=3.4, exitBid=3.3, exitAsk=3.5, exitReason="target",
                                 eExit=0.9, eExitBid=0.88, eExitAsk=0.92, lastAt=(later - timedelta(seconds=5)).isoformat())]
    m.tick(later)
    sells = [c for c in client.calls if c[0] == "sell_to_close"]
    assert sells == [("sell_to_close", E_LEG, 4, 0.9)]                   # not the main contract's $3.40


def test_ai_saying_no_does_not_stop_a_level_trade(tmp_path):
    client = FakeClient()
    m, _ = account(tmp_path, [level()], client, ai_policy=lambda *a: {"approved": False, "enabled": True, "reason": "no"})
    m.tick(NOW)
    assert len(buys(client)) == 1 and m.status()["rows"][0]["aiWouldSkip"] is True
    assert m.status()["rules"]["ai"].startswith("AI never blocks")


def test_ai_is_shown_the_contract_that_is_really_bought(tmp_path):
    seen = []
    client = FakeClient()
    m, _ = account(tmp_path, [level()], client, ai_policy=lambda side, t, ts: seen.append(t) or {"approved": True, "enabled": True})
    m.tick(NOW)
    assert (seen[0]["contract"], seen[0]["delta"], seen[0]["strike"], seen[0]["entry"]) == (E_LEG, 0.18, 285.0, 0.62)


def test_an_older_position_on_the_main_contract_is_still_managed_on_that_contract(tmp_path):
    """Positions opened under the old rules carry no leg: their exits keep using the main contract's quotes."""
    client = FakeClient()
    m, books = account(tmp_path, [], client)
    m._load(NOW.date().isoformat())
    old = level(rule="rvol", virtualOnly=False)
    key = pm.trade_key("bull", old)
    m._rows = {key: {"side": "bull", "rule": "rvol", "symbol": "AAPL", "at": old["at"], "contract": MAIN, "qty": 1,
                     "buyId": "b", "buyAt": old["at"], "buyFill": 3.1, "buyFilledQty": 1, "held": 1, "status": "open",
                     "peak": 0.0, "sells": []}}
    books["bull"].items = [{**old, "status": "closed", "exitPrice": 3.4, "exitBid": 3.3, "exitAsk": 3.5, "exitReason": "target",
                            "eExitBid": 0.88, "eExitAsk": 0.92}]
    m.tick(NOW)
    assert [c for c in client.calls if c[0] == "sell_to_close"] == [("sell_to_close", MAIN, 1, 3.4)]


# ---- review fixes, checked against the real 11-06 / 11-09 signal files
def test_a_missed_level_3_buy_is_not_tried_again_as_level_2_or_level_1(tmp_path):
    client = FakeClient(fill=False)
    trio = [level("level1"), level("level2"), level("level3")]
    m, books = account(tmp_path, trio, client)
    m.tick(NOW)
    assert len(buys(client)) == 1
    books["bull"].items = [dict(t, status="closed", exitPrice=0.5) for t in trio]       # the Bot left: the buy ends missed
    later = NOW + timedelta(seconds=pm.BUY_TTL_SECONDS + 10)
    m.tick(later)
    assert [r["status"] for r in m.status()["rows"]] == ["missed"]
    books["bull"].items = trio                                                           # the lower rows are still fresh and open
    m.tick(later + timedelta(seconds=10))
    assert len(buys(client)) == 1 and len(m.status()["rows"]) == 1


def test_a_new_signal_later_on_the_same_ticker_is_a_new_order(tmp_path):
    client = FakeClient(fill=False)
    m, books = account(tmp_path, [level("level2")], client)
    m.tick(NOW)
    books["bull"].items = [dict(level("level2"), status="closed", exitPrice=0.5)]
    m.tick(NOW + timedelta(seconds=pm.BUY_TTL_SECONDS + 10))                             # missed
    later = NOW + timedelta(minutes=30)
    books["bull"].items = [level("level3", now=later)]
    m.tick(later)
    assert len(buys(client)) == 2


def test_when_slots_are_short_the_newest_signal_gets_the_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "MAX_OPEN", 1)
    client = FakeClient()
    old = level("level2", symbol="OLD", eContract="OLD261113C00010000", at=(NOW - timedelta(minutes=9)).isoformat(),
                entryAt=(NOW - timedelta(minutes=9)).isoformat())
    new = level("level2", symbol="NEW", eContract="NEW261113C00010000", at=(NOW - timedelta(seconds=15)).isoformat(),
                entryAt=(NOW - timedelta(seconds=15)).isoformat())
    m, _ = account(tmp_path, [old, new], client)
    m.tick(NOW)
    assert [c[1] for c in buys(client)] == ["NEW261113C00010000"]


def test_a_buy_already_sent_is_still_resent_at_the_ask_when_the_signal_is_over_ten_minutes_old(tmp_path):
    """11-09: buys sent 9-10 minutes after the signal were cancelled and never re-sent, because of the age test."""
    client = FakeClient(fill=False)
    signal_at = (NOW - timedelta(minutes=9)).isoformat()
    m, books = account(tmp_path, [level(at=signal_at, entryAt=signal_at)], client)
    m.tick(NOW)
    later = NOW + timedelta(seconds=pm.BUY_TTL_SECONDS + 10)                             # the signal is now 11+ minutes old
    books["bull"].items = [level(at=signal_at, entryAt=signal_at, lastAt=(later - timedelta(seconds=5)).isoformat(),
                                 eLastBid=0.60, eLastAsk=0.66)]
    m.tick(later)
    assert [c[3] for c in buys(client)] == [0.62, 0.66]
    row = m.status()["rows"][0]
    assert row["status"] == "buying" and row["buyTries"] == 1


def test_an_option_nobody_is_bidding_for_or_under_five_cents_is_not_bought(tmp_path):
    for bid, ask in ((0.0, 0.01), (0.0, 0.30), (0.01, 0.03)):
        client = FakeClient()
        m, _ = account(tmp_path / ("q%s-%s" % (bid, ask)), [level(eLastBid=bid, eLastAsk=ask)], client)
        m.tick(NOW)
        assert buys(client) == [], (bid, ask)
    client = FakeClient()
    m, _ = account(tmp_path / "ok", [level(eLastBid=0.04, eLastAsk=0.06)], client)
    m.tick(NOW)
    assert buys(client) == [("buy_to_open", E_LEG, 50, 0.06)]                            # mid $0.05 is the floor; bought at the ask $0.06: 50 contracts = $300
    assert pm.sellable(0.04, 0.06) and not pm.sellable(0.0, 0.5) and not pm.sellable(None, None)


def test_a_resend_never_goes_out_on_a_zero_bid(tmp_path):
    client = FakeClient(fill=False)
    m, books = account(tmp_path, [level()], client)
    m.tick(NOW)
    later = NOW + timedelta(seconds=pm.BUY_TTL_SECONDS + 10)
    books["bull"].items = [level(lastAt=(later - timedelta(seconds=5)).isoformat(), eLastBid=0.0, eLastAsk=0.70)]
    m.tick(later)
    assert len(buys(client)) == 1 and m.status()["rows"][0]["status"] == "missed"


def test_a_later_level_3_after_the_first_buy_ended_counts_for_every_level(tmp_path):
    """11-09: 25 of 152 tickers began as Level 2 and were upgraded minutes later (CAT 09:35 -> 09:38).
    Since his re-entry rule (2026-11-10) an order placed after the ticker's earlier order has ENDED
    (here it missed) is a fresh signal and counts for all its levels; an upgrade while the earlier
    order is still live keeps its own level only (test_momx_paper_mirror: _levels_for)."""
    client = FakeClient(fill=False)
    m, books = account(tmp_path, [level("level1"), level("level2")], client)
    m.tick(NOW)
    first = m.status()["rows"][0]
    assert first["rule"] == "level2" and first["levels"] == ["level1", "level2"]
    books["bull"].items = [dict(t, status="closed", exitPrice=0.5) for t in books["bull"].items]
    m.tick(NOW + timedelta(seconds=pm.BUY_TTL_SECONDS + 10))                             # the first buy ends missed
    later = NOW + timedelta(minutes=4)
    books["bull"].items = [level("level3", now=later, eContract="AAPL261113C00290000")]   # the upgrade: a lone Level 3 row
    m.tick(later)
    rows = sorted(m.status()["rows"], key=lambda r: r["buyAt"])
    assert len(buys(client)) == 2 and [r["rule"] for r in rows] == ["level2", "level3"]
    assert rows[1]["levels"] == ["level1", "level2", "level3"]                           # the first order ended: a fresh signal

    restarted, _ = account(tmp_path, [level("level3", now=later, symbol="MSFT", eContract="MSFT261113C00520000")], FakeClient())
    restarted.tick(later + timedelta(seconds=10))                                        # a different ticker, first order today
    fresh = next(r for r in restarted.status()["rows"] if r["symbol"] == "MSFT")
    assert fresh["levels"] == ["level1", "level2", "level3"]
