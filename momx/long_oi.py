"""Long-dated open interest for the Ask chat (2026-11-16): which strikes 1 to 12 months out hold the most open
interest, with each one's delta.

Ganesh 2026-11-16: "Nvda check the leap which one had more OI", then "I want any stocks 1 months to 12 months which
strikes have high oi with delta". The chat answered with the Long Term page instead, because nothing in the app keeps
a chain that far out: the saved chains (oi_chain_cache) reach about 25 days.

So this is the ONE chat answer that asks Schwab, and it is kept small on purpose:
  * one option-chain request per ticker (calls and puts, expiries 28..366 days out, STRIKE_COUNT strikes per expiry);
  * kept CACHE_SECONDS in memory and on disk (artifacts/long_oi_cache), so asking again, or another user asking,
    reads the copy;
  * one request at a time across the app; a second question while one runs waits at most WAIT_SECONDS, then says
    "busy";
  * at most FETCHES_PER_HOUR new requests an hour across every user, and a ticker that failed is not asked again for
    FAIL_SECONDS (the 2026-09 Akamai block came from an app that kept retrying Schwab).
Read-only: it never places, changes or cancels anything.
"""
from __future__ import annotations

import gzip
import json
import math
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

FETCH_MIN_DAYS = 28            # "1 month" includes the monthly expiry four weeks out
FETCH_MAX_DAYS = 366           # "12 months"
STRIKE_COUNT = 80              # strikes per expiry around the price: far enough out for the big round strikes
CACHE_SECONDS = 30 * 60
FAIL_SECONDS = 5 * 60
FETCHES_PER_HOUR = 60          # chat + the LEAP OI Scanner page together; Schwab allows 120 a MINUTE
WAIT_SECONDS = 20.0
SCHEMA = 1

_LOCK = threading.Lock()                                   # one Schwab request at a time
_STATE = threading.Lock()                                  # guards the small dicts below
_MEMORY: dict[str, tuple[float, dict]] = {}
_FAILED: dict[str, float] = {}
_RECENT: deque = deque()


class Unavailable(Exception):
    """A plain-words reason there is no chain to answer from."""


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _delta(value: Any) -> float | None:
    """Schwab sends -999 / 999 or 0 when it has no greek: that is no delta, never a zero."""
    number = _num(value)
    return number if number is not None and 0 < abs(number) <= 1 else None


def rows_from_chain(chain: Any, today: date) -> tuple[float | None, list[dict]]:
    """(underlying price, one row per contract) from a Schwab /chains payload. Bad input gives (None, [])."""
    if not isinstance(chain, dict):
        return None, []
    spot = _num(chain.get("underlyingPrice")) or _num((chain.get("underlying") or {}).get("last") if isinstance(chain.get("underlying"), dict) else None)
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
            if not isinstance(strikes, dict):
                continue
            for contracts in strikes.values():
                for item in contracts if isinstance(contracts, list) else []:
                    if not isinstance(item, dict):
                        continue
                    strike = _num(item.get("strikePrice"))
                    if not strike:
                        continue
                    rows.append({"side": side, "expiry": expiry.isoformat(), "days": (expiry - today).days, "strike": strike,
                                 "oi": int(_num(item.get("openInterest")) or 0), "delta": _delta(item.get("delta")),
                                 "bid": _num(item.get("bid")), "ask": _num(item.get("ask")),
                                 "volume": int(_num(item.get("totalVolume")) or 0)})
    return spot, rows


#: The smallest delta worth showing (Ganesh 2026-11-16: "Don't give high oi delta 0.02 like that max up to 0.1 see our
#: option chain entry"): the option chain's ENTRY band starts at 0.10, so a far-away 0.02 lottery strike never makes the list.
MIN_DELTA = 0.10


ENTRY_DELTA = 0.20


def mark_entries(rows: list[dict], spot: float | None) -> list[dict]:
    """The option chain's ENTRY on every expiry and side, the same rule as momx/option_track.py::pick_entry_strike
    (Ganesh 2026-11-16, "See the entry like that logic", his NVDA chain: ENTRY on the 172.5 call at 0.18): the
    out-of-the-money contract with a price and 0 < |delta| < 0.20 whose delta is closest to 0.20, ties to the strike
    nearest the price. Sets row["entry"] True on those and False on the rest; returns the rows."""
    best: dict[tuple, tuple] = {}
    for row in rows:
        row["entry"] = False
        delta = abs(row["delta"]) if row.get("delta") is not None else 0.0
        priced = (row.get("ask") or 0) > 0 or (row.get("bid") or 0) > 0
        if not spot or not priced or not 0 < delta < ENTRY_DELTA:
            continue
        if not ((row["strike"] > spot) if row["side"] == "CALL" else (row["strike"] < spot)):
            continue
        rank = (abs(ENTRY_DELTA - delta), abs(row["strike"] - spot))
        key = (row["side"], row["expiry"])
        if key not in best or rank < best[key][0]:
            best[key] = (rank, row)
    for _, row in best.values():
        row["entry"] = True
    return rows


