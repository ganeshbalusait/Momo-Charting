"""Time Machine: "what are the odds from here?" for a chart's ROI plan.

Ganesh 2026-11-15 ("Time Machine how it will work on our app?" ... "Yes, do
it"). Idea from github.com/Jakeschincariol/day-trader-skill (MIT), rebuilt on
the app's own data: the 60-day five-minute tape the chart already caches
(Schwab), no new data source.

Two answers, both read-only (nothing here places, blocks or changes a trade):

1. Replay. The rest of today is replayed `paths` times. Each five-minute step
   is a real candle from the SAME time of day on one of the previous sessions,
   picked at random, scaled to where the path is (the candle's high, low and
   close relative to the candle before it). Every path stops at the first of
   target / stop it touches, or at 3:55 PM ET. When one candle touches both,
   the stop counts (the cautious reading: a five-minute bar does not say which
   came first).
2. Same trade, same time, past days. On every previous session, the same plan
   (target and stop at the same % distance from the entry) entered at the same
   time of day, walked forward on the real candles.

Before the open or after 3:50 PM the replay covers a whole session from the
open instead ("next session"). Seeded per symbol / day / time, so the same
question asked twice gives the same numbers.
"""
from __future__ import annotations

import math
import zlib
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

EASTERN = ZoneInfo("America/New_York")
OPEN = dtime(9, 30)
SLOT_MINUTES = 5
# Bars that START at 09:30 ... 15:50 (77 of them); the last ends at 15:55, the "flat by"
# time the ROI plan and the alerts use.
SESSION_SLOTS = 77
FLAT_LABEL = "3:55 PM"
MAX_DAYS = 59
MIN_DAYS = 10
MIN_SLOTS_PER_DAY = 70
DEFAULT_PATHS = 10_000
FAN_PATHS = 48


def _slot_of(moment: datetime) -> int:
    """Five-minute slot index of a bar start, 0 = 09:30."""
    minutes = (moment.hour * 60 + moment.minute) - (OPEN.hour * 60 + OPEN.minute)
    return minutes // SLOT_MINUTES


def _session_table(bars) -> dict:
    """{day: array (SESSION_SLOTS, 4) of open/high/low/close, NaN where missing}."""
    frame = bars if isinstance(bars, pd.DataFrame) else pd.DataFrame(list(bars or []))
    if frame.empty or "timestamp" not in frame:
        return {}
    stamps = pd.to_datetime(frame["timestamp"], utc=True, errors="coerce").dt.tz_convert(EASTERN)
    table: dict = {}
    for stamp, o, h, l, c in zip(stamps, frame["open"], frame["high"], frame["low"], frame["close"]):
        if stamp is pd.NaT or stamp is None or pd.isna(stamp):
            continue
        if stamp.second or stamp.minute % SLOT_MINUTES:
            continue
        slot = _slot_of(stamp)
        if not 0 <= slot < SESSION_SLOTS:
            continue
        values = [float(o), float(h), float(l), float(c)]
        if not all(math.isfinite(v) and v > 0 for v in values):
            continue
        day = stamp.date()
        if day not in table:
            table[day] = np.full((SESSION_SLOTS, 4), np.nan)
        table[day][slot] = values
    return table


def _verdict(target_pct: float, stop_pct: float) -> str:
    edge = target_pct - stop_pct
    if abs(edge) < 5:
        return "Close to a coin flip"
    if edge >= 15:
        return "Clear edge to the target"
    if edge > 0:
        return "Slight edge to the target, not a sure thing"
    if edge <= -15:
        return "The stop is clearly more likely"
    return "The stop is slightly more likely"


