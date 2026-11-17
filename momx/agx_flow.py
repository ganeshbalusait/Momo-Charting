"""AGX Flow (2026-11-17): a free, Unusual-Whales-style options flow board built from Schwab's option chain.

For each ticker on the board (up to MAX_SYMBOLS), one Schwab option-chain request reads every call and put expiring
0..FETCH_MAX_DAYS days out, STRIKE_COUNT strikes per expiry. From that:
  * a contract is "unusual" when today's volume is at least MIN_VOLUME and above its open interest
    (Unusual Whales' "volume over OI");
  * premium = volume x mark x 100 (the bid/ask midpoint when Schwab has no mark);
  * the fill is a GUESS from where the last trade printed: at/above the ask -> "ask" (bought), at/below the bid ->
    "bid" (sold), otherwise "mid";
  * newVolume = volume minus the volume at the previous read of the same contract today (0 on the first read).

Facts only, never a pick or a forecast. Read-only: it never places, changes or cancels anything.

Kept small on purpose, like momx/long_oi.py:
  * each ticker's read is kept CACHE_SECONDS in memory and on disk (<root>/agx_flow_cache/{T}.json.gz);
  * a ticker that failed is not asked again for FAIL_SECONDS;
  * at most FETCHES_PER_HOUR Schwab requests an hour, one at a time;
  * board() never waits on Schwab: it answers from what is held and refreshes stale tickers on ONE background thread.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import re
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
FETCH_MAX_DAYS = 45
STRIKE_COUNT = 40
CACHE_SECONDS = 5 * 60
FAIL_SECONDS = 5 * 60
FETCHES_PER_HOUR = 240
LOCK_WAIT_SECONDS = 30.0
MAX_SYMBOLS = 20
MIN_VOLUME = 500
TOP_ROWS = 60
SCHEMA = 1

NOTE = ("Volume and prices from Schwab's option chain. 'Bought/Sold' is a guess from where the last trade printed vs "
        "the bid/ask. Unusual = today's volume above open interest. Facts only, not a direction signal.")

_SYMBOL = re.compile(r"^[A-Z][A-Z.\-]{0,5}$")

_LOCK = threading.Lock()                                   # one Schwab request at a time
_STATE = threading.Lock()                                  # guards the small dicts below
_MEMORY: dict[str, dict] = {}
_FAILED: dict[str, tuple[float, str]] = {}
_RECENT: deque = deque()
_PENDING: dict[str, Path] = {}                             # ticker -> root, waiting for the background thread
_SKIPPED: dict[str, str] = {}                              # ticker -> why the last refresh did not ask (hourly cap, busy)
_WORKER: threading.Thread | None = None


class Unavailable(Exception):
    """A plain-words reason there is no chain for a ticker."""


def default_root() -> Path:
    """The artifacts folder, or the test suite's AGX_MOMX_GRADE_DIR override."""
    override = os.environ.get("AGX_MOMX_GRADE_DIR", "").strip()
    if override:
        return Path(override)
    from config import ARTIFACTS_DIR
    return Path(ARTIFACTS_DIR)


def parse_symbols(value: Any) -> list[str]:
    """Clean tickers from "AAPL,nvda, ..." or a list: uppercase, A-Z . - only, max 6 chars, no repeats, at most
    MAX_SYMBOLS. Anything else is dropped."""
    items = value.split(",") if isinstance(value, str) else list(value or [])
    out: list[str] = []
    for item in items:
        ticker = str(item or "").strip().upper()
        if _SYMBOL.match(ticker) and ticker not in out:
            out.append(ticker)
        if len(out) >= MAX_SYMBOLS:
            break
    return out


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _delta(value: Any) -> float | None:
    number = _num(value)
    return number if number is not None and 0 < abs(number) <= 1 else None


def _iv(value: Any) -> float | None:
    """Schwab's volatility is a percent; -999 / NaN / 0 mean it has none."""
    number = _num(value)
    return number if number is not None and 0 < number < 1000 else None


def mark_price(mark: float | None, bid: float | None, ask: float | None) -> float | None:
    if mark and mark > 0:
        return mark
    if bid is not None and ask is not None and ask > 0 and bid >= 0:
        return (bid + ask) / 2
    return None


