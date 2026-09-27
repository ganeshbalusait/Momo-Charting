"""Overnight-session (20:00-04:00 ET) candles from Alpaca's BOATS feed.

Why this module exists (verified live, 2026-08-24 22:10 ET): thinkorswim
draws the Blue Ocean ATS overnight session ("EXTO") and its MTF EMA labels
are computed from those candles. None of the app's feeds carried that
session - Schwab price history refuses the current night, the Schwab
streamer is silent after 20:00, Tradier has no overnight session, and
Alpaca's default IEX/SIP feeds return nothing - until the Alpaca stock-bars
endpoint was asked for ``feed=boats``: 62 five-minute META bars for Sunday
20:00 -> Monday 03:55, on the owner's existing Alpaca key. The feed answers
multi-symbol requests, 1Min/5Min/15Min timeframes, and (on the free plan)
refuses only the most recent ~15 minutes ("subscription does not permit
querying recent BOATS data"), so the end of every request is clamped.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

import pandas as pd

EASTERN = ZoneInfo("America/New_York")
BOATS_BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
# Free-plan delay on "recent" BOATS data; a 16-minute clamp clears it.
BOATS_RECENT_DELAY_MINUTES = 16
BOATS_PAGE_LIMIT = 10000

_TIMEFRAMES = {
    "1min": "1Min",
    "1Min": "1Min",
    "5min": "5Min",
    "5Min": "5Min",
    "15min": "15Min",
    "15Min": "15Min",
    "30min": "30Min",
    "30Min": "30Min",
}


def boats_timeframe(interval: str) -> str:
    return _TIMEFRAMES.get(str(interval or "1min"), "1Min")


def clamp_end(end: datetime, now: datetime | None = None) -> datetime:
    """The newest instant the free plan will serve."""
    current = now or datetime.now(timezone.utc)
    limit = current - timedelta(minutes=BOATS_RECENT_DELAY_MINUTES)
    return min(end, limit)


def fetch_boats_bars(
    key: str,
    secret: str,
    symbols: Iterable[str],
    start: datetime,
    end: datetime,
    interval: str = "1min",
    *,
    get: Callable | None = None,
    now: datetime | None = None,
    feed: str = "boats",
) -> dict[str, list[dict]]:
    """Raw Alpaca bars per symbol between ``start`` and ``end`` (tz-aware).

    ``feed`` defaults to BOATS (the overnight tape). ``feed="sip"`` serves the
    04:00-07:00 premarket that neither BOATS nor Schwab carries for the current
    day - verified 2026-08-28 on the owner's free key: 161 one-minute AAPL bars
    for 04:00-07:00, while feed=iex (the feed the original rejection tested)
    returns zero. Both feeds share the same ~15-minute recency wall, which
    clamp_end already clears.

    ``get`` is the HTTP getter (``requests.get`` signature) so tests can
    stub it. Pages until ``next_page_token`` is empty. Returns ``{}`` on any
    HTTP error - the overnight patch is an enhancement and must never break
    a chart build.
    """
    if get is None:
        import requests

        get = requests.get
    wanted = [str(item).strip().upper() for item in symbols if str(item).strip()]
    if not wanted or not key or not secret:
        return {}
    end = clamp_end(end, now)
    if end <= start:
        return {}
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    params = {
        "symbols": ",".join(wanted),
        "timeframe": boats_timeframe(interval),
        "start": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": BOATS_PAGE_LIMIT,
        "feed": str(feed or "boats"),
    }
    out: dict[str, list[dict]] = {symbol: [] for symbol in wanted}
    token = None
    for _ in range(50):  # hard stop; 50 pages x 10k bars is far beyond any window
        query = dict(params)
        if token:
            query["page_token"] = token
        try:
            response = get(BOATS_BARS_URL, params=query, headers=headers, timeout=60)
            payload = response.json()
        except Exception:
            return {}
        if getattr(response, "status_code", 200) != 200 or not isinstance(payload, dict):
            return {}
        for symbol, bars in (payload.get("bars") or {}).items():
            if isinstance(bars, list):
                out.setdefault(str(symbol).upper(), []).extend(bars)
        token = payload.get("next_page_token")
        if not token:
            break
    return out


def boats_bars_to_frame(bars: object) -> pd.DataFrame:
    """Alpaca bar rows ({t,o,h,l,c,v}) -> the chart's frame shape.

    Alpaca stamps bars in UTC ("2026-08-24T04:00:00Z"); convert to Eastern so
    they merge with every other tape.
    """
    empty = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    rows: list[dict] = []
    for item in bars if isinstance(bars, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            rows.append(
                {
                    # Kept as the RAW string here and parsed once for the whole
                    # column below. This used to call pd.to_datetime per bar,
                    # inside this loop: every call re-ran pandas' format
                    # inference on a single scalar, so the cost was linear in
                    # bars with a very large constant. Measured 2026-08-27,
                    # 20,000 bars: 25.69s per-row against 0.098s vectorised -
                    # 261x. This function is on the chart build path, so with
                    # 61 builder threads live NOTHING finished: 357 of 399
                    # cached tickers had no complete session, every one of them
                    # parked in _array_strptime_with_fallback under
                    # _backfill_overnight_session.
                    "timestamp": item.get("t"),
                    "open": float(item["o"]),
                    "high": float(item["h"]),
                    "low": float(item["l"]),
                    "close": float(item["c"]),
                    "volume": float(item.get("v") or 0),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return empty
    frame = pd.DataFrame(rows)
    # ONE vectorised parse for the whole column. errors="coerce" keeps the old
    # behaviour of dropping an unparseable stamp rather than raising; no
    # explicit format is passed because Alpaca varies on fractional seconds and
    # a wrong format string coerces silently to NaT, which would drop real bars.
    stamps = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    keep = stamps.notna()
    if not keep.any():
        return empty
    frame = frame.loc[keep].copy()
    frame["timestamp"] = stamps.loc[keep].dt.tz_convert(EASTERN)
    return frame.sort_values("timestamp").reset_index(drop=True)
