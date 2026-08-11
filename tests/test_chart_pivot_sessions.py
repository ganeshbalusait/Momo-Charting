from __future__ import annotations

import pandas as pd

from api_server import _regular_session_daily_ohlc


def _bar(timestamp: str, open_: float, high: float, low: float, close: float, volume: int = 100) -> dict:
    return {
        "timestamp": pd.Timestamp(timestamp, tz="America/New_York"),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def test_pivot_daily_ohlc_uses_regular_session_and_ignores_overnight_bars() -> None:
    frame = pd.DataFrame([
        _bar("2026-07-26 20:00", 280, 285, 278, 282),
        _bar("2026-07-27 09:25", 270, 275, 269, 274),
        _bar("2026-07-27 09:30", 264, 266, 263, 265),
        _bar("2026-07-27 12:00", 265, 272.52, 262, 270),
        _bar("2026-07-27 16:00", 270, 271, 259.10, 266.75),
        _bar("2026-07-27 16:05", 267, 290, 250, 255),
    ])

    result = _regular_session_daily_ohlc(frame)

    assert len(result.index) == 1
    row = result.iloc[0]
    assert str(row["_date"]) == "2026-07-27"
    assert float(row["open"]) == 264
    assert float(row["high"]) == 272.52
    assert float(row["low"]) == 259.10
    assert float(row["close"]) == 266.75
    assert int(row["volume"]) == 300
