"""LEAP OI Scanner page (2026-11-17): which strikes 1 to 12 months out hold the most open interest, across a board of
tickers, with each strike's delta and the option chain's ENTRY tag.

Ganesh 2026-11-16: "Leap OI scanner is better option", "Don't give high oi delta 0.02 ... max up to 0.1 see our option
chain entry", "See the entry like that logic". 2026-11-17: "Build all 3 now I want to see before market open".

It reads the same long-dated chains as the Ask chat (momx/long_oi.py), with the same guards: one Schwab request at a
time, an hourly cap, failures remembered. Open interest only changes once a day (the OCC posts it overnight), so the
page is happy with a copy up to BOARD_MAX_AGE old; a ticker with no copy is read by ONE background thread, so the
page request itself never waits on Schwab. Facts only: it never picks, forecasts or places anything.
"""
from __future__ import annotations

import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from momx import long_oi

BOARD_MAX_AGE = 12 * 3600          # open interest is once a day; bid/ask in the copy are "as of" its read time
MAX_SYMBOLS = 25
ROWS_PER_SIDE = 10                 # per ticker, before the board-wide ranking
BOARD_ROWS = 60
ENTRY_ROWS = 30
MAG7 = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
_TICKER = re.compile(r"^[A-Z][A-Z.\-]{0,5}$")
NOTE = ("Open interest is how many contracts are still open; it updates once a day before the open. Delta 0.10 and up "
        "only, so far-away lottery strikes never show. ENTRY marks the contract the option chain marks ENTRY on that "
        "expiry (out of the money, delta just under 0.20). Big open interest is not a direction signal: our test on "
        "15,896 cases found it a coin flip. Facts only, not a pick.")

_REFRESH = threading.Lock()
_QUEUE: list[str] = []
_WORKER: dict[str, Any] = {"thread": None}


def clean_symbols(raw: Any) -> list[str]:
    """Upper-cased, de-duplicated, valid tickers from 'AAPL,nvda' or a list; at most MAX_SYMBOLS."""
    items = raw.split(",") if isinstance(raw, str) else list(raw or [])
    out: list[str] = []
    for item in items:
        ticker = str(item or "").strip().upper()
        if _TICKER.match(ticker) and ticker not in out:
            out.append(ticker)
    return out[:MAX_SYMBOLS]


def day_range(months_from: int, months_to: int) -> tuple[int, int]:
    """Days out for 'X to Y months', the same edges the chat uses: 1 month starts at 28 days, 12 ends at 366."""
    low = max(1, min(12, int(months_from)))
    high = max(low, min(12, int(months_to)))
    return (long_oi.FETCH_MIN_DAYS if low == 1 else low * 30 - 2), (long_oi.FETCH_MAX_DAYS if high == 12 else high * 30 + 2)


def _away(strike: float, spot: float | None) -> float | None:
    return round((strike / spot - 1) * 100, 1) if spot else None


def ticker_rows(doc: dict, *, min_days: int, max_days: int, side: str = "both", min_delta: float = long_oi.MIN_DELTA,
                entry_only: bool = False) -> list[dict]:
    """One ticker's biggest-open-interest contracts in the range, ENTRY marked, each tagged with ticker/spot/away."""
    spot = doc.get("spot")
    rows = long_oi.mark_entries([dict(row) for row in doc.get("rows") or []], spot)
    sides = [s for s in ("CALL", "PUT") if side == "both" or (side == "calls" and s == "CALL") or (side == "puts" and s == "PUT")]
    out: list[dict] = []
    for s in sides:
        pool = [row for row in rows if row.get("entry")] if entry_only else rows
        for row in long_oi.top_strikes(pool, side=s, min_days=min_days, max_days=max_days, n=ROWS_PER_SIDE,
                                       min_delta=min_delta):
            out.append({**row, "ticker": doc.get("symbol"), "spot": spot, "away": _away(row["strike"], spot)})
    return out


def _refresh_loop(root: Path) -> None:
    while True:
        with _REFRESH:
            if not _QUEUE:
                _WORKER["thread"] = None
                return
            ticker = _QUEUE.pop(0)
        try:
            long_oi.chain_for(ticker, root, datetime.now().astimezone())
        except long_oi.Unavailable:
            pass                                           # remembered by long_oi; the board shows the reason
        except Exception:  # noqa: BLE001 - one bad ticker never stops the rest
            pass


def _schedule(tickers: list[str], root: Path) -> None:
    with _REFRESH:
        for ticker in tickers:
            if ticker not in _QUEUE:
                _QUEUE.append(ticker)
        if _WORKER["thread"] is None and _QUEUE:
            thread = threading.Thread(target=_refresh_loop, args=(Path(root),), name="leap-oi-board", daemon=True)
            _WORKER["thread"] = thread
            thread.start()


def pending() -> list[str]:
    with _REFRESH:
        return list(_QUEUE)


def reset() -> None:
    with _REFRESH:
        _QUEUE.clear()


def board(symbols: Any, root: Path, now: datetime | None = None, *, months_from: int = 3, months_to: int = 12,
          side: str = "both", min_delta: float = long_oi.MIN_DELTA, entry_only: bool = False, wait: bool = False) -> dict:
    """The page's whole answer. Reads saved copies only; tickers without one are queued for the background reader
    (or read right here with wait=True, for tests)."""
    now = now or datetime.now().astimezone()
    tickers = clean_symbols(symbols)
    side = side if side in {"both", "calls", "puts"} else "both"
    min_delta = max(long_oi.MIN_DELTA, min(0.9, float(min_delta)))
    min_days, max_days = day_range(months_from, months_to)
    status: list[dict] = []
    rows: list[dict] = []
    missing: list[str] = []
    for ticker in tickers:
        doc = long_oi.cached_chain(ticker, root, now, BOARD_MAX_AGE)
        error = None
        if doc is None and wait:
            try:
                doc = long_oi.chain_for(ticker, root, now)
            except long_oi.Unavailable as exc:
                error = str(exc)
        if doc is None:
            if error is None and long_oi.failed_recently(ticker):
                error = f"Schwab did not answer for {ticker} a few minutes ago."
            if error is None:
                missing.append(ticker)
            status.append({"ticker": ticker, "state": "error" if error else "pending", "error": error})
            continue
        found = ticker_rows(doc, min_days=min_days, max_days=max_days, side=side, min_delta=min_delta, entry_only=entry_only)
        rows.extend(found)
        calls = sum(r["oi"] for r in found if r["side"] == "CALL")
        puts = sum(r["oi"] for r in found if r["side"] == "PUT")
        status.append({"ticker": ticker, "state": "ready", "spot": doc.get("spot"), "readAt": doc.get("fetchedAt"),
                       "callOi": calls, "putOi": puts, "rows": len(found)})
    if missing:
        _schedule(missing, root)
    rows.sort(key=lambda r: (-r["oi"], -r["volume"], r["days"], r["strike"]))
    entries = [r for r in rows if r.get("entry")][:ENTRY_ROWS]
    calls = [r for r in rows if r["side"] == "CALL"]
    puts = [r for r in rows if r["side"] == "PUT"]
    return {
        "asOf": now.isoformat(), "monthsFrom": int(months_from), "monthsTo": int(months_to), "minDays": min_days,
        "maxDays": max_days, "side": side, "minDelta": min_delta, "entryOnly": bool(entry_only),
        "tickers": status, "pending": [s["ticker"] for s in status if s["state"] == "pending"],
        "biggestCall": calls[0] if calls else None, "biggestPut": puts[0] if puts else None,
        "entries": entries, "rows": rows[:BOARD_ROWS], "note": NOTE,
    }