def fill_guess(last: float | None, bid: float | None, ask: float | None) -> str:
    """Where the last trade printed: at/above the ask = "ask" (bought), at/below the bid = "bid" (sold), else "mid"."""
    if last is None or last <= 0:
        return "mid"
    if ask is not None and ask > 0 and last >= ask:
        return "ask"
    if bid is not None and bid > 0 and last <= bid:
        return "bid"
    return "mid"


def is_unusual(volume: int, oi: int) -> bool:
    return volume >= MIN_VOLUME and volume > oi


def contracts_from_chain(chain: Any, today: date) -> tuple[float | None, list[dict]]:
    """(underlying price, one dict per contract 0..FETCH_MAX_DAYS out) from a Schwab /chains payload."""
    if not isinstance(chain, dict):
        return None, []
    underlying = chain.get("underlying") if isinstance(chain.get("underlying"), dict) else {}
    spot = _num(chain.get("underlyingPrice")) or _num(underlying.get("last"))
    rows: list[dict] = []
    for side, key in (("CALL", "callExpDateMap"), ("PUT", "putExpDateMap")):
        by_expiry = chain.get(key)
        if not isinstance(by_expiry, dict):
            continue
        for expiry_key, strikes in by_expiry.items():
            try:
                expiry = date.fromisoformat(str(expiry_key)[:10])
            except ValueError:
                continue
            days = (expiry - today).days
            if not 0 <= days <= FETCH_MAX_DAYS or not isinstance(strikes, dict):
                continue
            for contracts in strikes.values():
                for item in contracts if isinstance(contracts, list) else []:
                    if not isinstance(item, dict):
                        continue
                    strike = _num(item.get("strikePrice"))
                    if not strike:
                        continue
                    bid, ask = _num(item.get("bid")), _num(item.get("ask"))
                    rows.append({
                        "side": side, "expiry": expiry.isoformat(), "days": days, "strike": strike,
                        "volume": int(_num(item.get("totalVolume")) or 0), "oi": int(_num(item.get("openInterest")) or 0),
                        "last": _num(item.get("last")), "bid": bid, "ask": ask,
                        "mark": mark_price(_num(item.get("mark")), bid, ask),
                        "delta": _delta(item.get("delta")), "iv": _iv(item.get("volatility")),
                    })
    return spot, rows


def _key(row: dict) -> str:
    return f"{row['side']}|{row['expiry']}|{row['strike']}"


def with_new_volume(rows: list[dict], previous: dict | None, now: datetime) -> list[dict]:
    """Set newVolume on every row: volume minus the previous read's volume of the same contract, when that read was
    today; 0 on the first read of the day (volume restarts each session) and never below 0."""
    before: dict[str, int] = {}
    try:
        same_day = previous and datetime.fromisoformat(str(previous.get("fetchedAt"))).date() == now.date()
    except ValueError:
        same_day = False
    if same_day:
        before = {_key(row): int(row.get("volume") or 0) for row in previous.get("contracts") or []}
    for row in rows:
        seen = before.get(_key(row))
        row["newVolume"] = max(0, row["volume"] - seen) if seen is not None else 0
    return rows


def _schwab_fetch(ticker: str, today: date) -> dict:
    from data.schwab_client import SchwabClient
    client = SchwabClient()
    if not getattr(client, "configured", False):
        raise Unavailable("Schwab is not connected.")
    start = datetime.combine(today, datetime.min.time())
    end = datetime.combine(today + timedelta(days=FETCH_MAX_DAYS), datetime.min.time())
    return client.get_option_chain(ticker, "ALL", strike_count=STRIKE_COUNT, from_date=start, to_date=end)


#: Who asks Schwab. A test puts a fake here; nothing else should.
fetch: Callable[[str, date], dict] = _schwab_fetch


def _disk(root: Path, ticker: str) -> Path:
    return Path(root) / "agx_flow_cache" / f"{ticker}.json.gz"


def _read_disk(root: Path, ticker: str) -> dict | None:
    try:
        with gzip.open(_disk(root, ticker), "rt", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError, EOFError):
        return None
    return doc if isinstance(doc, dict) and doc.get("schema") == SCHEMA and doc.get("symbol") == ticker else None


