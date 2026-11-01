"""2026-10-03: dailyBars repeated a trading day 2-3 times (Schwab 01:00 ET
daily candles merged by timestamp with candles built from intraday tapes).
CRDO's daily squeeze read "mid" on the shortened window; one candle per day
gives "none", which is what TOS showed. Mirrors frontend/src/dailyBars.js."""
from datetime import datetime
from zoneinfo import ZoneInfo

import api_server

ET = ZoneInfo("America/New_York")


def et(*args) -> int:
    return int(datetime(*args, tzinfo=ET).timestamp())


def test_trading_day_is_the_own_et_date_and_drops_weekend_evening_stubs() -> None:
    day = api_server._daily_bar_trading_day
    assert day(et(2026, 10, 2, 1, 0)) == "2026-10-02"
    assert day(et(2026, 9, 23, 20, 15)) == "2026-09-23"
    assert day(et(2026, 10, 2, 20, 0)) == "2026-10-02"   # Friday full candle stamped 20:00 (SPY)
    assert day(et(2026, 9, 27, 20, 0)) is None            # Sunday evening = overnight stub


def test_one_candle_per_day_fullest_copy_wins() -> None:
    rows = [
        {"time": et(2026, 10, 1, 0, 0), "date": "2026-10-01", "close": 209.65, "volume": 7217937},
        {"time": et(2026, 10, 1, 0, 7), "date": "2026-10-01", "close": 210.0, "volume": 6900000},
        {"time": et(2026, 10, 2, 0, 0), "date": "2026-10-02", "close": 218.64, "volume": 7537915},
        {"time": et(2026, 10, 2, 0, 6), "date": "2026-10-02", "close": 218.6028, "volume": 7532828},
        {"time": et(2026, 9, 27, 20, 0), "date": "2026-09-27", "close": 203.5, "volume": 9},
        {"time": et(2026, 10, 1, 20, 0), "date": "2026-10-01", "close": 999.0, "volume": 33943},
    ]
    out = api_server._dedupe_daily_bars(rows)
    assert [(b["date"], b["close"]) for b in out] == [("2026-10-01", 209.65), ("2026-10-02", 218.64)]
