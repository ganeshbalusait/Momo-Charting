"""Today's 5-minute closes for the ticker card's mini chart (his ask 2026-10-03: "I want
same as momox" - MomoX's card draws "today 4:00-20:00 ET" with the OI walls on it).

Pure helper + one fetch: the worker serves it at /api/momx-scanner/daybars?symbol=X
(api_server proxies every /api/momx-scanner/* path to the worker). The fetch is the
board's own cached Alpaca 5m tape (feed.fetch_5m, cache honoured, no Schwab volume
pass), so opening a card costs at most one Alpaca request for a cold symbol.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
SESSION_START_MIN = 4 * 60      # 04:00 ET
SESSION_END_MIN = 20 * 60       # 20:00 ET
_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")


def session_closes(frame: Any, upto: str | None = None) -> dict:
    """{day, bars: [[epoch_s, close], ...], low, high} for the NEWEST ET day in ``frame``
    with bars inside 04:00-20:00 (Saturday shows Friday) - on or before ``upto``
    (YYYY-MM-DD, a History snapshot's day) when given. {} when there is nothing."""
    try:
        d = frame.reset_index()
    except AttributeError:
        return {}
    cols = {str(c).lower(): c for c in d.columns}
    tcol = cols.get("time") or cols.get("timestamp") or d.columns[0]
    ccol = cols.get("close")
    if ccol is None:
        return {}
    by_day: dict[str, list] = {}
    for r in d.to_dict("records"):
        tv = r[tcol]
        try:
            tv = tv.timestamp() if hasattr(tv, "timestamp") else float(tv)
            close = float(r[ccol])
        except (TypeError, ValueError):
            continue
        if tv > 1e12:
            tv /= 1000
        t = datetime.fromtimestamp(tv, ET)
        minute = t.hour * 60 + t.minute
        if SESSION_START_MIN <= minute < SESSION_END_MIN and close == close and close > 0:
            by_day.setdefault(t.date().isoformat(), []).append([int(tv), round(close, 4)])
    if upto:
        by_day = {d: v for d, v in by_day.items() if d <= upto}
    if not by_day:
        return {}
    day = max(by_day)
    bars = sorted(by_day[day])
    closes = [c for _, c in bars]
    return {"day": day, "bars": bars, "low": min(closes), "high": max(closes)}


def day_bars(symbol: str, day: str | None = None) -> dict:
    """The worker route's body; ``day`` = a History snapshot's date (its chart, not today's).
    Never raises: an error is {"symbol", "error"}."""
    sym = str(symbol or "").strip().upper()
    if not _SYMBOL.fullmatch(sym):
        return {"symbol": sym, "error": "bad symbol"}
    days = 1
    if day:
        try:
            back = (datetime.now(ET).date() - datetime.fromisoformat(str(day)[:10]).date()).days
        except ValueError:
            return {"symbol": sym, "error": "bad day"}
        if back < 0 or back > 30:
            return {"symbol": sym, "error": "day out of range"}
        days = back + 4                 # a weekend / holiday snapshot reaches back to its last session
    try:
        from momx import feed
        res = feed.fetch_5m([sym], days=days, schwab_volume=False)
        frame = res.bars.get(sym)
    except Exception as exc:  # noqa: BLE001 - a card chart never breaks the worker
        return {"symbol": sym, "error": type(exc).__name__}
    if frame is None:
        return {"symbol": sym, "error": "no bars"}
    return {"symbol": sym, **session_closes(frame, str(day)[:10] if day else None)}