def _write_disk(root: Path, ticker: str, doc: dict) -> None:
    path = _disk(root, ticker)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with gzip.open(temp, "wt", encoding="utf-8") as handle:
            json.dump(doc, handle)
        temp.replace(path)
    except OSError:
        pass                                               # the memory copy still answers


def _fresh(doc: dict | None, now: datetime) -> bool:
    try:
        saved = datetime.fromisoformat(str((doc or {}).get("fetchedAt")))
        return 0 <= (now - saved).total_seconds() < CACHE_SECONDS
    except (ValueError, TypeError):
        return False


def _held(ticker: str, root: Path) -> dict | None:
    with _STATE:
        doc = _MEMORY.get(ticker)
    if doc is None:
        doc = _read_disk(root, ticker)
        if doc is not None:
            with _STATE:
                _MEMORY.setdefault(ticker, doc)
    return doc


def _failure(ticker: str) -> str | None:
    with _STATE:
        failed = _FAILED.get(ticker)
    if failed and time.monotonic() - failed[0] < FAIL_SECONDS:
        return failed[1]
    return None


def _fail(ticker: str, message: str) -> None:
    with _STATE:
        _FAILED[ticker] = (time.monotonic(), message)


def reset() -> None:
    """Forget every copy, failure, count and queued ticker (tests)."""
    with _STATE:
        _MEMORY.clear()
        _FAILED.clear()
        _RECENT.clear()
        _PENDING.clear()
        _SKIPPED.clear()


def refresh(ticker: str, root: Path, now: datetime) -> dict:
    """Read one ticker's chain from Schwab (or a copy younger than CACHE_SECONDS). Raises Unavailable, and
    remembers the failure FAIL_SECONDS."""
    held = _held(ticker, root)
    if _fresh(held, now):
        return held
    reason = _failure(ticker)
    if reason:
        raise Unavailable(reason)
    mono = time.monotonic()
    with _STATE:
        while _RECENT and mono - _RECENT[0] > 3600:
            _RECENT.popleft()
        if len(_RECENT) >= FETCHES_PER_HOUR:
            _SKIPPED[ticker] = "AGX Flow has asked Schwab for many chains this hour. It will read again later."
            raise Unavailable(_SKIPPED[ticker])
    if not _LOCK.acquire(timeout=LOCK_WAIT_SECONDS):
        with _STATE:
            _SKIPPED[ticker] = "Another chain is loading. Trying again in a minute."
        raise Unavailable(_SKIPPED[ticker])
    try:
        held = _held(ticker, root)
        if _fresh(held, now):
            return held
        with _STATE:
            _RECENT.append(time.monotonic())
        try:
            chain = fetch(ticker, now.date())
        except Unavailable as exc:
            _fail(ticker, str(exc))
            raise
        except Exception as exc:  # noqa: BLE001 - any failure is "no answer now", remembered a few minutes
            message = f"Schwab did not answer for {ticker} ({type(exc).__name__}). Trying again in a few minutes."
            _fail(ticker, message)
            raise Unavailable(message) from exc
        spot, rows = contracts_from_chain(chain, now.date())
        if not rows:
            message = f"Schwab has no options 0 to {FETCH_MAX_DAYS} days out for {ticker}."
            _fail(ticker, message)
            raise Unavailable(message)
        with_new_volume(rows, held, now)
        doc = {"schema": SCHEMA, "symbol": ticker, "fetchedAt": now.isoformat(), "spot": spot, "contracts": rows}
        with _STATE:
            _MEMORY[ticker] = doc
            _FAILED.pop(ticker, None)
            _SKIPPED.pop(ticker, None)
        _write_disk(root, ticker, doc)
        return doc
    finally:
        _LOCK.release()


def _work() -> None:
    """The one background thread: refresh queued tickers in turn until none are left."""
    global _WORKER
    while True:
        with _STATE:
            if not _PENDING:
                _WORKER = None
                return
            ticker = next(iter(_PENDING))
            root = _PENDING[ticker]
        try:
            refresh(ticker, root, datetime.now(ET))
        except Unavailable:
            pass                                           # remembered in _FAILED; the board shows it
        except Exception as exc:  # noqa: BLE001 - never let the thread die with tickers queued
            _fail(ticker, f"AGX Flow could not read {ticker} ({type(exc).__name__}).")
        finally:
            with _STATE:
                _PENDING.pop(ticker, None)


