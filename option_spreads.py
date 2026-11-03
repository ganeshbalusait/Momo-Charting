"""Option bid/ask spread per watchlist ticker - the MomX board's Spread column.

2026-11-03, Ganesh: "from our watchlist tickers give me which tickers have
more wide option spread - when we fill mid price it won't fill". Then: "add
it as a Spread column in the watchlist" (the MomX board).

WHAT ONE READING IS
  The contracts he actually trades: the nearest expiry with at least 1 DTE
  plus the next standard monthly (3rd Friday), ATM and 2 strikes either side,
  calls and puts - up to 20 contracts. Each contract's spread is
  (ask - bid) / mid. A contract with no bid, no ask or a crossed quote is
  skipped and counted. The reading is the MEDIAN over the sampled contracts,
  so one stale strike cannot flag a ticker.

CLASSES (median spread as % of mid)
  Tight < 3%   OK 3-8%   Wide 8-15%   Avoid > 15%
  Avoid also when the ATM contracts priced under $3 carry a median spread of
  $0.30 or more - a $1.20 option with a 30c spread never fills at mid.

LOAD - the same rules every other background chain reader here lives under
  (momx/optionable.py, the after-close history loop in api_server):
  * ONE small Schwab request per ticker (strikeCount 6, 0-50 DTE), through
    SchwabClient's rate gate - never the heavy OI Finder build;
  * one ticker at a time, SWEEP_GAP_SECONDS apart;
  * only 10:00-15:30 ET on weekdays - opening and closing quotes are wide on
    every name and would flag the whole board;
  * a ticker is re-read at most every REFRESH_SECONDS;
  * the caller yields while the trader is using charts and stops on a pause
    marker or a Schwab block.
  Per day that is ~2-3 readings per ticker; the column shows the median of
  the latest session's readings.

Never raises into the caller. No thread starts at import.
"""
from __future__ import annotations

import json
import os
import statistics
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
STORE_NAME = "option_spreads.json"

STRIKES_EACH_SIDE = 2
CHAIN_STRIKE_COUNT = 6            # Schwab strikeCount: enough for ATM +/- 2
CHAIN_WINDOW_DAYS = 50            # always reaches the next monthly
CHEAP_OPTION_MID = 3.0
CHEAP_OPTION_MAX_SPREAD = 0.30

TIGHT_MAX_PCT = 3.0
OK_MAX_PCT = 8.0
WIDE_MAX_PCT = 15.0

SWEEP_START = (10, 0)
SWEEP_END = (15, 30)
SWEEP_GAP_SECONDS = 20.0
REFRESH_SECONDS = 90 * 60
READINGS_KEPT = 6
BLOCKED_PAUSE_SECONDS = 600.0
IDLE_SECONDS = 60.0

CLASSES = ("Tight", "OK", "Wide", "Avoid")


def store_path() -> Path:
    override = os.environ.get("AGX_OPTION_SPREADS_DIR", "").strip()
    if override:
        return Path(override) / STORE_NAME
    from config import ARTIFACTS_DIR

    return Path(ARTIFACTS_DIR) / STORE_NAME


def enabled() -> bool:
    return str(os.environ.get("AGX_OPTION_SPREADS", "1")).strip().lower() not in {"0", "false", "off", "no"}


# --------------------------------------------------------------------------
# Pure analysis
# --------------------------------------------------------------------------

