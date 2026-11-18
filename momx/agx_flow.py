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

from momx import board_filter
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
FETCH_MAX_DAYS = 45
STRIKE_COUNT = 40
CACHE_SECONDS = 5 * 60
FAIL_SECONDS = 5 * 60
NO_CHAIN_SECONDS = 3600       # a ticker Schwab has no options for (KITT, LIDR) is asked again after an hour, not every
                              # 5 minutes: that kept 'Reading Schwab... (260 of 261)' on the page (2026-11-17)
FETCHES_PER_HOUR = 1500       # whole watchlist (~370) about every 12 min in market hours; Schwab allows 120 a MINUTE
PACE_SECONDS = 1.5            # each background reader waits this long after each Schwab request
READERS = 2                   # 2026-11-17 "Still I don't see full loading": one reader took ~36 min for the watchlist;
                              # two readers 1.5 s apart read it in ~12 min at ~30 requests a minute (a quarter of Schwab's)
WARM_EVERY_SECONDS = 60       # the warm-up looks for stale watchlist tickers this often
LOCK_WAIT_SECONDS = 30.0
MAX_SYMBOLS = 400             # Mag7 + SPY/QQQ + his whole watchlist (2026-11-17: "what about other tickers")
MIN_VOLUME = 500
TOP_ROWS = 60
SCHEMA = 1

NOTE = ("Volume and prices from Schwab's option chain. 'Bought/Sold' is a guess from where the last trade printed vs "
        "the bid/ask. Unusual = today's volume above open interest. Facts only, not a direction signal.")

_SYMBOL = re.compile(r"^[A-Z][A-Z.\-]{0,5}$")

_LOCK = threading.BoundedSemaphore(READERS)               # at most READERS Schwab requests at a time
_STATE = threading.Lock()                                  # guards the small dicts below
_MEMORY: dict[str, dict] = {}
_FAILED: dict[str, tuple[float, str, float]] = {}   # ticker -> (when, why, how long it is remembered)
_RECENT: deque = deque()
_PENDING: dict[str, Path] = {}                             # ticker -> root, waiting for the background thread
_SKIPPED: dict[str, str] = {}                              # ticker -> why the last refresh did not ask (hourly cap, busy)
_WORKERS: list[threading.Thread] = []
_TAKEN: set[str] = set()                                   # tickers a reader is reading right now
_WARMER: dict[str, Any] = {"thread": None}


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


def market_open(now: datetime) -> bool:
    """Regular session, 9:30-16:00 ET on a weekday that is not a market holiday (review 2026-11-17: a holiday counted
    as open let the phone alert push the previous session's volume as new)."""
    et = now.astimezone(ET)
    if et.weekday() >= 5 or not (9, 30) <= (et.hour, et.minute) < (16, 0):
        return False
    try:
        from momx.chart_levels import MARKET_HOLIDAYS
    except Exception:  # noqa: BLE001 - without the calendar, a weekday counts as open
        return True
    return et.date().isoformat() not in MARKET_HOLIDAYS


def read_this_session(doc: dict | None, now: datetime) -> bool:
    """Was this copy read during the session that is open now? (Hot right now and the phone alert use only these.)"""
    if not market_open(now):
        return False
    try:
        at = datetime.fromisoformat(str((doc or {}).get("fetchedAt"))).astimezone(ET)
    except (TypeError, ValueError):
        return False
    return at.date() == now.astimezone(ET).date() and market_open(at)


def last_close(now: datetime) -> datetime:
    """The most recent 16:00 ET close at or before now, on a weekday."""
    et = now.astimezone(ET)
    day = et.date() if (et.hour, et.minute) >= (16, 0) else et.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return datetime(day.year, day.month, day.day, 16, 0, tzinfo=ET)


def _fresh(doc: dict | None, now: datetime) -> bool:
    """Younger than CACHE_SECONDS in market hours. Outside them, a read taken after the last close is fresh until the
    next open: option volume does not move then, so the whole watchlist is not read again all night."""
    try:
        saved = datetime.fromisoformat(str((doc or {}).get("fetchedAt")))
        age = (now - saved).total_seconds()
        if age < 0:
            return False
        if not market_open(now):
            return saved >= last_close(now)                # one read after the close gets the day's final volume
        return age < CACHE_SECONDS
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
    if failed and time.monotonic() - failed[0] < failed[2]:
        return failed[1]
    return None


def _fail(ticker: str, message: str, seconds: float | None = None) -> None:
    with _STATE:
        _FAILED[ticker] = (time.monotonic(), message, FAIL_SECONDS if seconds is None else seconds)


