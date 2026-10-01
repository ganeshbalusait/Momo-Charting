"""board._frame_to_bars_fast must equal the per-row loop exactly (speed pass 2026-09-25)."""
from __future__ import annotations

import pandas as pd

from momx import board, columns


def slow(frame):
    bars = []
    for row in columns._rows(frame):
        value = row.get("time")
        if value is None:
            value = row.get("timestamp")
        seconds = board._epoch_seconds(value)
        if seconds is None or seconds <= 0:
            continue
        bar = dict(row)
        bar["time"] = seconds
        bars.append(bar)
    return bars


def test_fast_path_equals_the_loop_on_a_tz_aware_frame_with_a_nat():
    frame = pd.DataFrame({
        "timestamp": pd.to_datetime(["2026-09-25 09:30", None, "2026-09-25 09:35"]).tz_localize("America/New_York"),
        "open": [1.0, 2.0, 3.0], "high": [1.5, 2.5, 3.5], "low": [0.5, 1.5, 2.5], "close": [1.2, 2.2, 3.2],
        "volume": [10, 20, 30],
    })
    fast = board._frame_to_bars_fast(frame)
    assert fast is not None and fast == slow(frame) and len(fast) == 2
    assert board.frame_to_bars(frame) == slow(frame)


def test_fast_path_declines_other_shapes():
    assert board._frame_to_bars_fast([{"time": 1}]) is None
    assert board._frame_to_bars_fast(pd.DataFrame({"time": [1], "close": [1.0]})) is None
