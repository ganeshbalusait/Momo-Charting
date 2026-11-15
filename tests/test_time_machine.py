"""Unit (2026-11-15, feature chart-time-machine): the Time Machine odds engine
(time_machine.py) on synthetic five-minute sessions, no network."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import time_machine as tm
from time_machine import EASTERN, run_time_machine


def sessions(count=30, drift=0.0, wiggle=0.002, end=date(2026, 11, 13), seed=7, wide=False):
    """`count` weekday sessions of 77 five-minute bars ending the day before `end`'s next."""
    rng = np.random.default_rng(seed)
    rows, day, made, price = [], end, 0, 100.0
    while made < count:
        if day.weekday() < 5:
            start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=EASTERN)
            for slot in range(77):
                o = price
                c = o * float(np.exp(drift + rng.normal(0, wiggle)))
                h, l = max(o, c) * (1.002 if not wide else 1.2), min(o, c) * (0.998 if not wide else 0.8)
                rows.append({"timestamp": start + timedelta(minutes=5 * slot), "open": o, "high": h, "low": l, "close": c})
                price = c
            made += 1
            price = 100.0
        day -= timedelta(days=1)
    return pd.DataFrame(rows)


MONDAY_1015 = datetime(2026, 11, 16, 10, 15, tzinfo=EASTERN)


def test_odds_add_up_and_the_answer_repeats():
    bars = sessions()
    first = run_time_machine(bars, side="CALL", spot=100, target=100.8, stop=99.4, now=MONDAY_1015, symbol="NVDA", paths=4000)
    again = run_time_machine(bars, side="CALL", spot=100, target=100.8, stop=99.4, now=MONDAY_1015, symbol="NVDA", paths=4000)
    assert first["ok"] is True
    odds = first["odds"]
    assert abs(odds["target"] + odds["stop"] + odds["neither"] - 100) < 0.2
    assert first == again  # seeded per symbol / day / time
    assert first["mode"] == "today" and first["startLabel"] == "10:15" and first["steps"] == 77 - 9
    assert first["sessions"] == 30 and len(first["history"]) == 30
    assert sum(first["historyCounts"].values()) == 30
    assert set(first["bands"]) == {"10", "25", "50", "75", "90"} and len(first["bands"]["50"]) == first["steps"]
    assert 0 < len(first["sample"]) <= tm.FAN_PATHS


def test_a_rising_tape_favours_the_call_target_and_the_put_stop():
    bars = sessions(drift=0.0015)
    call = run_time_machine(bars, side="CALL", spot=100, target=101, stop=99, now=MONDAY_1015, paths=3000)
    put = run_time_machine(bars, side="PUT", spot=100, target=99, stop=101, now=MONDAY_1015, paths=3000)
    assert call["odds"]["target"] > 80 and call["historyCounts"]["target"] > 25
    assert put["odds"]["stop"] > 80
    assert call["verdict"] == "Clear edge to the target"
    assert put["verdict"] == "The stop is clearly more likely"


def test_a_candle_that_touches_both_counts_as_the_stop():
    bars = sessions(wide=True)  # every candle spans +/-20%
    result = run_time_machine(bars, side="CALL", spot=100, target=101, stop=99, now=MONDAY_1015, paths=500)
    assert result["odds"] == {"target": 0.0, "stop": 100.0, "neither": 0.0}
    assert result["historyCounts"] == {"target": 0, "stop": 30, "neither": 0}


def test_outside_the_session_it_replays_a_whole_day_from_the_open():
    bars = sessions()
    sunday = datetime(2026, 11, 15, 14, 0, tzinfo=EASTERN)
    late = datetime(2026, 11, 16, 15, 52, tzinfo=EASTERN)
    for now in (sunday, late):
        result = run_time_machine(bars, side="CALL", spot=100, target=101, stop=99, now=now, paths=500)
        assert result["mode"] == "next-session" and result["startLabel"] == "9:30" and result["steps"] == 77


def test_bad_plans_and_thin_data_say_why():
    bars = sessions()
    wrong = run_time_machine(bars, side="CALL", spot=100, target=99, stop=98, now=MONDAY_1015)
    assert wrong == {"ok": False, "reason": "The target and the stop must sit on either side of the price."}
    assert run_time_machine(bars, side="PUT", spot=100, target=0, stop=101, now=MONDAY_1015)["ok"] is False
    thin = run_time_machine(sessions(count=5), side="CALL", spot=100, target=101, stop=99, now=MONDAY_1015)
    assert thin["ok"] is False and "at least 10" in thin["reason"]
    assert run_time_machine(pd.DataFrame(), side="CALL", spot=100, target=101, stop=99, now=MONDAY_1015)["ok"] is False


def test_today_and_half_days_are_not_in_the_history():
    bars = sessions(end=date(2026, 11, 16))  # includes "today" (Monday 11-16)
    short = bars[bars["timestamp"].map(lambda t: t.date()) == date(2026, 11, 13)].head(39)  # a half day
    others = bars[bars["timestamp"].map(lambda t: t.date()) != date(2026, 11, 13)]
    result = run_time_machine(pd.concat([others, short]), side="CALL", spot=100, target=101, stop=99, now=MONDAY_1015, paths=300)
    days = {item["day"] for item in result["history"]}
    assert "2026-11-16" not in days and "2026-11-13" not in days
    assert result["sessions"] == 28
