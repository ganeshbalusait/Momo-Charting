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
from zoneinfo import ZoneInfo

from momx import board_filter, long_oi

BOARD_MAX_AGE = 12 * 3600          # open interest is once a day; bid/ask in the copy are "as of" its read time
MAX_SYMBOLS = 400                  # Mag7 + his whole watchlist (about 360 on 2026-11-17)
PACE_SECONDS = 3.0                 # the background reader waits this long after each Schwab request
BOARD_FETCHES_PER_HOUR = 400       # its own allowance, separate from the chat's
BOARD_FAIL_SECONDS = 3600          # a ticker Schwab has no chain for (KITT, LIDR, PALU on 2026-11-17) is asked again
                                   # after an hour, not every 5 minutes (that kept "Reading Schwab… 366 of 369" up)
ROWS_PER_SIDE = 10                 # per ticker, before the board-wide ranking
KEEP_PER_EXPIRY = 10               # per side and expiry, for each delta floor the page offers (0.10 and 0.20)
BOARD_ROWS = 60
ENTRY_ROWS = 30
COMPACT_SCHEMA = 1
MAG7 = ("AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA")
_TICKER = re.compile(r"^[A-Z][A-Z.\-]{0,5}$")
ET = ZoneInfo("America/New_York")
NOTE = ("Open interest = how many of these option contracts people are holding right now. It updates once a day, "
        "before the market opens. Delta = roughly the chance the option ends up worth something (0.20 is about 1 in 5); "
        "only 0.10 and up shows, so long-shot strikes stay off. ENTRY = the contract our option chain marks ENTRY "
        "(delta just under 0.20). A big number does not tell you which way the stock goes: our test on 15,896 cases "
        "found it a coin flip. Facts only, not a pick.")

_REFRESH = threading.Lock()
_QUEUE: list[str] = []
_WORKER: dict[str, Any] = {"thread": None}
_COMPACT: dict[str, dict] = {}     # ticker -> compact copy (only the rows the page can show)
_FETCHED: deque = deque()          # monotonic times of this page's own Schwab requests
_GAVE_UP: dict[str, tuple[float, str]] = {}   # ticker -> (monotonic time, Schwab's reason) for BOARD_FAIL_SECONDS


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
    # allOi: every contract's open interest (not only the rows kept), on disk only, so tomorrow's change can be shown
    # for a strike that was NOT in today's top rows - the biggest new build-up is exactly that strike (review 11-17).
    all_oi = {contract_key(row): int(row.get("oi") or 0) for row in rows if int(row.get("oi") or 0) > 0}
    return {"schema": COMPACT_SCHEMA, "symbol": doc.get("symbol"), "fetchedAt": doc.get("fetchedAt"), "spot": spot,
            "rows": list(keep.values()), "allOi": all_oi}


def _compact_path(root: Path, ticker: str) -> Path:
    return Path(root) / "leap_oi_board" / f"{ticker}.json.gz"


def _read_compact_disk(root: Path, ticker: str) -> dict | None:
    try:
        with gzip.open(_compact_path(root, ticker), "rt", encoding="utf-8") as handle:
            disk = json.load(handle)
    except (OSError, ValueError, EOFError):
        return None
    if isinstance(disk, dict) and disk.get("schema") == COMPACT_SCHEMA and disk.get("symbol") == ticker:
        return disk
    return None


def contract_key(row: dict) -> str:
    """'CALL|2027-03-19|120' - one contract, the same on every day's read."""
    return f"{row.get('side')}|{str(row.get('expiry'))[:10]}|{float(row.get('strike') or 0):g}"


def _et_day(stamp: Any) -> str | None:
    try:
        return datetime.fromisoformat(str(stamp)).astimezone(ET).date().isoformat()
    except (TypeError, ValueError):
        return None


