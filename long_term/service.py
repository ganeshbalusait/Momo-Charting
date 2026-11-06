"""Long Term value screen: the store, the nightly refresher and the page / detail payloads.

The request path only reads the in-memory copy (``rows`` / ``page_payload`` / ``detail_payload``
never touch the disk and never raise). The refresh pass is paced (EDGAR <= 5/s, Finnhub 30/min),
runs on one daemon thread, and never starts between 09:30 and 16:00 ET on weekdays.

Store files (``directory``):
  fundamentals.json  {"records": {symbol: record}, "ciks": {cik: {"sic", "name"}}}
  summary.json       {"refreshed_at", "universe", "etfs", "rows_by_symbol": {symbol: computed}}
each written via temp file + os.replace (5 tries on PermissionError), the previous copy kept as
``<name>.prev.json``.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import math
import os
import shutil
import threading
import time
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from long_term.edgar import EdgarBlocked, EdgarClient, Snapshot, build_snapshot
from long_term.finnhub import FinnhubExtract, FinnhubPacer, fetch_metrics
from long_term.finnhub import default_get as _finnhub_default_get
from long_term.leaps import (LEAP_MAX_DTE, LEAP_MIN_DTE, REASON_CHAIN_UNAVAILABLE, REASON_NOT_FLAGGED,
                             pick_leap)
from long_term.metrics import CHECK_KEYS, Check, Metrics, compute_metrics, listing_factor, with_fcf
from long_term.symbols import CIK_OVERRIDES, canonical, to_schwab
from long_term.valuation import Valuation, price_guard, signal, valuation

log = logging.getLogger("long_term")

ET = ZoneInfo("America/New_York")
FRESH_DAYS = 3                 # a ticker's copy younger than this is not re-fetched
KEEP_DAYS = 30                 # a ticker that left the universe stays this long
STALE_HOURS = 24               # store older than this -> catch-up pass (outside market hours)
SAVE_EVERY = 10                # tickers between store writes
WRITE_TRIES = 5
QUOTE_TTL_S = 45.0             # one Schwab batch serves every viewer per window (page polls every 30 s)
QUOTE_CHUNK = 100
CHAIN_TTL_S = 300.0            # one Schwab option chain per flagged symbol per window (failures not cached)
CHAIN_STRIKES = 60
TICK_S = 60.0
SERVE_STALE_DAYS = 4           # a record or summary older than this is served STALE, whatever it said
FINNHUB_TRIES = 3
MARKET_OPEN, MARKET_CLOSE = dtime(9, 30), dtime(16, 0)
NIGHT_START, NIGHT_END = dtime(3, 0), dtime(3, 30)
DEFER_AT = dtime(16, 5)
DEFER_LABEL = "16:05 ET"
DEFAULT_MOS = 50
SCORED = frozenset({"BUY", "WAIT", "AVOID", "NO_PRICE"})

ROW_KEYS = (
    "symbol", "name", "price", "quoteTimeMs", "refPrice", "signal", "leaps", "reason", "buyBelow",
    "leapsBelow", "buyZonePct", "safetyBuy", "sticker", "buffett", "fairValue", "payback", "roic", "roe",
    "financial", "big5", "big5Na", "quality", "pe", "debtToEquity", "salesGrowth", "epsGrowth",
    "fcfGrowth", "equityGrowth", "marketCap", "filedEnd", "years", "currency", "young",
)
_METRICS_FIELDS = frozenset(f.name for f in dataclasses.fields(Metrics))
_VALUATION_FIELDS = frozenset(f.name for f in dataclasses.fields(Valuation))


# ---------- small helpers ----------

def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _pos(v) -> float | None:
    f = _num(v)
    return f if f is not None and f > 0 else None


def _et(now: datetime) -> datetime:
    return now.replace(tzinfo=ET) if now.tzinfo is None else now.astimezone(ET)


def _parse(stamp) -> datetime | None:
    if not stamp:
        return None
    try:
        return _et(datetime.fromisoformat(str(stamp)))
    except (TypeError, ValueError):
        return None


def market_hours(now: datetime) -> bool:
    t = _et(now)
    return t.weekday() < 5 and MARKET_OPEN <= t.time() < MARKET_CLOSE


def _mos(mos) -> int:
    f = _num(mos)
    return int(f) if f is not None and 0 <= f <= 90 else DEFAULT_MOS


def _metrics_from(d: dict) -> Metrics:
    kw = {k: v for k, v in d.items() if k in _METRICS_FIELDS}
    kw["checks"] = {k: (c if isinstance(c, Check) else Check(**c)) for k, c in (kw.get("checks") or {}).items()}
    return Metrics(**kw)


def _valuation_from(d: dict) -> Valuation:
    return Valuation(**{k: v for k, v in d.items() if k in _VALUATION_FIELDS})


_SNAPSHOT_FIELDS = frozenset(f.name for f in dataclasses.fields(Snapshot))
_FINNHUB_FIELDS = frozenset(f.name for f in dataclasses.fields(FinnhubExtract))


def _snapshot_from(d: dict) -> Snapshot:
    """Rebuild a Snapshot from its JSON form. JSON has no tuples, so each nested field is restored
    explicitly: ttm_ends values are (period end, is_fy) tuples; the rest are plain dicts / lists."""
    kw = {k: v for k, v in d.items() if k in _SNAPSHOT_FIELDS}
    kw["annual"] = {str(e): dict(r) for e, r in (kw.get("annual") or {}).items()}
    for name in ("units", "tags", "ttm", "balance_ends"):
        kw[name] = dict(kw.get(name) or {})
    for name in ("tag_breaks", "notes"):
        kw[name] = list(kw.get(name) or [])
    kw["ttm_ends"] = {k: tuple(v) for k, v in (kw.get("ttm_ends") or {}).items()}
    return Snapshot(**kw)


def _finnhub_from(d) -> FinnhubExtract | None:
    """Rebuild a FinnhubExtract: scalars plus four {period: value} dicts (no tuples or sets)."""
    if not isinstance(d, dict) or not d:
        return None
    kw = {k: v for k, v in d.items() if k in _FINNHUB_FIELDS}
    for name in ("eps_annual", "pe_annual", "roic_annual", "fcf_margin_annual"):
        kw[name] = dict(kw.get(name) or {})
    return FinnhubExtract(**kw)


# ---------- the store ----------

class Store:
    """fundamentals.json + summary.json, written atomically under one lock shared with the reader."""

    def __init__(self, directory: Path, *, sleep: Callable[[float], None] = time.sleep):
        self.directory = Path(directory)
        self.lock = threading.Lock()                # data lock: readers + writers, never held on disk I/O
        self._write_lock = threading.Lock()          # disk lock: one writer at a time, with the retries
        self._prev_kept: set[str] = set()            # files already copied to .prev.json this cycle
        self._sleep = sleep
        self.records: dict[str, dict] = {}
        self.ciks: dict[str, dict] = {}            # str(cik) -> {"sic", "name"}; SIC never refetched once known
        self.summary: dict = {}

    @property
    def records_path(self) -> Path:
        return self.directory / "fundamentals.json"

    @property
    def summary_path(self) -> Path:
        return self.directory / "summary.json"

    @staticmethod
    def _prev(path: Path) -> Path:
        return path.with_name(path.stem + ".prev.json")

    def _read(self, path: Path) -> dict:
        for p in (path, self._prev(path)):
            try:
                doc = json.loads(p.read_text(encoding="utf-8"))
            except FileNotFoundError:
                continue
            except (OSError, ValueError) as exc:
                log.warning("long_term: unreadable %s (%s); trying the previous copy", p.name, exc)
                continue
            if isinstance(doc, dict):
                return doc
        return {}

    def load(self) -> None:
        with self.lock:
            doc = self._read(self.records_path)
            recs, ciks = doc.get("records"), doc.get("ciks")
            self.records = {str(k): v for k, v in recs.items() if isinstance(v, dict)} if isinstance(recs, dict) else {}
            self.ciks = {str(k): v for k, v in ciks.items() if isinstance(v, dict)} if isinstance(ciks, dict) else {}
            dropped = (len(recs) if isinstance(recs, dict) else 0) - len(self.records)
            if dropped:
                log.warning("long_term: dropped %d unreadable records from %s", dropped, self.records_path.name)
            self.summary = self._read(self.summary_path)

    def _write(self, path: Path, text: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        try:
            if path.name not in self._prev_kept and path.exists():
                try:
                    shutil.copyfile(path, self._prev(path))
                    self._prev_kept.add(path.name)
                except OSError as exc:
                    log.warning("long_term: could not keep %s (%s)", self._prev(path).name, exc)
            for attempt in range(WRITE_TRIES):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    if attempt == WRITE_TRIES - 1:
                        raise
                    self._sleep(0.2 * (attempt + 1))
        finally:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass

    def begin_cycle(self) -> None:
        """Start a new write cycle: the next write of each file first keeps its copy as .prev.json."""
        with self._write_lock:
            self._prev_kept.clear()

    def save_records(self) -> None:
        with self.lock:                              # snapshot the data (encode) under the data lock
            text = json.dumps({"records": self.records, "ciks": self.ciks}, separators=(",", ":"))
        with self._write_lock:                       # disk I/O and retry sleeps never block readers
            self._write(self.records_path, text)

    def save_summary(self) -> None:
        with self.lock:
            text = json.dumps(self.summary, separators=(",", ":"))
        with self._write_lock:
            self._write(self.summary_path, text)

    def save(self) -> None:
        self.save_records()
        self.save_summary()


# ---------- the service ----------

def _default_now() -> datetime:
    return datetime.now(ET)


def _default_is_etf(symbol: str) -> bool | None:
    try:
        from momx.etf_list import is_etf
        return is_etf(symbol)
    except Exception:  # noqa: BLE001 - unknown is not an ETF
        return None


def _default_universe() -> list[str]:
    from long_term.symbols import universe
    return universe()


class LongTermService:
    def __init__(self, *, directory: Path | None = None, edgar: EdgarClient | None = None, finnhub_get=None,
                 finnhub_key: str | None = None, quotes_fn: Callable[[list[str]], dict] | None = None,
                 now_fn: Callable[[], datetime] | None = None, sleep: Callable[[float], None] = time.sleep,
                 is_etf_fn: Callable[[str], bool | None] | None = None,
                 universe_fn: Callable[[], list[str]] | None = None,
                 chain_fn: Callable[[str, date, date], dict] | None = None):
        self.directory = Path(directory) if directory is not None else default_directory()
        self._sleep = sleep
        self.store = Store(self.directory, sleep=sleep)
        self._edgar = edgar
        self._finnhub_get = finnhub_get or _finnhub_default_get
        self._finnhub_key = finnhub_key
        self._pacer = FinnhubPacer(per_minute=30, sleep=sleep)
        self._quotes_fn = quotes_fn
        self._chain_fn = chain_fn                   # (schwab symbol, from_date, to_date) -> chain dict
        self._schwab = None
        self._now = now_fn or _default_now
        self._is_etf = is_etf_fn or _default_is_etf
        self._universe_fn = universe_fn or _default_universe
        self._mono = time.monotonic

        self._state_lock = threading.Lock()
        self._running = False
        self._progress: dict | None = None
        self._requested = False
        self._deferred_until: datetime | None = None
        self._deferred_force = False                 # the deferral came from a Refresh-now click
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._quote_lock = threading.Lock()
        self._quote_cache: dict[str, tuple[float, dict | None]] = {}
        self._chain_lock = threading.Lock()
        self._chain_cache: dict[str, tuple[float, dict]] = {}   # schwab symbol -> (mono stamp, chain)
        self._computed: dict[str, dict] = {}       # symbol -> {"m": Metrics, "v": Valuation} | {"error": str}
        self._universe: list[str] | None = None

        self.store.load()
        self._computed = self._computed_from_summary(self.store.summary)
        # summary.json and its .prev both unreadable: requests serve "computing" rows and the
        # refresher thread rebuilds the summary from the records first thing (never a request).
        self._pending_rebuild = bool(self.store.records) and not self.store.summary

    # ----- clients -----

    @property
    def edgar(self) -> EdgarClient:
        if self._edgar is None:
            self._edgar = EdgarClient()
        return self._edgar

    def _schwab_quotes(self, schwab_symbols: list[str]) -> dict:
        if self._schwab is None:
            from data.schwab_client import SchwabClient
            self._schwab = SchwabClient()
        return self._schwab.get_quotes(schwab_symbols)

    def _schwab_chain(self, schwab_symbol: str, from_date: date, to_date: date) -> dict:
        """The live CALL chain for the LEAP window, today + 90 .. today + 365 days (Ganesh 11-06: "3 months
        to 12 months"); get_option_chain wants datetimes."""
        if self._schwab is None:
            from data.schwab_client import SchwabClient
            self._schwab = SchwabClient()
        return self._schwab.get_option_chain(
            schwab_symbol, contract_type="CALL", strike_count=CHAIN_STRIKES,
            from_date=datetime.combine(from_date, dtime.min), to_date=datetime.combine(to_date, dtime.min))

    def chain_for(self, schwab_symbol: str, from_date: date, to_date: date) -> dict:
        """One symbol's call chain, cached CHAIN_TTL_S per symbol. A failure raises and is not cached."""
        now = self._mono()
        with self._chain_lock:
            hit = self._chain_cache.get(schwab_symbol)
            if hit is not None and now - hit[0] < CHAIN_TTL_S:
                return hit[1]
        fn = self._chain_fn or self._schwab_chain
        chain = fn(schwab_symbol, from_date, to_date)
        if not isinstance(chain, dict):
            chain = {}
        with self._chain_lock:
            self._chain_cache[schwab_symbol] = (now, chain)
        return chain

    def quotes_for(self, symbols: list[str]) -> dict[str, dict]:
        """Live Schwab quotes keyed by canonical symbol: {"last_price", "quote_time_ms", "description"}.
        Cached 45 s; chunked; a failing chunk is skipped (and not cached)."""
        wanted = list(dict.fromkeys(s for s in (canonical(x) for x in (symbols or [])) if s))
        now = self._mono()
        out: dict[str, dict] = {}
        missing: list[str] = []
        with self._quote_lock:
            for s in wanted:
                hit = self._quote_cache.get(s)
                if hit is not None and now - hit[0] < QUOTE_TTL_S:
                    if hit[1] is not None:
                        out[s] = dict(hit[1])
                else:
                    missing.append(s)
        fn = self._quotes_fn or self._schwab_quotes
        for i in range(0, len(missing), QUOTE_CHUNK):
            chunk = missing[i:i + QUOTE_CHUNK]
            try:
                raw = fn([to_schwab(s) for s in chunk]) or {}
            except Exception as exc:  # noqa: BLE001 - a quote outage is "No price", never an error
                log.warning("long_term: quotes failed for %d symbols (%s)", len(chunk), exc)
                continue
            mapped = {canonical(k): v for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}
            with self._quote_lock:
                for s in chunk:
                    q = mapped.get(s)
                    val = None
                    if q is not None:
                        desc = q.get("description")
                        val = {"last_price": _num(q.get("last_price")), "quote_time_ms": _num(q.get("quote_time_ms")),
                               "description": str(desc).strip() if desc else None}
                        out[s] = dict(val)
                    self._quote_cache[s] = (now, val)
        return out

    def _finnhub(self, symbol: str, stamp: str) -> FinnhubExtract | None:
        if not self._finnhub_key:
            return None

        def get(url, timeout):
            for attempt in range(FINNHUB_TRIES):
                try:
                    return self._finnhub_get(url, timeout)
                except Exception as exc:
                    if getattr(exc, "code", None) == 429 and attempt < FINNHUB_TRIES - 1:
                        self._pacer.backoff(attempt + 1)
                        continue
                    raise

        try:
            self._pacer.wait()
            return fetch_metrics(symbol, self._finnhub_key, get=get, fetched_at=stamp)
        except Exception as exc:  # noqa: BLE001 - "Finnhub: no data"; the EDGAR snapshot is kept
            log.warning("long_term: Finnhub failed for %s (%s: %s)", symbol, type(exc).__name__, exc)
            return None

    # ----- universe -----

    def universe(self) -> list[str]:
        try:
            raw = self._universe_fn() or []
        except Exception as exc:  # noqa: BLE001
            log.warning("long_term: universe failed (%s)", exc)
            raw = []
        return sorted({s for s in (canonical(x) for x in raw) if s})

    def known_symbols(self) -> list[str]:
        """Every symbol the page shows (stored records + the ETF list), from memory only."""
        with self.store.lock:
            return sorted(set(self.store.records) | set(self.store.summary.get("etfs") or []))

    def _known_universe(self) -> list[str]:
        if self._universe is None:
            saved = self.store.summary.get("universe")
            self._universe = list(saved) if isinstance(saved, list) and saved else self.universe()
        return self._universe

    # ----- refresh pass -----

    @property
    def progress(self) -> dict | None:
        p = self._progress
        return dict(p) if p else None

    def _is_fresh(self, rec: dict | None, now: datetime, symbol: str | None = None) -> bool:
        """Fetched < 3 days ago and usable. A broken copy (no snapshot, or one that does not compute)
        is never fresh; a deliberate no-CIK record is."""
        if not rec or rec.get("error"):
            return False
        if rec.get("cik") is not None:
            if not isinstance(rec.get("snapshot"), dict) or not rec.get("snapshot"):
                return False
            c = self._computed.get(symbol) if symbol else None
            if c is not None and "error" in c:
                return False
        t = _parse(rec.get("fetched_at"))
        return t is not None and now - t < timedelta(days=FRESH_DAYS)

    def refresh(self, symbols: list[str] | None = None, *, force: bool = False, _gated: bool = False) -> dict:
        """One synchronous pass. `_gated` (the refresher thread) stops at the market open."""
        with self._state_lock:
            if self._running:
                return {"scored": 0, "total": 0, "fetched": 0, "skipped": 0, "errors": 0, "running": True}
            self._running = True
        try:
            return self._pass(symbols, force=force, gated=_gated)
        finally:
            self._progress = None
            with self._state_lock:
                self._running = False

    def _put(self, symbol: str, rec: dict) -> None:
        with self.store.lock:
            self.store.records[symbol] = rec

    def _pass(self, symbols, *, force: bool, gated: bool) -> dict:
        now = self._now()
        stamp = _et(now).isoformat()
        today = _et(now).date()
        full = symbols is None
        names = self.universe() if full else sorted({s for s in (canonical(x) for x in symbols) if s})
        etfs = [s for s in names if self._safe_is_etf(s)]
        candidates = [s for s in names if s not in set(etfs)]
        with self.store.lock:
            for s in candidates:
                if s in self.store.records:
                    self.store.records[s]["last_seen"] = stamp
        due = [s for s in candidates if force or not self._is_fresh(self.store.records.get(s), now, s)]
        result = {"scored": 0, "total": len(due), "fetched": 0, "skipped": len(candidates) - len(due),
                  "errors": 0, "blocked": False, "stopped": False}
        self._progress = {"done": 0, "total": len(due)}
        self.store.begin_cycle()             # each file keeps ONE .prev.json per pass (the pre-pass copy)
        fresh: dict[str, dict] = {}          # computed this pass (served at once, reused by the summary)
        log.info("long_term: pass start, %d to fetch, %d fresh, %d ETFs", len(due), result["skipped"], len(etfs))

        if due:
            try:
                cik_map = self.edgar.cik_map()
            except EdgarBlocked as exc:
                log.error("long_term: SEC blocked the ticker map (%s)", exc)
                cik_map, result["blocked"] = None, True
            except Exception as exc:  # noqa: BLE001
                log.error("long_term: ticker map failed (%s)", exc)
                cik_map, result["errors"] = None, len(due)
            if cik_map is not None:
                self._fetch_all(due, cik_map, stamp, today, gated, result, fresh)

        self.drop_departed(now)
        self._rebuild_summary(names if full else None, etfs if full else None, stamp, today, result, fresh)
        log.info("long_term: pass done %s", result)
        return result

    def _safe_is_etf(self, symbol: str) -> bool:
        try:
            return self._is_etf(symbol) is True
        except Exception:  # noqa: BLE001
            return False

    def _fetch_all(self, due: list[str], cik_map: dict, stamp: str, today: date, gated: bool, result: dict,
                   fresh: dict) -> None:
        quotes = self.quotes_for(due)
        # Tickers sharing a CIK (GOOG / GOOGL) sit next to each other so one facts fetch serves both.
        def cik_of(s: str) -> int | None:
            return CIK_OVERRIDES.get(s) or cik_map.get(s)        # R20: overrides win over the SEC map

        order = sorted(due, key=lambda s: (cik_of(s) or 0, s))
        facts_cik, facts = None, None
        for i, sym in enumerate(order):
            if gated and market_hours(self._now()):
                log.info("long_term: market open, pass stops at %d/%d (resumes after the close)", i, len(order))
                result["stopped"] = True
                with self._state_lock:            # a pending click keeps its force flag
                    self._deferred_until = _et(self._now()).replace(hour=DEFER_AT.hour, minute=DEFER_AT.minute,
                                                                     second=0, microsecond=0)
                break
            q = quotes.get(sym) or {}
            old = self.store.records.get(sym) or {}
            # A night without a quote keeps the previous reference (the 25% price check stays on).
            ref = _pos(q.get("last_price")) or _pos(old.get("ref_price"))
            try:
                cik = cik_of(sym)
                if cik is None:
                    self._put(sym, {"cik": None, "name": q.get("description") or old.get("name"), "sic": None,
                                    "snapshot": None, "finnhub": None, "ref_price": ref, "listing_factor": None,
                                    "fetched_at": stamp, "last_seen": stamp})
                else:
                    if facts_cik != cik:
                        facts_cik, facts = None, None               # release the previous filer first
                        facts = self.edgar.company_facts(cik)
                        facts_cik = cik
                    info = self.store.ciks.get(str(cik))
                    if not info or info.get("sic") is None:
                        sub = self.edgar.submission(cik) or {}
                        info = {"sic": sub.get("sic"), "name": sub.get("name")}
                        with self.store.lock:
                            self.store.ciks[str(cik)] = info
                    name = info.get("name") or q.get("description") or sym
                    fh = self._finnhub(sym, stamp)
                    snap = build_snapshot(sym, cik, facts, sic=info.get("sic"), name=name, fetched_at=stamp)
                    prior_lf = _pos(old.get("listing_factor"))
                    if prior_lf is None:
                        log.info("long_term: %s has no stored listing factor yet (no drift check tonight)", sym)
                    self._put(sym, {"cik": cik, "name": name, "sic": info.get("sic"),
                                    "snapshot": dataclasses.asdict(snap),
                                    "finnhub": dataclasses.asdict(fh) if fh is not None else None,
                                    "ref_price": ref,
                                    "listing_factor": listing_factor(ref, fh) if listing_factor(ref, fh) is not None
                                    else old.get("listing_factor"),
                                    "prior_listing_factor": prior_lf,
                                    "fetched_at": stamp, "last_seen": stamp})
                result["fetched"] += 1
                c = fresh[sym] = self._compute(self.store.records[sym], today)
                with self.store.lock:
                    self._computed[sym] = c
            except EdgarBlocked as exc:
                log.error("long_term: SEC block, pass stopped at %s (%s)", sym, exc)
                result["blocked"] = True
                self._put(sym, {**old, "error": "blocked", "error_at": stamp, "last_seen": stamp})
                break
            except Exception as exc:  # noqa: BLE001 - one bad ticker never aborts the pass
                log.warning("long_term: %s failed (%s: %s)", sym, type(exc).__name__, exc)
                result["errors"] += 1
                self._put(sym, {**old, "error": f"{type(exc).__name__}: {exc}"[:300], "error_at": stamp,
                                "last_seen": stamp})
            self._progress = {"done": i + 1, "total": len(order)}
            if (i + 1) % SAVE_EVERY == 0:
                self._save_records_safely()
        facts = None
        self._save_records_safely()

    def _save_records_safely(self) -> None:
        try:
            self.store.save_records()
        except Exception as exc:  # noqa: BLE001 - memory copy still serves; next save retries
            log.error("long_term: store write failed (%s)", exc)

    def _compute(self, rec: dict, today: date) -> dict:
        try:
            if rec.get("cik") is None or not rec.get("snapshot"):
                if rec.get("cik") is None and not rec.get("error"):
                    return {"no_filings": True}
                return {"error": rec.get("error") or "no snapshot"}
            snap = _snapshot_from(rec["snapshot"])
            fh = _finnhub_from(rec.get("finnhub"))
            m = compute_metrics(snap, fh, today=today, is_etf=False, price=rec.get("ref_price"),
                                prior_listing_factor=_pos(rec.get("prior_listing_factor")))
            return {"m": m, "v": valuation(m, fh)}
        except Exception as exc:  # noqa: BLE001 - a broken record is one CHECK_DATA row
            snap = rec.get("snapshot")
            log.warning("long_term: compute failed for %s (%s: %s)",
                        snap.get("symbol") if isinstance(snap, dict) else "?", type(exc).__name__, exc)
            return {"error": f"{type(exc).__name__}: {exc}"[:300]}

    def _rebuild_summary(self, names=None, etfs=None, stamp=None, today=None, result=None, fresh=None) -> None:
        """Recompute every row into summary.json. With no arguments (the refresher's start-up
        rebuild after an unreadable summary) the stamp is the newest record's fetch time, so the
        staleness rule still sees the real age of the data."""
        with self.store.lock:
            records = dict(self.store.records)
        if stamp is None:
            fetched = [t for t in (_parse(r.get("fetched_at")) for r in records.values()) if t is not None]
            stamp = max(fetched).isoformat() if fetched else None
        today = today or _et(self._now()).date()
        result = result if result is not None else {"scored": 0}
        fresh = fresh or {}
        computed: dict[str, dict] = {}
        rows: dict[str, dict] = {}
        for sym, rec in records.items():
            c = fresh.get(sym) or self._compute(rec, today)
            computed[sym] = c
            if "m" in c:
                rows[sym] = {"metrics": dataclasses.asdict(c["m"]), "valuation": dataclasses.asdict(c["v"])}
                if c["m"].status is None:
                    result["scored"] += 1
            else:
                rows[sym] = {k: v for k, v in c.items()}
        old = self.store.summary
        summary = {
            "refreshed_at": stamp,
            "universe": names if names is not None else sorted(set(old.get("universe") or []) | set(records)),
            "etfs": etfs if etfs is not None else list(old.get("etfs") or []),
            "rows_by_symbol": rows,
            "last_pass": {k: v for k, v in result.items()},
        }
        with self.store.lock:
            self.store.summary = summary
            self._computed = computed
            self._universe = list(summary["universe"])
            self._pending_rebuild = False
        try:
            self.store.save()
        except Exception as exc:  # noqa: BLE001
            log.error("long_term: store write failed (%s)", exc)

    def _computed_from_summary(self, summary: dict) -> dict:
        out: dict[str, dict] = {}
        for sym, row in (summary.get("rows_by_symbol") or {}).items():
            try:
                if "metrics" in row:
                    out[sym] = {"m": _metrics_from(row["metrics"]), "v": _valuation_from(row["valuation"])}
                elif row.get("no_filings"):
                    out[sym] = {"no_filings": True}
                elif row.get("error"):
                    out[sym] = {"error": str(row["error"])}
            except Exception as exc:  # noqa: BLE001 - recomputed lazily from the record instead
                log.warning("long_term: summary row %s unreadable (%s)", sym, exc)
        return out

    def drop_departed(self, now: datetime) -> None:
        cutoff = _et(now) - timedelta(days=KEEP_DAYS)
        with self.store.lock:
            gone = [s for s, r in self.store.records.items()
                    if (_parse(r.get("last_seen")) or cutoff) < cutoff]
            for s in gone:
                self.store.records.pop(s, None)
                self._computed.pop(s, None)
            if gone:
                rows = self.store.summary.get("rows_by_symbol")
                if isinstance(rows, dict):
                    for s in gone:
                        rows.pop(s, None)
        if gone:
            log.info("long_term: dropped %d departed tickers: %s", len(gone), ", ".join(gone))

    # ----- payloads (never raise) -----

    def _entry(self, sym: str, rec: dict | None) -> dict:
        c = self._computed.get(sym)
        if c is None and rec is not None:
            if self._pending_rebuild:
                return {"computing": True}           # the refresher thread rebuilds; never compute here
            c = self._compute(rec, _et(self._now()).date())
            with self.store.lock:
                self._computed[sym] = c
        return c or {}

    def _blank(self, sym: str) -> dict:
        row = dict.fromkeys(ROW_KEYS)
        row.update(symbol=sym, leaps=False, financial=False, young=False)
        return row

    def _row(self, sym: str, rec: dict | None, etf: bool, mos: int, quote, now: datetime) -> dict:
        row = self._blank(sym)
        try:
            q = quote if isinstance(quote, dict) else {}
            price, qt = _num(q.get("last_price")), _num(q.get("quote_time_ms"))
            rec = rec or {}
            ref = _pos(rec.get("ref_price"))
            row.update(name=rec.get("name") or q.get("description") or None, price=price, quoteTimeMs=qt,
                       refPrice=ref)
            if etf:
                row.update(signal="ETF", reason="ETF")
                return row
            c = self._entry(sym, rec)
            if c.get("computing"):
                row.update(signal="CHECK_DATA", reason="computing")
                return row
            if c.get("no_filings"):
                row.update(signal="NO_FILINGS", reason="No filings")
                return row
            if "m" not in c:
                row.update(signal="CHECK_DATA", reason=f"data error: {c.get('error') or 'unreadable record'}")
                return row
            m, v = c["m"], c["v"]
            ok, why = price_guard(price, qt, ref, now=now)
            s = signal(m, v, price=price, price_ok=ok, mos=mos, price_reason=why or None)
            stale = self._stale_reason(rec, now)
            if stale is not None:                    # old data never fires, whatever it said then
                s = dict(s, signal="STALE", leaps=False, buy_below=None, leaps_below=None, safety_buy=None,
                         buy_zone_pct=None, reason=stale)
            eps = _pos(m.eps_ttm_usd)
            equity = _num(m.equity)
            debt = _num(m.debt)
            g = m.growth or {}
            snap = rec.get("snapshot") or {}
            fh = rec.get("finnhub") or {}
            row.update(
                signal=s["signal"], leaps=bool(s["leaps"]), reason=s["reason"], buyBelow=s["buy_below"],
                leapsBelow=s["leaps_below"], buyZonePct=s["buy_zone_pct"], safetyBuy=s["safety_buy"],
                sticker=v.sticker, buffett=v.buffett, fairValue=v.fair_value, payback=v.payback_years,
                roic=m.roic_now, roe=m.roe_now, financial=bool(m.financial), big5=m.big5, big5Na=m.big5_na,
                quality=s["quality"],
                pe=(price / eps) if _pos(price) and eps else None,
                debtToEquity=(debt / equity) if debt is not None and equity is not None and equity > 0 else None,
                salesGrowth=g.get("sales"), epsGrowth=g.get("eps"), fcfGrowth=g.get("fcf"),
                equityGrowth=g.get("equity"),
                marketCap=_num(m.market_cap_usd) if _num(m.market_cap_usd) is not None
                else (_num(fh.get("market_cap_usd")) if m.currency == "USD" else None),   # R16
                filedEnd=snap.get("latest_annual_end"), years=m.years, currency=m.currency,
                young=bool(m.young),
            )
            return row
        except Exception as exc:  # noqa: BLE001 - never let one row break the page
            log.warning("long_term: row %s failed (%s: %s)", sym, type(exc).__name__, exc)
            blank = self._blank(sym)
            blank.update(name=row.get("name"), price=row.get("price"), quoteTimeMs=row.get("quoteTimeMs"),
                         refPrice=row.get("refPrice"), signal="CHECK_DATA", reason="data error")
            return blank

    def _stale_reason(self, rec: dict, now: datetime) -> str | None:
        """"Stale: last refresh N days ago" when the older of the record's fetch and the summary's
        refresh is more than SERVE_STALE_DAYS old; None when fresh. No fetch time at all is stale."""
        summary = self.store.summary if isinstance(self.store.summary, dict) else {}
        fetched = _parse(rec.get("fetched_at"))
        if fetched is None:
            return "Stale: no refresh time"
        oldest = min(t for t in (fetched, _parse(summary.get("refreshed_at"))) if t is not None)
        age = _et(now) - oldest
        if age <= timedelta(days=SERVE_STALE_DAYS):
            return None
        days = int(age.total_seconds() // 86400)
        return f"Stale: last refresh {days} day{'s' if days != 1 else ''} ago"

    def _view(self) -> tuple[dict, set, list]:
        with self.store.lock:
            records = dict(self.store.records)
            etfs = set(self.store.summary.get("etfs") or [])
        return records, etfs, self._known_universe()

    def rows(self, mos: int, quotes: dict[str, dict]) -> list[dict]:
        try:
            records, etfs, _ = self._view()
            now = self._now()
            m = _mos(mos)
            quotes = quotes if isinstance(quotes, dict) else {}
            out = [self._row(s, records.get(s), s in etfs, m, quotes.get(s), now)
                   for s in sorted(set(records) | etfs)]
            return out
        except Exception as exc:  # noqa: BLE001
            log.error("long_term: rows failed (%s: %s)", type(exc).__name__, exc)
            return []

    def page_payload(self, mos: int, quotes: dict) -> dict:
        rows = self.rows(mos, quotes)
        try:
            universe = len(self._known_universe())
        except Exception:  # noqa: BLE001
            universe = len(rows)
        refreshed = self.store.summary.get("refreshed_at") if isinstance(self.store.summary, dict) else None
        return {
            "rows": rows,
            "meta": {
                "universe": universe,
                "scored": sum(1 for r in rows if r["signal"] in SCORED),
                "etfs": [r["symbol"] for r in rows if r["signal"] == "ETF"],
                "noFilings": [r["symbol"] for r in rows if r["signal"] == "NO_FILINGS"],
                "refreshedAt": refreshed or None,
                "refreshing": self.progress,
                "deferredUntil": DEFER_LABEL if self._deferred_until is not None else None,
                "mos": _mos(mos),
            },
        }

    def detail_payload(self, symbol: str, mos: int, quote: dict | None) -> dict | None:
        sym = canonical(symbol)
        if not sym:
            return None
        try:
            records, etfs, universe = self._view()
            rec = records.get(sym)
            if rec is None and sym not in etfs and sym not in set(universe):
                return None
            row = self._row(sym, rec, sym in etfs, _mos(mos), quote, self._now())
            out = {"symbol": sym, "name": row["name"], "row": row, "checks": [], "years": [], "unit": None,
                   "explain": [], "crossChecks": [], "sources": [], "listingFactor": None,
                   "fetchedAt": (rec or {}).get("fetched_at"), "refreshedAt": self.store.summary.get("refreshed_at")}
            c = self._entry(sym, rec) if rec is not None and sym not in etfs else {}
            if "m" not in c:
                return out
            m, v = c["m"], c["v"]
            snap = rec.get("snapshot") or {}
            fh = rec.get("finnhub") or {}
            checks = []
            for k in CHECK_KEYS:
                ch = (m.checks or {}).get(k)
                if ch is not None:
                    checks.append({"key": ch.key, "label": ch.label, "value": ch.value, "threshold": ch.threshold,
                                   "passed": ch.passed, "detail": ch.detail})
            table = with_fcf(snap.get("annual") or {})
            years = []
            for e in sorted(m.window or []):
                r = table.get(e) or {}
                years.append({"end": e, "revenue": r.get("revenue"), "netIncome": r.get("net_income"),
                              "eps": r.get("eps"), "equity": r.get("equity"), "fcf": r.get("fcf"),
                              "roic": (m.roic_series or {}).get(e)})
            cross = []
            src = (m.checks or {}).get("sources")
            for part in str(src.detail if src else "").split("; "):
                if not part:
                    continue
                ok = None if ("skipped" in part or "not checked" in part or "no Finnhub series" in part
                              or "n/a" in part) else part.endswith(" ok")
                cross.append({"label": part.split(":")[0].split(" ")[0] if part else "", "ok": ok, "detail": part})
            for note in m.notes or []:
                cross.append({"label": str(note).split(":")[0], "ok": None, "detail": str(note)})
            lf = m.listing_factor if m.listing_factor is not None else rec.get("listing_factor")
            out.update(
                checks=checks, years=years, unit=snap.get("currency"), explain=list(v.explain or []),
                crossChecks=cross, listingFactor=lf,
                sources=[
                    {"label": "EDGAR CIK", "value": rec.get("cik")},
                    {"label": "Latest annual period", "value": snap.get("latest_annual_end")},
                    {"label": "Annual form", "value": snap.get("annual_form")},
                    {"label": "TTM period end", "value": snap.get("ttm_period_end")},
                    {"label": "EDGAR fetched", "value": snap.get("fetched_at")},
                    {"label": "Finnhub fetched", "value": fh.get("fetched_at") or None},
                    {"label": "Listing factor", "value": lf},
                    {"label": "SIC", "value": rec.get("sic")},
                ],
            )
            return out
        except Exception as exc:  # noqa: BLE001
            log.warning("long_term: detail %s failed (%s: %s)", sym, type(exc).__name__, exc)
            return {"symbol": sym, "name": None, "row": None, "checks": [], "years": [], "unit": None,
                    "explain": [], "crossChecks": [], "sources": [], "listingFactor": None,
                    "fetchedAt": None, "refreshedAt": None}

    def leap_payload(self, symbol: str, mos: int, quote: dict | None) -> dict | None:
        """The LEAP contracts for one row: ``contract`` (the ENTRY-style pick) and ``deep`` (the stock
        substitute), both on one expiry. None for an unknown ticker (the detail rule); otherwise always a
        dict and never raises. A row that is not LEAPS-flagged makes no chain call."""
        sym = canonical(symbol)
        if not sym:
            return None
        out = {"symbol": sym, "name": None, "leaps": False, "price": None, "leapsBelow": None, "contract": None,
               "deep": None, "reason": REASON_CHAIN_UNAVAILABLE, "expiries": [], "fetchedAt": None,
               "refreshedAt": None}
        try:
            records, etfs, universe = self._view()
            rec = records.get(sym)
            if rec is None and sym not in etfs and sym not in set(universe):
                return None
            now = self._now()
            row = self._row(sym, rec, sym in etfs, _mos(mos), quote, now)
            out.update(name=row["name"], price=row["price"], leapsBelow=row["leapsBelow"],
                       fetchedAt=(rec or {}).get("fetched_at"),
                       refreshedAt=self.store.summary.get("refreshed_at") if isinstance(self.store.summary, dict)
                       else None)
            if row["leaps"] is not True:
                out.update(reason=REASON_NOT_FLAGGED)
                return out
            out["leaps"] = True
            today = _et(now).date()
            try:
                # Schwab is asked only for the 3-12 month window (today + 90 .. today + 365 days)
                chain = self.chain_for(to_schwab(sym), today + timedelta(days=LEAP_MIN_DTE),
                                       today + timedelta(days=LEAP_MAX_DTE))
            except Exception as exc:  # noqa: BLE001 - the page says "chain unavailable", nothing else changes
                log.warning("long_term: option chain failed for %s (%s: %s)", sym, type(exc).__name__, exc)
                out.update(reason=REASON_CHAIN_UNAVAILABLE)
                return out
            out.update(pick_leap(chain, price=row["price"], today=today))
            return out
        except Exception as exc:  # noqa: BLE001
            log.warning("long_term: leap %s failed (%s: %s)", sym, type(exc).__name__, exc)
            out.update(contract=None, deep=None, reason=REASON_CHAIN_UNAVAILABLE, expiries=[])
            return out

    # ----- refresher -----

    def _due(self, now: datetime) -> bool:
        t = _et(now)
        if market_hours(t):
            return False
        with self._state_lock:
            requested, deferred = self._requested, self._deferred_until
        if requested:
            return True
        if deferred is not None and t >= deferred:
            return True
        last = _parse(self.store.summary.get("refreshed_at"))
        if last is None or t - last >= timedelta(hours=STALE_HOURS):
            return True
        return NIGHT_START <= t.time() < NIGHT_END and last.date() != t.date()

    def _tick(self, now: datetime) -> bool:
        """One loop step: run a pass when due. Returns whether a pass ran."""
        if not self._due(now):
            return False
        with self._state_lock:
            force = self._requested or (self._deferred_until is not None and self._deferred_force)
            self._requested = False
            self._deferred_until = None
            self._deferred_force = False
        self.refresh(force=force, _gated=True)
        return True

    def request_refresh(self, *, now: datetime) -> dict:
        with self._state_lock:
            running = self._running
        if running:
            return {"running": True}
        t = _et(now)
        if market_hours(t):
            with self._state_lock:
                self._deferred_until = t.replace(hour=DEFER_AT.hour, minute=DEFER_AT.minute, second=0,
                                                 microsecond=0)
                self._deferred_force = True
            log.info("long_term: refresh requested in market hours, deferred to %s", DEFER_LABEL)
            return {"deferredUntil": DEFER_LABEL}
        with self._state_lock:
            self._requested = True
        self.start_refresher()
        self._wake.set()
        return {"started": True}

    def _loop(self) -> None:
        if self._pending_rebuild:
            try:
                self._rebuild_summary()
            except Exception:  # noqa: BLE001 - the thread must survive; the next pass rebuilds too
                log.exception("long_term: start-up summary rebuild failed")
        while not self._stop.is_set():
            try:
                self._tick(self._now())
            except Exception:  # noqa: BLE001 - the thread must survive any pass failure
                log.exception("long_term: refresher tick failed")
            self._wake.wait(TICK_S)
            self._wake.clear()

    def start_refresher(self) -> None:
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="long-term-refresher", daemon=True)
            self._thread.start()

    def stop_refresher(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._wake.set()
        t = self._thread
        if t is not None and t is not threading.current_thread():
            t.join(timeout)


# ---------- the module singleton ----------

def default_directory() -> Path:
    env = os.environ.get("AGX_LONG_TERM_DIR")
    if env:
        return Path(env)
    try:
        from config import ARTIFACTS_DIR
        return Path(ARTIFACTS_DIR) / "long_term"
    except Exception:  # noqa: BLE001
        return Path(__file__).resolve().parent.parent / "artifacts" / "long_term"


_INSTANCE: LongTermService | None = None
_INSTANCE_LOCK = threading.Lock()


def service_instance() -> LongTermService:
    global _INSTANCE
    with _INSTANCE_LOCK:
        if _INSTANCE is None:
            _INSTANCE = LongTermService(directory=default_directory(),
                                        finnhub_key=os.environ.get("FINNHUB_API_KEY") or None)
        return _INSTANCE
