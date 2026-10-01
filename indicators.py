from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False).mean()


def atr(frame: pd.DataFrame, length: int = 14) -> pd.Series:
    high_low = frame["high"] - frame["low"]
    high_close = (frame["high"] - frame["close"].shift(1)).abs()
    low_close = (frame["low"] - frame["close"].shift(1)).abs()
    true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    return true_range.rolling(length).mean()


def cumulative_vwap(frame: pd.DataFrame) -> pd.Series:
    typical_price = (frame["high"] + frame["low"] + frame["close"]) / 3.0
    cumulative_value = (typical_price * frame["volume"]).cumsum()
    cumulative_volume = frame["volume"].replace(0, np.nan).cumsum()
    return cumulative_value / cumulative_volume


def volume_acceleration(frame: pd.DataFrame, bars: int = 3) -> bool:
    if len(frame) < bars:
        return False
    recent = frame["volume"].tail(bars).tolist()
    return all(left < right for left, right in zip(recent, recent[1:]))


def relative_volume(today_volume: float, avg_daily_volume: float, elapsed_fraction: float) -> float:
    baseline = max(avg_daily_volume * max(elapsed_fraction, 0.05), 1.0)
    return today_volume / baseline


def session_progress(last_timestamp: pd.Timestamp) -> float:
    minutes_since_open = (last_timestamp.hour * 60 + last_timestamp.minute) - (9 * 60 + 30)
    bounded_minutes = min(max(minutes_since_open, 0), 390)
    return bounded_minutes / 390 if bounded_minutes else 0.0


def intraday_vwap_position(frame: pd.DataFrame) -> pd.Series:
    vwap = cumulative_vwap(frame)
    return frame["close"] - vwap


def regular_session(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[
        (
            (frame["timestamp"].dt.hour > 9)
            | ((frame["timestamp"].dt.hour == 9) & (frame["timestamp"].dt.minute >= 30))
        )
        & (
            (frame["timestamp"].dt.hour < 16)
            | ((frame["timestamp"].dt.hour == 16) & (frame["timestamp"].dt.minute == 0))
        )
    ].copy()
