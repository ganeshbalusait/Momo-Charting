"""LEAP OI Scanner page (2026-11-17): which strikes 1 to 12 months out hold the most open interest, across a board of
tickers, with each strike's delta and the option chain's ENTRY tag.

Ganesh 2026-11-16: "Leap OI scanner is better option", "Don't give high oi delta 0.02 ... max up to 0.1 see our option
chain entry", "See the entry like that logic". 2026-11-17: "Build all 3 now I want to see before market open", then
"I see only mag7 tickets what about other tickets from watchlist ?": the board now covers Mag7 + his WHOLE watchlist
(about 360 tickers), with a By-ticker list so the small names are not buried under Mag7's huge open interest.

It reads the same long-dated chains as the Ask chat (momx/long_oi.py): one Schwab request at a time, failures
remembered. Open interest only changes once a day (the OCC posts it overnight), so a copy up to BOARD_MAX_AGE old is
used; tickers with no copy are read by ONE background thread, PACE_SECONDS apart, with its own hourly allowance, so a
whole-watchlist sweep takes about 20 minutes once a day and the page request itself never waits on Schwab. Only the
rows the page can show are held (compact()), not every strike of every ticker.
Facts only: it never picks, forecasts or places anything.
"""
from __future__ import annotations

import gzip
import json
import re
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any

from momx import long_oi

BOARD_MAX_AGE = 12 * 3600          # open interest is once a day; bid/ask in the copy are "as of" its read time
MAX_SYMBOLS = 400                  # Mag7 + his whole watchlist (about 360 on 2026-11-17)
PACE_SECONDS = 3.0                 # the background reader waits this long after each Schwab request
BOARD_FETCHES_PER_HOUR = 400       # its own allowance, separate from the chat's
ROWS_PER_SIDE = 10                 # per ticker, before the board-wide ranking
KEEP_PER_EXPIRY = 10               # per side and expiry, for each delta floor the page offers (0.10 and 0.20)
BOARD_ROWS = 60
ENTRY_ROWS = 30
COMPACT_SCHEMA = 1
MAG7 = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
_TICKER = re.compile(r"^[A-Z][A-Z.\-]{0,5}$")
NOTE = ("Open interest is how many contracts are still open; it updates once a day before the open. Delta 0.10 and up "
        "only, so far-away lottery strikes never show. ENTRY marks the contract the option chain marks ENTRY on that "
        "expiry (out of the money, delta just under 0.20). Big open interest is not a direction signal: our test on "
        "15,896 cases found it a coin flip. Facts only, not a pick.")

_REFRESH = threading.Lock()
_QUEUE: list[str] = []
_WORKER: dict[str, Any] = {"thread": None}
_COMPACT: dict[str, dict] = {}     # ticker -> compact copy (only the rows the page can show)
_FETCHED: deque = deque()          # monotonic times of this page's own Schwab requests


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


def compact(doc: dict) -> dict:
    """Only the rows the page can ever show: ENTRY rows, and per side and expiry the KEEP_PER_EXPIRY biggest open
    interest at delta 0.10+ and at 0.20+ (each ticker shows at most ROWS_PER_SIDE per side, so this is exact for the
    page's filters). Rows carry their ENTRY mark."""
    spot = doc.get("spot")
    rows = long_oi.mark_entries([dict(row) for row in doc.get("rows") or []], spot)
    keep: dict[int, dict] = {}
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        if row.get("oi", 0) <= 0 or row.get("delta") is None or abs(row["delta"]) < long_oi.MIN_DELTA - 1e-9:
            continue
        if row.get("entry"):
            keep[id(row)] = row
        groups.setdefault((row["side"], row["expiry"]), []).append(row)
    for group in groups.values():
        group.sort(key=lambda r: (-r["oi"], -r["volume"], r["strike"]))
        for floor in (long_oi.MIN_DELTA, 0.20):
            for row in [r for r in group if abs(r["delta"]) >= floor - 1e-9][:KEEP_PER_EXPIRY]:
                keep[id(row)] = row
    return {"schema": COMPACT_SCHEMA, "symbol": doc.get("symbol"), "fetchedAt": doc.get("fetchedAt"), "spot": spot,
            "rows": list(keep.values())}


def _compact_path(root: Path, ticker: str) -> Path:
    return Path(root) / "leap_oi_board" / f"{ticker}.json.gz"


def _save_compact(root: Path, ticker: str, doc: dict) -> None:
    with _REFRESH:
        _COMPACT[ticker] = doc
    path = _compact_path(root, ticker)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with gzip.open(temp, "wt", encoding="utf-8") as handle:
            json.dump(doc, handle)
        temp.replace(path)
    except OSError:
        pass


