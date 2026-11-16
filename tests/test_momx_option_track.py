"""momx/option_track.py - real option prices for the scanner's rules (2026-09-27)."""
from __future__ import annotations

import threading

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from momx import option_track as ot

import pytest

ET = ZoneInfo("America/New_York")
_REAL_LIVE_TODAY = ot.live_today


@pytest.fixture(autouse=True)
def _rows_are_live(monkeypatch):
    """The fixture rows carry no candle times; the live-today gate has its own tests below."""
    monkeypatch.setattr(ot, "live_today", lambda row, now: True)
MON = datetime(2026, 9, 28, 9, 50, tzinfo=ET)          # Monday
GO_AT = datetime(2026, 9, 28, 9, 40, tzinfo=ET).timestamp()


def chain(spot=100.0, call_mid=(1.0, 1.1), put_mid=(0.9, 1.0), today_only=False):
    rows = []
    for strike in (97.5, 100.0, 101.0, 102.5, 105.0):
        for side, mid in (("CALL", call_mid), ("PUT", put_mid)):
            rows.append({"symbol": f"X_{side[0]}{strike}_1002", "side": side, "strike": strike, "expiry": "2026-10-02",
                         "days_to_expiration": 4, "bid": mid[0], "ask": mid[1], "delta": 0.4})
            rows.append({"symbol": f"X_{side[0]}{strike}_0928", "side": side, "strike": strike, "expiry": "2026-09-28",
                         "days_to_expiration": 0, "bid": 0.2, "ask": 0.3, "delta": 0.3})
    if today_only:
        rows = [r for r in rows if r["days_to_expiration"] == 0]
    return {"underlyingPrice": spot, "selectedExpiryChainRows": rows}


def go_row(sym="X", **over):
    row = {"symbol": sym, "last": 100.0, "grade": {"letter": "A+"},
           "m5": {"gapGo": {"goAt": GO_AT}}, "adx": {"30m": {"plus": 30, "minus": 12}}, "rvol": {}}
    row.update(over)
    return row


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakeJev:
    """Stands in for momx.jev_trade.JevTradeJudge: records what was asked, answers at once."""

    def __init__(self, p=0.42):
        self.asked, self.p = [], p

    def submit(self, state, side, done):
        self.asked.append((state, side))
        done({"jevP": self.p, "jevAt": "2026-09-28T09:50:01-04:00", "regime": 0.6, "direction": 0.7,
              "pressure": 0.5, "quality": 1.2, "risk": 0.8, "news": 0.3})
        return True


def book(tmp_path, answers, direction="bull", clock=None, jev=None):
    calls = []

    def fetch(sym):
        calls.append(sym)
        a = answers[sym]
        return a.pop(0) if isinstance(a, list) else a

    b = ot.OptionTrackBook(tmp_path, direction=direction, fetch=fetch, healthy=lambda: True, background=False,
                           clock=clock or Clock(MON.timestamp()), sleep=lambda s: None, jev=jev or FakeJev())
    return b, calls


def test_the_rules_it_recognises():
    bull = go_row(momoxAPlus={"at": "x"}, strategy={"daily2": {"rank": 1}}, marketTurn={"at": "y"},
                  rvol={"15m": {"bg": "cyan"}},
                  m5={"gapGo": {"goAt": GO_AT}, "goConfirmation": {"barAt": GO_AT, "macdUp": True, "buyers30": True}})
    assert ot.fired(bull, False, MON) == ["go", "goRvolMacd", "mxaplus", "daily2", "turn"]
    # bear (2026-09-28): MX A+ / market turn stamps come from the BEAR books and
    # are taken too; GO needs sellers in control
    bear = go_row(adx={"30m": {"plus": 10, "minus": 25}}, momoxAPlus={"at": "x"}, marketTurn={"at": "y"})
    assert ot.fired(bear, True, MON) == ["go", "mxaplus", "turn"]
    assert ot.fired(go_row(grade={"letter": "B"}), False, MON) == []
    assert ot.fired(go_row(adx={"30m": {"plus": 5, "minus": 20}}), False, MON) == []   # sellers on a bull GO


@pytest.mark.parametrize("direction", ["bull", "bear"])
@pytest.mark.parametrize("fast", [False, True])
def test_no_virtual_book_waits_for_ai_before_recording_an_entry(tmp_path, direction, fast):
    clock = Clock(MON.timestamp())
    b, _ = book(tmp_path, {"X": chain()}, direction=direction, clock=clock)
    b._day = MON.date().isoformat()
    b._trades = [{"rule": rule, "symbol": "X", "at": MON.isoformat(), "status": "pending", "stockPrice": 100}
                 for rule in ("go", "news")]
    verdict = {"enabled": True, "approved": False, "reason": "AI review pending"}
    b._ai_policy = lambda *args: dict(verdict)
    pick = ot.pick_contract(chain(), direction == "bear")
    b._quote_fetch = lambda _: {"results": [{"ok": True, "symbol": "X", "pick": pick}]}
    price = (lambda: b._fast_price(["X"])) if fast else (lambda: b._price_symbol("X", False))
    price()
    # 2026-11-09, his "Don't block any trade using AI": the entry is recorded at
    # once on the first quote; the AI's "no" is kept as advice only.
    assert all(t["status"] == "open" and t["entry"] == pick["entry"] and "aiCandidate" not in t for t in b.trades())
    assert all(t["entryAt"] == datetime.fromtimestamp(clock.t, ET).isoformat() for t in b.trades())
    assert all(t["aiDecision"]["approved"] is False and t["aiWouldSkip"] is True for t in b.trades())
    # The 0.20-delta contract the paper accounts buy has a usable quote from the first second.
    for t in b.trades():
        if t.get("eContract"):
            assert t["eLastBid"] == t["eEntryBid"] and t["eLastAsk"] == t["eEntryAsk"] and t["eLastAsk"] > 0


def test_an_ai_no_or_error_never_refuses_a_virtual_entry_but_the_time_rules_still_do(tmp_path):
    b, _ = book(tmp_path, {"X": chain()})
    b._ai_policy = lambda *args: {"approved": False, "enabled": True, "provider": "ollama", "reason": "weak setup"}
    t = {"rule": "news", "symbol": "X", "at": MON.isoformat(), "status": "pending"}
    assert b._approve_entry(t, ot.pick_contract(chain(), False), MON.timestamp()) is True
    assert t["status"] == "pending" and t["aiWouldSkip"] is True and "aiCandidate" not in t
    assert t["aiDecision"]["reason"] == "weak setup"

    def down(*args):
        raise RuntimeError("provider down")

    b._ai_policy = down
    t2 = {"rule": "news", "symbol": "X", "at": MON.isoformat(), "status": "pending"}
    assert b._approve_entry(t2, ot.pick_contract(chain(), False), MON.timestamp()) is True
    assert t2["status"] == "pending" and "taken anyway" in t2["aiDecision"]["reason"] and "aiWouldSkip" not in t2

    b._ai_policy = lambda *args: pytest.fail("expired signals must not request AI")
    assert not b._approve_entry(t, {}, MON.timestamp() + 601)
    assert t["status"] == "ai_skipped" and "time rule" in t["aiDecision"]["reason"]
    late = MON.replace(hour=15, minute=31)
    t.update(at=late.replace(minute=29).isoformat(), status="pending")
    assert not b._approve_entry(t, {}, late.timestamp())
    assert "window ended" in t["aiDecision"]["reason"]


def test_contract_is_the_nearest_otm_after_today():
    pick = ot.pick_contract(chain(spot=100.4), bear=False)
    assert pick["contract"] == "X_C101.0_1002" and pick["expiry"] == "2026-10-02" and pick["entry"] == 1.05
    put = ot.pick_contract(chain(spot=100.4), bear=True)
    assert put["contract"] == "X_P100.0_1002"
    assert ot.pick_contract(chain(today_only=True), bear=False) is None      # 0DTE only -> nothing
    assert ot.pick_contract({}, bear=False) is None


def test_signal_prices_a_contract_then_follows_it_to_the_close(tmp_path):
    clock = Clock(MON.timestamp())
    b, calls = book(tmp_path, {"X": [chain(call_mid=(1.0, 1.1)), chain(call_mid=(1.9, 2.1)),
                                     chain(call_mid=(0.5, 0.6)), chain(call_mid=(1.4, 1.6))]}, clock=clock)
    b.apply("Watchlist", {"rows": [go_row()]}, MON)
    t = b.trades()[0]
    assert (t["rule"], t["status"], t["contract"], t["entry"]) == ("go", "open", "X_C101.0_1002", 1.05)
    for minutes, expect in ((20, 2.0), (40, 0.55)):
        clock.t = MON.timestamp() + minutes * 60
        b.apply("Watchlist", {"rows": [go_row()]}, datetime.fromtimestamp(clock.t, ET))
    clock.t = datetime(2026, 9, 28, 15, 52, tzinfo=ET).timestamp()
    b.apply("Watchlist", {"rows": [go_row()]}, datetime.fromtimestamp(clock.t, ET))
    t = b.trades()[0]
    assert t["best"] == 2.0 and t["worst"] == 0.55 and t["close"] == 1.5 and t["status"] == "closed"
    assert len(calls) == 4
    saved = json.loads((tmp_path / ot.DIRNAME / "2026-09-28.json").read_text(encoding="utf-8"))
    assert saved["trades"][0]["close"] == 1.5 and saved["direction"] == "bull"


def test_not_re_priced_before_ten_minutes(tmp_path):
    clock = Clock(MON.timestamp())
    b, calls = book(tmp_path, {"X": chain()}, clock=clock)
    b.apply("Watchlist", {"rows": [go_row()]}, MON)
    clock.t += 300
    b.apply("Watchlist", {"rows": [go_row()]}, datetime.fromtimestamp(clock.t, ET))
    assert len(calls) == 1


def test_one_trade_per_rule_and_ticker_and_puts_on_bear(tmp_path):
    b, _ = book(tmp_path, {"X": chain(spot=100.4)}, direction="bear")
    row = go_row(adx={"30m": {"plus": 10, "minus": 25}})
    b.apply("Watchlist", {"rows": [row]}, MON)
    b.apply("Watchlist BEAR", {"rows": [row]}, MON)
    trades = [t for t in b.trades() if t["rule"] == "go"]
    assert len(trades) == 1 and trades[0]["contract"] == "X_P100.0_1002"
    assert {t["rule"] for t in b.trades()} == {"go", "star1"}     # a lone bear GO is also the bear Star #1


def test_no_new_signals_outside_0930_1530_or_on_weekends(tmp_path):
    b, calls = book(tmp_path, {"X": chain()})
    b.apply("Watchlist", {"rows": [go_row()]}, datetime(2026, 9, 28, 15, 45, tzinfo=ET))
    b.apply("Watchlist", {"rows": [go_row()]}, datetime(2026, 9, 26, 10, 0, tzinfo=ET))   # Saturday
    assert b.trades() == [] and calls == []


def test_a_missing_chain_keeps_the_trade_pending(tmp_path):
    b, _ = book(tmp_path, {"X": {}})
    b.apply("Watchlist", {"rows": [go_row()]}, MON)
    assert b.trades()[0]["status"] == "pending"


@pytest.mark.parametrize("direction", ["bull", "bear"])
def test_incomplete_chain_recovers_without_losing_or_duplicating_scanner_signal(tmp_path, direction):
    clock = Clock(MON.timestamp())
    b, _ = book(tmp_path, {"X": [chain(today_only=True), chain()]}, direction=direction, clock=clock)
    b._day = MON.date().isoformat()
    b._trades = [{"rule": "go", "symbol": "X", "at": MON.isoformat(), "status": "pending"}]
    b._price_symbol("X", False)
    assert b.trades()[0]["status"] == "pending"
    clock.t += 30
    b._price_symbol("X", False)
    assert b.capture_status()["entered"] == 1
    assert len(b.trades()) == 1 and b.trades()[0]["entryAt"] != MON.isoformat()
    assert "why" not in b.trades()[0]


def test_unpriced_signal_expires_without_backdated_fill_and_reports_reason(tmp_path):
    clock = Clock(MON.timestamp()+601)
    b, _ = book(tmp_path, {"X": chain()}, clock=clock)
    b._day = MON.date().isoformat()
    b._trades = [{"rule": "news", "symbol": "X", "at": MON.isoformat(), "status": "pending"}]
    b._price_symbol("X", False)
    c = b.capture_status()
    assert c["notEntered"] == 1 and c["entered"] == 0 and c["pending"] == 0
    assert "10-minute" in next(iter(c["reasons"]))
    b._save(force=True)
    restarted, _ = book(tmp_path, {"X": chain()}, clock=clock)
    restarted._load(b._day)
    assert restarted.capture_status() == c