def _num(value: Any, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed == parsed else fallback


def _m(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def is_third_friday(day: date) -> bool:
    return day.weekday() == 4 and 15 <= day.day <= 21


def in_sweep_window(now: datetime) -> bool:
    local = now.astimezone(ET)
    if local.weekday() >= 5:
        return False
    return SWEEP_START <= (local.hour, local.minute) < SWEEP_END


def spot_price(chain: Mapping) -> float:
    spot = _num(chain.get("underlyingPrice"))
    if spot > 0:
        return spot
    quote = _m(chain.get("underlying"))
    return _num(quote.get("last")) or _num(quote.get("mark")) or _num(quote.get("close"))


def chain_contracts(chain: Mapping) -> list[dict]:
    """Schwab-shaped chain (Tradier is normalised to it) -> flat contracts."""
    rows: list[dict] = []
    for side, key in (("C", "callExpDateMap"), ("P", "putExpDateMap")):
        for exp_key, strikes in _m(chain.get(key)).items():
            try:
                expiry = date.fromisoformat(str(exp_key).split(":")[0][:10])
            except ValueError:
                continue
            for strike_key, contracts in _m(strikes).items():
                for contract in contracts if isinstance(contracts, list) else ():
                    c = _m(contract)
                    strike = _num(c.get("strikePrice") or strike_key)
                    if strike <= 0:
                        continue
                    rows.append({
                        "side": side,
                        "expiry": expiry,
                        "strike": strike,
                        "bid": _num(c.get("bid", c.get("bidPrice"))),
                        "ask": _num(c.get("ask", c.get("askPrice"))),
                        "oi": _num(c.get("openInterest", c.get("open_interest"))),
                        "volume": _num(c.get("totalVolume", c.get("volume"))),
                    })
    return rows


def pick_expiries(expiries: Iterable[date], today: date) -> list[date]:
    ordered = sorted(set(expiries))
    future = [e for e in ordered if e > today] or [e for e in ordered if e >= today]
    if not future:
        return []
    picked = [future[0]]
    monthly = next((e for e in future if is_third_friday(e) and e != future[0]), None)
    if monthly is not None:
        picked.append(monthly)
    return picked


def classify(median_pct: Any, cheap_atm_spread: Any = None) -> str | None:
    if median_pct is None:
        return None
    pct = _num(median_pct, -1.0)
    if pct < 0:
        return None
    if pct > WIDE_MAX_PCT or (cheap_atm_spread is not None and _num(cheap_atm_spread) >= CHEAP_OPTION_MAX_SPREAD):
        return "Avoid"
    if pct >= OK_MAX_PCT:
        return "Wide"
    if pct >= TIGHT_MAX_PCT:
        return "OK"
    return "Tight"


def analyse(chain: Any, today: date) -> dict:
    """One chain -> one reading. {"error": ...} when nothing usable."""
    chain = _m(chain)
    spot = spot_price(chain)
    rows = chain_contracts(chain)
    if not rows:
        return {"error": "no option contracts"}
    if spot <= 0:
        return {"error": "no underlying price"}
    expiries = pick_expiries((r["expiry"] for r in rows), today)
    sampled: list[dict] = []
    atm: list[dict] = []
    skipped = 0
    for index, expiry in enumerate(expiries):
        exp_rows = [r for r in rows if r["expiry"] == expiry]
        strikes = sorted({r["strike"] for r in exp_rows})
        if not strikes:
            continue
        atm_index = min(range(len(strikes)), key=lambda k: abs(strikes[k] - spot))
        wanted = set(strikes[max(0, atm_index - STRIKES_EACH_SIDE): atm_index + STRIKES_EACH_SIDE + 1])
        atm_strike = strikes[atm_index]
        for r in exp_rows:
            if r["strike"] not in wanted:
                continue
            if r["bid"] <= 0 or r["ask"] <= 0 or r["ask"] < r["bid"]:
                skipped += 1
                continue
            mid = (r["bid"] + r["ask"]) / 2.0
            rec = dict(r, mid=mid, spread=r["ask"] - r["bid"], pct=(r["ask"] - r["bid"]) / mid * 100.0,
                       front=index == 0)
            sampled.append(rec)
            if r["strike"] == atm_strike:
                atm.append(rec)
    if not sampled:
        return {"error": f"no two-sided quotes near the money ({skipped} without a bid)"}
    cheap = [r["spread"] for r in atm if r["mid"] < CHEAP_OPTION_MID]
    return {
        "pct": round(statistics.median(r["pct"] for r in sampled), 2),
        "usd": round(statistics.median(r["spread"] for r in sampled), 3),
        "worstAtmPct": round(max((r["pct"] for r in atm), default=0.0), 2),
        "cheapAtmUsd": round(statistics.median(cheap), 3) if cheap else None,
        "atmOi": int(sum(r["oi"] for r in atm if r["front"])),
        "volume": int(sum(r["volume"] for r in sampled)),
        "spot": round(spot, 4),
        "sampled": len(sampled),
        "skipped": skipped,
        "expiries": [e.isoformat() for e in expiries],
    }


def summarise(readings: Iterable[Mapping]) -> dict | None:
    """The column's value: median of the LATEST session's readings."""
    good = [dict(r) for r in readings or () if isinstance(r, Mapping) and r.get("pct") is not None and r.get("at")]
    if not good:
        return None
    good.sort(key=lambda r: str(r["at"]))
    session = str(good[-1]["at"])[:10]
    today = [r for r in good if str(r["at"])[:10] == session]
    cheap = [r["cheapAtmUsd"] for r in today if r.get("cheapAtmUsd") is not None]
    pct = round(statistics.median(_num(r["pct"]) for r in today), 2)
    cheap_usd = round(statistics.median(_num(c) for c in cheap), 3) if cheap else None
    last = today[-1]
    return {
        "pct": pct,
        "usd": round(statistics.median(_num(r.get("usd")) for r in today), 3),
        "cheapAtmUsd": cheap_usd,
        "class": classify(pct, cheap_usd),
        "worstAtmPct": last.get("worstAtmPct"),
        "atmOi": last.get("atmOi"),
        "volume": last.get("volume"),
        "sampled": last.get("sampled"),
        "expiries": last.get("expiries"),
        "source": last.get("source"),
        "readings": len(today),
        "session": session,
        "asOf": last["at"],
    }


# --------------------------------------------------------------------------
# The book: readings on disk, one writer (api_server), many readers
# --------------------------------------------------------------------------

class SpreadBook:
    def __init__(self, path: Path | None = None) -> None:
        self._path_override = Path(path) if path is not None else None
        self._lock = threading.Lock()
        self._entries: dict[str, dict] | None = None

    def path(self) -> Path:
        return self._path_override or store_path()

    def _load(self) -> dict:
        if self._entries is None:
            try:
                doc = json.loads(self.path().read_text(encoding="utf-8"))
                symbols = _m(doc).get("symbols")
                self._entries = {str(k).upper(): dict(v) for k, v in _m(symbols).items() if isinstance(v, Mapping)}
            except (OSError, ValueError, UnicodeDecodeError):
                self._entries = {}
        return self._entries

    def _save(self) -> None:
        path = self.path()
        doc = {"source": "Schwab option chain (Tradier fallback), option_spreads.py", "symbols": self._entries or {}}
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(doc, separators=(",", ":"), sort_keys=True), encoding="utf-8")
            for attempt in range(5):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:   # Windows: a reader has it open
                    time.sleep(0.05 * (attempt + 1))
            tmp.unlink(missing_ok=True)
        except OSError:
            pass

    def record(self, symbol: str, reading: Mapping, at: datetime, epoch: float) -> None:
        symbol = str(symbol or "").strip().upper()
        if not symbol:
            return
        stamp = at.astimezone(ET).replace(microsecond=0).isoformat()
        with self._lock:
            entry = self._load().setdefault(symbol, {})
            entry["checkedEpoch"] = float(epoch)
            if reading.get("error"):
                entry["lastError"] = str(reading["error"])[:200]
                entry["lastErrorAt"] = stamp
            else:
                kept = [r for r in entry.get("readings") or [] if isinstance(r, Mapping)]
                kept.append({**{k: v for k, v in reading.items() if k != "error"}, "at": stamp})
                entry["readings"] = kept[-READINGS_KEPT:]
                entry.pop("lastError", None)
                entry.pop("lastErrorAt", None)
            self._save()

    def due(self, symbols: Iterable[str], epoch: float) -> list[str]:
        """Symbols to read, never-read first, then oldest first."""
        with self._lock:
            entries = self._load()
            candidates = []
            for raw in symbols or ():
                symbol = str(raw or "").strip().upper()
                if not symbol:
                    continue
                entry = entries.get(symbol)
                if entry is None or entry.get("checkedEpoch") is None:
                    candidates.append((float("-inf"), symbol))     # never read: always due, first
                    continue
                checked = _num(entry.get("checkedEpoch"))
                if epoch - checked >= REFRESH_SECONDS:
                    candidates.append((checked, symbol))
        candidates.sort()
        return [symbol for _, symbol in candidates]

    def payload(self, symbols: Iterable[str] | None = None) -> dict:
        with self._lock:
            entries = dict(self._load())
        wanted = None if symbols is None else {str(s or "").strip().upper() for s in symbols}
        spreads: dict[str, dict] = {}
        errors: dict[str, str] = {}
        for symbol, entry in entries.items():
            if wanted is not None and symbol not in wanted:
                continue
            summary = summarise(entry.get("readings") or [])
            if summary:
                spreads[symbol] = summary
            elif entry.get("lastError"):
                errors[symbol] = entry["lastError"]
        return {
            "spreads": spreads,
            "errors": errors,
            "thresholds": {"tightMaxPct": TIGHT_MAX_PCT, "okMaxPct": OK_MAX_PCT, "wideMaxPct": WIDE_MAX_PCT,
                           "cheapOptionMid": CHEAP_OPTION_MID, "cheapOptionMaxSpread": CHEAP_OPTION_MAX_SPREAD},
            "window": f"{SWEEP_START[0]:02d}:{SWEEP_START[1]:02d}-{SWEEP_END[0]:02d}:{SWEEP_END[1]:02d} ET",
        }


# --------------------------------------------------------------------------
# The sweep (api_server runs this on its own daemon thread)
# --------------------------------------------------------------------------

class SweepStatus:
    def __init__(self) -> None:
        self.state = "Idle"
        self.message = ""
        self.done_today = 0
        self.failed_today = 0
        self.day = ""

    def as_dict(self) -> dict:
        return {"state": self.state, "message": self.message, "readToday": self.done_today,
                "failedToday": self.failed_today}


def sweep_once(book: SpreadBook, *, symbols: Callable[[], list[str]], fetch: Callable[[str], tuple[Any, str]],
               should_wait: Callable[[], bool], paused: Callable[[], bool], status: SweepStatus,
               now: Callable[[], datetime] = lambda: datetime.now(ET), clock: Callable[[], float] = time.time,
               sleep: Callable[[float], None] = time.sleep, blocked: Callable[[BaseException], bool] = lambda e: False,
               max_symbols: int | None = None) -> int:
    """Read the due symbols once, paced. Returns how many chains were requested.

    Stops early when the window closes, a pause marker appears, or Schwab
    reports a block (then sleeps BLOCKED_PAUSE_SECONDS)."""
    current = now()
    if not in_sweep_window(current):
        status.state, status.message = "Waiting", "Reads spreads 10:00-15:30 ET on weekdays."
        return 0
    day = current.astimezone(ET).date().isoformat()
    if status.day != day:
        status.day, status.done_today, status.failed_today = day, 0, 0
    due = book.due(symbols(), clock())
    if max_symbols is not None:
        due = due[:max_symbols]
    if not due:
        status.state, status.message = "Up to date", "Every watchlist ticker has a recent reading."
        return 0
    requested = 0
    for index, symbol in enumerate(due):
        if paused():
            status.state, status.message = "Paused", "A pause marker is set in the artifacts folder."
            return requested
        waited = 0.0
        while should_wait() and waited < 120.0:
            status.state = "Yielding to active trader"
            sleep(2.0)
            waited += 2.0
        stamp = now()
        if not in_sweep_window(stamp):
            status.state, status.message = "Waiting", "Spread window closed for today."
            return requested
        status.state = "Reading spreads"
        status.message = f"{symbol} ({index + 1}/{len(due)} due)"
        try:
            chain, source = fetch(symbol)
            reading = analyse(chain, stamp.astimezone(ET).date())
            reading["source"] = source
        except Exception as exc:  # noqa: BLE001 - one bad ticker never stops the sweep
            if blocked(exc):
                status.state, status.message = "Schwab blocked", "Backing off before the next read."
                sleep(BLOCKED_PAUSE_SECONDS)
                return requested
            reading = {"error": f"{type(exc).__name__}: {exc}"}
        requested += 1
        book.record(symbol, reading, stamp, clock())
        if reading.get("error"):
            status.failed_today += 1
        else:
            status.done_today += 1
        if index < len(due) - 1:
            sleep(SWEEP_GAP_SECONDS)
    return requested