def held(ticker: str, root: Path, now: datetime) -> dict | None:
    """This ticker's compact copy younger than BOARD_MAX_AGE: memory, its small file, or the full long-dated chain on
    disk (the chat's copy). Never asks Schwab."""
    with _REFRESH:
        doc = _COMPACT.get(ticker)
    if long_oi._fresh(doc, now, BOARD_MAX_AGE):
        return doc
    try:
        with gzip.open(_compact_path(root, ticker), "rt", encoding="utf-8") as handle:
            disk = json.load(handle)
    except (OSError, ValueError, EOFError):
        disk = None
    if (isinstance(disk, dict) and disk.get("schema") == COMPACT_SCHEMA and disk.get("symbol") == ticker
            and long_oi._fresh(disk, now, BOARD_MAX_AGE)):
        with _REFRESH:
            _COMPACT[ticker] = disk
        return disk
    full = long_oi._read_disk(root, ticker)
    if long_oi._fresh(full, now, BOARD_MAX_AGE):
        small = compact(full)
        _save_compact(root, ticker, small)
        return small
    return None


def ticker_rows(doc: dict, *, min_days: int, max_days: int, side: str = "both", min_delta: float = long_oi.MIN_DELTA,
                entry_only: bool = False) -> list[dict]:
    """One ticker's biggest-open-interest contracts in the range (ENTRY already marked by compact()), each tagged with
    ticker/spot/away."""
    spot = doc.get("spot")
    rows = doc.get("rows") or []
    sides = [s for s in ("CALL", "PUT") if side == "both" or (side == "calls" and s == "CALL") or (side == "puts" and s == "PUT")]
    out: list[dict] = []
    for s in sides:
        pool = [row for row in rows if row.get("entry")] if entry_only else rows
        for row in long_oi.top_strikes(pool, side=s, min_days=min_days, max_days=max_days, n=ROWS_PER_SIDE,
                                       min_delta=min_delta):
            out.append({**row, "ticker": doc.get("symbol"), "spot": spot, "away": _away(row["strike"], spot)})
    return out


def _ticker_summary(ticker: str, spot: Any, found: list[dict]) -> dict:
    """One line per ticker for the page's By-ticker list: its biggest call and put, and its biggest ENTRY call and put."""
    def top(side: str, entry: bool = False) -> dict | None:
        pool = [r for r in found if r["side"] == side and (r.get("entry") or not entry)]
        return max(pool, key=lambda r: (r["oi"], r["volume"]), default=None)
    return {"ticker": ticker, "spot": spot, "call": top("CALL"), "put": top("PUT"),
            "entryCall": top("CALL", True), "entryPut": top("PUT", True),
            "callOi": sum(r["oi"] for r in found if r["side"] == "CALL"),
            "putOi": sum(r["oi"] for r in found if r["side"] == "PUT")}


def _refresh_loop(root: Path) -> None:
    while True:
        with _REFRESH:
            if not _QUEUE:
                _WORKER["thread"] = None
                return
            ticker = _QUEUE.pop(0)
            mono = time.monotonic()
            while _FETCHED and mono - _FETCHED[0] > 3600:
                _FETCHED.popleft()
            if len(_FETCHED) >= BOARD_FETCHES_PER_HOUR:
                _QUEUE.clear()                             # asked again on a later page poll, after the hour
                continue
        asked = False
        try:
            now = datetime.now().astimezone()
            if held(ticker, root, now) is None:
                with _REFRESH:
                    _FETCHED.append(time.monotonic())
                asked = True
                doc = long_oi.chain_for(ticker, root, now, count=False, keep=False)
                _save_compact(root, ticker, compact(doc))
        except long_oi.Unavailable:
            pass                                           # remembered by long_oi; the board shows the reason
        except Exception:  # noqa: BLE001 - one bad ticker never stops the rest
            pass
        if asked and PACE_SECONDS > 0:
            time.sleep(PACE_SECONDS)                       # gentle on Schwab: charts and chains share its limit


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
        _COMPACT.clear()
        _FETCHED.clear()


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
    by_ticker: list[dict] = []
    for ticker in tickers:
        doc = held(ticker, root, now)
        error = None
        if doc is None and wait:
            try:
                doc = compact(long_oi.chain_for(ticker, root, now, count=False, keep=False))
                _save_compact(root, ticker, doc)
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
        by_ticker.append(_ticker_summary(ticker, doc.get("spot"), found))
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
        "entries": entries, "rows": rows[:BOARD_ROWS], "byTicker": sorted(by_ticker, key=lambda t: t["ticker"]),
        "note": NOTE,
    }