def test_capture_audit_includes_caps_and_deduplicates_recovered_skips(tmp_path, monkeypatch):
    b, _ = book(tmp_path, {"X": chain()})
    monkeypatch.setattr(ot, "fired", lambda *args: ["c2h"])
    monkeypatch.setitem(ot.INFO_CAP, "c2h", 0)
    b.apply("Watchlist", {"rest": [go_row()]}, MON)
    assert b.capture_status()["notEntered"] == 1
    assert b.capture_status()["reasons"] == {"Daily signal cap for this rule": 1}
    monkeypatch.setitem(ot.INFO_CAP, "c2h", 10)
    b.apply("Watchlist", {"rest": [go_row()]}, MON)
    assert any(t["rule"] == "c2h" and t["status"] == "open" for t in b.trades())
    assert b.capture_status()["notEntered"] == 0


def test_oi_walls_and_expected_move_are_saved_at_the_signal(tmp_path):
    c = chain(spot=100.4)
    for r in c["selectedExpiryChainRows"]:
        r["open_interest"] = 5000 if r["strike"] == 105.0 else 800
    c["currentAtm"] = {"expectedMove": 3.2}
    b, _ = book(tmp_path, {"X": c})
    b.apply("Watchlist", {"rows": [go_row()]}, MON)
    t = b.trades()[0]
    assert t["expectedMove"] == 3.2
    assert t["callWalls"][0]["strike"] == 105.0 and t["callWalls"][0]["oi"] == 5000
    assert isinstance(t["putWalls"], list)


def test_history_newest_first_and_180_day_prune(tmp_path):
    folder = tmp_path / ot.DIRNAME
    folder.mkdir()
    for day in ("2026-03-01", "2026-06-01", "2026-09-25", "2026-09-26"):
        (folder / f"{day}.json").write_text(json.dumps({"trades": [{"rule": "go", "symbol": "X", "entry": 1.0,
                                                                     "callWalls": [1]}]}), encoding="utf-8")
    (folder / ot.CARRY_FILE).write_text("{}", encoding="utf-8")
    h = ot.history(tmp_path, 2)
    assert [d["day"] for d in h] == ["2026-09-26", "2026-09-25"]
    assert h[0]["trades"] == [{"rule": "go", "symbol": "X", "entry": 1.0}]
    ot.prune(tmp_path, "2026-09-28")
    assert not (folder / "2026-03-01.json").exists()                 # 211 days old: deleted
    assert (folder / "2026-06-01.json").exists() and (folder / "2026-09-25.json").exists()   # 119 days: kept
    assert (folder / ot.CARRY_FILE).exists()


def test_c2h_arrows_are_tracked_bull_only_and_capped(tmp_path):
    arrow = {"chartSignals": [{"label": "C2H", "at": "2026-09-28T09:30:00-04:00", "seenAt": "2026-09-28T09:34:00-04:00"}]}
    plain = {"symbol": "X", "last": 100.0, "grade": {"letter": "B"}, "adx": {}, "rvol": {}, "m5": {}}
    assert ot.fired({**plain, **arrow}, False, MON) == ["c2h"]
    assert ot.fired({**plain, **arrow}, True, MON) == []
    old = {"chartSignals": [{"label": "C2H", "at": "2026-09-25T15:00:00-04:00"}]}
    assert ot.fired({**plain, **old}, False, MON) == []
    assert ot.fired({**plain, "chartSignals": [{"label": "CALL2H", "at": "2026-09-28T09:30:00-04:00"}]}, False, MON) == ["call2h"]
    # 2026-09-29: the cap is only a flood guard (150/day) - 60 arrows are all taken
    syms = [f"S{i}" for i in range(60)]
    b, _ = book(tmp_path, {s: chain() for s in syms})
    b.apply("Watchlist", {"rows": [{**plain, **arrow, "symbol": s} for s in syms]}, MON)
    assert len([t for t in b.trades() if t["rule"] == "c2h"]) == 60
    assert ot.INFO_CAP.get("c2h", ot.INFO_MAX_PER_DAY) >= 150


def test_solo_and_hot_leader_are_bull_rules():
    plain = {"symbol": "X", "last": 100.0, "grade": {"letter": "B"}, "adx": {}, "rvol": {}, "m5": {}}
    row = {**plain, "solo": {"at": "2026-09-28T09:50:00-04:00", "ratio": 7.0}, "hotLeader": {"rank": 1}}
    assert ot.fired(row, False, MON) == ["solo", "hotLead"]
    assert ot.fired(row, True, MON) == []
    old = {**plain, "solo": {"at": "2026-09-25T09:50:00-04:00"}}
    assert ot.fired(old, False, MON) == []


def test_new_trades_are_priced_before_repricing(tmp_path):
    b, _ = book(tmp_path, {"OLD": chain(), "NEW": chain()})
    b._background = True               # queue only - nothing priced yet
    b._thread = threading.Thread(target=lambda: None)
    b._thread.start(); b._thread.join()
    b._trades.append({"rule": "go", "symbol": "OLD", "status": "open"})
    b._trades.append({"rule": "go", "symbol": "NEW", "status": "pending"})
    b._thread = None
    b._background = False
    b._queue.put("OLD"); b._queued.add("OLD")
    order = []
    b._price_symbol = lambda sym, waits: (order.append(sym), b._queued.discard(sym))
    b.request(["OLD", "NEW"])
    assert order == ["NEW", "OLD"]



def test_every_setup_tag_is_a_rule(tmp_path):
    at = lambda hm: f"2026-09-28T{hm}:00-04:00"
    bar = datetime(2026, 9, 28, 9, 30, tzinfo=ET).timestamp()
    row = {"symbol": "X", "last": 100.0, "grade": {"letter": "B"}, "m5": {},
           "adx": {"5m": {"adx": 30, "plus": 28}, "30m": {"plus": 10, "minus": 20}},
           "skittles": {"4h": {"bg": "lime"}},
           "rvol": {"30m": {"bg": "cyan", "barAt": bar}},
           "gradeFresh": {"timeline": [{"at": at("09:20"), "what": "SKIT 4h bg lime"},
                                       {"at": at("09:40"), "what": "SQZ 2h released"}]}}
    assert ot.fired(row, False, MON) == ["adx", "skit", "rvol", "sqz"]
    # a cross first seen more than an hour ago is not fresh; yesterday's RVOL bar is not today's
    stale = {**row, "gradeFresh": {"timeline": [{"at": at("08:40"), "what": "SKIT 4h bg lime"}]},
             "rvol": {"30m": {"bg": "cyan", "barAt": bar - 86400}}, "adx": {"5m": {"adx": 20, "plus": 28}}}
    assert ot.fired(stale, False, MON) == []


def test_star1_follows_the_boards_ranking(tmp_path):
    hot = {"hot": True}
    a = go_row("A", sectorRotation={}, pctChange=9.0)                   # GO, one ⚠ (up 9%)
    b = go_row("B", sectorRotation={})                                    # GO, clean
    b_hot = go_row("C", sectorRotation=hot)                               # GO in a 🔥 sector beats both
    today = MON.date()
    assert ot.star_one([a, b], {}, today) == "B"
    assert ot.star_one([a, b, b_hot], {}, today) == "C"
    assert ot.star_one([{"symbol": "Z", "grade": {"letter": "A"}}], {"Z": 1.0}, today) == "Z"   # OPT only
    assert ot.star_one([{**b_hot, "etf": True}], {}, today) is None
    # the bot: one trade per symbol that becomes ⭐1
    bk, _ = book(tmp_path, {"A": chain(), "B": chain(), "C": chain()})
    bk.apply("Watchlist", {"rows": [a, b]}, MON)
    bk.apply("Watchlist", {"rows": [a, b, b_hot]}, MON)
    bk.apply("Watchlist", {"rows": [a, b, b_hot]}, MON)
    assert [t["symbol"] for t in bk.trades() if t["rule"] == "star1"] == ["B", "C"]


def test_opt_first_seen_before_ten_is_remembered_across_a_restart(tmp_path):
    opt = {"symbol": "O", "last": 50.0, "grade": {"letter": "A"}, "m5": {"state": "extended"},
           "skittles": {"2h": {"bg": "cyan"}, "4h": {"bg": "green"}}, "adx": {"30m": {"plus": 30, "minus": 10}}, "rvol": {}}
    early = datetime(2026, 9, 28, 9, 45, tzinfo=ET)
    bk, _ = book(tmp_path, {"O": chain()}, clock=Clock(early.timestamp()))
    bk.apply("Watchlist", {"rows": [opt]}, early)
    assert [t["rule"] for t in bk.trades()] == ["star1"]
    again, _ = book(tmp_path, {"O": chain()})
    cooled = {**opt, "m5": {"state": "holding"}}
    again.apply("Watchlist", {"rows": [cooled]}, datetime(2026, 9, 28, 11, 0, tzinfo=ET))
    assert again._opt_seen["Watchlist"]["O"] == early.timestamp()


def m5_bar(close, t, vwap=99.0, e21=99.5):
    return {"lastCompleted": {"close": close, "time": t}, "vwap": vwap, "ema21": e21,
            "gapGo": {"goAt": GO_AT}}


def test_exit_signal_reads_completed_candles_after_entry():
    entry = MON.timestamp()
    bar_after = entry - 60            # a candle that CLOSES after the entry
    bar_before = entry - 600          # closed before the entry: ignored
    assert ot.exit_signal({"m5": m5_bar(98.0, bar_after)}, entry, False) == "below EMA21"
    assert ot.exit_signal({"m5": m5_bar(99.2, bar_after)}, entry, False) == "below EMA21"
    assert ot.exit_signal({"m5": m5_bar(100.0, bar_after)}, entry, False) is None
    assert ot.exit_signal({"m5": m5_bar(98.0, bar_before)}, entry, False) is None
    assert ot.exit_signal({"m5": m5_bar(101.0, bar_after, vwap=100.5, e21=99.0)}, entry, True) == "above EMA21"


def test_a_close_across_vwap_alone_no_longer_exits():
    """2026-11-10, his "Remove vwap exit strategy in BOT's": the NVDA put was sold on a 5m close above
    VWAP at 10:00 while still under the 21 EMA. Now that holds; only the 21 EMA ends the trade early."""
    entry = MON.timestamp()
    bar_after = entry - 60
    put_bounce = m5_bar(172.9, bar_after, vwap=172.56, e21=175.75)    # above VWAP, under the EMA21
    assert ot.exit_signal({"m5": put_bounce}, entry, True) is None
    call_dip = m5_bar(99.2, bar_after, vwap=99.5, e21=99.0)           # below VWAP, over the EMA21
    assert ot.exit_signal({"m5": call_dip}, entry, False) is None
    assert ot.exit_signal({"m5": m5_bar(176.0, bar_after, vwap=172.56, e21=175.75)}, entry, True) == "above EMA21"


def test_zero_dte_pick():
    z = ot.pick_contract(chain(spot=100.4), bear=False, zero_dte=True)
    assert z["contract"] == "X_C101.0_0928" and z["expiry"] == "2026-09-28"


def test_his_exit_the_0dte_leg_and_the_weekly_hold_carry(tmp_path):
    clock = Clock(MON.timestamp())
    prices = [chain(call_mid=(1.0, 1.1)), chain(call_mid=(1.4, 1.6)), chain(call_mid=(1.9, 2.1))]
    b, _ = book(tmp_path, {"X": prices}, clock=clock)
    row = go_row(m5={"gapGo": {"goAt": GO_AT}, **m5_bar(101.0, MON.timestamp() - 60)})
    b.apply("Watchlist", {"rows": [row]}, MON)
    t = next(x for x in b.trades() if x["rule"] == "go")
    assert t["zContract"] == "X_C101.0_0928" and t["zStatus"] == "open"
    # a 5m candle closes below the 21 EMA 20 minutes later -> exit priced right away
    clock.t = MON.timestamp() + 1200
    later = datetime.fromtimestamp(clock.t, ET)
    row2 = go_row(m5={"gapGo": {"goAt": GO_AT}, **m5_bar(98.0, clock.t - 300)})
    b.apply("Watchlist", {"rows": [row2]}, later)
    t = next(x for x in b.trades() if x["rule"] == "go")
    assert t["exitReason"] == "below EMA21" and t["exitPrice"] == 1.5 and t["zStatus"] == "closed"
    # 15:52: the day closes; the weekly (expires 10/02) is carried for holdBest
    clock.t = datetime(2026, 9, 28, 15, 52, tzinfo=ET).timestamp()
    b.apply("Watchlist", {"rows": [row2]}, datetime.fromtimestamp(clock.t, ET))
    t = next(x for x in b.trades() if x["rule"] == "go")
    assert t["status"] == "closed" and t["exitPrice"] == 1.5 and t["holdBest"] == 2.0
    carry = json.loads((tmp_path / ot.DIRNAME / ot.CARRY_FILE).read_text(encoding="utf-8"))["open"]
    assert {(c["rule"], c["symbol"], c["expiry"]) for c in carry} >= {("go", "X", "2026-10-02")}