def reset() -> None:
    """Forget every copy, failure, count and queued ticker (tests)."""
    with _STATE:
        _MEMORY.clear()
        _FAILED.clear()
        _RECENT.clear()
        _PENDING.clear()
        _SKIPPED.clear()
        _TAKEN.clear()


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
    if not _LOCK.acquire(timeout=LOCK_WAIT_SECONDS):       # noqa: SIM115 - released in finally
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
            _fail(ticker, message, NO_CHAIN_SECONDS)
            raise Unavailable(message)
        with_new_volume(rows, held, now)
        rows = [row for row in rows if row["volume"] > 0]  # untraded contracts add nothing; 400 tickers stay light
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
    """One of READERS background threads: take the next queued ticker nobody else is reading, until none are left."""
    me = threading.current_thread()
    while True:
        with _STATE:
            ticker = next((t for t in _PENDING if t not in _TAKEN), None)
            if ticker is None:
                if me in _WORKERS:
                    _WORKERS.remove(me)
                return
            _TAKEN.add(ticker)
            root = _PENDING[ticker]
        asked = False
        try:
            now = datetime.now(ET)
            asked = not _fresh(_held(ticker, root), now) and not _failure(ticker)
            refresh(ticker, root, now)
        except Unavailable:
            pass                                           # remembered in _FAILED; the board shows it
        except Exception as exc:  # noqa: BLE001 - never let the thread die with tickers queued
            _fail(ticker, f"AGX Flow could not read {ticker} ({type(exc).__name__}).")
        finally:
            with _STATE:
                _PENDING.pop(ticker, None)
                _TAKEN.discard(ticker)
        if asked and PACE_SECONDS > 0:
            time.sleep(PACE_SECONDS)                       # gentle on Schwab: charts and chains share its limit


def _queue(tickers: list[str], root: Path, front: bool = False) -> None:
    """Queue tickers for the readers. front=True (an open page, e.g. a ticker search, 2026-11-17 "Add search ticker
    also") puts them ahead of the warm-up's long queue; readers take _PENDING in order."""
    with _STATE:
        if front:
            waiting = dict(_PENDING)
            _PENDING.clear()
            for ticker in tickers:
                _PENDING.setdefault(ticker, waiting.pop(ticker, Path(root)))
            _PENDING.update(waiting)
        for ticker in tickers:
            _PENDING.setdefault(ticker, Path(root))
        _WORKERS[:] = [w for w in _WORKERS if w.is_alive()]
        want = min(READERS, len(_PENDING) - len(_TAKEN))
        while len(_WORKERS) < want:
            worker = threading.Thread(target=_work, name=f"agx-flow-reader-{len(_WORKERS) + 1}", daemon=True)
            _WORKERS.append(worker)
            worker.start()


def warm(symbols: Any, root: Path, now: datetime | None = None) -> int:
    """Queue every stale ticker for the background readers without building a board. Returns how many were queued."""
    tickers, _ = board_filter.drop_etfs(parse_symbols(symbols))
    now = now or datetime.now(ET)
    stale = [t for t in tickers if _wanted(t, root, now)]
    if stale:
        _queue(stale, Path(root))
    return len(stale)


def start_warmer(get_symbols: Callable[[], list[str]], root: Path) -> None:
    """Keep the whole watchlist read in the background, so AGX Flow's Watchlist board is full when it opens (2026-11-17
    "Still I don't see full loading"). In market hours every ticker is re-read about every 12 minutes; after the close
    each is read once and then left alone until the next open (see _fresh)."""
    def loop() -> None:
        while True:
            try:
                warm(get_symbols(), root)
            except Exception:  # noqa: BLE001 - the warm-up never stops; the page still reads on demand
                pass
            time.sleep(WARM_EVERY_SECONDS)
    with _STATE:
        if _WARMER["thread"] is not None:
            return
        _WARMER["thread"] = threading.Thread(target=loop, name="agx-flow-warmer", daemon=True)
        _WARMER["thread"].start()


