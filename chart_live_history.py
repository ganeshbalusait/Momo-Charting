"""Join recorded broker CHART minutes to cached candles without broker I/O.

Only the stream's OHLCV history is accepted here, never a last-price quote.
Missing/no-trade intervals stay empty. REST owns existing closed minutes;
recorded minutes fill holes and extend the tail while study jobs catch up.
"""
from __future__ import annotations

import math
import time


def with_recorded_chart_history(payload: dict, recorded, now_epoch=None) -> dict:
    now = time.time() if now_epoch is None else float(now_epoch)
    existing = payload.get("bars") or []
    # Bound this request work to the recent two-day bridge, not the stream's
    # entire retained archive. This performs no file read or provider request.
    floor = now - 2 * 86400
    candidates = []
    for raw in recorded or []:
        try:
            stamp = int(raw["time"])
            values = {key: float(raw[key]) for key in ("open", "high", "low", "close", "volume")}
        except (KeyError, TypeError, ValueError, OverflowError):
            continue
        if (stamp < floor or stamp > now + 5 or stamp % 60
                or not all(math.isfinite(value) for value in values.values())
                or min(values[key] for key in ("open", "high", "low", "close")) <= 0
                or values["volume"] < 0
                or values["high"] < max(values["open"], values["close"])
                or values["low"] > min(values["open"], values["close"])):
            continue
        candidates.append({"time": stamp, **values})
    if not candidates:
        return payload
    merged = {int(bar["time"]): bar for bar in existing}
    changed = False
    for incoming in candidates:
        stamp = incoming["time"]
        held = merged.get(stamp)
        if held is None:
            merged[stamp] = incoming
            changed = True
        elif stamp + 60 > now:
            # Only the forming minute can restate. Keep its known opening
            # price and cumulative extrema/volume; a closed REST bar wins.
            updated = {**held, "high": max(held["high"], incoming["high"]),
                       "low": min(held["low"], incoming["low"]),
                       "close": incoming["close"],
                       "volume": max(held.get("volume", 0), incoming["volume"])}
            if updated != held:
                merged[stamp] = updated
                changed = True
    if not changed:
        return payload
    bars = [merged[key] for key in sorted(merged)]
    return {**payload, "bars": bars, "recordedHistoryApplied": True,
            "historyBarsThrough": bars[-1]["time"]}
