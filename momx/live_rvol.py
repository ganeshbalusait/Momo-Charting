"""LIVE RVOL: the forming bar rescored from the Schwab stream between builds.

2026-11-03, his ask: RVOL 5m/15m/30m "real time same as TOS". The formula and
the candles matched, but a Watchlist build takes ~2.5 min, so the board's
forming bar was ~3 minutes old when he looked; TOS rescores it every tick.

Only the forming bar changes between builds. Each build leaves, per symbol and
timeframe, the previous 49 bars' volume sum ``s`` and sum of squares ``q``, the
forming bucket's volume ``v0`` as the build counted it, and the window's oldest
volumes (``columns.rvol_live_base``), harvested by ``board.build_board`` into
one per-symbol store that every list shares. Here the forming bucket's
volume/high/low/close come from the api_server stream minutes the worker
already polls (``feed._STREAM_LIVE``), and his column script is re-evaluated
exactly:

    relVol = (volume - Average(volume, 50)) / StDev(volume, 50)   # population
    isBullish = close - low >= high - close

with Average/StDev over the 49 previous bars plus the forming one. When the
stream has moved into a later bucket, each finished bucket joins the window and
the oldest volume leaves it (up to ``columns.RVOL_LIVE_ROLLOVERS`` times).

TRUST RULES (adversarial review 2026-11-03: a per-symbol stream gap made 27%
of naive live cells differ from the correct board value). A symbol/timeframe
is served live only when ALL hold, otherwise the board's value stays:
  * the stream is alive (newest minute held <= STREAM_STALE_SECONDS old) and
    this symbol's newest minute is just as recent (still leased);
  * this symbol has EVERY minute from the bucket start to its newest minute
    (a released-and-re-leased symbol leaves a hole; under-counts only ever
    came from missing whole minutes);
  * the stream's count of the forming bucket is at least the build's ``v0``
    (less tolerance) - it reproduces what the board already counted;
  * the bucket does not hold the 16:00 closing-cross minute (the stream
    over-counts it against the Schwab bar).
The live value only changes what the RVOL cells SHOW; alerts, filters, the
bot and history keep the board's values. Never raises.
"""
from __future__ import annotations

import bisect
import math
import threading
import time
from datetime import datetime
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

from momx import columns, feed

#: Bucket length in seconds per RVOL timeframe ("D" is not served live).
SPANS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400}
LENGTH = columns.RVOL_LENGTH
#: A poller may ask for at most this many symbols (the rows on screen).
MAX_SYMBOLS = 120
#: Identical requests within this many seconds share one computation.
CACHE_SECONDS = 2.0
#: The stream counts as frozen when its newest minute is older than this.
STREAM_STALE_SECONDS = 150
#: The stream's forming-bucket volume may fall this far below the build's.
VOLUME_TOLERANCE = 0.01     # complete-minute buckets matched REST within 1% (review 2026-11-03)
#: Bases older than this are dropped (a symbol no list has scanned for hours).
BASE_MAX_AGE_SECONDS = 4 * 3600

_ET = ZoneInfo("America/New_York")
_LOCK = threading.Lock()
_STORE: dict = {}        # symbol -> {"at": wall seconds, "bases": {tf: base}}
_CACHE: dict = {}        # tuple(symbols) -> (monotonic, result)


def remember(symbol: str, bases: Mapping[str, Any] | None) -> None:
    """Keep the newest base per symbol and timeframe (merged, never wiped)."""
    if not symbol or not isinstance(bases, Mapping):
        return
    good = {tf: list(base) for tf, base in bases.items() if tf in SPANS and isinstance(base, (list, tuple))}
    if not good:
        return
    key = str(symbol).strip().upper()
    with _LOCK:
        held = _STORE.get(key)
        if held is None:
            _STORE[key] = {"at": time.time(), "bases": good}
        else:
            held["bases"].update(good)
            held["at"] = time.time()