def _oi_map(doc: dict) -> tuple[dict[str, int], bool]:
    """(contract -> open interest, complete?). complete = the whole chain (allOi), so a contract missing from it had
    no open interest; otherwise only the kept rows are known."""
    whole = doc.get("allOi")
    if isinstance(whole, dict) and whole:
        return {str(k): int(v or 0) for k, v in whole.items()}, True
    return {contract_key(r): int(r.get("oi") or 0) for r in doc.get("rows") or [] if isinstance(r, dict)}, False


def with_previous(old: dict | None, new: dict) -> dict:
    """Open interest change since the previous posting (2026-11-17 "See any features we can add" -> "All six").

    The OCC posts open interest once a trading-day morning, so the new copy keeps, as prevOi, the open interest of the
    last read on an EARLIER ET day - but only when the numbers actually moved since then. A weekend, holiday or
    after-midnight read sees the same numbers again and keeps the last real change (review 2026-11-17: otherwise every
    row read "no change" all weekend). A later read the same day carries prevOi forward. Contracts with no open
    interest yesterday count from 0 when the whole chain was kept, so a brand-new build-up shows in full.
    No earlier day read yet -> no prevOi (the page shows nothing rather than a made-up change)."""
    new_day = _et_day(new.get("fetchedAt"))
    old_day = _et_day((old or {}).get("fetchedAt"))
    if not new_day or not old_day or old_day > new_day:
        return new
    old_prev = old.get("prevOi") if isinstance(old.get("prevOi"), dict) and old.get("prevOi") else None
    carry = {**new, "prevOi": old_prev, "prevAt": old.get("prevAt")} if old_prev else new
    if old_day == new_day:
        return carry
    old_oi, complete = _oi_map(old)
    new_oi = {contract_key(r): int(r.get("oi") or 0) for r in new.get("rows") or [] if isinstance(r, dict)}
    seen = [k for k in new_oi if k in old_oi]
    if old_prev and seen and all(old_oi[k] == new_oi[k] for k in seen):
        return carry                                       # nothing posted since: keep the last real change
    prev = {k: old_oi.get(k, 0) for k in new_oi if complete or k in old_oi}
    return {**new, "prevOi": prev, "prevAt": old.get("fetchedAt")} if prev else new


def _slim(doc: dict) -> dict:
    """The copy kept in memory: without allOi (the whole chain's open interest stays on disk only)."""
    return {k: v for k, v in doc.items() if k != "allOi"} if "allOi" in doc else doc


def _save_compact(root: Path, ticker: str, doc: dict) -> dict:
    """Keep and write this ticker's compact copy; returns it (without allOi) with the previous day's open interest
    attached (with_previous). The earlier copy comes from disk first, since only the disk copy has allOi."""
    old = _read_compact_disk(root, ticker)
    if old is None:
        with _REFRESH:
            old = _COMPACT.get(ticker)
    if old is not doc:
        doc = with_previous(old, doc)
    with _REFRESH:
        _COMPACT[ticker] = _slim(doc)
    path = _compact_path(root, ticker)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        with gzip.open(temp, "wt", encoding="utf-8") as handle:
            json.dump(doc, handle)
        temp.replace(path)
    except OSError:
        pass
    return _slim(doc)


def held(ticker: str, root: Path, now: datetime) -> dict | None:
    """This ticker's compact copy younger than BOARD_MAX_AGE: memory, its small file, or the full long-dated chain on
    disk (the chat's copy). Never asks Schwab."""
    with _REFRESH:
        doc = _COMPACT.get(ticker)
    if long_oi._fresh(doc, now, BOARD_MAX_AGE):
        return doc
    disk = _read_compact_disk(root, ticker)
    if disk is not None and long_oi._fresh(disk, now, BOARD_MAX_AGE):
        disk = _slim(disk)
        with _REFRESH:
            _COMPACT[ticker] = disk
        return disk
    full = long_oi._read_disk(root, ticker)
    if long_oi._fresh(full, now, BOARD_MAX_AGE):
        return _save_compact(root, ticker, compact(full))
    return None


