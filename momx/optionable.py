"""Which tickers really have listed options - read from real option chains.

2026-09-26, Ganesh: "in scanner only show which ticker have option only.
Don't scan which have no option - no guess work." He trades short-dated OTM
calls; a ticker with no options is a ticker he cannot trade, and the back-test
that day found 103 of the 520 names the rules had fired on had none (SVRN,
PCLA, RETO, JAGX ... - several of the "big winners").

THE SOURCE is the same option chain the ticker card and OI Finder draw
(api_server ``/api/oi-finder-chain``, Schwab). Nothing here is hand-edited:
the curated weeklies table this replaces (momx/flags.py) was wrong for 157 of
358 Watchlist names. The first store was seeded from a full census of 520
chains on 2026-09-26; 5 of its answers were spot-checked against Yahoo
Finance and all 5 agreed.

WHAT ONE CHAIN READ PROVES (api_server.py, mapped 2026-09-26):
  * rows in ``selectedExpiryChainRows``      -> HAS options. Expiries come from
    those rows only - the top-level ``expiries`` field under-reports (NVDA 4
    of 8). Payloads flagged ``frontExpiryOnly`` / ``mobileFast`` hold one
    expiry and are not read for weeklies.
  * ``live`` false, no ``warming``, ``errors == []``, a Schwab ``source``
    -> Schwab answered and listed NO contracts in the next 31 days.
  * anything else (warming stub, errors, network failure, Schwab unhealthy)
    -> UNKNOWN. A Schwab outage, an Akamai block, a dead token and an invalid
    symbol all look like "warming forever"; reading that as "no options"
    would hide every ticker during an outage. Unknown is never stored as no.
The request window is 0-31 days (api_server OI_FINDER_MAX_DAYS_TO_EXPIRATION)
and monthlies can be 35 days apart, so on the few days when the NEXT monthly
expiry is more than 31 days away a monthly-only name has no rows at all. On
those GAP DAYS (a calendar rule, :func:`window_gap`) an empty read is unknown,
never "no". A "no" must also be a FRESH build - a stale/disk/refreshing copy
can be days old (an empty build cached before a listing). YES IS STICKY: a
symbol once seen with options is never downgraded by an empty read (listings
do not vanish); a "no" is re-read after an hour, then daily, off-hours.

WEEKLY = two or more distinct expiries within the next 21 days. Checked
against all 417 optionable chains of the census: 334 weekly / 83 monthly-only,
0 disagreements with the calendar reading (a non-monthly Friday listed).

LOAD. One chain build costs api_server 2 Schwab requests and 2-15 s (60-199 s
at the open) of a 4-worker pool it shares with his charts (the CPU-saturation
incident, momx/flags.py). So: ONE checker-started build in flight at a time -
the checker keeps polling a symbol until api_server's build settles (up to
SETTLE_SECONDS) before it starts the next; only unknown symbols are read
during market hours; the periodic re-checks run off-hours; nothing runs while
Schwab's transport is blocked or cooling down; and calls go straight to :3002
with ``X-AGX-Background: 1`` so they do not pause api_server's own background
scanning the way a :3001 call does. (Review 2026-09-26: the health gate reads
the TRANSPORT, not the market_data profile's "healthy" - chains fall back to
the trading profile, so a refused market_data key must not stop the checker.)

Never raises into a build. No thread starts at import.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from config import ARTIFACTS_DIR

ET = ZoneInfo("America/New_York")
STORE_NAME = "momx_optionable.json"
CHAIN_URL = os.environ.get("AGX_MOMX_OPTIONABLE_URL", "http://127.0.0.1:3002/api/oi-finder-chain?symbol=")
STATUS_URL = os.environ.get("AGX_MOMX_OPTIONABLE_STATUS_URL", "http://127.0.0.1:3002/api/schwab/status")
HEADERS = {"X-AGX-Background": "1"}

WEEKLY_WINDOW_DAYS = 21
WEEKLY_MIN_EXPIRIES = 2
#: A cold symbol answers with a warming stub first; the chain lands in ~2-20 s
#: (IBM took 17 s on 2026-09-02), 60-199 s at the open. The checker waits for
#: it to SETTLE so it never has two builds of its own in flight.
WARM_POLL_SECONDS = 4.0
SETTLE_SECONDS = 180.0
#: A monthly-only name has no expiry within the 31-day request window when
#: the next monthly is further out than this.
REQUEST_WINDOW_DAYS = 31
FETCH_GAP_MARKET = 8.0       # seconds between two symbols, 09:00-16:30 ET
FETCH_GAP_OFF_HOURS = 2.0
UNHEALTHY_PAUSE = 60.0
STATUS_TTL = 60.0
YES_RECHECK_SECONDS = 7 * 86400
NO_CONFIRM_SECONDS = 3600    # a first "no" is re-read after an hour
NO_RECHECK_SECONDS = 86400
MAX_TRIES_PER_DAY = 6        # an invalid symbol answers "warming" forever


def store_path() -> Path:
    """Same root as the other scanner books (service.grade_dir), so the test
    suite's AGX_MOMX_GRADE_DIR keeps fixtures out of the real artifacts."""
    override = os.environ.get("AGX_MOMX_GRADE_DIR", "").strip()
    return (Path(override) if override else ARTIFACTS_DIR) / STORE_NAME