def top_strikes(rows: list[dict], *, side: str, min_days: int, max_days: int, delta: float | None = None,
                band: float = 0.05, n: int = 10, min_delta: float = 0.0) -> list[dict]:
    """The `n` contracts of one side with the most open interest, expiries min_days..max_days out. With `delta`, only
    contracts whose delta (as a size, puts too) is within `band` of it. With `min_delta`, only contracts whose delta
    (as a size) is at least that; a contract with no delta is then left out. Ties: more volume, then the nearer expiry."""
    picked = [row for row in rows if row["side"] == side and min_days <= row["days"] <= max_days and row["oi"] > 0]
    if min_delta > 0:
        picked = [row for row in picked if row["delta"] is not None and abs(row["delta"]) >= min_delta - 1e-9]
    if delta is not None:
        picked = [row for row in picked if row["delta"] is not None and abs(abs(row["delta"]) - delta) <= band + 1e-9]
    picked.sort(key=lambda row: (-row["oi"], -row["volume"], row["days"], row["strike"]))
    return picked[:n]


def _schwab_fetch(ticker: str, today: date) -> dict:
    from data.schwab_client import SchwabClient
    client = SchwabClient()
    if not getattr(client, "configured", False):
        raise Unavailable("Schwab is not connected, so I cannot read option chains that far out.")
    start = datetime.combine(today + timedelta(days=FETCH_MIN_DAYS), datetime.min.time())
    end = datetime.combine(today + timedelta(days=FETCH_MAX_DAYS), datetime.min.time())
    # get_option_chain turns SPX into $SPX itself; it goes through the app's Schwab rate gate.
    return client.get_option_chain(ticker, "ALL", strike_count=STRIKE_COUNT, from_date=start, to_date=end)


#: Who asks Schwab. A test puts a fake here; nothing else should.
fetch: Callable[[str, date], dict] = _schwab_fetch


def _disk(root: Path, ticker: str) -> Path:
    return Path(root) / "long_oi_cache" / f"{ticker}.json.gz"


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


def _fresh(doc: dict | None, now: datetime, max_age: float = CACHE_SECONDS) -> bool:
    try:
        saved = datetime.fromisoformat(str((doc or {}).get("fetchedAt")))
    except ValueError:
        return False
    return 0 <= (now - saved).total_seconds() < max_age


def cached_chain(ticker: str, root: Path, now: datetime, max_age: float = CACHE_SECONDS) -> dict | None:
    """The saved copy younger than `max_age` seconds (memory, then disk), or None. Never asks Schwab."""
    ticker = str(ticker or "").upper()
    with _STATE:
        held = _MEMORY.get(ticker)
    if held and _fresh(held[1], now, max_age):
        return {**held[1], "cached": True}
    disk = _read_disk(root, ticker)
    if _fresh(disk, now, max_age):
        with _STATE:
            current = _MEMORY.get(ticker)
            if not current or not _fresh(current[1], now, max_age):
                _MEMORY[ticker] = (time.monotonic(), disk)
        return {**disk, "cached": True}
    return None


def failed_recently(ticker: str) -> bool:
    with _STATE:
        failed = _FAILED.get(str(ticker or "").upper())
    return bool(failed and time.monotonic() - failed < FAIL_SECONDS)


def reset() -> None:
    """Forget every copy, failure and count (tests)."""
    with _STATE:
        _MEMORY.clear()
        _FAILED.clear()
        _RECENT.clear()


def chain_for(ticker: str, root: Path, now: datetime) -> dict:
    """{"symbol", "fetchedAt", "spot", "rows", "cached"} for one ticker, from a copy younger than CACHE_SECONDS or one
    new Schwab request. Raises Unavailable with a plain reason."""
    ticker = str(ticker or "").upper()
    with _STATE:
        held = _MEMORY.get(ticker)
    if held and _fresh(held[1], now):
        return {**held[1], "cached": True}
    disk = _read_disk(root, ticker)
    if _fresh(disk, now):
        with _STATE:
            _MEMORY[ticker] = (time.monotonic(), disk)
        return {**disk, "cached": True}
    mono = time.monotonic()
    with _STATE:
        failed = _FAILED.get(ticker)
        if failed and mono - failed < FAIL_SECONDS:
            raise Unavailable(f"Schwab did not answer for {ticker} a few minutes ago. Try again in a few minutes.")
        while _RECENT and mono - _RECENT[0] > 3600:
            _RECENT.popleft()
        if len(_RECENT) >= FETCHES_PER_HOUR:
            raise Unavailable("The chat has asked Schwab for many long-dated chains this hour. Try again later.")
    if not _LOCK.acquire(timeout=WAIT_SECONDS):
        raise Unavailable("Another long-dated chain is loading right now. Try again in a minute.")
    try:
        with _STATE:                                       # the request that held the lock may have loaded this one
            held = _MEMORY.get(ticker)
        if held and _fresh(held[1], now):
            return {**held[1], "cached": True}
        with _STATE:
            _RECENT.append(time.monotonic())
        try:
            chain = fetch(ticker, now.date())
        except Unavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - any failure is "no answer now", remembered a few minutes
            with _STATE:
                _FAILED[ticker] = time.monotonic()
            raise Unavailable(f"Schwab did not answer for {ticker} ({type(exc).__name__}). Try again in a few minutes.") from exc
        spot, rows = rows_from_chain(chain, now.date())
        if not rows:
            with _STATE:
                _FAILED[ticker] = time.monotonic()
            raise Unavailable(f"Schwab has no options 1 to 12 months out for {ticker}.")
        doc = {"schema": SCHEMA, "symbol": ticker, "fetchedAt": now.isoformat(), "spot": spot, "rows": rows}
        with _STATE:
            _MEMORY[ticker] = (time.monotonic(), doc)
            _FAILED.pop(ticker, None)
        _write_disk(root, ticker, doc)
        return {**doc, "cached": False}
    finally:
        _LOCK.release()
