"""momx/strategy_g.py - Strategy G (Ganesh's own entry method), pure rules."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from momx import strategy_g as g

ET = ZoneInfo("America/New_York")


def at(hhmm: str, day: str = "2026-09-23") -> datetime:
    h, m = hhmm.split(":")
    return datetime.fromisoformat(f"{day}T{int(h):02d}:{int(m):02d}:00").replace(tzinfo=ET)


def ep(hhmm: str, day: str = "2026-09-23") -> int:
    return int(at(hhmm, day).timestamp())


def row(**over):
    base = {
        "symbol": "PLTR",
        "rvol": {"30m": {"value": 3.4, "bg": "cyan"}, "1h": {"value": 1.2, "bg": "black"}},
        "sqz": {"2h": {"bg": "black"}, "4h": {"bg": "cyan"}},
        "skittles": {
            "2h": {"bg": "cyan", "barAt": ep("09:00")},
            "4h": {"bg": "cyan", "barAt": ep("08:00")},
            "D": {"bg": "black", "barAt": ep("00:00")},
        },
        "m5": {"ema20": 189.0, "vwap": 188.5, "crossUpAt": None,
               "lastCompleted": {"close": 191.0, "time": ep("09:50")}},
    }
    base.update(over)
    return base


# ------------------------------------------------------------- rules 1-4

def test_all_four_rules_pass():
    v = g.rules(row(), at("09:55"), prev_rvol={"30m": 3.1})
    assert v["pass"] is True, v


def test_rule1_needs_cyan_rvol_on_30m_or_higher_and_rising():
    assert g.rules(row(), at("09:55"), prev_rvol={"30m": 3.4})["r1"] is False, "not rising"
    assert g.rules(row(), at("09:55"), prev_rvol={})["r1"] is False, "no previous reading yet"
    low = row(rvol={"5m": {"value": 7.0, "bg": "cyan"}, "15m": {"value": 6.0, "bg": "cyan"}})
    assert g.rules(low, at("09:55"), prev_rvol={"5m": 1.0, "15m": 1.0})["r1"] is False, "5m/15m do not count"


def test_rule2_squeeze_2h_4h_fired_or_none_never_coiling():
    assert g.rules(row(sqz={"2h": {"bg": "orange"}, "4h": {"bg": "cyan"}}), at("09:55"), {"30m": 3})["r2"] is False
    assert g.rules(row(sqz={"2h": {"bg": "white"}}), at("09:55"), {"30m": 3})["r2"] is False
    assert g.rules(row(sqz={}), at("09:55"), {"30m": 3})["r2"] is True, "no squeeze at all is fine"


def test_rule3_fresh_2h_cross_today_after_8am_and_a_fresh_4h_or_d_cross():
    stale2h = row(skittles={"2h": {"bg": "cyan", "barAt": ep("07:00")}, "4h": {"bg": "cyan", "barAt": ep("08:00")}})
    assert g.rules(stale2h, at("09:55"), {"30m": 3})["r3"] is False
    yday = row(skittles={"2h": {"bg": "cyan", "barAt": ep("10:00", "2026-09-22")}, "4h": {"bg": "cyan", "barAt": ep("08:00")}})
    assert g.rules(yday, at("09:55"), {"30m": 3})["r3"] is False
    no_higher = row(skittles={"2h": {"bg": "cyan", "barAt": ep("09:00")}, "4h": {"bg": "black"}, "D": {"bg": "green"}})
    assert g.rules(no_higher, at("09:55"), {"30m": 3})["r3"] is False, "needs a fresh 4h or D cyan cross"
    old_d = row(skittles={"2h": {"bg": "cyan", "barAt": ep("09:00")}, "4h": {"bg": "cyan", "barAt": ep("08:00")},
                          "D": {"bg": "cyan", "barAt": ep("09:30", "2026-09-21")}})
    assert g.rules(old_d, at("09:55"), {"30m": 3})["r3"] is False, "a D cross older than 24h fails"


def test_rule4_above_ema_and_vwap_or_a_recent_cross_up():
    below = row(m5={"ema20": 192.0, "vwap": 188.5, "crossUpAt": None, "lastCompleted": {"close": 191.0}})
    assert g.rules(below, at("09:55"), {"30m": 3})["r4"] is False
    below["m5"]["crossUpAt"] = ep("09:40")
    assert g.rules(below, at("09:55"), {"30m": 3})["r4"] is True, "C5 / CALL5 arrow in the last 30 min"
    below["m5"]["crossUpAt"] = ep("09:00")
    assert g.rules(below, at("09:55"), {"30m": 3})["r4"] is False, "arrow too old"


def test_outside_the_window_nothing_passes():
    assert g.rules(row(), at("09:25"), {"30m": 3})["pass"] is False
    assert g.rules(row(), at("15:31"), {"30m": 3})["pass"] is False


def test_rules_never_raise():
    assert g.rules(None, at("10:00"), {})["pass"] is False
    assert g.rules({"rvol": "x", "m5": 3}, at("10:00"), None)["pass"] is False


# ------------------------------------------------------------- rule 5: the option plan

def c(strike, oi, delta, bid, ask, gamma=0.03, expiry="2026-09-25", dte=2, side="CALL"):
    return {"side": side, "strike": strike, "open_interest": oi, "delta": delta, "gamma": gamma,
            "bid": bid, "ask": ask, "mark": (bid + ask) / 2, "expiry": expiry, "days_to_expiration": dte,
            "symbol": f"PLTR 260925C{int(strike * 1000):08d}"}


# PLTR 2026-09-23 ~10:59 ET, from his screenshot (Sep 25 expiry)
PLTR = {
    "underlyingPrice": 191.07,
    "selectedExpiryChainRows": [
        c(190.0, 9611, 0.60, 4.05, 4.15),
        c(192.5, 4946, 0.47, 2.73, 2.81),
        c(195.0, 13624, 0.35, 1.75, 1.83, gamma=0.045),
        c(197.5, 10384, 0.24, 1.03, 1.09),
        c(200.0, 5867, 0.16, 0.66, 0.70, gamma=0.03),
        c(202.5, 1698, 0.11, 0.40, 0.44),
        c(205.0, 2653, 0.07, 0.24, 0.28),
        c(195.0, 900, 0.10, 0.10, 0.12, expiry="2026-09-23", dte=0),   # today's expiry - ignored
        c(185.0, 1592, -0.18, 0.5, 0.6, side="PUT"),
    ],
}


def test_plan_matches_the_chain_screen():
    p = g.option_plan(PLTR)
    assert p["ok"] is True, p
    assert p["expiry"] == "2026-09-25"
    assert p["target"] == 195.0 and p["targetOi"] == 13624
    assert p["entryStrike"] == 200.0 and abs(p["entryPrice"] - 0.68) < 1e-9
    assert round(p["roi"]) == 163          # (1.79 - 0.68) / 0.68 - the chain's ROI column
    assert 100 < p["gammaRoi"] < 160       # the chain's delta+gamma column (his screen: 130%)
    assert p["pass"] is True


def test_plan_fails_below_150_percent_roi():
    rows = [dict(r) for r in PLTR["selectedExpiryChainRows"]]
    for r in rows:
        if r["side"] == "CALL" and r["strike"] == 195.0 and r["expiry"] == "2026-09-25":
            r["bid"], r["ask"] = 1.30, 1.34
    p = g.option_plan({"underlyingPrice": 191.07, "selectedExpiryChainRows": rows})
    assert p["ok"] is True and p["pass"] is False and round(p["roi"]) < 150


def test_plan_needs_a_strong_call_wall_above():
    rows = [c(192.5, 100, 0.47, 2.7, 2.8), c(200.0, 5000, 0.16, 0.66, 0.70)]
    p = g.option_plan({"underlyingPrice": 191.07, "selectedExpiryChainRows": rows})
    # the 200 wall IS strong (it is the max) but it is the entry strike itself: no target below it
    assert p["pass"] is False


def test_plan_handles_junk():
    assert g.option_plan(None)["ok"] is False
    assert g.option_plan({"underlyingPrice": 0})["ok"] is False
    assert g.option_plan({"underlyingPrice": 10, "selectedExpiryChainRows": [None, 3, {}]})["ok"] is False


# ------------------------------------------------------------- BEAR (spec 2026-09-24)

def bear_row(**over):
    """row() mirrored: magenta paints, price below EMA20 and VWAP."""
    base = {
        "symbol": "PLTR",
        "rvol": {"30m": {"value": 3.4, "bg": "magenta"}, "1h": {"value": 1.2, "bg": "black"}},
        "sqz": {"2h": {"bg": "black"}, "4h": {"bg": "magenta"}},
        "skittles": {
            "2h": {"bg": "magenta", "barAt": ep("09:00")},
            "4h": {"bg": "magenta", "barAt": ep("08:00")},
            "D": {"bg": "black", "barAt": ep("00:00")},
        },
        "m5": {"ema20": 189.0, "vwap": 188.5, "crossUpAt": None, "crossDownAt": None,
               "lastCompleted": {"close": 186.0, "time": ep("09:50")}},
    }
    base.update(over)
    return base


def test_bear_rules_pass_on_the_mirrored_row_and_fail_the_bull_reading():
    v = g.rules(bear_row(), at("09:55"), {"30m": 3.0}, direction="bear")
    assert v["pass"] and v["rising"] == ["30m"] and v["aboveEmaVwap"] is True
    assert not g.rules(bear_row(), at("09:55"), {"30m": 3.0})["pass"]
    assert not g.rules(row(), at("09:55"), {"30m": 3.0}, direction="bear")["pass"]


def test_bear_rule_4_accepts_a_9x20_cross_down_in_30_minutes():
    r = bear_row(m5={"ema20": 180.0, "vwap": 180.0, "crossUpAt": None, "crossDownAt": ep("09:40"),
                     "lastCompleted": {"close": 190.0, "time": ep("09:50")}})
    v = g.rules(r, at("09:55"), {"30m": 3.0}, direction="bear")
    assert v["r4"] and v["arrow"] is True
    assert "cross down" in " ".join(g.rules(bear_row(m5={}), at("09:55"), {"30m": 3.0}, direction="bear")["why"])


def p(strike, oi, delta, bid, ask, gamma=0.03):
    return dict(c(strike, oi, -delta, bid, ask, gamma=gamma, side="PUT"),
                symbol=f"PLTR 260925P{int(strike * 1000):08d}")


PLTR_PUTS = {
    "underlyingPrice": 191.07,
    "selectedExpiryChainRows": [
        c(195.0, 13624, 0.35, 1.75, 1.83),                     # a call: ignored on the bear side
        p(190.0, 4000, 0.45, 2.60, 2.70),
        p(187.5, 15000, 0.30, 2.60, 2.70),                     # the strong put wall between spot and ENTRY
        p(185.0, 20000, 0.18, 1.00, 1.10),                     # ENTRY: |delta| closest under 0.20
        p(180.0, 9000, 0.09, 0.40, 0.46),
    ],
}


def test_bear_option_plan_picks_a_put_below_spot_and_the_wall_between():
    plan = g.option_plan(PLTR_PUTS, direction="bear")
    assert plan["ok"] and plan["side"] == "P" and plan["expiry"] == "2026-09-25"
    assert plan["entryStrike"] == 185.0 and plan["entrySymbol"].startswith("PLTR 260925P")
    assert plan["target"] == 187.5 and plan["targetOi"] == 15000
    assert plan["roi"] == round((2.65 - 1.05) / 1.05 * 100, 1) and plan["pass"] is True
    assert plan["gammaRoi"] > 0


def test_bear_option_plan_needs_a_strong_put_wall_between_spot_and_entry():
    weak = dict(PLTR_PUTS, selectedExpiryChainRows=[
        r if r["strike"] != 187.5 else dict(r, open_interest=500) for r in PLTR_PUTS["selectedExpiryChainRows"]])
    plan = g.option_plan(weak, direction="bear")
    assert plan["ok"] and plan["pass"] is False and "put wall" in plan["why"]
    assert g.option_plan(PLTR_PUTS)["ok"] and g.option_plan(PLTR_PUTS)["pass"] is False   # bull reading: no wall above