def _m(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _norm(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def _today() -> date:
    return datetime.now(ET).date()


def weekly_from_expiries(expiries: Iterable[str], today: date) -> bool:
    near = set()
    for raw in expiries or ():
        try:
            day = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        if 0 <= (day - today).days <= WEEKLY_WINDOW_DAYS:
            near.add(day)
    return len(near) >= WEEKLY_MIN_EXPIRIES


def monthly_expiry(year: int, month: int) -> date:
    """The standard monthly: the third Friday."""
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def window_gap(today: date) -> bool:
    """True on the days a monthly-only chain can show NO rows in the 0-31 day
    request window: the next monthly expiry is more than 31 days away."""
    nxt = monthly_expiry(today.year, today.month)
    if nxt < today:
        year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        nxt = monthly_expiry(year, month)
    return (nxt - today).days > REQUEST_WINDOW_DAYS


def settled(payload: Any) -> bool:
    """api_server has finished building this symbol (nothing of ours running)."""
    chain = _m(payload)
    return bool(chain) and not chain.get("warming") and not chain.get("refreshing")


def classify(payload: Any, today: date) -> dict | None:
    """One chain read -> {"hasOptions": True, "expiries", "weeklies"} |
    {"hasOptions": False} | None (unknown). See the module docstring."""
    chain = _m(payload)
    if not chain:
        return None
    rows = chain.get("selectedExpiryChainRows") or []
    if isinstance(rows, list) and rows:
        if chain.get("frontExpiryOnly") or chain.get("mobileFast"):
            return {"hasOptions": True, "expiries": None, "weeklies": None}
        expiries = sorted({str(_m(r).get("expiry") or "")[:10] for r in rows if _m(r).get("expiry")})
        return {"hasOptions": True, "expiries": expiries, "weeklies": weekly_from_expiries(expiries, today)}
    if (chain.get("live") is False and not chain.get("warming") and chain.get("errors") == []
            and str(chain.get("source") or "").startswith("Schwab")
            and not (chain.get("callRows") or chain.get("putRows"))
            # a FRESH build only: a stale / disk / refreshing copy can be days old
            and not chain.get("stale") and not chain.get("diskCached") and not chain.get("refreshing")
            # a monthly-only chain can be empty on a gap day - unknown, not "no"
            and not window_gap(today)):
        return {"hasOptions": False}
    return None


def _http_json(url: str, timeout: float = 40.0) -> Any:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _default_fetch(symbol: str) -> Any:
    return _http_json(CHAIN_URL + symbol)


def _default_healthy() -> bool:
    """Schwab's TRANSPORT is usable. Not the top-level "healthy" - that is the
    market_data profile alone, and chains fall back to the trading profile."""
    status = _m(_http_json(STATUS_URL, timeout=10.0))
    transport = _m(status.get("transport"))
    return (not status.get("transportBlocked") and transport.get("coolingDown") is not True
            and transport.get("reachable") is not False)


def _market_hours(now: datetime) -> bool:
    local = now.astimezone(ET)
    if local.weekday() >= 5:
        return False
    minute = local.hour * 60 + local.minute
    return 9 * 60 <= minute <= 16 * 60 + 30


# --------------------------------------------------------------------------
# The read-only table (for flags.build_badge's W marker)
# --------------------------------------------------------------------------

_TABLE_LOCK = threading.Lock()
_TABLE: dict = {"key": None, "weekly": {}}


def _read_store(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    symbols = _m(doc).get("symbols")
    return {k: dict(v) for k, v in symbols.items() if isinstance(v, Mapping)} if isinstance(symbols, Mapping) else {}


def weekly_table() -> dict:
    """{SYMBOL: True | False | None} read from the store on disk, re-read only
    when the file changes. A missing / corrupt store is an EMPTY table, which
    flags reads as unknown for every symbol - never as "no weeklies"."""
    path = store_path()
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return {}
    with _TABLE_LOCK:
        if _TABLE["key"] == key:
            return _TABLE["weekly"]
    weekly = {}
    for symbol, entry in _read_store(path).items():
        has = entry.get("hasOptions")
        weekly[symbol] = False if has is False else (entry.get("weeklies") if has is True else None)
    with _TABLE_LOCK:
        _TABLE["key"], _TABLE["weekly"] = key, weekly
    return weekly


# --------------------------------------------------------------------------
# The book (worker process): gate + background checker
# --------------------------------------------------------------------------

class OptionableBook:
    def __init__(self, path: Path | None = None, *, fetch: Callable[[str], Any] | None = None,
                 healthy: Callable[[], bool] | None = None, background: bool = True,
                 clock: Callable[[], float] = time.time, sleep: Callable[[float], None] = time.sleep,
                 now: Callable[[], datetime] | None = None) -> None:
        self._path_override = Path(path) if path is not None else None
        self._fetch = fetch or _default_fetch
        self._healthy_fn = healthy or _default_healthy
        self._background = background
        self._clock = clock
        self._sleep = sleep
        self._now = now or (lambda: datetime.now(ET))
        self._lock = threading.Lock()
        self._entries: dict[str, dict] | None = None     # lazy: no I/O at import
        self._tries: dict[str, list] = {}                 # symbol -> [day, count]
        self._queue: queue.Queue = queue.Queue()
        self._urgent: queue.Queue = queue.Queue()
        self._queued: set[str] = set()
        self._thread: threading.Thread | None = None
        self._health: tuple[float, bool] | None = None

    # ---------------------------------------------------------------- store

    def path(self) -> Path:
        return self._path_override or store_path()

    def _load(self) -> dict:
        if self._entries is None:
            self._entries = _read_store(self.path())
        return self._entries

    def _save(self) -> None:
        path = self.path()
        doc = {"source": "api_server /api/oi-finder-chain (Schwab), momx/optionable.py",
               "savedAt": self._now().replace(microsecond=0).isoformat(), "symbols": self._entries or {}}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
            for attempt in range(5):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:   # Windows: a reader has it open
                    time.sleep(0.05 * (attempt + 1))
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

    # ---------------------------------------------------------------- reads

    def status(self, symbol: Any) -> bool | None:
        with self._lock:
            entry = self._load().get(_norm(symbol))
        return None if entry is None else entry.get("hasOptions")

    def split(self, symbols: Iterable[Any], *, request: bool = True) -> tuple[list[str], list[str], list[str]]:
        """(kept, hidden, pending) in the list's own order: kept = confirmed
        options; hidden = confirmed none; pending = not known yet (held out,
        and queued first). Due re-checks are queued too (they run off-hours)."""
        kept, hidden, pending, due = [], [], [], []
        now = self._clock()
        with self._lock:
            entries = self._load()
            for raw in symbols or ():
                symbol = _norm(raw)
                if not symbol:
                    continue
                entry = entries.get(symbol)
                has = None if entry is None else entry.get("hasOptions")
                if has is True:
                    kept.append(symbol)
                elif has is False:
                    hidden.append(symbol)
                else:
                    pending.append(symbol)
                if entry is not None and has is not None and now >= float(entry.get("nextCheck") or 0):
                    due.append(symbol)
        if request:
            with self._lock:
                pending_ok = [s for s in pending if self._tries_left(s)]
                due_ok = [s for s in due if self._tries_left(s)]
            self.request(pending_ok, urgent=True)
            if not _market_hours(self._now()):
                self.request(due_ok)    # known answers are re-verified off-hours only
        return kept, hidden, pending

    # ------------------------------------------------------------- checking

    def request(self, symbols: Iterable[Any], *, urgent: bool = False) -> None:
        wanted = [_norm(s) for s in symbols or () if _norm(s)]
        if not wanted:
            return
        with self._lock:
            fresh = [s for s in wanted if s not in self._queued]
            self._queued.update(fresh)
            for symbol in fresh:
                (self._urgent if urgent else self._queue).put(symbol)
            start = self._background and bool(fresh) and (self._thread is None or not self._thread.is_alive())
            if start:
                self._thread = threading.Thread(target=self._run, name="momx-optionable", daemon=True)
        if not self._background:
            self.drain()
            return
        if start:
            self._thread.start()

    def _next(self, block: bool) -> str | None:
        for source in (self._urgent, self._queue):
            try:
                return source.get_nowait()
            except queue.Empty:
                continue
        if not block:
            return None
        try:
            return self._urgent.get(timeout=5.0)
        except queue.Empty:
            return None

    def _healthy(self) -> bool:
        stamp = self._clock()
        if self._health is not None and stamp - self._health[0] < STATUS_TTL:
            return self._health[1]
        try:
            ok = bool(self._healthy_fn())
        except Exception:  # noqa: BLE001 - an unreachable status is "not healthy"
            ok = False
        self._health = (stamp, ok)
        return ok

    def drain(self) -> None:
        """Synchronous: check everything queued (tests, background=False).
        Nothing is read while Schwab is unhealthy - the queue is kept."""
        if not self._healthy():
            return
        while True:
            symbol = self._next(block=False)
            if symbol is None:
                return
            self._check(symbol, waits=False)

    def _run(self) -> None:
        while True:
            symbol = self._next(block=True)
            if symbol is None:
                continue
            with self._lock:
                entry = (self._entries or {}).get(symbol)
            recheck = entry is not None and entry.get("hasOptions") is not None
            if recheck and _market_hours(self._now()):
                # A re-check queued before the open: drop it, the next
                # off-hours build queues it again.
                with self._lock:
                    self._queued.discard(symbol)
                continue
            if not self._healthy():
                with self._lock:
                    self._queued.discard(symbol)
                self.request([symbol], urgent=not recheck)
                self._sleep(UNHEALTHY_PAUSE)
                continue
            if self._check(symbol, waits=True):
                self._sleep(FETCH_GAP_MARKET if _market_hours(self._now()) else FETCH_GAP_OFF_HOURS)

    def _tries_left(self, symbol: str) -> bool:
        held = self._tries.get(symbol)
        return not held or held[0] != self._now().date().isoformat() or held[1] < MAX_TRIES_PER_DAY

    def _tries_ok(self, symbol: str) -> bool:
        day = self._now().date().isoformat()
        held = self._tries.get(symbol)
        if not held or held[0] != day:
            held = self._tries[symbol] = [day, 0]
        held[1] += 1
        return held[1] <= MAX_TRIES_PER_DAY

    def _read(self, symbol: str, waits: bool) -> dict | None:
        """Poll until a verdict, or until api_server's build SETTLES (so the
        checker never has two builds of its own in flight), or SETTLE_SECONDS."""
        today = self._now().date()
        polls = max(1, int(SETTLE_SECONDS / WARM_POLL_SECONDS)) if waits else 1
        for attempt in range(polls):
            try:
                payload = self._fetch(symbol)
                verdict = classify(payload, today)
            except Exception:  # noqa: BLE001 - a failed read is unknown, never "no"
                payload, verdict = None, None
            if verdict is not None:
                return verdict
            if payload is None or (settled(payload) and attempt > 0):
                return None          # failed, or finished without a usable answer
            if waits and attempt < polls - 1:
                self._sleep(WARM_POLL_SECONDS)
        return None

    def _check(self, symbol: str, *, waits: bool) -> bool:
        """Read one symbol; True when a chain was actually requested."""
        allowed = False
        try:
            with self._lock:
                allowed = self._tries_ok(symbol)
            verdict = self._read(symbol, waits) if allowed else None
            now = self._clock()
            stamp = self._now().replace(microsecond=0).isoformat()
            with self._lock:
                self._queued.discard(symbol)
                entries = self._load()
                old = dict(entries.get(symbol) or {})
                if verdict is None:
                    return allowed                           # unknown: nothing stored
                if verdict["hasOptions"]:
                    entry = {"hasOptions": True, "checkedAt": stamp, "nextCheck": now + YES_RECHECK_SECONDS}
                    if verdict.get("expiries") is not None:
                        entry["expiries"] = verdict["expiries"][:8]
                        entry["weeklies"] = verdict["weeklies"]
                    else:                                    # a one-expiry payload: keep the old read
                        entry["expiries"] = old.get("expiries")
                        entry["weeklies"] = old.get("weeklies")
                        entry["nextCheck"] = now + NO_CONFIRM_SECONDS
                elif old.get("hasOptions") is True:
                    # YES IS STICKY (31-day window gap after a monthly expiry).
                    entry = dict(old, lastEmptyAt=stamp, nextCheck=now + NO_RECHECK_SECONDS)
                else:
                    confirmed = old.get("hasOptions") is False
                    entry = {"hasOptions": False, "checkedAt": stamp, "noReads": int(old.get("noReads") or 0) + 1,
                             "nextCheck": now + (NO_RECHECK_SECONDS if confirmed else NO_CONFIRM_SECONDS)}
                entries[symbol] = entry
                self._save()
            return allowed
        except Exception:  # noqa: BLE001 - the checker must never die
            with self._lock:
                self._queued.discard(symbol)
            return allowed