def test_zs_rule_is_a_test_rule(tmp_path):
    bar = datetime(2026, 9, 28, 9, 40, tzinfo=ET).timestamp()
    zs = {"above": True, "clear2": True, "blockers": [], "barAt": bar}
    row = {"symbol": "ZS", "last": 200.0, "grade": {"letter": "B"}, "adx": {}, "rvol": {},
           "m5": {"pillars": {"zs": zs}}}
    assert ot.fired(row, False, MON) == ["zs", "zs2"]
    # On the bear board the row's m5 is the BEAR m5 (its zs = below every line,
    # clear path down) - same keys, so the bear board fires it too (2026-09-28).
    assert ot.fired(row, True, MON) == ["zs", "zs2"]
    blocked = {**row, "m5": {"pillars": {"zs": {**zs, "clear2": False, "blockers": ["pDH"]}}}}
    assert ot.fired(blocked, False, MON) == []
    day_high_only = {**row, "m5": {"pillars": {"zs": {**zs, "clear2": False, "blockers": ["dH"]}}}}
    assert ot.fired(day_high_only, False, MON) == ["zs2"]      # ABVX 2026-09-28
    old = {**row, "m5": {"pillars": {"zs": {**zs, "barAt": bar - 86400}}}}
    assert ot.fired(old, False, MON) == []



def test_entry_strike_is_the_chains_delta_020_pick():
    c = chain(spot=100.4)
    deltas = {97.5: 0.8, 100.0: 0.55, 101.0: 0.45, 102.5: 0.19, 105.0: 0.08}
    for r in c["selectedExpiryChainRows"]:
        r["delta"] = deltas[r["strike"]] if r["side"] == "CALL" else -deltas[r["strike"]]
    e = ot.pick_entry_strike(c, False, "2026-10-02")
    assert e["eContract"] == "X_C102.5_1002" and e["eEntry"] == 1.05
    assert ot.pick_entry_strike(c, False, "2026-12-18") is None



def test_sector_leaders_are_test_rules():
    base = {"symbol": "ZS", "last": 200.0, "grade": {"letter": "B"}, "adx": {}, "rvol": {}, "m5": {}}
    moving = {**base, "sectorRotation": {"hot": False, "late": True, "leaders": [{"symbol": "PANW"}, {"symbol": "ZS"}]}}
    assert ot.fired(moving, False, MON) == ["sectorMove"]
    hot = {**base, "sectorRotation": {"hot": True, "late": False, "leaders": [{"symbol": "ZS"}]}}
    assert ot.fired(hot, False, MON) == ["sectorHot"]
    not_leader = {**base, "sectorRotation": {"hot": True, "leaders": [{"symbol": "PANW"}]}}
    assert ot.fired(not_leader, False, MON) == []



def test_fading_blocks_nothing(tmp_path):
    b, _ = book(tmp_path, {"X": chain()})
    row = go_row(m5={"gapGo": {"goAt": GO_AT}, "state": "fading"}, momoxAPlus={"at": "x"})
    b.apply("Watchlist", {"rows": [row]}, MON)
    rules = {t["rule"] for t in b.trades()}
    assert {"go", "mxaplus"} <= rules
    assert all(t["momentum"] == "fading" for t in b.trades() if t["rule"] in ("go", "mxaplus"))


def test_open_path_abvx_rule():
    bar = datetime(2026, 9, 28, 9, 45, tzinfo=ET).timestamp()
    # 2026-09-30: ABVX checks EMA 9/21 + VWAP + cloud, NOT the 50 EMA ("above" is the ZS rule's flag)
    zs = {"above": False, "clear2": False, "blockers": ["R2"], "clear050": True, "blockers050": [], "barAt": bar,
          "close": 94.37, "ema9": 94.0, "ema21": 93.5, "ema50": 95.0, "vwap": 93.8, "cloud": 92.0}
    arrow = [{"label": "CALL2H", "at": "2026-09-28T09:00:00-04:00", "seenAt": "2026-09-28T09:20:00-04:00"}]
    row = {"symbol": "ABVX", "last": 94.37, "grade": {"letter": "B"}, "adx": {}, "rvol": {},
           "m5": {"pillars": {"zs": zs}}, "chartSignals": arrow}
    at = datetime(2026, 9, 28, 9, 50, tzinfo=ET)
    assert "openPath" in ot.fired(row, False, at)
    assert "openPath" not in ot.fired(row, False, datetime(2026, 9, 28, 11, 0, tzinfo=ET))   # after 10:30
    assert "openPath" not in ot.fired({**row, "chartSignals": []}, False, at)                  # no arrow
    blocked = {**row, "m5": {"pillars": {"zs": {**zs, "clear050": False, "blockers050": ["R1"]}}}}
    assert "openPath" not in ot.fired(blocked, False, at)
    under21 = {**row, "m5": {"pillars": {"zs": {**zs, "ema21": 94.5}}}}
    assert "openPath" not in ot.fired(under21, False, at)                                       # below EMA 21



def test_momentum_state_is_saved_at_entry(tmp_path):
    b, _ = book(tmp_path, {"X": chain()})
    b.apply("Watchlist", {"rows": [go_row(m5={"gapGo": {"goAt": GO_AT}, "state": "extended"})]}, MON)
    go = next(t for t in b.trades() if t["rule"] == "go")
    assert go["momentum"] == "extended"
    assert b.summary()[0]["momentum"] == "extended"



def test_bear_board_gets_the_same_setup_rules(tmp_path):
    bar = datetime(2026, 9, 28, 9, 30, tzinfo=ET).timestamp()
    at = lambda hm: f"2026-09-28T{hm}:00-04:00"
    row = {"symbol": "X", "last": 100.0, "grade": {"letter": "B"}, "m5": {},
           "adx": {"5m": {"adx": 30, "plus": 10, "minus": 28}, "30m": {"plus": 10, "minus": 20}},
           "skittles": {"4h": {"bg": "magenta"}},
           "rvol": {"30m": {"bg": "magenta", "barAt": bar}},
           "chartSignals": [{"label": "P2H", "at": at("09:30"), "seenAt": at("09:34")},
                            {"label": "PUT4H", "at": at("09:00"), "seenAt": at("09:36")}],
           "gradeFresh": {"timeline": [{"at": at("09:20"), "what": "SKIT 4h bg magenta"},
                                       {"at": at("09:40"), "what": "SQZ 2h released"}]}}
    assert ot.fired(row, True, MON) == ["c2h", "call2h", "adx", "skit", "rvol", "sqz"]
    # the bull reading of the same row fires none of the coloured ones (the
    # squeeze timeline is per board in real life, so "sqz" is shared here)
    assert ot.fired(row, False, MON) == ["sqz"]



def test_bear_sectors_falling_leaders(tmp_path):
    rows = [{"symbol": s, "industry": "Crypto", "pctChange": p, "last": 9.0, "m5": {"vwap": 10.0}, "adx": {}, "rvol": {}}
            for s, p in (("RIOT", -6.0), ("CIFR", -5.6), ("IREN", -5.5), ("BTBT", -4.5))]
    assert list(ot.falling_sectors(rows)) == ["Crypto"]
    assert ot.falling_sectors(rows)["Crypto"][0] == "RIOT"
    b, _ = book(tmp_path, {s["symbol"]: chain() for s in rows}, direction="bear")
    b.apply("Watchlist", {"rows": rows}, MON)                  # 09:50: inside the hot window
    got = {(t["rule"], t["symbol"]) for t in b.trades()}
    assert ("sectorHot", "RIOT") in got and len([1 for r, _ in got if r == "sectorHot"]) == 4


def test_mom_rule_is_the_squeeze_momentum_colour():
    bar = datetime(2026, 9, 28, 9, 55, tzinfo=ET).timestamp()
    plain = {"symbol": "X", "last": 100.0, "grade": {"letter": "B"}, "adx": {}, "rvol": {}}
    cyan = {**plain, "m5": {"sqzMom": {"color": "cyan", "barAt": bar}}}
    assert "mom" in ot.fired(cyan, False, MON) and "mom" not in ot.fired(cyan, True, MON)
    magenta = {**plain, "m5": {"sqzMom": {"color": "magenta", "barAt": bar}}}
    assert "mom" in ot.fired(magenta, True, MON) and "mom" not in ot.fired(magenta, False, MON)
    old = {**plain, "m5": {"sqzMom": {"color": "cyan", "barAt": bar - 3 * 86400}}}
    assert "mom" not in ot.fired(old, False, MON)


def test_mom_flip_reads_the_closed_candle_cross_and_momentum_direction():
    bar = datetime(2026, 9, 28, 9, 55, tzinfo=ET).timestamp()
    row = lambda cross, value, prev: {"symbol": "APLD", "last": 26.0, "grade": {"letter": "B"}, "rvol": {},
                                      "adx": {"5m": {"closedCross": cross, "closedCrossAt": bar}},
                                      "m5": {"sqzMom": {"color": "purple", "value": value, "prev": prev, "barAt": bar}}}
    assert "momFlip" in ot.fired(row("bear", 0.66, 0.83), True, MON)        # APLD 10:50, dots purple + falling
    assert "momFlip" not in ot.fired(row("bear", 0.66, 0.83), False, MON)
    assert "momFlip" not in ot.fired(row("bear", 0.9, 0.83), True, MON)     # dots rising
    assert "momFlip" in ot.fired(row("bull", -0.3, -0.5), False, MON)


def test_mom_strong_needs_5m_and_30m_adx_background():
    bar = datetime(2026, 9, 28, 9, 55, tzinfo=ET).timestamp()
    cell = lambda plus, minus: {"plus": plus, "minus": minus, "adx": 30}
    row = lambda color, adx: {"symbol": "X", "last": 100.0, "grade": {"letter": "B"}, "rvol": {}, "adx": adx,
                              "m5": {"sqzMom": {"color": color, "barAt": bar}}}
    strong = row("cyan", {"5m": cell(31, 12), "30m": cell(28, 15)})
    assert {"mom", "momStrong"} <= set(ot.fired(strong, False, MON))
    weak30 = row("cyan", {"5m": cell(31, 12), "30m": cell(22, 15)})
    assert "mom" in ot.fired(weak30, False, MON) and "momStrong" not in ot.fired(weak30, False, MON)
    bear = row("magenta", {"5m": cell(12, 31), "30m": cell(10, 27)})
    assert "momStrong" in ot.fired(bear, True, MON) and "momStrong" not in ot.fired(strong, True, MON)


def test_sqz_fire_counts_daily_and_weekly():
    at = lambda hm: f"2026-09-28T{hm}:00-04:00"
    plain = {"symbol": "X", "last": 100.0, "grade": {"letter": "B"}, "adx": {}, "rvol": {}, "m5": {}}
    for tf in ("D", "Wk"):
        row = {**plain, "gradeFresh": {"timeline": [{"at": at("10:05"), "what": f"SQZ {tf} released"}]}}
        assert "sqz" in ot.fired(row, False, MON), tf