def score(base: list, minutes: list, span: int) -> dict | None:
    """The live cell for one base + that symbol's stream minutes, or None.

    ``minutes`` are ascending {"time", "high", "low", "close", "volume"} dicts
    at or after the base's t0, already checked for coverage.
    """
    try:
        t0, s, q, v0 = int(base[0]), float(base[1]), float(base[2]), float(base[3])
        oldest = [float(v) for v in base[4:]]
    except (TypeError, ValueError, IndexError):
        return None
    buckets: dict[int, list] = {}
    for bar in minutes:
        try:
            t = int(bar["time"])
            v = float(bar.get("volume") or 0.0)
            h, lo, c = float(bar["high"]), float(bar["low"]), float(bar["close"])
        except (KeyError, TypeError, ValueError):
            continue
        if t < t0:
            continue
        k = (t - t0) // span
        held = buckets.get(k)
        if held is None:
            buckets[k] = [v, h, lo, c, t, [(t, v)]]
        else:
            held[0] += v
            held[1] = max(held[1], h)
            held[2] = min(held[2], lo)
            if t >= held[4]:
                held[3], held[4] = c, t
            held[5].append((t, v))
    if 0 not in buckets:
        return None
    if buckets[0][0] < v0 * (1.0 - VOLUME_TOLERANCE) - 1.0:
        return None                 # the stream lost part of what the build counted
    last = max(buckets)
    finished = [k for k in sorted(buckets) if k < last]     # every bucket with minutes is a bar
    if len(finished) > len(oldest):
        return None                 # too many rollovers since the base: wait for a build
    for k, leaving in zip(finished, oldest):
        joining = buckets[k][0]
        s += joining - leaving
        q += joining * joining - leaving * leaving
    v, high, low, close, _, parts = buckets[last]
    mean = (s + v) / LENGTH
    var = (q + v * v) / LENGTH - mean * mean
    sd = math.sqrt(var) if var > 0 else 0.0
    rel = (v - mean) / sd if sd > 0 else 0.0
    if not math.isfinite(rel):
        rel = 0.0
    bg, fg = columns.rvol_colors(rel, (close - low) >= (high - close))
    total, run, came = sum(x for _, x in parts), 0.0, parts[-1][0]
    for t, x in sorted(parts):
        run += x
        if total > 0 and run >= total / 2:
            came = t
            break
    return {"value": columns._ts_round(rel, 1), "bg": bg, "fg": fg,
            "barAt": int(came), "bucketAt": int(t0 + last * span), "live": True}


def _closing_cross(now: float) -> int:
    return int(datetime.fromtimestamp(now, _ET).replace(hour=16, minute=0, second=0, microsecond=0).timestamp())


def compute(symbols: Iterable[str], now: float | None = None) -> dict:
    """{"asOf", "cells": {symbol: {tf: cell}}, "stream"} for the asked symbols."""
    wanted = tuple(sorted({str(s or "").strip().upper() for s in symbols or [] if str(s or "").strip()}))[:MAX_SYMBOLS]
    now_mono = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(wanted)
        if cached and now_mono - cached[0] < CACHE_SECONDS:
            return cached[1]
        now = time.time() if now is None else float(now)
        bases = {sym: dict(_STORE[sym]["bases"]) for sym in wanted
                 if sym in _STORE and now - _STORE[sym]["at"] < BASE_MAX_AGE_SECONDS}
    result: dict = {"asOf": now, "cells": {}, "stream": False}
    if not bases:
        return result
    try:
        live_ok = feed.STREAM_LIVE_ENABLED and feed._stream_live_refresh()
    except Exception:  # noqa: BLE001
        live_ok = False
    if not live_ok:
        return result
    earliest = min(int(b[0]) for per in bases.values() for b in per.values())
    snapshot: dict = {}
    with feed._STREAM_LIVE_LOCK:
        newest_any = feed._STREAM_LIVE.get("max_t") or 0
        for sym in bases:
            held = feed._STREAM_LIVE["bars"].get(sym)
            if not held:
                continue
            times = sorted(t for t in held if t >= earliest - 60)
            snapshot[sym] = (min(held), [held[t] for t in times])
    if not newest_any or now - newest_any > STREAM_STALE_SECONDS:
        return result               # the stream itself is frozen or down
    result["stream"] = True
    close_cross = _closing_cross(now)
    cells: dict = {}
    for sym, per in bases.items():
        got = snapshot.get(sym)
        if not got:
            continue
        first_held, minutes = got
        if not minutes or newest_any - int(minutes[-1]["time"]) > 120:
            continue                # this symbol stopped streaming (released)
        stamps = [int(m["time"]) for m in minutes]
        newest = stamps[-1]
        out: dict = {}
        for tf, base in per.items():
            try:
                t0 = int(base[0])
            except (TypeError, ValueError, IndexError):
                continue
            span = SPANS[tf]
            if first_held > t0 or newest < t0:
                continue
            lo = bisect.bisect_left(stamps, t0)
            window = stamps[lo:]
            if len(window) != (newest - t0) // 60 + 1:
                continue            # a missing minute: never under-count
            if t0 <= close_cross < newest + 60:
                continue            # closing cross: the stream over-counts it
            cell = score(base, minutes[lo:], span)
            if cell is not None:
                out[tf] = cell
        if out:
            cells[sym] = out
    result["cells"] = cells
    with _LOCK:
        _CACHE[wanted] = (now_mono, result)
        if len(_CACHE) > 16:
            for key in sorted(_CACHE, key=lambda k: _CACHE[k][0])[:-16]:
                _CACHE.pop(key, None)
    return result