def ticker_rows(doc: dict, *, min_days: int, max_days: int, side: str = "both", min_delta: float = long_oi.MIN_DELTA,
                entry_only: bool = False) -> list[dict]:
    """One ticker's biggest-open-interest contracts in the range (ENTRY already marked by compact()), each tagged with
    ticker/spot/away."""
    spot = doc.get("spot")
    rows = doc.get("rows") or []
    prev = doc.get("prevOi") if isinstance(doc.get("prevOi"), dict) else {}
    sides =[s for s in ("CALL", "PUT") if side == "both" or (side == "calls" and s == "CALL") or (side == "puts" and s == "PUT")]
    out: list[dict] = []
    for s in sides:
        pool = [row for row in rows if row.get("entry")] if entry_only else rows
        for row in long_oi.top_strikes(pool, side=s, min_days=min_days, max_days=max_days, n=ROWS_PER_SIDE,
                                       min_delta=min_delta):
            before = prev.get(contract_key(row))
            out.append({**row, "ticker": doc.get("symbol"), "spot": spot, "away": _away(row["strike"], spot),
                        "oiChange": (int(row["oi"]) - int(before)) if before is not None else None,
                        "oiChangeSince": doc.get("prevAt") if before is not None else None})
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
        except long_oi.Unavailable as exc:
            _give_up(ticker, str(exc))                     # the board shows the reason and stops asking for an hour
        except Exception as exc:  # noqa: BLE001 - one bad ticker never stops the rest
            _give_up(ticker, f"Schwab did not answer for {ticker} ({type(exc).__name__}).")
        if asked and PACE_SECONDS > 0:
            time.sleep(PACE_SECONDS)                       # gentle on Schwab: charts and chains share its limit


def _schedule(tickers: list[str], root: Path, front: bool = False) -> None:
    """Queue tickers for the background reader. front=True (an open page, e.g. a ticker search, 2026-11-17 "Add search
    ticker also") puts them ahead of the warm-up's long queue, so the trader is not left waiting behind 260 names."""
    with _REFRESH:
        if front:
            ahead = list(dict.fromkeys(tickers))
            _QUEUE[:] = ahead + [t for t in _QUEUE if t not in ahead]
        for ticker in tickers:
            if ticker not in _QUEUE:
                _QUEUE.append(ticker)
        if _WORKER["thread"] is None and _QUEUE:
            thread = threading.Thread(target=_refresh_loop, args=(Path(root),), name="leap-oi-board", daemon=True)
            _WORKER["thread"] = thread
            thread.start()


def _give_up(ticker: str, reason: str) -> None:
    with _REFRESH:
        _GAVE_UP[ticker] = (time.monotonic(), reason)


def _given_up(ticker: str) -> str | None:
    with _REFRESH:
        held = _GAVE_UP.get(ticker)
        if held and time.monotonic() - held[0] < BOARD_FAIL_SECONDS:
            return held[1]
        _GAVE_UP.pop(ticker, None)
        return None


WARM_EVERY_SECONDS = 5 * 60
_WARMER: dict[str, Any] = {"thread": None}


def warm(symbols: Any, root: Path, now: datetime | None = None) -> int:
    """Queue every ticker with no copy younger than BOARD_MAX_AGE (and no failure in the last hour) for the background
    reader, without building a board. Returns how many were queued."""
    now = now or datetime.now().astimezone()
    stocks, _ = board_filter.drop_etfs(clean_symbols(symbols))
    missing = [t for t in stocks
               if held(t, root, now) is None and _given_up(t) is None and not long_oi.failed_recently(t)]
    if missing:
        _schedule(missing, root)
    return len(missing)


def start_warmer(get_symbols: Any, root: Path) -> None:
    """Keep Mag7 + the whole watchlist read in the background (2026-11-17 "Still I don't see full loading"), so the page
    is full when it opens instead of reading for ~20 minutes then. Open interest is once a day, so each ticker is read
    about once every 12 hours."""
    def loop() -> None:
        while True:
            try:
                warm(get_symbols(), root)
            except Exception:  # noqa: BLE001 - the warm-up never stops; the page still reads on demand
                pass
            time.sleep(WARM_EVERY_SECONDS)
    with _REFRESH:
        if _WARMER["thread"] is not None:
            return
        _WARMER["thread"] = threading.Thread(target=loop, name="leap-oi-warmer", daemon=True)
        _WARMER["thread"].start()