def test_jev_is_asked_once_when_the_option_is_bought_and_never_changes_the_trade(tmp_path):
    jev = FakeJev(p=0.42)
    spy = {"symbol": "SPY", "last": 500.0, "pctChange": 0.4, "m5": {"vwap": 499.0}}
    b, _ = book(tmp_path, {"X": chain(spot=100.4), "SPY": chain(spot=500)}, jev=jev)
    b.apply("Watchlist", {"rows": [go_row(industry="Semis", pctChange=2.0), spy]}, MON)
    t = next(x for x in b.trades() if x["symbol"] == "X")
    assert t["status"] == "open" and t["contract"] == "X_C101.0_1002"     # the BOT still bought it
    assert t["jevP"] == 0.42 and t["jevFactors"]["direction"] == 0.7
    state, side = jev.asked[0]
    assert side == "bull" and "Ticker: X (Semis)" in state and "Market (SPY): +0.40% today, above its VWAP" in state
    assert "Option: X_C101.0_1002, entry 1.05" in state
    asked = len(jev.asked)                                                 # one question per trade (GO and ⭐1 here)
    assert asked == len([x for x in b.trades() if x.get("status") == "open"])
    b.apply("Watchlist", {"rows": [go_row(), spy]}, MON)                   # never asked again
    assert len(jev.asked) == asked
    assert {"jevP": 0.42}.items() <= next(x for x in b.summary() if x["symbol"] == "X").items()
    saved = json.loads((tmp_path / ot.DIRNAME / "2026-09-28.json").read_text(encoding="utf-8"))
    assert saved["trades"][0]["jevP"] == 0.42


def test_no_jev_key_means_nothing_is_sent(monkeypatch):
    from momx.jev_trade import JevTradeJudge
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    posts = []
    j = JevTradeJudge(post=lambda p, k: posts.append(p) or {}, background=False)
    assert j.enabled is False and j.submit("state", "bull", lambda r: None) is False and posts == []


def test_an_old_chain_copy_is_never_used_to_buy(tmp_path):
    """2026-10-02: 523 trades were priced from disk copies hours old (APLD 24.5C at $0.25, stock $25.90)."""
    old = dict(chain(spot=100.4), diskCached=True, scannedAt="2026-09-28T07:00:00-04:00")
    b, calls = book(tmp_path, {"X": [old, old, old, old, old, old, old, old]})
    b.apply("Watchlist", {"rows": [go_row()]}, MON)
    assert all(t["status"] == "pending" and "entry" not in t for t in b.trades())
    fresh = dict(chain(spot=100.4), scannedAt=datetime.fromtimestamp(MON.timestamp() - 30, ET).isoformat())
    b2, _ = book(tmp_path / "f", {"X": fresh})
    b2.apply("Watchlist", {"rows": [go_row()]}, MON)
    assert next(t for t in b2.trades() if t["rule"] == "go")["status"] == "open"


def test_chain_spot_far_from_the_live_price_is_an_old_copy(tmp_path):
    b, _ = book(tmp_path, {"X": chain(spot=95.0)})                 # scanner says 100.0
    b.apply("Watchlist", {"rows": [go_row()]}, MON)
    assert all(t["status"] == "pending" for t in b.trades())


def test_entry_below_its_in_the_money_value_is_refused():
    assert ot.entry_impossible(0.25, 24.5, 25.9, bear=False) is True     # the APLD case
    assert ot.entry_impossible(1.45, 24.5, 25.9, bear=False) is False
    assert ot.entry_impossible(0.30, 26.0, 25.9, bear=False) is False    # out of the money: no floor
    assert ot.entry_impossible(0.10, 5.5, 5.0, bear=True) is True        # put 0.50 in the money for 0.10
    assert ot.chain_fresh({"scannedAt": "2026-10-02T16:30:00-05:00"}, datetime(2026, 10, 2, 16, 31, tzinfo=ZoneInfo("America/Chicago")).timestamp())
    assert not ot.chain_fresh({"diskCached": True}, 0)


def test_jev_v2_text_carries_the_rule_definition_and_rvol_meaning():
    from momx.jev_trade import describe
    t = {"rule": "mxaplus", "symbol": "TSLA", "at": "2026-10-05T09:44:00-04:00", "stockPrice": 370.0,
         "contract": "TSLA  261009C00375000", "entry": 3.8, "delta": 0.4, "expiry": "2026-10-09"}
    row = {"industry": "Autos", "pctChange": 4.5, "rvol": {"15m": {"value": 6.0, "bg": "cyan"}}, "m5": {}}
    v2 = describe(t, row, None, "bull", "2026-10-05")
    assert "What this rule means (the trader's own definition): The MomoX-style A+" in v2
    assert "How to read RVOL" in v2 and "15m 6.0 (lit)" in v2
    assert "PUT side" in describe(t, row, None, "bear", "2026-10-05")
    v1 = describe(t, row, None, "bull", "2026-10-05", version=1)
    assert "What this rule means" not in v1 and "How to read RVOL" not in v1


def test_the_scanner_row_gets_a_jev_pick_stamp_only_for_a_live_jev_take(tmp_path):
    b, _ = book(tmp_path, {"X": chain(spot=100.4)}, jev=FakeJev(p=0.42))
    row = go_row()
    b.apply("Watchlist", {"rows": [row]}, MON)
    b.apply("Watchlist", {"rows": [row]}, MON)
    assert row["dayLines"]["jev"] and row["jevPick"]["p"] == 0.42
    low = go_row()
    b2, _ = book(tmp_path / "low", {"X": chain(spot=100.4)}, jev=FakeJev(p=0.1))
    b2.apply("Watchlist", {"rows": [low]}, MON)
    b2.apply("Watchlist", {"rows": [low]}, MON)
    assert "jevPick" not in low and "jev" not in (low.get("dayLines") or {})


def test_both_books_share_one_fresh_chain_for_a_minute(monkeypatch):
    calls = []
    fresh = {"scannedAt": datetime.now(ET).isoformat(), "selectedExpiryChainRows": [{"symbol": "x"}]}
    stale = {"scannedAt": "2026-09-28T07:00:00-04:00", "diskCached": True, "selectedExpiryChainRows": [{"symbol": "x"}]}
    answers = {"AAA": fresh, "BBB": stale}
    monkeypatch.setattr(ot, "_http_json", lambda url: calls.append(url) or answers[url.rsplit("=", 1)[-1]])
    monkeypatch.setattr(ot, "_SHARED_CHAINS", {})
    assert ot._default_fetch("AAA") is fresh and ot._default_fetch("AAA") is fresh
    assert len(calls) == 1                                   # second book reused it
    ot._default_fetch("BBB"); ot._default_fetch("BBB")
    assert len(calls) == 3                                   # a stale copy is never shared


def test_risk_check_two_expected_moves_and_100_percent_roi():
    """His rule 2026-10-03: the option must be worth >= 2x its cost at 2x the expected move."""
    from momx.option_track import risk_check
    # call: spot 100, EM 2 -> edge 104; 102C bought at 0.80 is worth 2.00 there = +150%
    r = risk_check(100, 2, 102, 0.80, bear=False)
    assert r == {"em2Edge": 104.0, "roi2em": 1.5, "riskOk": True}
    # same strike bought at 1.20 -> 2.00 is only +67%: fails
    assert risk_check(100, 2, 102, 1.20, bear=False)["riskOk"] is False
    # put: spot 50, EM 1 -> edge 48; 49P at 0.40 worth 1.00 = +150%
    assert risk_check(50, 1, 49, 0.40, bear=True)["riskOk"] is True
    # strike beyond the edge is worth nothing there: -100%
    assert risk_check(100, 2, 105, 0.30, bear=False)["roi2em"] == -1.0
    # missing inputs -> unknown, never a silent pass
    assert risk_check(100, None, 102, 0.8, bear=False) == {}
    assert risk_check(100, 2, 102, 0, bear=False) == {}


def test_a_new_trade_is_priced_at_the_signal_from_a_live_quote(tmp_path):
    """2026-10-08: entries waited 40-75 min behind the chain queue; now the live
    quote prices the trade on the same pass, the chain queue only re-prices."""
    asked = []

    def quotes(query):
        asked.append(query)
        return {"results": [{"symbol": "X", "ok": True, "chainAt": "2026-09-28T09:40:00-04:00",
                             "pick": {"contract": "X_C101.0_1002", "strike": 101.0, "expiry": "2026-10-02",
                                      "delta": 0.4, "spot": 100.0, "entry": 1.2},
                             "z": {"contract": "X_C101.0_0928", "strike": 101.0, "entry": 0.25},
                             "e": None, "levels": {"expectedMove": 2.0}}]}

    calls = []

    def fetch(sym):
        calls.append(sym)
        return chain(call_mid=(1.9, 2.1))     # a later, different chain - must NOT set the entry

    b = ot.OptionTrackBook(tmp_path, direction="bull", fetch=fetch, healthy=lambda: True, background=False,
                           clock=Clock(MON.timestamp()), sleep=lambda s: None, jev=FakeJev(), quote_fetch=quotes)
    b.apply("Watchlist", {"rows": [go_row()]}, MON)
    t = b.trades()[0]
    assert asked == ["X:C:100.0"]
    assert (t["status"], t["contract"], t["entry"], t["entryVia"]) == ("open", "X_C101.0_1002", 1.2, "quote")
    assert t["entryAt"].startswith("2026-09-28T09:50") and t["zEntry"] == 0.25 and t["expectedMove"] == 2.0


def test_no_live_quote_falls_back_to_the_chain_queue(tmp_path):
    b = ot.OptionTrackBook(tmp_path, direction="bull", fetch=lambda s: chain(), healthy=lambda: True,
                           background=False, clock=Clock(MON.timestamp()), sleep=lambda s: None, jev=FakeJev(),
                           quote_fetch=lambda q: {"results": [{"symbol": "X", "ok": False, "why": "no chain held"}]})
    b.apply("Watchlist", {"rows": [go_row()]}, MON)
    t = b.trades()[0]
    assert (t["status"], t["entry"]) == ("open", 1.05) and "entryVia" not in t


def test_live_today_needs_a_5m_candle_from_today():
    today = datetime(2026, 9, 28, 9, 45, tzinfo=ET).timestamp()
    yday = datetime(2026, 9, 25, 15, 55, tzinfo=ET).timestamp()
    assert _REAL_LIVE_TODAY({"m5": {"sqzMom": {"barAt": today}}}, MON) is True
    assert _REAL_LIVE_TODAY({"rvol": {"5m": {"barAt": today}}}, MON) is True
    assert _REAL_LIVE_TODAY({"m5": {"sqzMom": {"barAt": yday}}, "rvol": {"5m": {"barAt": yday}}}, MON) is False
    assert _REAL_LIVE_TODAY({}, MON) is False


def test_a_stale_board_takes_no_trade(tmp_path, monkeypatch):
    """Holiday / stale feed: yesterday's rows must not open trades today."""
    monkeypatch.setattr(ot, "live_today", _REAL_LIVE_TODAY)
    yday = datetime(2026, 9, 25, 15, 55, tzinfo=ET).timestamp()
    stale = go_row(momoxAPlus={"at": "x"}, marketTurn={"at": "y"}, rvol={"5m": {"barAt": yday}})
    b, _ = book(tmp_path, {"X": chain()})
    b.apply("Watchlist", {"rows": [stale]}, MON)
    assert b.trades() == []
    fresh = go_row(rvol={"5m": {"barAt": datetime(2026, 9, 28, 9, 45, tzinfo=ET).timestamp()}})
    b.apply("Watchlist", {"rows": [fresh]}, MON)
    assert "go" in [t["rule"] for t in b.trades()]   # (a GO row is also the day's ⭐1)


def test_info_rules_are_no_longer_capped_at_150():
    assert ot.INFO_MAX_PER_DAY >= 1000 and ot.INFO_CAP.get("jev", 0) >= 1000


def test_background_saves_are_batched_and_flushed(tmp_path):
    """10-08: the day file and carry.json were re-encoded after EVERY priced symbol."""
    clock = Clock(MON.timestamp())
    b = ot.OptionTrackBook(tmp_path, fetch=lambda s: {}, healthy=lambda: True, background=True, clock=clock,
                           sleep=lambda s: None, quote_fetch=lambda q: None)
    b._day, b._trades = "2026-09-28", [{"rule": "go", "symbol": "A"}]
    b._save()
    path = tmp_path / ot.DIRNAME / "2026-09-28.json"
    b._trades.append({"rule": "go", "symbol": "B"})
    clock.t += 3
    b._save()
    assert len(json.loads(path.read_text(encoding="utf-8"))["trades"]) == 1     # batched
    b._flush()
    assert len(json.loads(path.read_text(encoding="utf-8"))["trades"]) == 2
    assert "\n" not in path.read_text(encoding="utf-8")                          # compact


