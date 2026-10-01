"""momx/option_track.py - real option prices for the scanner's rules (2026-09-27)."""
from __future__ import annotations

import threading

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from momx import option_track as ot

ET = ZoneInfo("America/New_York")
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


def book(tmp_path, answers, direction="bull", clock=None):
    calls = []

    def fetch(sym):
        calls.append(sym)
        a = answers[sym]
        return a.pop(0) if isinstance(a, list) else a

    b = ot.OptionTrackBook(tmp_path, direction=direction, fetch=fetch, healthy=lambda: True, background=False,
                           clock=clock or Clock(MON.timestamp()), sleep=lambda s: None)
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


def test_sqz_fire_counts_daily_and_weekly():
    at = lambda hm: f"2026-09-28T{hm}:00-04:00"
    plain = {"symbol": "X", "last": 100.0, "grade": {"letter": "B"}, "adx": {}, "rvol": {}, "m5": {}}
    for tf in ("D", "Wk"):
        row = {**plain, "gradeFresh": {"timeline": [{"at": at("10:05"), "what": f"SQZ {tf} released"}]}}
        assert "sqz" in ot.fired(row, False, MON), tf
