"""momx/option_track.py - real option prices for the scanner's rules (2026-09-27)."""
from __future__ import annotations

import threading

import json
from datetime import datetime
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
def test_every_virtual_book_requires_ai_before_recording_an_entry(tmp_path, direction, fast):
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
    assert all(t["status"] == "pending" and "entry" not in t and t["aiCandidate"]["entry"] > 0 for t in b.trades())
    verdict.update(approved=True, provider="openclaw", reason="consistent entry", probability=.7)
    clock.t += 30
    price()
    assert all(t["status"] == "open" and t["entry"] == pick["entry"] and "aiCandidate" not in t for t in b.trades())
    assert all(t["entryAt"] == datetime.fromtimestamp(clock.t, ET).isoformat() for t in b.trades())


def test_virtual_ai_skip_and_expired_signal_never_get_an_entry(tmp_path):
    b, _ = book(tmp_path, {"X": chain()})
    b._ai_policy = lambda *args: {"approved": False, "enabled": True, "provider": "ollama", "reason": "weak setup"}
    t = {"rule": "news", "symbol": "X", "at": MON.isoformat(), "status": "pending"}
    assert not b._approve_entry(t, ot.pick_contract(chain(), False), MON.timestamp())
    assert t["status"] == "ai_skipped" and "entry" not in t and "aiCandidate" not in t
    b._ai_policy = lambda *args: pytest.fail("expired signals must not request AI")
    assert not b._approve_entry(t, {}, MON.timestamp() + 601)
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
    assert ot.exit_signal({"m5": m5_bar(98.0, bar_after)}, entry, False) == "below VWAP"
    assert ot.exit_signal({"m5": m5_bar(99.2, bar_after)}, entry, False) == "below EMA21"
    assert ot.exit_signal({"m5": m5_bar(100.0, bar_after)}, entry, False) is None
    assert ot.exit_signal({"m5": m5_bar(98.0, bar_before)}, entry, False) is None
    assert ot.exit_signal({"m5": m5_bar(101.0, bar_after, vwap=100.5, e21=99.0)}, entry, True) == "above VWAP"


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
    # a 5m candle closes below VWAP 20 minutes later -> exit priced right away
    clock.t = MON.timestamp() + 1200
    later = datetime.fromtimestamp(clock.t, ET)
    row2 = go_row(m5={"gapGo": {"goAt": GO_AT}, **m5_bar(98.0, clock.t - 300)})
    b.apply("Watchlist", {"rows": [row2]}, later)
    t = next(x for x in b.trades() if x["rule"] == "go")
    assert t["exitReason"] == "below VWAP" and t["exitPrice"] == 1.5 and t["zStatus"] == "closed"
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