def test_finished_carries_write_each_past_day_file_once(tmp_path):
    """11-02: 8,884 Friday expiries each re-read and re-wrote a 6 MB day file under
    the lock, and every scanner board waited hours behind it."""
    clock = Clock(MON.timestamp())
    b = ot.OptionTrackBook(tmp_path, fetch=lambda s: {}, healthy=lambda: True, background=True, clock=clock,
                           sleep=lambda s: None, quote_fetch=lambda q: None)
    b._day = "2026-09-28"
    path = tmp_path / ot.DIRNAME / "2026-09-25.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"trades": [{"rule": "go", "symbol": "A"}, {"rule": "go", "symbol": "B"},
                                           {"rule": "go", "symbol": "C"}]}), encoding="utf-8")
    writes = []
    real_replace = ot.os.replace
    ot.os.replace = lambda a, z: (writes.append(str(z)), real_replace(a, z))[1]
    try:
        for sym, best in (("A", 2.5), ("B", 3.0)):
            b._finish_carry({"day": "2026-09-25", "rule": "go", "symbol": sym, "holdBest": best,
                             "holdBestAt": "2026-09-26T10:00:00-04:00"})
        assert not any(w.endswith("2026-09-25.json") for w in writes)      # queued, not written per carry
        b._flush()
    finally:
        ot.os.replace = real_replace
    assert sum(w.endswith("2026-09-25.json") for w in writes) == 1
    trades = {t["symbol"]: t for t in json.loads(path.read_text(encoding="utf-8"))["trades"]}
    assert trades["A"]["holdDone"] and trades["A"]["holdBest"] == 2.5
    assert trades["B"]["holdDone"] and trades["B"]["holdBest"] == 3.0
    assert "holdDone" not in trades["C"]


def test_the_1550_quote_pass_closes_every_trade_and_its_side_contracts(tmp_path):
    """10-09: the chain queue never reached MRNA after 15:50 - no close recorded, so
    'held to the close' (+934% on the same-day 200C) was invisible."""
    asked = []

    def quotes(contracts):
        asked.append(list(contracts))
        return {"quotes": {
            "MAIN": {"bid": 26.35, "ask": 28.0, "mid": 27.175},
            "ZERO": {"bid": 23.6, "ask": 25.45, "mid": 24.525},
        }}

    clock = Clock(datetime(2026, 9, 28, 15, 51, tzinfo=ET).timestamp())
    b = ot.OptionTrackBook(tmp_path, direction="bull", fetch=lambda s: {}, healthy=lambda: True, background=False,
                           clock=clock, sleep=lambda s: None, jev=FakeJev(), quote_fetch=lambda q: None,
                           quotes_fetch=quotes)
    b._load("2026-09-28")
    b._trades = [
        {"rule": "skit", "symbol": "MRNA", "status": "open", "contract": "MAIN", "entry": 8.475, "best": 13.8,
         "expiry": "2026-10-16", "zContract": "ZERO", "zEntry": 2.38, "zStatus": "closed", "zExit": 5.35,
         "zBest": 5.35, "exitAt": "2026-09-28T10:45:00-04:00", "exitPrice": 11.3},
        {"rule": "go", "symbol": "XYZ", "status": "open", "contract": "NOQUOTE", "entry": 1.0, "expiry": "2026-10-16"},
    ]
    b._quote_close(datetime(2026, 9, 28, 15, 51, tzinfo=ET))
    mrna, xyz = b.trades()
    assert mrna["status"] == "closed" and mrna["close"] == 27.175 and mrna["closeBid"] == 26.35
    assert mrna["exitPrice"] == 11.3                       # his exit is kept
    assert mrna["holdBest"] == 27.175                      # the close was the day's best
    assert mrna["zClose"] == 24.525 and mrna["zBest"] == 24.525 and mrna["zExit"] == 5.35
    assert xyz["status"] == "open" and "closeQ" not in xyz  # no quote: left to the chain queue
    assert sorted(asked[0]) == ["MAIN", "NOQUOTE", "ZERO"]


def _close_book(tmp_path, quotes):
    clock = Clock(datetime(2026, 9, 28, 15, 51, tzinfo=ET).timestamp())
    b = ot.OptionTrackBook(tmp_path, direction="bull", fetch=lambda s: {}, healthy=lambda: True, background=False,
                           clock=clock, sleep=lambda s: None, jev=FakeJev(), quote_fetch=lambda q: None,
                           quotes_fetch=quotes)
    b._load("2026-09-28")
    return b


def test_a_failed_close_batch_is_retried_not_dropped(tmp_path):
    calls = {"n": 0}

    def quotes(contracts):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("api_server busy")
        return {"quotes": {"MAIN": {"bid": 2.0, "ask": 2.2, "mid": 2.1}}}

    b = _close_book(tmp_path, quotes)
    b._trades = [{"rule": "go", "symbol": "AAA", "status": "open", "contract": "MAIN", "entry": 1.0, "expiry": "2026-09-28"}]
    local = datetime(2026, 9, 28, 15, 51, tzinfo=ET)
    b._quote_close(local)
    assert b.trades()[0]["status"] == "open" and b._quote_closed_day == ""
    b._quote_close(local)
    assert b.trades()[0]["close"] == 2.1 and b._quote_closed_day == "2026-09-28"


def test_close_batch_keeps_the_exit_reason_and_prices_side_legs_without_a_main_quote(tmp_path):
    b = _close_book(tmp_path, lambda c: {"quotes": {"MAIN": {"bid": 3.0, "ask": 3.2, "mid": 3.1},
                                                     "ZERO": {"bid": 0.9, "ask": 1.1, "mid": 1.0}}})
    b._trades = [
        {"rule": "go", "symbol": "AAA", "status": "open", "contract": "MAIN", "entry": 1.0, "expiry": "2026-09-28",
         "exitAt": "2026-09-28T14:05:00-04:00", "exitReason": "below VWAP"},
        {"rule": "go", "symbol": "BBB", "status": "open", "contract": "NOQUOTE", "entry": 1.0, "expiry": "2026-09-28",
         "zContract": "ZERO", "zEntry": 0.5, "zStatus": "open"},
    ]
    b._quote_close(datetime(2026, 9, 28, 15, 51, tzinfo=ET))
    a, bb = b.trades()
    assert a["exitReason"] == "below VWAP" and a["exitPrice"] == 3.1 and a["close"] == 3.1
    assert bb["zClose"] == 1.0 and bb["status"] == "open" and "closeQ" not in bb


def test_zero_bid_records_loss_instead_of_stale_last_trade():
    assert ot._price({"bid": 0, "ask": 0.02, "last": 5, "mark": 5}) == 0.01
    assert ot._price({"ask": 0.02, "last": 5}) == 0
    assert ot._price({"bid": 2, "ask": 1, "last": 5}) == 0
    assert ot._price({"bid": 0, "ask": float("inf")}) == 0


def test_missing_delta_close_retries_after_main_is_closed(tmp_path):
    quotes = {"MAIN": {"bid": 2, "ask": 2.2}}
    b = _close_book(tmp_path, lambda c: {"quotes": quotes})
    b._trades = [{"rule": "go", "symbol": "AAA", "status": "open", "contract": "MAIN",
                  "entry": 1, "expiry": "2026-09-28", "eContract": "DELTA", "eEntry": 1,
                  "eLast": 1, "eStatus": "open"}]
    local = datetime(2026, 9, 28, 15, 51, tzinfo=ET)
    b._quote_close(local)
    assert b.trades()[0]["status"] == "closed"
    assert b._quote_closed_day == ""
    quotes["DELTA"] = {"bid": 0, "ask": 0.02, "last": 1}
    b._quote_close(local)
    assert b.trades()[0]["eExit"] == 0.01
    assert b.trades()[0]["eClose"] == 0.01
    assert b._quote_closed_day == "2026-09-28"


def test_explosive_and_steady_patterns_are_bot_rules_on_both_boards():
    now = datetime(2026, 10, 12, 10, 0, tzinfo=ET)
    for bear in (False, True):
        assert "explosive" in ot.fired({"symbol": "X", "m5": {"pattern": "explosive"}}, bear, now)
        assert "steady" in ot.fired({"symbol": "X", "m5": {"pattern": "steady"}}, bear, now)
        assert not {"explosive", "steady"} & set(ot.fired({"symbol": "X", "m5": {"pattern": None}}, bear, now))
    assert "explosive" in ot.INFO_RULES and "steady" in ot.INFO_RULES


def test_per_minute_price_path_is_saved_and_reloaded(tmp_path):
    """2026-10-17: one mid per minute per trade, in its own day file."""
    from momx import option_track as ot
    book = ot.OptionTrackBook(tmp_path)
    book._load("2026-10-19")
    t = {"rule": "zs2", "symbol": "AAPL", "at": "2026-10-19T09:35:00-04:00"}
    book._note_path(t, 1.20, "2026-10-19T09:36:05-04:00")
    book._note_path(t, 1.25, "2026-10-19T09:36:40-04:00")     # same minute: last value wins
    book._note_path(t, 1.40, "2026-10-19T09:37:10-04:00")
    book._write_day()
    paths = ot.load_paths(tmp_path, "2026-10-19")
    assert paths[ot.path_key(t)] == {"09:36": 1.25, "09:37": 1.4}
    again = ot.OptionTrackBook(tmp_path)
    again._load("2026-10-19")
    assert again._paths == paths


def test_price_path_file_is_rewritten_at_most_every_5_minutes(tmp_path):
    """2026-10-18: the paths file is a few MB by the close - not on every save."""
    from momx import option_track as ot
    clock = [1000.0]
    book = ot.OptionTrackBook(tmp_path, clock=lambda: clock[0])
    book._load("2026-10-19")
    t = {"rule": "zs2", "symbol": "AAPL", "at": "2026-10-19T09:35:00-04:00"}
    book._note_path(t, 1.20, "2026-10-19T09:36:05-04:00")
    book._write_day()                                           # first write goes out
    book._note_path(t, 1.30, "2026-10-19T09:37:05-04:00")
    clock[0] += 60
    book._write_day()                                           # a minute later: held back
    assert ot.load_paths(tmp_path, "2026-10-19")[ot.path_key(t)] == {"09:36": 1.2}
    clock[0] += ot.PATHS_SAVE_EVERY_SECONDS
    book._write_day()                                           # 5 minutes on: written
    assert ot.load_paths(tmp_path, "2026-10-19")[ot.path_key(t)] == {"09:36": 1.2, "09:37": 1.3}
    book._note_path(t, 1.50, "2026-10-19T15:59:05-04:00")
    book._load("2026-10-20")                                    # the day switch never loses a minute
    assert ot.load_paths(tmp_path, "2026-10-19")[ot.path_key(t)]["15:59"] == 1.5


def test_retired_jev_scanner_pick_does_not_create_a_virtual_entry(tmp_path):
    b,_=book(tmp_path,{'X':chain(spot=100.4)})
    row=go_row(jevRow={'p':.99,'at':MON.isoformat()})
    b.apply('Watchlist',{'rows':[row]},MON)
    assert not any(t['rule']=='jev' for t in b.trades())
    assert any(t['rule']=='go' for t in b.trades())


def test_new_entry_carries_its_bid_ask_for_the_paper_mirror(tmp_path):
    """2026-11-04 truth check: the paper mirror buys at the mid of lastBid/lastAsk.
    A new entry set lastAt but not the bid/ask, so the mirror waited for the
    ticker's next re-price (median 230 s). Both entry paths now carry them."""
    quotes = lambda q: {"results": [{"symbol": "X", "ok": True, "chainAt": "2026-09-28T09:40:00-04:00",
                                     "pick": {"contract": "X_C101.0_1002", "strike": 101.0, "expiry": "2026-10-02",
                                              "delta": 0.4, "spot": 100.0, "entry": 1.2,
                                              "entryBid": 1.15, "entryAsk": 1.25},
                                     "z": None, "e": None, "levels": {}}]}
    fast = ot.OptionTrackBook(tmp_path / "fast", direction="bull", fetch=lambda s: None, healthy=lambda: True,
                              background=False, clock=Clock(MON.timestamp()), sleep=lambda s: None, jev=FakeJev(),
                              quote_fetch=quotes)
    fast.apply("Watchlist", {"rows": [go_row()]}, MON)
    t = fast.trades()[0]
    assert (t["entryVia"], t["lastBid"], t["lastAsk"]) == ("quote", 1.15, 1.25)
    slow = ot.OptionTrackBook(tmp_path / "slow", direction="bull", fetch=lambda s: chain(), healthy=lambda: True,
                              background=False, clock=Clock(MON.timestamp()), sleep=lambda s: None, jev=FakeJev(),
                              quote_fetch=lambda q: {"results": [{"symbol": "X", "ok": False}]})
    slow.apply("Watchlist", {"rows": [go_row()]}, MON)
    t = slow.trades()[0]
    assert t["status"] == "open" and "entryVia" not in t
    assert (t["lastBid"], t["lastAsk"]) == (t["entryBid"], t["entryAsk"]) and t["lastBid"] is not None


