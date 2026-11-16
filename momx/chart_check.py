"""The paper bots' chart check (his rule 2026-11-10, "Yes change it rule or confirm").

His manual step 3, before he buys: on the 5m chart the price is on the right side
of the 21 EMA, and there is room before the next level in the way (a pivot, the
previous day's high/low, the premarket high/low). Pure functions over the frozen
completed-5m chart snapshot that momx/chart_learning.snapshot() returns:
{referencePrice, levels: {ema21, P, R1, R2, S1, S2, prevHigh, prevLow,
premarketHigh, premarketLow, ...}}.

This is a rules check, not AI. Missing chart data never blocks a buy: the signal
passes and is marked "not checked" so the Scorecard can say so.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

#: The nearest level ahead of the price must be at least this far away, in % of
#: the price (his "yes" to 0.5%, 2026-11-10). Closer than that = no room to run.
ROOM_PCT = 0.5
#: The levels that can stand in the way. The EMAs and VWAP are not "room" levels:
#: the 21 EMA is its own test and the others move with the price.
ROOM_LEVELS = ("P", "R1", "R2", "S1", "S2", "prevHigh", "prevLow", "premarketHigh", "premarketLow")
LABELS = {"P": "Pivot", "R1": "R1", "R2": "R2", "S1": "S1", "S2": "S2",
          "prevHigh": "Previous-day high", "prevLow": "Previous-day low",
          "premarketHigh": "Premarket high", "premarketLow": "Premarket low"}

PASSED, NOT_CHECKED, FAILED = "passed", "not checked", "failed"


def _price(v: Any) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    f = float(v)
    return f if math.isfinite(f) and f > 0 else None


def _parts(chart: Any) -> tuple[float, float, Mapping] | None:
    """(price, ema21, levels) when the chart can be checked at all, else None."""
    if not isinstance(chart, Mapping) or not isinstance(chart.get("levels"), Mapping):
        return None
    price, ema21 = _price(chart.get("referencePrice")), _price(chart["levels"].get("ema21"))
    if price is None or ema21 is None:
        return None
    return price, ema21, chart["levels"]


def checkable(chart: Any) -> bool:
    """False when the chart is missing or has no price / 21 EMA: the check cannot
    run, the signal passes, and the row says "chart not checked"."""
    return _parts(chart) is not None


def check(side: str, chart: Any) -> str | None:
    """None = the chart check passes (or could not run - see checkable()).
    Otherwise a short plain reason, e.g. "price under the 21 EMA" or
    "R1 $182.27 is only 0.3% ahead"."""
    parts = _parts(chart)
    if parts is None:
        return None                       # never block on missing data
    price, ema21, levels = parts
    bear = side == "bear"
    # Calls want the price above the 21 EMA, puts below it.
    if bear and price >= ema21:
        return "price above the 21 EMA" if price > ema21 else "price on the 21 EMA"
    if not bear and price <= ema21:
        return "price under the 21 EMA" if price < ema21 else "price on the 21 EMA"
    # Room: the nearest level ahead (above for calls, below for puts). A level
    # exactly AT the price is ahead with 0% room: the price is sitting on it.
    nearest = None
    for key in ROOM_LEVELS:
        level = _price(levels.get(key))
        if level is None or (level > price if bear else level < price):
            continue
        room = (price - level) / price * 100 if bear else (level - price) / price * 100
        if nearest is None or room < nearest[0]:
            nearest = (room, key, level)
    if nearest is not None and nearest[0] < ROOM_PCT:
        room, key, level = nearest
        return f"{LABELS[key]} ${level:.2f} is only {_shown(room)}% ahead"
    return None


def _shown(room: float) -> str:
    """The room, TRUNCATED (never rounded up), so a fail can never read as the 0.5%
    minimum: 0.46 -> "0.46", 0.499 -> "0.49", 0.3 -> "0.3", 0 -> "0.0"."""
    hundredths = math.floor(max(room, 0.0) * 100 + 1e-6)   # the 1e-6 keeps 0.3 (0.2999...) at 0.30
    return f"{hundredths / 100:.1f}" if hundredths % 10 == 0 else f"{hundredths / 100:.2f}"


def verdict(side: str, chart: Any) -> tuple[str, str | None]:
    """("passed", None), ("not checked", None) or ("failed", reason)."""
    if not checkable(chart):
        return NOT_CHECKED, None
    why = check(side, chart)
    return (FAILED, why) if why else (PASSED, None)
