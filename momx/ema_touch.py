"""21 / 50 EMA touch - the two derived MomX lists (2026-10-04).

The EMA lists use the four ALL conditions confirmed on 2026-11-02:
Last >= $3, 4h EXT volume >= 1.0% above two bars ago, 1h EXT close
>= 0.5% above two bars ago, and current-period EMA candle touch.
At least one shown MACD, squeeze-fire or RVOL signal must also pass.
EMA touch determines membership; ALL and ANY determine scanPass.

TIMEFRAME is one setting per EMA length (``artifacts/momx_ema_touch_tf.json``,
default Monthly) and is part of the list's NAME ("21 EMA Touch Weekly"), so
History, matched-since stamps and the Jev log never mix timeframes - switching
is switching to another list.

THE GATE matches the supplied TOS custom study:
  ema = ExpAverage(close, length)
  scan = low <= ema AND high >= ema
The current period's close participates in the EMA; no trend condition is
added. At least 2n historical bars are retained as a data-quality guard.

DATA: Alpaca SIP bars, split-adjusted (monthly matched Yahoo to the cent on
MSFT/TSLA/BKNG): 1Month since 2015, 1Week ~3 years, 1Day ~14 months. One
batched request per 100 symbols per timeframe, every 10 minutes 04:00-20:00
ET on weekdays (hourly otherwise), by the warmer process only; every other
reader takes the in-memory copy, reloaded from ``artifacts/momx_ema_touch.json``
when another process rewrote it. Reading never touches the network.

AGX_MOMX_EMA_LISTS=0 removes both lists (the test suite does).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from config import ARTIFACTS_DIR

FORMULA_VERSION = 2
LENGTHS = (21, 50)
#: picker order; name -> (Alpaca timeframe, days of history, period key length)
TIMEFRAMES: dict[str, tuple[str, int | None, int]] = {
    "Monthly": ("1Month", None, 7),     # None = since HISTORY_START
    "Weekly": ("1Week", 365 * 3, 10),
    "Daily": ("1Day", 430, 10),
}
DEFAULT_TIMEFRAME = "Monthly"
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
HISTORY_START = "2015-01-01"
BATCH = 100
REFRESH_SECONDS = 600.0
QUIET_REFRESH_SECONDS = 3600.0
#: a cache older than this is not trusted (refresher dead / no data access).
STALE_SECONDS = 3 * 86400.0
#: SIP bars younger than this are refused on the standard data plan.
SIP_DELAY = timedelta(minutes=16)
_ET = ZoneInfo("America/New_York")

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"symbols": {}, "fetchedAt": None, "periods": {}}
_CACHE_MTIME: float | None = None
_THREAD: threading.Thread | None = None
_WANTED: set[str] = set()


def enabled() -> bool:
    return os.environ.get("AGX_MOMX_EMA_LISTS", "1").strip() != "0"


def cache_path() -> Path:
    override = os.environ.get("AGX_MOMX_EMA_TOUCH_PATH")
    return Path(override) if override else Path(ARTIFACTS_DIR) / "momx_ema_touch.json"


def settings_path() -> Path:
    override = os.environ.get("AGX_MOMX_EMA_TF_PATH")
    return Path(override) if override else Path(ARTIFACTS_DIR) / "momx_ema_touch_tf.json"


# ---------------------------------------------------------------------------
# names and the timeframe setting
# ---------------------------------------------------------------------------


def list_name(length: int, tf: str | None = None) -> str:
    return f"{int(length)} EMA Touch {tf or timeframe(length)}"


def parse_name(name: Any) -> tuple[int, str] | None:
    """"21 ema touch weekly" -> (21, "Weekly"); anything else -> None."""
    wanted = str(name or "").strip().lower()
    for length in LENGTHS:
        for tf in TIMEFRAMES:
            if wanted == list_name(length, tf).lower():
                return length, tf
    return None


_SETTINGS: dict[str, Any] = {"key": None, "saved": {}}


def timeframe(length: int) -> str:
    # Read on every list-document read (many per build), so re-parsed only
    # when the file changes.
    path = settings_path()
    try:
        key = (str(path), path.stat().st_mtime)
    except OSError:
        key = (str(path), None)
    if key != _SETTINGS["key"]:
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            saved = {}
        _SETTINGS.update(key=key, saved=saved if isinstance(saved, Mapping) else {})
    tf = str(_SETTINGS["saved"].get(str(int(length))) or "")
    return tf if tf in TIMEFRAMES else DEFAULT_TIMEFRAME


def set_timeframe(length: Any, tf: Any) -> str:
    """Save the timeframe for one EMA length; returns the list's new name."""
    try:
        n = int(length)
    except (TypeError, ValueError):
        raise ValueError(f"Unknown EMA length {length!r}.") from None
    if n not in LENGTHS:
        raise ValueError(f"Unknown EMA length {length!r}.")
    choice = next((t for t in TIMEFRAMES if t.lower() == str(tf or "").strip().lower()), None)
    if choice is None:
        raise ValueError(f"Timeframe must be one of {', '.join(TIMEFRAMES)}.")
    path = settings_path()
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved = dict(saved) if isinstance(saved, Mapping) else {}
    except (OSError, ValueError):
        saved = {}
    saved[str(n)] = choice
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(saved), encoding="utf-8")
    os.replace(temporary, path)
    return list_name(n, choice)