def test_the_bot_holds_through_a_vwap_only_cross_and_exits_on_the_21_ema(tmp_path):
    """2026-11-10 (NVDA put sold on a VWAP bounce): the real book keeps the trade open on a close
    across VWAP alone, and closes it on the first close past the 21 EMA."""
    clock = Clock(MON.timestamp())
    prices = [chain(call_mid=(1.0, 1.1)), chain(call_mid=(1.4, 1.6)), chain(call_mid=(1.9, 2.1))]
    b, _ = book(tmp_path, {"X": prices}, clock=clock)
    b.apply("Watchlist", {"rows": [go_row(m5={"gapGo": {"goAt": GO_AT}, **m5_bar(101.0, MON.timestamp() - 60)})]}, MON)
    clock.t = MON.timestamp() + 1200
    dip = go_row(m5={"gapGo": {"goAt": GO_AT}, **m5_bar(99.2, clock.t - 300, vwap=99.5, e21=99.0)})
    b.apply("Watchlist", {"rows": [dip]}, datetime.fromtimestamp(clock.t, ET))
    t = next(x for x in b.trades() if x["rule"] == "go")
    assert not t.get("exitReason")
    clock.t += 600
    under = go_row(m5={"gapGo": {"goAt": GO_AT}, **m5_bar(98.5, clock.t - 300, vwap=99.5, e21=99.0)})
    b.apply("Watchlist", {"rows": [under]}, datetime.fromtimestamp(clock.t, ET))
    t = next(x for x in b.trades() if x["rule"] == "go")
    assert t["exitReason"] == "below EMA21"


# --- Level re-entry (his rule 2026-11-10: "re-enter if it sets up again") -----------------
def _at(h, m, s=0):
    return datetime(2026, 9, 28, h, m, s, tzinfo=ET)


def _bear_row(close, bar_start):
    """A put setup: e21 99.5; close under it = right side for a put, over it = the 21 EMA exit."""
    return {"symbol": "X", "last": close, "m5": m5_bar(close, bar_start.timestamp(), e21=99.5)}


def _bull_row(close, bar_start):
    """A call setup: e21 99.5; close above it = right side for a call, under it = the 21 EMA exit."""
    return {"symbol": "X", "last": close, "m5": m5_bar(close, bar_start.timestamp(), e21=99.5)}


#: per book: (row helper, right-side close, wrong-side close, the 21 EMA exit reason)
SIDES = {"bear": (_bear_row, 99.0, 100.5, "above EMA21"),
         "bull": (_bull_row, 100.5, 99.0, "below EMA21")}


def _level_book(tmp_path, monkeypatch, rules=(), direction="bear"):
    clock = Clock(_at(9, 35).timestamp())
    b, _ = book(tmp_path, {"X": chain(spot=100.4)}, direction=direction, clock=clock)
    monkeypatch.setattr(ot, "fired", lambda *args: list(rules))
    b._levels.evaluate = lambda *a, **k: {"X": {"version": 1, "highestLevel": 3, "setupCount": 3, "virtualOnly": True}}
    return b, clock


def _scan(b, clock, when, row):
    clock.t = when.timestamp()
    b.apply("Mag7", {"rows": [row]}, when)


def _level3(b):
    return [t for t in b.trades() if t["rule"] == "level3"]


def _dropped_scan(b, clock, when, row):
    """A scan the evaluator really evaluated (a valid publication, status ready) on which the
    ticker's Level signal is absent - the setup broke. {} = evaluated, nothing qualified; the
    ticker has a context, i.e. the worker really judged its (fresh) row."""
    keep, keep_ctx = b._levels.evaluate, getattr(b._levels, "contexts", {})

    def judged(*a, **k):
        b._levels.contexts = {row["symbol"]: {"version": 1, "highestLevel": 0}}
        return {}

    b._levels.evaluate = judged
    try:
        _scan(b, clock, when, row)
    finally:
        b._levels.evaluate, b._levels.contexts = keep, keep_ctx


def _stale_row_scan(b, clock, when, row):
    """A good publication on which this ticker's row was NOT judged (stale / unobserved: no context)."""
    keep, keep_ctx = b._levels.evaluate, getattr(b._levels, "contexts", {})

    def unjudged(*a, **k):
        b._levels.contexts = {}
        return {}

    b._levels.evaluate = unjudged
    try:
        _scan(b, clock, when, row)
    finally:
        b._levels.evaluate, b._levels.contexts = keep, keep_ctx


def _failed_scan(b, clock, when, row):
    """A scan the evaluator could NOT evaluate (stale publication, worker timeout / error): None."""
    keep = b._levels.evaluate
    b._levels.evaluate = lambda *a, **k: None
    try:
        _scan(b, clock, when, row)
    finally:
        b._levels.evaluate = keep


@pytest.mark.parametrize("side", ["bear", "bull"])
def test_level_signal_re_enters_after_the_bot_exited_like_nvda(tmp_path, monkeypatch, side):
    """NVDA 2026-11-10: Level 3 put 09:35, sold 10:00 on the 21 EMA, Level 3 again 10:30 -> a second trade.
    The bull book (calls) mirrors it: right side = close above the 21 EMA."""
    row, right, wrong, exit_reason = SIDES[side]
    b, clock = _level_book(tmp_path, monkeypatch, direction=side)
    _scan(b, clock, _at(9, 35), row(right, _at(9, 25)))
    first = _level3(b)
    assert len(first) == 1 and first[0]["status"] == "open" and first[0]["virtualOnly"] is True
    # 10:00 the 5m candle closes across the 21 EMA: the bot exits (the signal is still on).
    _scan(b, clock, _at(10, 0), row(wrong, _at(9, 55)))
    assert len(_level3(b)) == 1 and _level3(b)[0]["exitReason"] == exit_reason
    # Seconds later the level still shows, but no candle has closed back on the right side since
    # the exit: no instant re-buy loop.
    _scan(b, clock, _at(10, 0, 30), row(wrong, _at(9, 55)))
    assert len(_level3(b)) == 1
    # The Level signal drops on a later scan (the setup broke)...
    _dropped_scan(b, clock, _at(10, 15), row(wrong, _at(10, 10)))
    # ...and 10:30 it set up again (signal back + a candle closed on the right side): a new record.
    _scan(b, clock, _at(10, 30), row(right, _at(10, 25)))
    recs = _level3(b)
    assert len(recs) == 2
    second = recs[1]
    assert second["at"] == _at(10, 30).isoformat() and second["at"] != recs[0]["at"]
    assert second["status"] == "open" and second["entry"] > 0          # priced (need.add)
    assert second["virtualOnly"] is True and second["levelSignal"]["strategyLevel"] == 3
    assert second["levelReentry"] is True and not recs[0].get("levelReentry")
    assert recs[0]["exitReason"] == exit_reason                         # the first is untouched
    # Every level fired again, each on its own record.
    assert sum(t["rule"] == "level1" for t in b.trades()) == 2
    # The BOT panel (api_server reads SUMMARY_KEYS) shows which record is the re-entry.
    shown = [t for t in b.summary() if t["rule"] == "level3"]
    assert "levelReentry" in ot.OptionTrackBook.SUMMARY_KEYS
    assert shown[1]["levelReentry"] is True and "levelReentry" not in shown[0]


def test_a_failed_level_evaluation_does_not_re_arm(tmp_path, monkeypatch):
    """Review finding: evaluate() failing (stale publication > 120 s, 5 s worker timeout, worker
    error / restart) is not "the setup went away" - it must not count as the dropped scan."""
    b, clock = _level_book(tmp_path, monkeypatch)
    _scan(b, clock, _at(9, 35), _bear_row(99.0, _at(9, 25)))
    _scan(b, clock, _at(10, 0), _bear_row(100.5, _at(9, 55)))          # 21 EMA exit
    _failed_scan(b, clock, _at(10, 15), _bear_row(100.5, _at(10, 10)))  # evaluator hiccup
    _stale_row_scan(b, clock, _at(10, 20), _bear_row(100.5, _at(10, 15)))  # good scan, this row not judged
    assert len(_level3(b)) == 1                                          # and nothing opens on it
    _scan(b, clock, _at(10, 30), _bear_row(99.0, _at(10, 25)))
    assert len(_level3(b)) == 1, "a failed evaluation re-armed the Level re-entry"
    # A real evaluation that leaves the ticker out still re-arms it.
    _dropped_scan(b, clock, _at(10, 35), _bear_row(99.0, _at(10, 30)))
    _scan(b, clock, _at(10, 40), _bear_row(99.0, _at(10, 35)))
    assert len(_level3(b)) == 2 and _level3(b)[1]["levelReentry"] is True


def test_level_signal_never_opens_a_second_copy_while_one_is_open(tmp_path, monkeypatch):
    b, clock = _level_book(tmp_path, monkeypatch)
    _scan(b, clock, _at(9, 35), _bear_row(99.0, _at(9, 25)))
    for when in (_at(10, 0), _at(10, 30), _at(11, 22)):
        _scan(b, clock, when, _bear_row(99.0, when - timedelta(minutes=5)))
    assert len(_level3(b)) == 1 and not _level3(b)[0].get("exitAt")


def test_non_level_rules_still_fire_once_a_day(tmp_path, monkeypatch):
    b, clock = _level_book(tmp_path, monkeypatch, rules=("go",))
    b._levels.evaluate = lambda *a, **k: {}
    _scan(b, clock, _at(9, 35), _bear_row(99.0, _at(9, 25)))
    _scan(b, clock, _at(10, 0), _bear_row(100.5, _at(9, 55)))
    go = [t for t in b.trades() if t["rule"] == "go"]
    assert len(go) == 1 and go[0]["exitReason"] == "above EMA21"
    _scan(b, clock, _at(10, 30), _bear_row(99.0, _at(10, 25)))
    assert len([t for t in b.trades() if t["rule"] == "go"]) == 1


def test_level_re_entry_survives_a_reload_without_duplicates(tmp_path, monkeypatch):
    b, clock = _level_book(tmp_path, monkeypatch)
    _scan(b, clock, _at(9, 35), _bear_row(99.0, _at(9, 25)))
    _scan(b, clock, _at(10, 0), _bear_row(100.5, _at(9, 55)))
    _dropped_scan(b, clock, _at(10, 15), _bear_row(100.5, _at(10, 10)))
    _scan(b, clock, _at(10, 30), _bear_row(99.0, _at(10, 25)))
    b._save(force=True)
    again, _ = book(tmp_path, {"X": chain(spot=100.4)}, direction="bear", clock=clock)
    again._levels.evaluate = b._levels.evaluate
    again._load(_at(10, 30).date().isoformat())
    assert [t["at"] for t in _level3(again)] == [_at(9, 35).isoformat(), _at(10, 30).isoformat()]
    # The reloaded book keeps scanning: the second record is open, so nothing new is added.
    _scan(again, clock, _at(10, 35), _bear_row(99.0, _at(10, 30)))
    assert len(_level3(again)) == 2
    saved = json.loads((tmp_path / ot.DIRNAME / "2026-09-28.json").read_text(encoding="utf-8"))
    assert sum(t["rule"] == "level3" for t in saved["trades"]) == 2


def test_two_level_records_close_and_carry_separately(tmp_path, monkeypatch):
    """Both records of a re-entry reach 15:50 and each weekly carries its own holdBest."""
    b, clock = _level_book(tmp_path, monkeypatch)
    _scan(b, clock, _at(9, 35), _bear_row(99.0, _at(9, 25)))
    _scan(b, clock, _at(10, 0), _bear_row(100.5, _at(9, 55)))
    _dropped_scan(b, clock, _at(10, 15), _bear_row(100.5, _at(10, 10)))
    _scan(b, clock, _at(10, 30), _bear_row(99.0, _at(10, 25)))
    _scan(b, clock, _at(15, 52), _bear_row(99.0, _at(15, 45)))
    recs = _level3(b)
    assert [t["status"] for t in recs] == ["closed", "closed"]
    assert recs[0]["exitReason"] == "above EMA21" and recs[1]["exitReason"] == "15:50 close"
    carried = [c for c in b._carry_list() if c["rule"] == "level3"]
    assert sorted(c["at"] for c in carried) == [t["at"] for t in recs]


def _levels_at(b, highest):
    b._levels.evaluate = lambda *a, **k: {"X": {"version": 1, "highestLevel": highest, "setupCount": highest,
                                                "virtualOnly": True}}


