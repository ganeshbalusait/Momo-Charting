"""Option bid/ask SPREAD class per ticker - the board's "Spread" column.

2026-11-03, his ask: show next to each ticker how wide its options trade, so he
knows which names to skip or to price above the mid (he buys short-dated OTM
options with limit orders at the mid).

THE CLASS (from a read-only study on 2026-11-03, three Schwab snapshots of the
watchlist 13:34-14:46 ET; his paper-mirror mid orders filled 72% Tight, 50% OK,
16% Wide, 16% Avoid):

    contracts = nearest Friday WEEKLY + next MONTHLY (third Friday) expiry,
                ATM +/-2 strikes, calls AND puts, quotes with bid 0 skipped
    median    = median over those contracts of (ask - bid) / mid
    Tight  < 3%      OK 3-8%      Wide 8-15%
    Avoid  > 15%, or an ATM contract under $3 whose spread is $0.30 or more

NO NEW BROKER CALLS. A read-only sweep of 371 tickers drew Schwab 429s on
2026-11-03, so this reads ONLY the chains api_server already saved to
artifacts/oi_chain_cache/<SYMBOL>.json.gz (the OI finder / ticker card / Bot
reads), re-reading a file only when it changed. Quotes outside regular hours
are stale or blank, so a class is measured only from a chain read between
10:00 and 15:30 ET on a weekday, and is shown only while it is fresh (see
``is_fresh``); otherwise the column prints "-". Never raises.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import statistics
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

#: Class cut-offs on the median spread as a fraction of the mid.
TIGHT_BELOW = 0.03
OK_BELOW = 0.08
WIDE_UP_TO = 0.15
#: The ATM rule: a contract under this price ...
ATM_CHEAP_MID = 3.0
#: ... whose spread is at least this many dollars is Avoid on its own.
ATM_WIDE_USD = 0.30
#: Strikes either side of the ATM strike.
STRIKES_EACH_SIDE = 2
#: The class is the median over this many of the day's latest chain reads.
READS_KEPT = 3

#: Measured only from chains read inside this ET window (weekdays).
WINDOW_START = (10, 0)
WINDOW_END = (15, 30)
#: A chain older than this when we look at it is not measured.
CHAIN_MAX_AGE_SECONDS = 30 * 60
#: Inside the window a class is shown for this long after its chain was read;
#: after 15:30 the day's last class stays up until midnight ET.
SHOW_MAX_AGE_SECONDS = 60 * 60

#: Background pass cadence, and a pause between files so the worker's other
#: threads keep the GIL (a full cold pass is ~660 files, ~6 ms each).
PASS_SECONDS = 120.0
FILE_PAUSE_SECONDS = 0.02

CHAIN_SCHEMA = 1  # api_server OI_FINDER_CHAIN_QUOTE_SCHEMA_VERSION

#: What the class means in his terms, from the paper-mirror mid-order history.
CLASS_NOTE = {
    "Tight": "Mid orders usually fill (72% filled at the mid in paper trading).",
    "OK": "Mid orders fill about half the time (50% in paper trading).",
    "Wide": "Mid orders rarely fill (16% in paper trading) - price above the mid or skip.",
    "Avoid": "Mid orders rarely fill (16% in paper trading) and crossing the spread is costly - skip.",
}


# --------------------------------------------------------------------------- math

def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _is_monthly(day: date) -> bool:
    """The standard monthly expiry: the third Friday (day 15-21)."""
    return day.weekday() == 4 and 15 <= day.day <= 21


def pick_expiries(expiries: Iterable[date], today: date) -> dict[str, date]:
    """{"weekly": nearest non-monthly Friday 1+ days out, "monthly": the next
    third Friday 1+ days out}; a key is missing when the chain has none."""
    ahead = sorted({d for d in expiries if (d - today).days >= 1})
    out: dict[str, date] = {}
    monthly = next((d for d in ahead if _is_monthly(d)), None)
    weekly = next((d for d in ahead if d.weekday() == 4 and d != monthly), None)
    if weekly:
        out["weekly"] = weekly
    if monthly:
        out["monthly"] = monthly
    return out


def _quote(row: Mapping) -> tuple[float, float, float] | None:
    """(bid, ask, mid) of a usable two-sided quote, else None (bid 0 skipped)."""
    bid, ask = _num(row.get("bid")), _num(row.get("ask"))
    if bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    return bid, ask, (bid + ask) / 2


def classify(median_pct: float, cheap_wide_atm: bool) -> str:
    pct = round(median_pct, 6)  # 0.03 must not read as 3.0000000001%
    if pct > WIDE_UP_TO or cheap_wide_atm:
        return "Avoid"
    if pct >= OK_BELOW:
        return "Wide"
    if pct >= TIGHT_BELOW:
        return "OK"
    return "Tight"


def measure_chain(chain: Any, today: date) -> dict | None:
    """The spread class of one saved chain payload, or None when it has no
    usable quote on the picked expiries/strikes."""
    if not isinstance(chain, Mapping):
        return None
    spot = _num(chain.get("underlyingPrice"))
    rows = chain.get("selectedExpiryChainRows")
    if not spot or spot <= 0 or not isinstance(rows, list):
        return None
    by_expiry: dict[date, list[Mapping]] = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("side") not in ("CALL", "PUT"):
            continue
        try:
            day = date.fromisoformat(str(row.get("expiry") or "")[:10])
        except ValueError:
            continue
        if _num(row.get("strike")) is None:
            continue
        by_expiry.setdefault(day, []).append(row)
    picks = pick_expiries(by_expiry, today)
    if not picks:
        return None
    contracts: list[dict] = []
    skipped = 0
    for bucket, day in picks.items():
        for side in ("CALL", "PUT"):
            legs = sorted((r for r in by_expiry[day] if r.get("side") == side), key=lambda r: _num(r["strike"]))
            if not legs:
                continue
            atm = min(range(len(legs)), key=lambda i: abs(_num(legs[i]["strike"]) - spot))
            for i in range(max(0, atm - STRIKES_EACH_SIDE), min(len(legs), atm + STRIKES_EACH_SIDE + 1)):
                q = _quote(legs[i])
                if q is None:
                    skipped += 1
                    continue
                bid, ask, mid = q
                contracts.append({"bucket": bucket, "atm": i == atm, "mid": mid,
                                  "pct": (ask - bid) / mid, "usd": round(ask - bid, 4)})
    if not contracts:
        return None
    median_pct = statistics.median(c["pct"] for c in contracts)
    cheap_wide_atm = any(c["atm"] and c["mid"] < ATM_CHEAP_MID and c["usd"] >= ATM_WIDE_USD for c in contracts)
    median_usd = statistics.median(c["usd"] for c in contracts)
    return {
        "cls": classify(median_pct, cheap_wide_atm),
        "medPct": round(median_pct * 100, 1),
        "medUsd": round(median_usd, 2),
        "atmRule": cheap_wide_atm,
        "pct": round(median_pct, 6),
        "usd": round(median_usd, 4),
        "contracts": len(contracts),
        "skipped": skipped,
        "expiries": {k: v.isoformat() for k, v in picks.items()},
        "spot": round(spot, 4),
    }


def smooth(reads: list[Mapping]) -> dict:
    """The class over a ticker's last READS_KEPT same-day reads, as the study
    did over its three snapshots: median spread %, median $, and the ATM rule
    when it held on more than half of them. One read on its own flips a ticker
    sitting on a cut-off from class to class between chain refreshes."""
    pct = statistics.median(float(r["pct"]) for r in reads)
    usd = statistics.median(float(r["usd"]) for r in reads)
    atm = sum(1 for r in reads if r.get("atmRule")) * 2 > len(reads)
    return {"cls": classify(pct, atm), "medPct": round(pct * 100, 1), "medUsd": round(usd, 2),
            "atmRule": atm, "reads": len(reads)}


# --------------------------------------------------------------------------- time rules

def _et(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, ET)


def in_window(ts: float) -> bool:
    """A weekday between 10:00 and 15:30 ET."""
    t = _et(ts)
    return t.weekday() < 5 and WINDOW_START <= (t.hour, t.minute) < WINDOW_END


def is_fresh(entry: Mapping, now: float) -> bool:
    """Show the class? Inside the window: its chain was read within the last
    hour. After 15:30 ET: it was measured today. Before 10:00: never."""
    chain_ts = _num(entry.get("chainTs"))
    if chain_ts is None or chain_ts > now + 60:
        return False
    if _et(chain_ts).date() != _et(now).date():
        return False
    if in_window(now):
        return now - chain_ts <= SHOW_MAX_AGE_SECONDS
    return (_et(now).hour, _et(now).minute) >= WINDOW_END


def _scanned_ts(raw: Any) -> float | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ET)
    return parsed.timestamp()


# --------------------------------------------------------------------------- the book

class SpreadBook:
    """Per-ticker spread classes measured from the saved chain files, cached
    with their timestamps and persisted so a worker restart keeps the day."""

    def __init__(self, chain_dir: Path, store_path: Path | None = None):
        self.chain_dir = Path(chain_dir)
        self.store_path = Path(store_path) if store_path else None
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        self._seen_mtime: dict[str, float] = {}
        self._last_pass: float | None = None
        self._load()

    def _load(self) -> None:
        if not self.store_path:
            return
        try:
            doc = json.loads(self.store_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - no or broken store: start empty
            return
        entries = doc.get("entries") if isinstance(doc, dict) else None
        if isinstance(entries, dict):
            self._entries = {str(k).upper(): v for k, v in entries.items() if isinstance(v, dict)}

    def _save(self) -> None:
        if not self.store_path:
            return
        with self._lock:
            doc = {"source": "momx/option_spread.py from artifacts/oi_chain_cache", "entries": dict(self._entries)}
        tmp = self.store_path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
            os.replace(tmp, self.store_path)
        except OSError:
            pass

    def measure_file(self, path: Path, now: float) -> dict | None:
        """One chain file -> a cache entry, or None when it may not be measured
        (outside the window, too old, unreadable, or no usable quote)."""
        try:
            payload = json.loads(gzip.decompress(path.read_bytes()))
        except Exception:  # noqa: BLE001 - half-written or truncated file
            return None
        if not isinstance(payload, dict) or payload.get("optionQuoteSchemaVersion") != CHAIN_SCHEMA:
            return None
        chain_ts = _scanned_ts(payload.get("scannedAt"))
        if chain_ts is None or not in_window(chain_ts) or now - chain_ts > CHAIN_MAX_AGE_SECONDS:
            return None
        if chain_ts > now + 60:
            return None
        result = measure_chain(payload, _et(chain_ts).date())
        if result is None:
            return None
        return {**result, "chainTs": round(chain_ts, 1), "measuredTs": round(now, 1)}

    def refresh(self, now: float | None = None, pause: float = 0.0) -> int:
        """Re-measure every chain file that changed since the last pass.
        Does nothing outside the window. Returns how many classes changed."""
        now = time.time() if now is None else now
        if not in_window(now):
            return 0
        try:
            paths = list(self.chain_dir.glob("*.json.gz"))
        except OSError:
            return 0
        changed = 0
        for path in paths:
            symbol = path.name[: -len(".json.gz")].upper()
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if self._seen_mtime.get(symbol) == mtime or now - mtime > CHAIN_MAX_AGE_SECONDS:
                continue
            self._seen_mtime[symbol] = mtime
            entry = self.measure_file(path, now)
            if pause:
                time.sleep(pause)
            if entry is None:
                continue
            with self._lock:
                self._entries[symbol] = self._merge(self._entries.get(symbol), entry)
            changed += 1
        self._last_pass = now
        if changed:
            self._save()
        return changed

    @staticmethod
    def _merge(prior: Mapping | None, entry: dict) -> dict:
        """Add one read to the ticker's same-day reads and re-class over them."""
        day = _et(entry["chainTs"]).date()
        read = {"chainTs": entry["chainTs"], "pct": entry["pct"], "usd": entry["usd"], "atmRule": entry["atmRule"]}
        kept = [r for r in (prior or {}).get("recent") or []
                if isinstance(r, Mapping) and _num(r.get("chainTs")) and _num(r.get("pct")) is not None
                and _num(r.get("usd")) is not None and _et(float(r["chainTs"])).date() == day
                and float(r["chainTs"]) != entry["chainTs"]]
        recent = (kept + [read])[-READS_KEPT:]
        out = {k: v for k, v in entry.items() if k not in ("pct", "usd")}
        out.update(smooth(recent))
        out["recent"] = recent
        return out

    def snapshot(self, symbols: Iterable[str] | None = None, now: float | None = None) -> dict:
        """{"spreads": {SYMBOL: entry}} for the FRESH classes only."""
        now = time.time() if now is None else now
        wanted = {str(s).strip().upper() for s in symbols or () if str(s).strip()} or None
        with self._lock:
            items = list(self._entries.items())
        out = {}
        for symbol, entry in items:
            if wanted is not None and symbol not in wanted:
                continue
            if is_fresh(entry, now):
                shown = {k: v for k, v in entry.items() if k != "recent"}
                out[symbol] = {**shown, "note": CLASS_NOTE.get(entry.get("cls"), "")}
        return {
            "spreads": out,
            "inWindow": in_window(now),
            "window": "10:00-15:30 ET",
            "lastPassTs": self._last_pass,
            "held": len(items),
        }


# --------------------------------------------------------------------------- the worker's shared book

_BOOK: SpreadBook | None = None
_BOOK_LOCK = threading.Lock()


def book() -> SpreadBook:
    global _BOOK
    with _BOOK_LOCK:
        if _BOOK is None:
            from config import ARTIFACTS_DIR

            root = Path(ARTIFACTS_DIR)
            _BOOK = SpreadBook(root / "oi_chain_cache", root / "momx_option_spread.json")
        return _BOOK


def _loop() -> None:
    while True:
        try:
            book().refresh(pause=FILE_PAUSE_SECONDS)
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            print(f"[option-spread] pass failed: {type(exc).__name__}: {exc}", flush=True)
        time.sleep(PASS_SECONDS)


_STARTED = False


def start() -> None:
    """Start the background pass once (momx_worker.main)."""
    global _STARTED
    with _BOOK_LOCK:
        if _STARTED:
            return
        _STARTED = True
    threading.Thread(target=_loop, name="option-spread", daemon=True).start()