def current_lists() -> dict[str, int]:
    """Visible list name -> EMA length, in picker order."""
    return {list_name(n): n for n in LENGTHS} if enabled() else {}


def list_names() -> list[str]:
    return list(current_lists())


def is_derived(name: Any) -> bool:
    """Any timeframe's name, current or not - none may ever be saved/edited."""
    return enabled() and parse_name(name) is not None


# ---------------------------------------------------------------------------
# the gate (pure)
# ---------------------------------------------------------------------------


def ema(values: Sequence[float], length: int) -> list[float]:
    """thinkorswim ExpAverage: alpha 2/(n+1), seeded with the first value."""
    alpha = 2.0 / (length + 1.0)
    out: list[float] = []
    for value in values:
        out.append(value if not out else out[-1] + alpha * (value - out[-1]))
    return out


def touch_state(bars: Sequence[Mapping[str, Any]], length: int, period: str) -> dict | None:
    """Current-period candle straddles its current EMA, as in the TOS study."""
    import math
    usable = [b for b in bars if str(b.get("period")) <= period]
    if len(usable) < max(2 * length + 1, 3) or usable[-1].get("period") != period:
        return None
    try:
        closes = [float(b["close"]) for b in usable]
        low, high = float(usable[-1]["low"]), float(usable[-1]["high"])
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in closes + [low, high]) or low > high:
        return None
    line = ema(closes, length)
    level = line[-1]
    touched = low <= level <= high
    return {"level": round(level, 4), "uptrend": bool(closes[-1] > level and level > line[-2]),
            "low": low, "high": high, "touched": bool(touched), "pass": bool(touched)}



# ---------------------------------------------------------------------------
# reading (never blocks, never raises)
# ---------------------------------------------------------------------------


def _reload_from_disk() -> None:
    global _CACHE_MTIME
    path = cache_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return
    if mtime == _CACHE_MTIME:
        return
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if isinstance(document, Mapping) and isinstance(document.get("symbols"), Mapping):
        with _LOCK:
            _CACHE.clear()
            _CACHE.update(document)
            _CACHE_MTIME = mtime


def _fresh(fetched_at: Any) -> bool:
    try:
        stamp = datetime.fromisoformat(str(fetched_at))
    except ValueError:
        return False
    return (datetime.now(timezone.utc) - stamp).total_seconds() <= STALE_SECONDS


def touchers(symbols: Iterable[str], length: int, tf: str | None = None) -> list[str]:
    """The given symbols (in their order) that pass the gate.

    Uses the last refresh. A symbol never fetched is simply not a member yet;
    it is queued so the next refresh reads it.
    """
    universe = [str(s) for s in symbols or []]
    if not enabled() or not universe:
        return []
    _reload_from_disk()
    key = f"{int(length)}:{tf or timeframe(length)}"
    with _LOCK:
        table = _CACHE.get("symbols") or {}
        missing = [s for s in universe if s not in table]
        if missing:
            _WANTED.update(missing)
        if _CACHE.get("formulaVersion") != FORMULA_VERSION or not _fresh(_CACHE.get("fetchedAt")):
            return []
        return [s for s in universe if ((table.get(s) or {}).get(key) or {}).get("pass")]


def ready() -> bool:
    """Whether membership can be evaluated using the current formula."""
    _reload_from_disk()
    with _LOCK:
        return _CACHE.get("formulaVersion") == FORMULA_VERSION and _fresh(_CACHE.get("fetchedAt"))


def detail(symbol: str) -> dict:
    """``{"21:Monthly": {...}, ...}`` for one symbol, for display/debug."""
    _reload_from_disk()
    with _LOCK:
        return dict((_CACHE.get("symbols") or {}).get(str(symbol).upper()) or {})