def _cycle(b, clock, when, side="bear"):
    """One full set-up-again cycle after an open Level trade: 21 EMA exit, a dropped scan, back."""
    row, right, wrong, _ = SIDES[side]
    _scan(b, clock, when, row(wrong, when - timedelta(minutes=5)))
    _dropped_scan(b, clock, when + timedelta(minutes=5), row(wrong, when))
    back = when + timedelta(minutes=10)
    _scan(b, clock, back, row(right, back - timedelta(minutes=5)))


@pytest.mark.parametrize("side", ["bear", "bull"])
def test_choppy_level_that_holds_all_day_does_not_round_trip(tmp_path, monkeypatch, side):
    """Review finding: the Level holds every scan while the 5m candles flip across the 21 EMA.
    Without a dropped scan there is no re-entry, so the records stay bounded."""
    row, right, wrong, exit_reason = SIDES[side]
    b, clock = _level_book(tmp_path, monkeypatch, direction=side)
    when = _at(9, 35)
    for i in range(70):                                   # 09:35 -> 15:20, every 5 minutes
        close = right if i % 2 == 0 else wrong
        _scan(b, clock, when, row(close, when - timedelta(minutes=5)))
        when += timedelta(minutes=5)
    assert len(_level3(b)) == 1 and _level3(b)[0]["exitReason"] == exit_reason
    assert all(sum(t["rule"] == r for t in b.trades()) == 1 for r in ot.LEVEL_RULES)


@pytest.mark.parametrize("side", ["bear", "bull"])
def test_level_re_entries_are_capped_per_ticker_per_day(tmp_path, monkeypatch, side):
    row, right, _, _ = SIDES[side]
    b, clock = _level_book(tmp_path, monkeypatch, direction=side)
    _scan(b, clock, _at(9, 35), row(right, _at(9, 25)))
    for start in (_at(10, 0), _at(10, 30), _at(11, 0), _at(11, 30), _at(12, 0)):
        _cycle(b, clock, start, side)
    recs = _level3(b)
    assert len(recs) == 1 + ot.MAX_LEVEL_REENTRIES == 4
    assert sum(bool(t.get("levelReentry")) for t in recs) == 3
    assert sum(t["rule"] == "level1" for t in b.trades()) == 4


def test_unpriced_re_entry_does_not_block_a_later_one(tmp_path, monkeypatch):
    """A Level record that ended never-bought (no_contract / ai_skipped) counts as done at its signal time."""
    b, clock = _level_book(tmp_path, monkeypatch)
    _scan(b, clock, _at(9, 35), _bear_row(99.0, _at(9, 25)))
    _cycle(b, clock, _at(10, 0))                          # re-entry at 10:10
    second_at = _at(10, 10).isoformat()
    assert [t["at"] for t in _level3(b)][-1] == second_at
    for t in b._trades:                                   # that re-entry never got a contract
        if t["at"] == second_at:
            for k in ("exitAt", "exitReason", "entry", "entryAt", "contract"):
                t.pop(k, None)
            t["status"] = "no_contract" if t["rule"] != "level2" else "ai_skipped"
    # Still set up, but no dropped scan since that record: nothing new.
    _scan(b, clock, _at(10, 20), _bear_row(99.0, _at(10, 15)))
    assert len(_level3(b)) == 2
    _dropped_scan(b, clock, _at(10, 25), _bear_row(99.0, _at(10, 20)))
    _scan(b, clock, _at(10, 35), _bear_row(99.0, _at(10, 30)))
    recs = _level3(b)
    assert len(recs) == 3 and recs[-1]["at"] == _at(10, 35).isoformat() and recs[-1]["status"] == "open"


def test_open_level3_blocks_a_level1_re_entry_on_the_same_ticker(tmp_path, monkeypatch):
    """Re-entry is per ticker, not per rule: any Level record still open blocks it."""
    b, clock = _level_book(tmp_path, monkeypatch)
    _levels_at(b, 1)
    _scan(b, clock, _at(9, 35), _bear_row(99.0, _at(9, 25)))
    _scan(b, clock, _at(10, 0), _bear_row(100.5, _at(9, 55)))           # level1 exits
    _levels_at(b, 3)
    _scan(b, clock, _at(10, 5), _bear_row(99.0, _at(10, 0)))             # level2/3 fire the first time
    assert len(_level3(b)) == 1 and _level3(b)[0]["status"] == "open"
    _dropped_scan(b, clock, _at(10, 15), _bear_row(99.0, _at(10, 10)))
    _scan(b, clock, _at(10, 30), _bear_row(99.0, _at(10, 25)))
    assert sum(t["rule"] == "level1" for t in b.trades()) == 1          # level3 still open
    # Once level3 has exited too, the same set-up-again path re-enters (all levels together).
    _cycle(b, clock, _at(10, 45))
    assert sum(t["rule"] == "level1" for t in b.trades()) == 2
    assert len(_level3(b)) == 2


# --- His top-of-board rule (2026-11-10: "Mag7 top 3", "use H/L column top") ----------------
def _hl(sym, hl, **over):
    """A board row; a scan MATCH unless scanPass=False is passed (rank_board also ships non-matches in "rows")."""
    row = {"symbol": sym, "last": 100.0, "highLow": {"value": hl}, "scanPass": True}
    row.update(over)
    return row


def test_hl_ranking_orders_calls_high_first_and_puts_low_first_with_stable_ties():
    rows = [_hl("A", 0.2), _hl("B", 0.9), _hl("C", 0.2), {"symbol": "D", "highLow": {}, "scanPass": True},
            {"symbol": "E", "scanPass": True}, _hl("F", -0.7), "junk", _hl("B", -1.0)]
    assert ot.hl_ranking(rows, bear=False) == [("B", 0.9), ("A", 0.2), ("C", 0.2), ("F", -0.7)]
    # Puts: lowest first; the tie A / C keeps the board's own order.
    assert ot.hl_ranking(rows, bear=True) == [("F", -0.7), ("A", 0.2), ("C", 0.2), ("B", 0.9)]
    assert ot.hl_ranking(None, bear=False) == []


def _top_book(tmp_path, monkeypatch, direction="bull", syms=("X",)):
    clock = Clock(_at(9, 35).timestamp())
    b, _ = book(tmp_path, {s: chain(spot=100.4) for s in syms}, direction=direction, clock=clock)
    monkeypatch.setattr(ot, "fired", lambda *args: [])
    b._levels.evaluate = lambda *a, **k: {}
    return b, clock


def _fire(b, sym):
    b._levels.evaluate = lambda *a, **k: {sym: {"version": 1, "highestLevel": 3, "setupCount": 3, "virtualOnly": True}}


def _board(b, clock, when, rows, board="Watchlist", rest=()):
    clock.t = when.timestamp()
    b.apply(board, {"rows": list(rows), "rest": list(rest)}, when)


def test_top_runs_break_on_drop_out_and_on_a_long_gap(tmp_path, monkeypatch):
    b, _ = _top_book(tmp_path, monkeypatch)
    b._load("2026-09-28")
    t0 = _at(10, 0).timestamp()
    others = [("O%d" % i, 0.1 * i) for i in range(1, 7)]           # 6 names under X
    for s in (0, 60, 120):
        b._top_update("Watchlist", [("X", 0.95)] + others, t0 + s)
    assert b._top_stamp("Watchlist", "X", t0 + 120) == {"board": "Watchlist", "n": 7, "rank": 1,
                                                          "heldSeconds": 120, "hl": 0.95}
    # Out of the top 7 on one scan (#8): the run breaks, and starts again when it is back.
    b._top_update("Watchlist", [(f"P{i}", 1.0) for i in range(7)] + [("X", 0.95)], t0 + 180)
    assert b._top_stamp("Watchlist", "X", t0 + 180) == {"board": "Watchlist", "n": 7, "rank": 8,
                                                          "heldSeconds": 0, "hl": 0.95}
    b._top_update("Watchlist", [("X", 0.95)] + others, t0 + 240)
    b._top_update("Watchlist", [("X", 0.95)] + others, t0 + 300)
    assert b._top_stamp("Watchlist", "X", t0 + 300)["heldSeconds"] == 60
    # No scan for more than TOP_GAP_SECONDS (420 s): the board was not watched, every run starts again.
    assert ot.TOP_GAP_SECONDS == 420
    b._top_update("Watchlist", [("X", 0.95)] + others, t0 + 300 + 421)
    assert b._top_stamp("Watchlist", "X", t0 + 721)["heldSeconds"] == 0
    # Exactly 420 s apart still counts as watched.
    b._top_update("Watchlist", [("X", 0.95)] + others, t0 + 1141)
    assert b._top_stamp("Watchlist", "X", t0 + 1141)["heldSeconds"] == 420
    # A ticker not ranked at all (no H/L) gets rank None, held 0.
    assert b._top_stamp("Watchlist", "ZZ", t0 + 1141) == {"board": "Watchlist", "n": 7, "rank": None,
                                                           "heldSeconds": 0, "hl": None}
    # Other boards are not filtered: no stamp.
    assert b._top_stamp("21 EMA Touch Monthly", "X", t0) is None


def test_mag7_is_top_3_and_watchlist_top_7(tmp_path, monkeypatch):
    assert ot.TOP_N == {"Mag7": 3, "Watchlist": 7} and ot.TOP_HOLD_SECONDS == 300
    b, _ = _top_book(tmp_path, monkeypatch)
    b._load("2026-09-28")
    t0 = _at(10, 0).timestamp()
    ranking = [(f"S{i}", 1 - i / 10) for i in range(1, 9)]       # S1 .. S8
    for board in ("Mag7", "Watchlist"):
        b._top_update(board, ranking, t0)
        b._top_update(board, ranking, t0 + 120)
        b._top_update(board, ranking, t0 + 240)
        b._top_update(board, ranking, t0 + 300)
    assert set(b._top_runs["Mag7"]["runs"]) == {"S1", "S2", "S3"}
    assert set(b._top_runs["Watchlist"]["runs"]) == {f"S{i}" for i in range(1, 8)}
    assert b._top_stamp("Mag7", "S4", t0 + 300)["heldSeconds"] == 0
    assert b._top_stamp("Watchlist", "S7", t0 + 300)["heldSeconds"] == 300


def test_only_scan_matches_are_ranked_and_new_records_are_stamped(tmp_path, monkeypatch):
    """"rest" (non-matches) never take a top spot; the stamp goes on every new record, by board."""
    b, clock = _top_book(tmp_path, monkeypatch, syms=("X",))
    rows = [_hl("A", 0.9), _hl("B", 0.8), _hl("X", 0.7)]
    rest = [_hl("R1", 1.0), _hl("R2", 1.0), _hl("R3", 1.0)]
    for m in range(0, 6):
        _board(b, clock, _at(10, m), rows, board="Mag7", rest=rest)
    _fire(b, "X")
    _board(b, clock, _at(10, 6), rows, board="Mag7", rest=rest)
    recs = [t for t in b.trades() if t["symbol"] == "X"]
    assert {t["rule"] for t in recs} == {"level1", "level2", "level3"}
    for t in recs:
        assert t["topHL"] == {"board": "Mag7", "n": 3, "rank": 3, "heldSeconds": 360, "hl": 0.7}
    shown = [t for t in b.summary() if t["symbol"] == "X"]
    assert "topHL" in ot.OptionTrackBook.SUMMARY_KEYS and shown[0]["topHL"]["rank"] == 3


def test_a_board_outside_top_n_gets_no_stamp(tmp_path, monkeypatch):
    b, clock = _top_book(tmp_path, monkeypatch, syms=("X",))
    _fire(b, "X")
    _board(b, clock, _at(10, 0), [_hl("X", 0.9)], board="21 EMA Touch Monthly")
    recs = [t for t in b.trades() if t["symbol"] == "X"]
    assert recs and all("topHL" not in t for t in recs)


def test_bear_book_ranks_lowest_hl_first(tmp_path, monkeypatch):
    b, clock = _top_book(tmp_path, monkeypatch, direction="bear", syms=("X",))
    rows = [_hl("A", 0.9), _hl("X", -0.8), _hl("B", -0.5), _hl("C", -0.9)]
    _fire(b, "X")
    _board(b, clock, _at(10, 0), rows, board="Mag7")
    t = next(t for t in b.trades() if t["symbol"] == "X")
    assert t["topHL"]["rank"] == 2 and t["topHL"]["hl"] == -0.8


