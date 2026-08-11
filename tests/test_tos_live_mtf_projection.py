from __future__ import annotations

import pandas as pd

from scanner import _tos_mtf_ema_signal_payload


def _flat_then_move_frame(move_time: str, move_close: float) -> pd.DataFrame:
    timestamps = pd.date_range(
        "2026-07-20 04:00",
        "2026-07-22 16:00",
        freq="5min",
        tz="America/New_York",
    )
    closes = [100.0 if timestamp < pd.Timestamp(move_time, tz="America/New_York") else move_close for timestamp in timestamps]
    return pd.DataFrame({"timestamp": timestamps, "close": closes})


def test_tos_projection_backfills_higher_timeframe_call_to_bucket_start() -> None:
    payload = _tos_mtf_ema_signal_payload(_flat_then_move_frame("2026-07-22 10:00", 110.0))

    matching = [
        signal
        for signal in payload["signals"]
        if signal["family"] == "9x20"
        and signal["timeframe"] == "1H"
        and signal["direction"] == "CALL"
    ]

    assert payload["mode"] == "tos_secondary_bucket_projection"
    assert matching
    signal_time = pd.to_datetime(matching[-1]["time"], unit="s", utc=True).tz_convert("America/New_York")
    assert signal_time.strftime("%Y-%m-%d %H:%M") == "2026-07-22 10:00"
    assert matching[-1]["label"] == "CALL1H"
    assert matching[-1]["compact"] is False
    assert matching[-1]["liveForming"] is True
    assert matching[-1]["secondaryBucketStart"] is True


def test_tos_projection_keeps_call30_and_call1h_on_same_5m_candle() -> None:
    payload = _tos_mtf_ema_signal_payload(_flat_then_move_frame("2026-07-22 10:00", 110.0))
    expected_time = int(pd.Timestamp("2026-07-22 10:00", tz="America/New_York").timestamp())

    simultaneous_labels = {
        signal["label"]
        for signal in payload["signals"]
        if signal["family"] == "9x20"
        and signal["direction"] == "CALL"
        and signal["time"] == expected_time
    }

    assert {"CALL30", "CALL1H"}.issubset(simultaneous_labels)


def test_tos_projection_detects_cross_from_equal_ema_state() -> None:
    payload = _tos_mtf_ema_signal_payload(_flat_then_move_frame("2026-07-22 13:00", 90.0))

    put_signals = [
        signal
        for signal in payload["signals"]
        if signal["family"] == "9x20"
        and signal["direction"] == "PUT"
        and signal["time"] == int(pd.Timestamp("2026-07-22 13:00", tz="America/New_York").timestamp())
    ]

    assert put_signals
    assert all(signal["liveForming"] is True for signal in put_signals)
    assert all(signal["secondaryBucketStart"] is True for signal in put_signals)
