"""Unit: momx/strategy.py day rollover - Daily 2 / V2 / V3 lists across days and DST.

The Daily 2 list is the day's order of qualifying tickers (#1, #2 ...), saved
per ET day. A Friday latch must not leak into Monday, the 09:35 ET window must
follow the clock change (2026-11-01 ended daylight time), old day files are
pruned after KEEP_DAYS, and a failing option-chain fetch must never cost a scan.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from momx import strategy

ET = ZoneInfo("America/New_York")


def row(symbol, signal: datetime, last=100.0, rvol=("5m", "15m")):
    return {
        "symbol": symbol, "last": last, "pctChange": 1.0,
        "grade": {"letter": "A+", "checks": {"pushRvol": list(rvol), "sqzFired": ["D"]}},
        "m5": {"state": "extended"},
        "gradeFresh": {"firstToday": {"A+": {"at": signal.isoformat(), "price": last}, "A": None, "B": None}},
    }


def book(tmp_path, fetch=lambda symbol: None):
    return strategy.StrategyBook(tmp_path, chain_fetcher=fetch, background=False)


def apply(b, symbol, now):
    payload = {"rows": [row(symbol, now)], "rest": []}
    b.apply("Watchlist", payload, now)
    return payload["rows"][0]["strategy"]


def test_a_friday_close_latch_is_gone_on_monday(tmp_path):
    b = book(tmp_path)
    friday = datetime(2026, 10, 30, 15, 59, tzinfo=ET)
    assert apply(b, "ARM", friday)["daily2"]["rank"] == 1
    monday = datetime(2026, 11, 2, 9, 40, tzinfo=ET)
    assert b.today(monday) == []
    assert apply(b, "STX", monday)["daily2"] == {"rank": 1, "at": "2026-11-02T09:40:00-05:00", "price": 100.0}
    assert [e["symbol"] for e in b.today(monday)] == ["STX"]


def test_the_0935_window_follows_eastern_time_after_the_clock_change(tmp_path):
    b = book(tmp_path)
    # Monday 2026-11-02 is on EST (UTC-5): 14:34Z is 09:34 ET, 14:36Z is 09:36 ET.
    early = datetime(2026, 11, 2, 14, 34, tzinfo=timezone.utc)
    assert apply(b, "EARLY", early) == {"v2": False, "v3": False, "daily2": None, "g": None}
    on_time = datetime(2026, 11, 2, 14, 36, tzinfo=timezone.utc)
    stamp = apply(b, "ONTIME", on_time)
    assert stamp["daily2"] == {"rank": 1, "at": "2026-11-02T09:36:00-05:00", "price": 100.0}
    late_edt_guess = datetime(2026, 11, 2, 21, 1, tzinfo=timezone.utc)   # 16:01 ET: after the close
    assert apply(b, "LATE", late_edt_guess)["v2"] is False


def test_day_files_older_than_keep_days_are_pruned_and_recent_ones_kept(tmp_path):
    folder = tmp_path / strategy.DIRNAME
    folder.mkdir()
    for day in ("2026-09-01", "2026-09-18", "2026-10-01", "not-a-day"):
        (folder / f"{day}.json").write_text(json.dumps({"daily2": []}), encoding="utf-8")
    b = book(tmp_path)
    apply(b, "ARM", datetime(2026, 11, 2, 9, 40, tzinfo=ET))       # a latch writes and prunes
    kept = sorted(p.stem for p in folder.glob("*.json"))
    assert kept == ["2026-09-18", "2026-10-01", "2026-11-02", "not-a-day"]   # 09-18 is exactly 45 days back
    assert strategy.KEEP_DAYS == 45


def test_a_failing_option_chain_fetch_never_costs_the_scan(tmp_path):
    def broken(symbol):
        raise ConnectionError("chain route down")

    b = book(tmp_path, fetch=broken)
    now = datetime(2026, 10, 28, 10, 0, tzinfo=ET)
    payload = {"rows": [row("NVDA", now), row("AMD", now)], "rest": []}
    b.apply("Mag7", payload, now)
    assert [r["strategy"]["daily2"]["rank"] for r in payload["rows"]] == [1, 2]
    assert all(r["strategy"]["g"] is None for r in payload["rows"])
    assert payload["strategyG"] == []