def run_time_machine(
    bars,
    *,
    side: str,
    spot: float,
    target: float,
    stop: float,
    now: datetime,
    paths: int = DEFAULT_PATHS,
    symbol: str = "",
) -> dict:
    """Odds for one plan. `bars`: five-minute candles (timestamp, open, high,
    low, close), the chart's 60-day tape. Returns a JSON-ready dict; `ok`
    False with a plain `reason` when the plan or the data cannot answer."""
    side = "PUT" if str(side).upper() == "PUT" else "CALL"
    spot, target, stop = float(spot or 0), float(target or 0), float(stop or 0)
    up = side == "CALL"
    if not (spot > 0 and target > 0 and stop > 0):
        return {"ok": False, "reason": "The plan needs a price, a target and a stop."}
    if up and not (stop < spot < target) or not up and not (target < spot < stop):
        return {"ok": False, "reason": "The target and the stop must sit on either side of the price."}

    now_et = now.astimezone(EASTERN) if now.tzinfo else now.replace(tzinfo=EASTERN)
    table = _session_table(bars)
    today = now_et.date()
    days = sorted(day for day, rows in table.items()
                  if day < today and np.count_nonzero(~np.isnan(rows[:, 3])) >= MIN_SLOTS_PER_DAY)[-MAX_DAYS:]
    if len(days) < MIN_DAYS:
        return {"ok": False, "reason": f"Only {len(days)} full sessions of five-minute candles; at least {MIN_DAYS} are needed."}

    # Where the replay starts: the next bar after now, or the open when the
    # session is not running (before 09:30, after 15:50, weekend).
    minutes = (now_et.hour * 60 + now_et.minute) - (OPEN.hour * 60 + OPEN.minute)
    in_session = now_et.weekday() < 5 and 0 <= minutes < SESSION_SLOTS * SLOT_MINUTES - SLOT_MINUTES
    start = -(-minutes // SLOT_MINUTES) if in_session else 0  # ceil: the bar still forming is skipped
    start = min(max(start, 0), SESSION_SLOTS - 1)
    mode = "today" if in_session else "next-session"
    steps = SESSION_SLOTS - start

    # Each day's candles as moves relative to the close before them.
    cube = np.stack([table[day] for day in days])  # (D, S, 4)
    prev_close = np.concatenate([cube[:, :1, 0], cube[:, :-1, 3]], axis=1)  # slot 0: its own open
    prev_close = np.where(np.isnan(prev_close), cube[:, :, 0], prev_close)
    with np.errstate(invalid="ignore", divide="ignore"):
        hi = np.log(cube[:, :, 1] / prev_close)
        lo = np.log(cube[:, :, 2] / prev_close)
        cl = np.log(cube[:, :, 3] / prev_close)
    for arr in (hi, lo, cl):
        np.nan_to_num(arr, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

    seed = zlib.crc32(f"{symbol.upper()}|{today.isoformat()}|{start}|{side}|{target:.2f}|{stop:.2f}".encode())
    rng = np.random.default_rng(seed)
    paths = int(max(100, min(paths, 50_000)))
    picks = rng.integers(0, len(days), size=(paths, steps))
    cols = np.arange(start, SESSION_SLOTS)
    step_hi, step_lo, step_cl = hi[picks, cols], lo[picks, cols], cl[picks, cols]
    log_close = math.log(spot) + np.cumsum(step_cl, axis=1)
    log_prev = np.concatenate([np.full((paths, 1), math.log(spot)), log_close[:, :-1]], axis=1)
    high, low = np.exp(log_prev + step_hi), np.exp(log_prev + step_lo)
    hit_target = high >= target if up else low <= target
    hit_stop = low <= stop if up else high >= stop
    never = steps + 1
    first_target = np.where(hit_target.any(axis=1), hit_target.argmax(axis=1), never)
    first_stop = np.where(hit_stop.any(axis=1), hit_stop.argmax(axis=1), never)
    stop_first = (first_stop <= first_target) & (first_stop < never)  # a tie is the stop
    target_first = (first_target < first_stop)
    neither = ~(stop_first | target_first)
    closes = np.exp(log_close)

    pct = lambda mask: round(float(mask.mean()) * 100, 1)  # noqa: E731
    target_pct, stop_pct, neither_pct = pct(target_first), pct(stop_first), pct(neither)

    # The drawing: price bands per step and a few whole paths, each cut where
    # it hit target or stop.
    bands = {str(q): [round(float(v), 2) for v in np.percentile(closes, q, axis=0)] for q in (10, 25, 50, 75, 90)}
    sample = []
    for index in rng.choice(paths, size=min(FAN_PATHS, paths), replace=False):
        end = min(first_target[index], first_stop[index])
        cut = steps if end >= never else int(end) + 1
        outcome = "target" if target_first[index] else "stop" if stop_first[index] else "neither"
        sample.append({"outcome": outcome, "closes": [round(float(v), 2) for v in closes[index, :cut]]})

    # Same trade, same time, every previous session (real candles only).
    up_pct, down_pct = target / spot - 1, stop / spot - 1
    history = []
    for day_index, day in enumerate(days):
        rows = cube[day_index]
        entry = rows[start - 1, 3] if start > 0 else rows[0, 0]
        if not (entry > 0):
            continue
        day_target, day_stop = entry * (1 + up_pct), entry * (1 + down_pct)
        outcome = "neither"
        for slot in range(start, SESSION_SLOTS):
            h, l = rows[slot, 1], rows[slot, 2]
            if np.isnan(h) or np.isnan(l):
                continue
            touched_stop = l <= day_stop if up else h >= day_stop
            touched_target = h >= day_target if up else l <= day_target
            if touched_stop:
                outcome = "stop"
                break
            if touched_target:
                outcome = "target"
                break
        history.append({"day": day.isoformat(), "outcome": outcome})
    counts = {key: sum(1 for item in history if item["outcome"] == key) for key in ("target", "stop", "neither")}

    start_label = f"{(9 * 60 + 30 + start * SLOT_MINUTES) // 60 % 12 or 12}:{(30 + start * SLOT_MINUTES) % 60:02d}"
    neither_closes = closes[neither, -1]
    return {
        "ok": True,
        "symbol": symbol.upper(),
        "side": side,
        "spot": round(spot, 2),
        "target": round(target, 2),
        "stop": round(stop, 2),
        "mode": mode,
        "startSlot": int(start),
        "startLabel": start_label,
        "flatLabel": FLAT_LABEL,
        "steps": int(steps),
        "paths": paths,
        "sessions": len(days),
        "firstDay": days[0].isoformat(),
        "lastDay": days[-1].isoformat(),
        "seed": int(seed),
        "odds": {"target": target_pct, "stop": stop_pct, "neither": neither_pct},
        "neitherMedianClose": round(float(np.median(neither_closes)), 2) if neither_closes.size else None,
        "bands": bands,
        "sample": sample,
        "history": history,
        "historyCounts": counts,
        "verdict": _verdict(target_pct, stop_pct),
    }
