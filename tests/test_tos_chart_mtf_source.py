"""The TOS MTF label engine must read a five-minute tape (what the trader's
TOS 5m chart reads), not the 30-minute study tape: on 2026-08-24 META's
labels only landed on :00/:30 stamps and the CALL2H TOS printed at 09:10
never appeared."""
from __future__ import annotations

import pandas as pd

import api_server


def _owner():
    return next(
        candidate for candidate in vars(api_server).values()
        if isinstance(candidate, type) and hasattr(candidate, "_tos_chart_mtf_source_frame")
    )


def _frame(start: str, periods: int, freq: str, first_close: float) -> pd.DataFrame:
    stamps = pd.date_range(start, periods=periods, freq=freq, tz="America/New_York")
    return pd.DataFrame({
        "timestamp": stamps,
        "open": first_close,
        "high": first_close,
        "low": first_close,
        "close": [first_close + index for index in range(periods)],
        "volume": 1,
    })


def test_minute_tail_is_floored_to_five_minutes_and_wins_over_the_fine_tape() -> None:
    fine = _frame("2026-08-24 09:30", 4, "5min", 100.0)          # 09:30..09:45
    minute = _frame("2026-08-24 09:41", 7, "1min", 500.0)        # 09:41..09:47
    source = _owner()._tos_chart_mtf_source_frame(fine, minute, pd.DataFrame())

    stamps = [ts.tz_convert("America/New_York").strftime("%H:%M") for ts in source["timestamp"]]
    assert stamps == ["09:30", "09:35", "09:40", "09:45"]
    closes = dict(zip(stamps, source["close"]))
    assert closes["09:30"] == 100.0 and closes["09:35"] == 101.0      # fine tape only
    assert closes["09:40"] == 503.0                                   # minute 09:44 close wins
    assert closes["09:45"] == 506.0                                   # forming bucket: last minute so far


def test_falls_back_to_the_study_tape_when_no_five_minute_data_exists() -> None:
    study = _frame("2026-08-24 09:30", 3, "30min", 10.0)
    source = _owner()._tos_chart_mtf_source_frame(pd.DataFrame(), None, study)
    assert source is study


def test_naive_timestamps_are_read_as_eastern() -> None:
    fine = _frame("2026-08-24 09:30", 2, "5min", 1.0)
    fine["timestamp"] = fine["timestamp"].dt.tz_localize(None)
    source = _owner()._tos_chart_mtf_source_frame(fine, None, pd.DataFrame())
    assert source["timestamp"].iloc[0].tz_convert("America/New_York").strftime("%H:%M") == "09:30"