def _queue(tickers: list[str], root: Path) -> None:
    global _WORKER
    with _STATE:
        for ticker in tickers:
            _PENDING.setdefault(ticker, Path(root))
        if _WORKER is None or not _WORKER.is_alive():
            _WORKER = threading.Thread(target=_work, name="agx-flow-refresh", daemon=True)
            _WORKER.start()


def wait_idle(timeout: float = 10.0) -> bool:
    """Wait for the background thread to finish its queue (tests). True when idle."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _STATE:
            worker = _WORKER
            busy = bool(_PENDING)
        if worker is None and not busy:
            return True
        if worker is not None:
            worker.join(timeout=0.05)
        else:
            time.sleep(0.02)
    return False


def _flow_row(ticker: str, spot: float | None, row: dict) -> dict:
    premium = row["volume"] * (row.get("mark") or 0) * 100
    oi = row["oi"]
    return {
        "ticker": ticker, "spot": spot, "side": row["side"], "expiry": row["expiry"], "days": row["days"],
        "strike": row["strike"], "volume": row["volume"], "oi": oi,
        "volOi": round(row["volume"] / oi, 2) if oi > 0 else None,
        "last": row.get("last"), "bid": row.get("bid"), "ask": row.get("ask"), "premium": round(premium),
        "delta": row.get("delta"), "iv": row.get("iv"),
        "fill": fill_guess(row.get("last"), row.get("bid"), row.get("ask")), "newVolume": int(row.get("newVolume") or 0),
    }


def board(symbols: Any, root: Path, now: datetime | None = None, wait: bool = False) -> dict:
    """The flow board for up to MAX_SYMBOLS tickers. Answers at once from what is held; stale tickers are refreshed
    on one background thread (synchronously with wait=True). Raises ValueError when no ticker is valid.

    Per ticker: callPremium / putPremium / callVolume / putVolume over every contract read (not only unusual ones),
    unusualCount, cached (a fresh copy answered), pending (a read is queued), error (plain words, or None)."""
    tickers = parse_symbols(symbols)
    if not tickers:
        raise ValueError("Give at least one ticker, e.g. symbols=AAPL,NVDA.")
    now = now or datetime.now(ET)
    root = Path(root)
    if wait:
        for ticker in tickers:
            try:
                refresh(ticker, root, now)
            except Unavailable:
                pass
    else:
        stale = [t for t in tickers if not _fresh(_held(t, root), now) and not _failure(t)]
        if stale:
            _queue(stale, root)
    with _STATE:
        pending = set(_PENDING)
    summary: list[dict] = []
    rows: list[dict] = []
    for ticker in tickers:
        doc = _held(ticker, root)
        fresh = _fresh(doc, now)
        with _STATE:
            skipped = None if fresh else _SKIPPED.get(ticker)
        error = _failure(ticker) or skipped
        entry = {"ticker": ticker, "spot": None, "callPremium": 0, "putPremium": 0, "callVolume": 0, "putVolume": 0,
                 "unusualCount": 0, "cached": fresh, "pending": ticker in pending, "error": error,
                 "fetchedAt": (doc or {}).get("fetchedAt")}
        if doc:
            spot = doc.get("spot")
            entry["spot"] = spot
            for contract in doc.get("contracts") or []:
                flow = _flow_row(ticker, spot, contract)
                prefix = "call" if contract["side"] == "CALL" else "put"
                entry[f"{prefix}Premium"] += flow["premium"]
                entry[f"{prefix}Volume"] += flow["volume"]
                if is_unusual(flow["volume"], flow["oi"]):
                    entry["unusualCount"] += 1
                    rows.append(flow)
        summary.append(entry)
    rows.sort(key=lambda r: (-r["premium"], -r["volume"], r["ticker"], r["days"], r["strike"]))
    return {"asOf": now.isoformat(), "tickers": summary, "rows": rows[:TOP_ROWS], "note": NOTE}