def wait_idle(timeout: float = 10.0) -> bool:
    """Wait for the background readers to finish their queue (tests). True when idle."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _STATE:
            workers = [w for w in _WORKERS if w.is_alive()]
            busy = bool(_PENDING)
        if not workers and not busy:
            return True
        if workers:
            workers[0].join(timeout=0.05)
        else:
            time.sleep(0.02)
    return False


def _wanted(ticker: str, root: Path, now: datetime) -> bool:
    """Should the background readers read this ticker now? Not when fresh, failed recently, or read TODAY and found
    under $10 (it is left off the board; a new day checks the price again)."""
    doc = _held(ticker, root)
    if _fresh(doc, now) or _failure(ticker):
        return False
    if doc and board_filter.too_small(doc.get("spot")):
        try:
            return datetime.fromisoformat(str(doc.get("fetchedAt"))).astimezone(ET).date() != now.astimezone(ET).date()
        except (ValueError, TypeError):
            return True
    return True


EXPIRIES = ("all", "week", "next", "month")   # 2026-11-17 "All six": This week / Next week / 30 days
ORDERS = ("premium", "hot")                     # biggest premium today / most volume added since the last read


def expiry_window(bucket: str, today: date) -> tuple[date | None, date | None]:
    """Which expiries a bucket keeps, as (after, through), both ET dates; None = no edge.
    week = through this week's Friday (on a weekend, the coming Friday; a Thursday holiday expiry is still inside),
    next = after that Friday through the next one, month = through 30 days out, all = everything read (0-45 days)."""
    friday = today + timedelta(days=(4 - today.weekday()) % 7)
    if bucket == "week":
        return None, friday
    if bucket == "next":
        return friday, friday + timedelta(days=7)
    if bucket == "month":
        return None, today + timedelta(days=30)
    return None, None


def in_window(expiry: Any, window: tuple[date | None, date | None]) -> bool:
    after, through = window
    if after is None and through is None:
        return True
    try:
        day = date.fromisoformat(str(expiry)[:10])
    except ValueError:
        return False
    return (after is None or day > after) and (through is None or day <= through)


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
        "newPremium": round(int(row.get("newVolume") or 0) * (row.get("mark") or 0) * 100),
    }


def board(symbols: Any, root: Path, now: datetime | None = None, wait: bool = False, *, expiry: str = "all",
          order: str = "premium") -> dict:
    """The flow board for up to MAX_SYMBOLS tickers. Answers at once from what is held; stale tickers are refreshed
    on one background thread (synchronously with wait=True). Raises ValueError when no ticker is valid.

    Per ticker: callPremium / putPremium / callVolume / putVolume over every contract read (not only unusual ones),
    unusualCount, cached (a fresh copy answered), pending (a read is queued), error (plain words, or None).
    expiry (EXPIRIES) keeps only contracts in that window, totals included; order="hot" ranks by volume added since the
    last read (newPremium) and keeps only contracts that traded since then."""
    expiry = expiry if expiry in EXPIRIES else "all"
    order = order if order in ORDERS else "premium"
    tickers = parse_symbols(symbols)
    if not tickers:
        raise ValueError("Give at least one ticker, e.g. symbols=AAPL,NVDA.")
    tickers, excluded = board_filter.drop_etfs(tickers)    # "No need spy, qqq ... and etf" (2026-11-17)
    if not tickers:
        raise ValueError("AGX Flow shows stocks only, and every ticker given is an ETF.")
    now = now or datetime.now(ET)
    root = Path(root)
    window = expiry_window(expiry, now.astimezone(ET).date())
    if wait:
        for ticker in tickers:
            try:
                refresh(ticker, root, now)
            except Unavailable:
                pass
    else:
        stale = [t for t in tickers if _wanted(t, root, now)]
        if stale:
            _queue(stale, root, front=True)                # what is on screen is read before the warm-up's queue
    with _STATE:
        pending = set(_PENDING)
    summary: list[dict] = []
    rows: list[dict] = []
    for ticker in tickers:
        doc = _held(ticker, root)
        if doc and board_filter.too_small(doc.get("spot")):  # "... and small stocks": under $10 left off the board
            excluded.append(board_filter.small(ticker))
            continue
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
            live = read_this_session(doc, now)
            for contract in doc.get("contracts") or []:
                if not in_window(contract.get("expiry"), window):
                    continue
                flow = _flow_row(ticker, spot, contract)
                prefix = "call" if contract["side"] == "CALL" else "put"
                entry[f"{prefix}Premium"] += flow["premium"]
                entry[f"{prefix}Volume"] += flow["volume"]
                if is_unusual(flow["volume"], flow["oi"]):
                    entry["unusualCount"] += 1
                    if order != "hot" or live:             # yesterday's after-close volume is never "hot right now"
                        rows.append(flow)
        summary.append(entry)
    if order == "hot":
        rows = [r for r in rows if r["newVolume"] > 0]
        rows.sort(key=lambda r: (-r["newPremium"], -r["newVolume"], -r["premium"], r["ticker"], r["days"], r["strike"]))
    else:
        rows.sort(key=lambda r: (-r["premium"], -r["volume"], r["ticker"], r["days"], r["strike"]))
    return {"asOf": now.isoformat(), "tickers": summary, "rows": rows[:TOP_ROWS], "note": NOTE,
            "excluded": excluded, "minPrice": board_filter.MIN_PRICE, "expiry": expiry, "order": order,
            "marketOpen": market_open(now)}