# ---------------------------------------------------------------------------
# refreshing (warmer process only)
# ---------------------------------------------------------------------------


def _fetch(symbols: list[str], tf: str, now: datetime) -> dict[str, list[dict]]:
    import requests

    from momx import feed

    alpaca_tf, days, key_len = TIMEFRAMES[tf]
    start = HISTORY_START if days is None else (now - timedelta(days=days)).date().isoformat()
    end = (now - SIP_DELAY).isoformat()
    out: dict[str, list[dict]] = {}
    credentials = feed.resolve_credentials()
    for offset in range(0, len(symbols), BATCH):
        chunk = symbols[offset:offset + BATCH]
        for credential in credentials:
            params = {
                "symbols": ",".join(chunk), "timeframe": alpaca_tf, "start": start,
                "end": end, "adjustment": "split", "feed": "sip", "limit": 10000,
            }
            got: dict[str, list[dict]] = {}
            ok = True
            while True:
                response = requests.get(BARS_URL, params=params, headers=credential.headers, timeout=30)
                if response.status_code in (401, 403):
                    ok = False
                    break
                response.raise_for_status()
                body = response.json() or {}
                for sym, bars in (body.get("bars") or {}).items():
                    got.setdefault(sym, []).extend(bars or [])
                token = body.get("next_page_token")
                if not token:
                    break
                params["page_token"] = token
            if ok:
                for sym, bars in got.items():
                    out[sym] = [
                        {"period": str(b["t"])[:key_len], "low": b["l"], "high": b["h"], "close": b["c"]}
                        for b in bars if b.get("t") and b.get("c") is not None
                    ]
                break
    return out


def refresh(symbols: Iterable[str] | None = None, now: datetime | None = None) -> dict:
    """Fetch every timeframe and rewrite the cache. Returns a short summary."""
    now = now or datetime.now(timezone.utc)
    with _LOCK:
        wanted = sorted(set(symbols or []) | _WANTED | set((_CACHE.get("symbols") or {}).keys()))
    if not wanted:
        return {"symbols": 0}
    table: dict[str, dict] = {sym: {} for sym in wanted}
    periods: dict[str, str] = {}
    summary: dict[str, Any] = {"symbols": len(wanted)}
    for tf in TIMEFRAMES:
        bars = _fetch(wanted, tf, now)
        latest = max((rows[-1]["period"] for rows in bars.values() if rows), default="")
        periods[tf] = latest
        for sym in wanted:
            rows = bars.get(sym) or []
            for n in LENGTHS:
                table[sym][f"{n}:{tf}"] = touch_state(rows, n, latest) if latest else None
        for n in LENGTHS:
            summary[f"{n}:{tf}"] = sum(1 for v in table.values() if (v.get(f"{n}:{tf}") or {}).get("pass"))
    document = {"formulaVersion": FORMULA_VERSION, "fetchedAt": now.isoformat(), "periods": periods, "symbols": table}
    path = cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(document), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass
    global _CACHE_MTIME
    with _LOCK:
        _CACHE.clear()
        _CACHE.update(document)
        _WANTED.difference_update(wanted)
        try:
            _CACHE_MTIME = path.stat().st_mtime
        except OSError:
            _CACHE_MTIME = None
    summary["periods"] = periods
    return summary


def _interval(now: datetime) -> float:
    et = now.astimezone(_ET)
    if et.weekday() < 5 and 4 <= et.hour < 20:
        return REFRESH_SECONDS
    return QUIET_REFRESH_SECONDS


def _loop(universe: Any) -> None:
    while True:
        started = time.monotonic()
        try:
            refresh(universe())
        except Exception:  # noqa: BLE001 - a refresher must never die
            pass
        wait = _interval(datetime.now(timezone.utc))
        # Wake early when a reader queued a symbol we have never fetched.
        while time.monotonic() - started < wait:
            time.sleep(15.0)
            with _LOCK:
                if _WANTED:
                    break


def start_refresher(universe: Any) -> None:
    """Start the background refresher once. ``universe()`` -> symbols to read."""
    global _THREAD
    if not enabled():
        return
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(target=_loop, args=(universe,), name="momx-ema-touch", daemon=True)
        thread = _THREAD
    thread.start()


__all__ = [
    "DEFAULT_TIMEFRAME",
    "LENGTHS",
    "TIMEFRAMES",
    "cache_path",
    "current_lists",
    "detail",
    "ema",
    "enabled",
    "is_derived",
    "list_name",
    "list_names",
    "parse_name",
    "refresh",
    "set_timeframe",
    "start_refresher",
    "timeframe",
    "touch_state",
    "touchers",
]