def pending() -> list[str]:
    with _REFRESH:
        return list(_QUEUE)


def reset() -> None:
    with _REFRESH:
        _QUEUE.clear()
        _COMPACT.clear()
        _FETCHED.clear()
        _GAVE_UP.clear()


def with_flow(rows: list[dict], root: Path) -> list[dict]:
    """Put today's trades on each LEAP row (2026-11-20, "If I see leap oi scanner like INTC I don't know anything like
    buyers or seller ... then I have to search agx flow ... I feel little confusion"): the SAME contract's numbers from
    AGX Flow's latest read (momx/agx_flow.py, which reads 12 months out), so both pages agree and the trader does not
    have to switch. row["flow"] = {volume, premium, fill ("ask"/"bid"/"mid"), unusual, at} or nothing when AGX Flow
    has not read that ticker or the contract did not trade. Never asks Schwab."""
    from momx import agx_flow
    by_ticker: dict[str, dict[str, dict]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "")
        if ticker not in by_ticker:
            doc = agx_flow._held(ticker, Path(root)) if ticker else None
            contracts = (doc or {}).get("contracts") or []
            by_ticker[ticker] = {contract_key(c): {**c, "_at": (doc or {}).get("fetchedAt")} for c in contracts if isinstance(c, dict)}
        hit = by_ticker[ticker].get(contract_key(row))
        if not hit or int(hit.get("volume") or 0) <= 0:
            continue
        flow = agx_flow._flow_row(ticker, None, hit)
        row["flow"] = {"volume": flow["volume"], "premium": flow["premium"], "fill": flow["fill"],
                       "unusual": agx_flow.is_unusual(flow["volume"], flow["oi"]), "at": hit.get("_at")}
    return rows


def board(symbols: Any, root: Path, now: datetime | None = None, *, months_from: int = 3, months_to: int = 12,
          side: str = "both", min_delta: float = long_oi.MIN_DELTA, entry_only: bool = False, wait: bool = False) -> dict:
    """The page's whole answer. Reads saved copies only; tickers without one are queued for the background reader
    (or read right here with wait=True, for tests)."""
    now = now or datetime.now().astimezone()
    tickers, excluded = board_filter.drop_etfs(clean_symbols(symbols))   # "No need spy, qqq ... and etf" (2026-11-17)
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
                doc = _save_compact(root, ticker, compact(long_oi.chain_for(ticker, root, now, count=False, keep=False)))
            except long_oi.Unavailable as exc:
                error = str(exc)
                _give_up(ticker, error)
        if doc is None:
            if error is None:
                error = _given_up(ticker)
            if error is None and long_oi.failed_recently(ticker):
                error = f"Schwab did not answer for {ticker} a few minutes ago."
            if error is None:
                missing.append(ticker)
            status.append({"ticker": ticker, "state": "error" if error else "pending", "error": error})
            continue
        if board_filter.too_small(doc.get("spot")):        # "... and small stocks": under $10 left off the board
            excluded.append(board_filter.small(ticker))
            continue
        found = ticker_rows(doc, min_days=min_days, max_days=max_days, side=side, min_delta=min_delta, entry_only=entry_only)
        rows.extend(found)
        calls = sum(r["oi"] for r in found if r["side"] == "CALL")
        puts = sum(r["oi"] for r in found if r["side"] == "PUT")
        status.append({"ticker": ticker, "state": "ready", "spot": doc.get("spot"), "readAt": doc.get("fetchedAt"),
                       "callOi": calls, "putOi": puts, "rows": len(found)})
        by_ticker.append(_ticker_summary(ticker, doc.get("spot"), found))
    if missing:
        _schedule(missing, root, front=True)               # what is on screen is read before the warm-up's queue
    with_flow(rows, root)
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
        "excluded": excluded, "minPrice": board_filter.MIN_PRICE,
        "note": NOTE,
    }