def test_new_day_resets_the_top_runs(tmp_path, monkeypatch):
    b, _ = _top_book(tmp_path, monkeypatch)
    b._load("2026-09-28")
    b._top_update("Mag7", [("X", 0.9)], _at(10, 0).timestamp())
    assert b._top_runs
    b._load("2026-09-29")
    assert b._top_runs == {}


def test_cmcsa_like_watchlist_top_7_for_6_min_passes_and_number_9_fails(tmp_path, monkeypatch):
    from momx.paper_mirror import TOP_OK, stamp_verdict
    names = [f"N{i}" for i in range(1, 11)]

    def rows(cmcsa_place):
        out = [_hl(s, round(0.99 - i * 0.05, 3)) for i, s in enumerate(names)]
        out.insert(cmcsa_place - 1, _hl("CMCSA", round(0.99 - (cmcsa_place - 1) * 0.05 + 0.001, 3)))
        return out

    for place, ok in ((5, True), (9, False)):
        b, clock = _top_book(tmp_path / str(place), monkeypatch, syms=("CMCSA",))
        for m in range(0, 6):
            _board(b, clock, _at(10, m), rows(place))
        _fire(b, "CMCSA")
        _board(b, clock, _at(10, 6), rows(place))
        t = next(t for t in b.trades() if t["symbol"] == "CMCSA" and t["rule"] == "level3")
        assert t["topHL"]["rank"] == place
        assert t["topHL"]["heldSeconds"] == (360 if ok else 0)
        verdict = stamp_verdict(t)
        assert (verdict[0] == TOP_OK) is ok
        if not ok:
            assert verdict[1] == "CMCSA was #9 on the Watchlist by H/L (needs top 7 for 5 min from 9:45)"


def test_a_level_re_entry_is_stamped_with_the_current_run_not_the_first(tmp_path, monkeypatch):
    """The re-entry record must see the run that holds at ITS time: X fell out of the Mag7 top 3
    at 10:15, so the 10:30 re-entry has been on top since 10:16 (14 min), not since 9:35."""
    b, clock = _level_book(tmp_path, monkeypatch, direction="bear")
    others = [_hl("A", -0.7), _hl("B", -0.6), _hl("C", -0.5)]

    def scan(when, close, bar, top=True):
        x = _bear_row(close, bar)
        x["highLow"], x["scanPass"] = {"value": -0.9 if top else 0.5}, True
        clock.t = when.timestamp()
        b.apply("Mag7", {"rows": [x] + others}, when)

    scan(_at(9, 35), 99.0, _at(9, 25))
    first = _level3(b)[0]
    assert first["topHL"] == {"board": "Mag7", "n": 3, "rank": 1, "heldSeconds": 0, "hl": -0.9}
    for m in range(37, 60, 2):
        scan(_at(9, m), 99.0, _at(9, 25))
    scan(_at(10, 0), 100.5, _at(9, 55))                                # the 21 EMA exit
    for m in range(2, 15, 2):
        scan(_at(10, m), 100.5, _at(9, 55))
    # 10:15: the Level signal drops AND X falls to #4 of the board.
    keep = b._levels.evaluate

    def judged(*a, **k):
        b._levels.contexts = {"X": {"version": 1, "highestLevel": 0}}
        return {}

    b._levels.evaluate = judged
    scan(_at(10, 15), 100.5, _at(10, 10), top=False)
    b._levels.evaluate, b._levels.contexts = keep, {}
    for m in range(16, 30, 2):
        scan(_at(10, m), 100.5, _at(10, 10))
    scan(_at(10, 30), 99.0, _at(10, 25))
    recs = _level3(b)
    assert len(recs) == 2 and recs[1]["levelReentry"] is True
    assert recs[1]["topHL"] == {"board": "Mag7", "n": 3, "rank": 1, "heldSeconds": 14 * 60, "hl": -0.9}
    assert recs[0]["topHL"]["heldSeconds"] == 0                        # the first record is untouched


def test_non_matches_in_rows_never_take_a_top_spot(tmp_path, monkeypatch):
    """rank_board ships every match first, then NON-matches (scanPass False) up to RANK_LIMIT in "rows" -
    Mag7's 12 tickers are always all there. Only the matches are ranked."""
    from momx import scan
    b, clock = _top_book(tmp_path, monkeypatch, syms=("X",))
    board_rows = scan.rank_board([_hl("N1", 1.0, scanPass=False, pctChange=9.0), _hl("A", 0.9, pctChange=1.0),
                                  _hl("N2", 0.99, scanPass=False, pctChange=8.0), _hl("X", 0.5, pctChange=0.5),
                                  _hl("N3", 0.98, scanPass=False, pctChange=7.0), _hl("B", 0.2, pctChange=0.1)])
    assert [r["symbol"] for r in board_rows][:3] == ["A", "X", "B"] and len(board_rows) == 6
    assert ot.hl_ranking(board_rows, bear=False) == [("A", 0.9), ("X", 0.5), ("B", 0.2)]
    for m in range(0, 6):
        _board(b, clock, _at(10, m), board_rows, board="Mag7")
    _fire(b, "X")
    _board(b, clock, _at(10, 6), board_rows, board="Mag7")
    t = next(t for t in b.trades() if t["symbol"] == "X" and t["rule"] == "level3")
    assert t["topHL"]["rank"] == 2 and t["topHL"]["heldSeconds"] == 360
    assert b.top_now("Mag7", "N1", _at(10, 6).timestamp())["rank"] is None


def test_bear_view_rows_rank_on_the_promoted_bear_scan_pass(tmp_path, monkeypatch):
    from momx import board as momx_board
    payload = {"rows": [_hl("A", -0.9, scanPass=True, bear={"scanPass": False}),
                        _hl("P", -0.5, scanPass=False, bear={"scanPass": True}),
                        _hl("Q", -0.2, scanPass=False, bear={"scanPass": True})], "rest": []}
    bear = momx_board.bear_view(payload)
    assert ot.hl_ranking(bear["rows"], bear=True) == [("P", -0.5), ("Q", -0.2)]


def test_realistic_watchlist_scan_spacing_keeps_the_run(tmp_path, monkeypatch):
    """Real Watchlist scans: 141-200 s apart, one busy 400 s gap - the run must survive all of them."""
    b, _ = _top_book(tmp_path, monkeypatch)
    b._load("2026-09-28")
    t0 = _at(10, 0).timestamp()
    ranking = [("X", 0.9)] + [(f"O{i}", 0.1 * i) for i in range(1, 7)]
    ts = t0
    for gap in (0, 150, 200, 141, 400, 180, 160):
        ts += gap
        b._top_update("Watchlist", ranking, ts)
    assert b._top_stamp("Watchlist", "X", ts)["heldSeconds"] == 1231
    assert b.top_now("Watchlist", "X", ts)["heldSeconds"] == 1231


def test_top_now_reads_the_latest_scan_and_is_none_when_unknown(tmp_path, monkeypatch):
    b, clock = _top_book(tmp_path, monkeypatch)
    assert b.started_at == _at(9, 35).timestamp()
    t0 = _at(10, 0).timestamp()
    assert b.top_now("Mag7", "X", t0) is None                       # no scan of the board yet (restart)
    assert b.top_now("21 EMA Touch Monthly", "X", t0) is None       # a board without TOP_N
    rows = [_hl("A", 0.9), _hl("X", 0.8), _hl("B", 0.7), _hl("C", 0.6)]
    _board(b, clock, _at(10, 0), rows, board="Mag7")
    _board(b, clock, _at(10, 3), rows, board="Mag7")
    assert b.top_now("Mag7", "X", _at(10, 4).timestamp()) == {
        "board": "Mag7", "n": 3, "rank": 2, "heldSeconds": 180, "hl": 0.8, "at": _at(10, 3).isoformat()}
    assert b.top_now("Mag7", "C", _at(10, 4).timestamp())["rank"] == 4
    assert b.top_now("Mag7", "C", _at(10, 4).timestamp())["heldSeconds"] == 0
    assert b.top_now("Mag7", "ZZ", _at(10, 4).timestamp())["rank"] is None
    # The next day, before that day's first scan, yesterday's runs are unknown.
    assert b.top_now("Mag7", "X", _at(10, 4).timestamp() + 86400) is None


def test_hl_ranking_sorts_on_the_unrounded_degree_and_ties_on_symbol_like_the_board():
    """Review fix: rank exactly like his board (momxCells.js highLowSortValue + sortRows): the
    unrounded "degree" (value only when degree is missing), ties by symbol ascending both ways."""
    def row(sym, value, degree):
        return {"symbol": sym, "highLow": {"value": value, "degree": degree}, "scanPass": True}

    # Both show 1.0 on the board, but 0.996 < 1.0: the true 1.0 ranks first for calls, last for puts.
    rows = [row("A", 1.0, 0.996), row("B", 1.0, 1.0)]
    assert ot.hl_ranking(rows, bear=False) == [("B", 1.0), ("A", 1.0)]
    assert ot.hl_ranking(rows, bear=True) == [("A", 1.0), ("B", 1.0)]
    # Equal degrees: symbol ascending in BOTH directions, whatever order the rows came in.
    tied = [row("ZM", 0.5, 0.5), row("AMD", 0.5, 0.5), row("MU", 0.9, 0.9)]
    assert ot.hl_ranking(tied, bear=False) == [("MU", 0.9), ("AMD", 0.5), ("ZM", 0.5)]
    assert ot.hl_ranking(tied, bear=True) == [("AMD", 0.5), ("ZM", 0.5), ("MU", 0.9)]
    # No degree: the value is the sort value (older cells, the rounded live value).
    mixed = [row("A", 0.3, None), {"symbol": "B", "highLow": {"value": 0.4}, "scanPass": True}, row("C", 0.35, 0.349)]
    assert [s for s, _ in ot.hl_ranking(mixed, bear=False)] == ["B", "C", "A"]


def test_a_scan_match_with_no_hl_breaks_its_run_but_is_rank_unknown_not_a_fail(tmp_path, monkeypatch):
    """Review fix: columns.null_cell in a candle gap ({'value': None, 'degree': None}) on a top
    ticker: out of the top for that scan (its run breaks), but the paper bot waits (retry), and the
    next good scan restarts the run."""
    from momx.paper_mirror import TOP_FAIL, TOP_OK, TOP_WAIT, top_verdict
    b, clock = _top_book(tmp_path, monkeypatch)
    others = [_hl("A", 0.5), _hl("B", 0.4), _hl("C", 0.3)]
    for m in range(0, 6):
        _board(b, clock, _at(10, m), [_hl("X", 0.9)] + others, board="Mag7")
    assert b.top_now("Mag7", "X", _at(10, 5).timestamp())["heldSeconds"] == 300
    gap = dict(_hl("X", None), highLow={"value": None, "degree": None})
    assert ot.hl_missing([gap] + others) == {"X"} and ot.hl_ranking([gap] + others, bear=False)[0][0] == "A"
    _board(b, clock, _at(10, 6), [gap] + others, board="Mag7")
    now = b.top_now("Mag7", "X", _at(10, 6).timestamp())
    assert now["rank"] is None and now["heldSeconds"] == 0 and now["hlMissing"] is True
    assert b.top_now("Mag7", "A", _at(10, 6).timestamp())["rank"] == 1          # the others still rank
    assert b._top_stamp("Mag7", "X", _at(10, 6).timestamp())["hlMissing"] is True  # a record's stamp too
    sig = {"rule": "level3", "symbol": "X", "board": "Mag7"}
    state, why = top_verdict(sig, now, _at(10, 6))
    assert state == TOP_WAIT and "no H/L on the last scan" in why                # retried, never final
    # A symbol that is not a scan match at all is still a fail.
    assert top_verdict(dict(sig, symbol="ZZ"), b.top_now("Mag7", "ZZ", _at(10, 6).timestamp()),
                       _at(10, 6))[0] == TOP_FAIL
    # The next good scan restarts the run (a new 5-min hold from there).
    _board(b, clock, _at(10, 7), [_hl("X", 0.9)] + others, board="Mag7")
    back = b.top_now("Mag7", "X", _at(10, 7).timestamp())
    assert back["rank"] == 1 and back["heldSeconds"] == 0 and "hlMissing" not in back
    for m in range(8, 13):
        _board(b, clock, _at(10, m), [_hl("X", 0.9)] + others, board="Mag7")
    assert top_verdict(sig, b.top_now("Mag7", "X", _at(10, 12).timestamp()), _at(10, 12))[0] == TOP_OK
    assert "hlMissing" not in b._top_stamp("Mag7", "X", _at(10, 12).timestamp())
