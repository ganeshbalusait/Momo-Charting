from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from chart_backfill import merge_backfill, timesales_to_frame, today_premarket_hole

EASTERN = ZoneInfo("America/New_York")


def _stamp(hour, minute):
    return datetime(2026, 8, 21, hour, minute, tzinfo=EASTERN)


def _frame(times):
    return pd.DataFrame(
        {
            "timestamp": list(times),
            "open": [100.0] * len(times),
            "high": [101.0] * len(times),
            "low": [99.0] * len(times),
            "close": [100.5] * len(times),
            "volume": [1000] * len(times),
        }
    )


def test_detects_the_schwab_current_day_hole():
    """Schwab's price history starts the CURRENT day at 07:00 ET (verified
    live 2026-08-21: an explicit 04:00-07:00 request returned bars from
    07:00). The 04:00-07:00 candles exist only at Tradier."""
    frame = _frame([_stamp(7, 0), _stamp(7, 1)])
    hole = today_premarket_hole(frame, _stamp(8, 30))
    assert hole is not None
    start, end = hole
    assert start == _stamp(4, 0)
    assert end == _stamp(7, 0)


def test_no_hole_once_early_premarket_is_present():
    frame = _frame([_stamp(4, 1), _stamp(6, 30), _stamp(7, 0)])
    assert today_premarket_hole(frame, _stamp(8, 30)) is None


def test_no_hole_check_outside_market_days_or_hours():
    frame = _frame([_stamp(7, 0)])
    saturday = datetime(2026, 8, 22, 8, 0, tzinfo=EASTERN)
    assert today_premarket_hole(frame, saturday) is None
    small_hours = _stamp(3, 0)
    assert today_premarket_hole(frame, small_hours) is None


def test_hole_before_seven_ends_at_now():
    """At 05:30 there are no Schwab today-bars at all; the hole runs to now."""
    frame = _frame([datetime(2026, 8, 20, 19, 55, tzinfo=EASTERN)])
    hole = today_premarket_hole(frame, _stamp(5, 30))
    assert hole is not None
    start, end = hole
    assert start == _stamp(4, 0)
    assert end == _stamp(5, 30)


def test_empty_frame_still_gets_the_backfill_window():
    """A failed Schwab fetch should not also lose the Tradier premarket."""
    hole = today_premarket_hole(pd.DataFrame(), _stamp(8, 0))
    assert hole is not None
    assert hole[0] == _stamp(4, 0)


def test_timesales_normalizes_to_the_chart_frame_shape():
    raw = [
        {"time": "2026-08-21T04:00:00", "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10},
        {"time": "2026-08-21T04:01:00", "open": 1.5, "high": 2.5, "low": 1.0, "close": 2.0, "volume": 20},
        {"time": "bad"},
    ]
    frame = timesales_to_frame(raw)
    assert list(frame.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert len(frame) == 2
    assert frame.iloc[0]["timestamp"] == _stamp(4, 0)
    assert str(frame.iloc[0]["timestamp"].tzinfo)  # tz-aware


def test_merge_keeps_schwab_rows_on_timestamp_collision():
    schwab = _frame([_stamp(7, 0)])
    fill = timesales_to_frame([
        {"time": "2026-08-21T06:59:00", "open": 9.0, "high": 9.0, "low": 9.0, "close": 9.0, "volume": 1},
        {"time": "2026-08-21T07:00:00", "open": 5.0, "high": 5.0, "low": 5.0, "close": 5.0, "volume": 1},
    ])
    merged = merge_backfill(schwab, fill)
    assert len(merged) == 2
    seven = merged[merged["timestamp"] == _stamp(7, 0)].iloc[0]
    assert seven["open"] == 100.0  # the Schwab row survived
    assert merged.iloc[0]["timestamp"] == _stamp(6, 59)


def test_merge_with_empty_backfill_is_identity():
    schwab = _frame([_stamp(7, 0)])
    merged = merge_backfill(schwab, pd.DataFrame())
    assert len(merged) == 1


def test_hole_is_still_patched_in_the_evening() -> None:
    """A restart after 20:00 rebuilds the tapes from Schwab (day starts
    07:00); the 04:00-07:00 hole must still be filled so the evening chart
    and the TOS MTF labels keep the premarket candles."""
    frame = pd.DataFrame({"timestamp": [_stamp(7, 0), _stamp(19, 55)], "close": [1.0, 2.0]})
    hole = today_premarket_hole(frame, _stamp(20, 40))
    assert hole is not None
    assert hole[0].hour == 4 and hole[1].hour == 7
