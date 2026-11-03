"""Industry for ANY ticker, not just the hand-curated map.

Why this exists (trader, 2026-08-28: "I need any tickers industry is available
add it that's it"): ``momx.industries.INDUSTRY_SYMBOLS`` is a hand-edited map
covering the current watchlist. Any NEW symbol he pastes tomorrow would render a
blank Industry cell again. This module fills the gap from providers the app
already uses for the earnings calendar -- Finnhub first, FMP as fallback -- so
no new dependency and no new key.

Precedence, deliberate:
  1. the hand map (``momx.industries``) -- the trader's own MomoX-style labels
     always win, and editing that file overrides anything a provider says;
  2. the persistent cache (``artifacts/momx_industry_cache.json``);
  3. one provider fetch, after which the answer is cached forever -- including
     the answer "the provider has nothing" (stored as ""), because refetching a
     known-empty answer on every build would burn the rate limit on nothing.

Rate safety: a resolve pass fetches at most ``FETCH_BUDGET_PER_CALL`` missing
symbols (Finnhub free tier is 60 calls/min). A fresh 400-name list therefore
converges over a few board refreshes rather than hammering the API once. The
board never blocks on this: no key, network down, provider error -- the symbol
just stays blank this build and is retried on a later one (failures are NOT
negative-cached; only real empty answers are).
"""

from __future__ import annotations

import calendar
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

from momx.industries import industry_for

#: Max provider fetches per resolve() call. Finnhub free tier allows 60/min;
#: staying well under it leaves room for the earnings-calendar calls that share
#: the same key.
FETCH_BUDGET_PER_CALL = 40

#: Per-request timeout. A board build must never hang on a profile lookup.
FETCH_TIMEOUT_SECONDS = 10.0

CACHE_FILENAME = "momx_industry_cache.json"
CACHE_PATH_ENV = "MOMX_INDUSTRY_CACHE_PATH"

#: Provider labels -> the short MomoX-style labels the hand map uses, so a
#: fetched row looks like a curated one. Labels not listed here pass through
#: unchanged -- Finnhub's are readable ("Insurance", "Chemicals", ...).
SHORT_LABELS = {
    "Banking": "Banks",
    "Semiconductors": "Semis",
    "Biotechnology": "Biotech",
    "Pharmaceuticals": "Pharma",
    "Telecommunication": "Telecom",
    "Health Care": "Healthcare",
    "Financial Services": "Fintech",
    "Auto Components": "Auto-Tech",
    "Automobiles": "Autos",
    "Aerospace & Defense": "Defense",
    "Oil & Gas Services": "Oil & Gas",
    "Energy": "Oil & Gas",
    "Metals & Mining": "Metals",
    "Real Estate Management & Development": "Real Estate",
    "Hotels, Restaurants & Leisure": "Travel",
    "Textiles, Apparel & Luxury Goods": "Apparel",
    "Media": "Interactive Media",
    "Communication Services": "Interactive Media",
    "Consumer products": "Consumer",
    "Food Products": "Food",
    "Electrical Equipment": "Industrials",
    "Machinery": "Industrials",
    "Computers": "Tech-HW",
    "Technology": "Tech-HW",
    # FMP's label for leveraged single-stock/thematic ETFs (SNXX, MUU, ...)
    "Asset Management - Leveraged": "ETF-Lev",
    "Asset Management": "ETF",
    "Exchange Traded Fund": "ETF",
}

_LOCK = threading.Lock()
_CACHE: dict[str, dict] | None = None
_ENV_LOADED = False


def _ensure_env() -> None:
    """Load .env once if the keys are not already in the environment.

    Inside api_server the keys are present (config loads dotenv at boot), but
    this module is also used from bare scripts and tests-of-tools, where they
    are not - and a missing key looks identical to a provider outage ("fetch
    failed, will retry"), which cost a debugging round on 2026-08-28.
    """
    global _ENV_LOADED
    if _ENV_LOADED or os.getenv("FINNHUB_API_KEY") or os.getenv("FMP_API_KEY"):
        _ENV_LOADED = True
        return
    _ENV_LOADED = True
    try:
        from dotenv import load_dotenv

        load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    except Exception:
        pass


def cache_path() -> Path:
    override = os.getenv(CACHE_PATH_ENV)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "artifacts" / CACHE_FILENAME


def _load_cache(path: Path) -> dict[str, dict]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_cache(path: Path, cache: dict[str, dict]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(cache, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass  # a cache that cannot persist is a slower cache, not a failure


def _http_get(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def _shorten(label: Any) -> str:
    text = str(label or "").strip()
    if not text or text.upper() in ("N/A", "NA", "NONE"):
        return ""
    return SHORT_LABELS.get(text, text)


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 and number == number and number != float("inf") else None


def fetch_industry(
    symbol: str,
    *,
    get: Callable[[str, float], Any] | None = None,
    profile: dict | None = None,
) -> str | None:
    """One provider lookup. '' = provider answered 'nothing'; None = call failed.

    ``profile``, when given, also receives ``marketCap`` (USD) from the SAME
    response -- the Sector heatmap sizes its tiles by it, and reading it off the
    call we already make costs no extra provider request.

    The distinction matters for caching: '' is a real answer and is cached so it
    is never refetched; None must NOT be cached, so a transient outage does not
    permanently blank a symbol.
    """
    getter = get or _http_get
    _ensure_env()
    finnhub = str(os.getenv("FINNHUB_API_KEY", "")).strip()
    finnhub_answered = False
    if finnhub:
        try:
            data = getter(
                "https://finnhub.io/api/v1/stock/profile2?"
                + urllib.parse.urlencode({"symbol": symbol, "token": finnhub}),
                FETCH_TIMEOUT_SECONDS,
            )
            if isinstance(data, dict):
                finnhub_answered = True
                # Finnhub reports marketCapitalization in millions of USD.
                cap = _positive_float(data.get("marketCapitalization"))
                if profile is not None and cap is not None:
                    profile["marketCap"] = cap * 1_000_000
                label = _shorten(data.get("finnhubIndustry"))
                if label:
                    return label
                # An empty profile2 is a real Finnhub answer (ETFs, funds).
                # Fall through to FMP before concluding "nothing".
        except Exception:
            # A raise is NOT an answer. Without this distinction a one-off
            # outage was cached as "" and the symbol stayed blank forever.
            pass
    fmp = str(os.getenv("FMP_API_KEY", "")).strip()
    if fmp:
        try:
            data = getter(
                "https://financialmodelingprep.com/stable/profile?"
                + urllib.parse.urlencode({"symbol": symbol, "apikey": fmp}),
                FETCH_TIMEOUT_SECONDS,
            )
            rows = data if isinstance(data, list) else [data]
            for row in rows:
                if isinstance(row, dict):
                    cap = _positive_float(row.get("marketCap") or row.get("mktCap"))
                    if profile is not None and cap is not None and "marketCap" not in profile:
                        profile["marketCap"] = cap
                    label = _shorten(row.get("industry") or row.get("sector"))
                    if label:
                        return label
            return ""  # both providers answered; neither has an industry
        except Exception:
            return None
    if finnhub_answered:
        return ""  # Finnhub genuinely answered empty and there is no FMP to try
    return None  # no keys, or every configured provider failed


def resolve(
    symbols: Iterable[Any],
    *,
    budget: int = FETCH_BUDGET_PER_CALL,
    get: Callable[[str, float], Any] | None = None,
) -> dict[str, str]:
    """Industry for every symbol: hand map, then cache, then budgeted fetch."""
    wanted = []
    for item in symbols:
        name = str(item or "").strip().upper()
        if name and name not in wanted:
            wanted.append(name)

    path = cache_path()
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            _CACHE = _load_cache(path)
        cache = _CACHE

    out: dict[str, str] = {}
    missing: list[str] = []
    for name in wanted:
        curated = industry_for(name)
        if curated:
            out[name] = curated
            continue
        with _LOCK:
            entry = cache.get(name)
        if isinstance(entry, dict) and "industry" in entry:
            out[name] = str(entry.get("industry") or "")
            continue
        missing.append(name)

    fetched_any = False
    for name in missing[: max(0, int(budget))]:
        profile: dict = {}
        label = fetch_industry(name, get=get, profile=profile)
        if label is None:
            continue  # transient failure: leave uncached, retry next build
        out[name] = label
        stamp = _now_stamp()
        with _LOCK:
            cache[name] = {
                "industry": label,
                "fetchedAt": stamp,
                "marketCap": profile.get("marketCap"),
                "marketCapAt": stamp,
            }
        fetched_any = True

    if fetched_any:
        with _LOCK:
            snapshot = dict(cache)
        _save_cache(path, snapshot)
    return out


# ---------------------------------------------------------------------------
# Market cap for the Sector heatmap (trader ask, 2026-11-03).
#
# Same rules as the industry above: the request path only READS the cache;
# filling it is a small budgeted pass on a daemon thread, started at most once
# per FILL_MIN_INTERVAL_SECONDS and only while the Sector panel is open (the
# endpoint passes profile=1). It is not a standing background collector.
# ---------------------------------------------------------------------------

#: Provider fetches per fill pass. Shares the Finnhub 60/min key with the
#: earnings calendar and the board's industry resolve, so stay small.
MARKET_CAP_FETCH_BUDGET = 15
FILL_MIN_INTERVAL_SECONDS = 60.0
#: Caps drift; refresh a cached one after a week. Relative tile size is all
#: that depends on it, so a week-old number is fine.
MARKET_CAP_MAX_AGE_SECONDS = 7 * 24 * 3600

_FILL_LOCK = threading.Lock()
_FILL_RUNNING = False
_LAST_FILL_MONOTONIC: float | None = None


def _now_stamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _stamp_age_seconds(stamp: Any) -> float | None:
    try:
        parsed = time.strptime(str(stamp), "%Y-%m-%dT%H:%M:%SZ")
    except (TypeError, ValueError):
        return None
    return time.time() - calendar.timegm(parsed)


def _loaded_cache() -> dict[str, dict]:
    global _CACHE
    with _LOCK:
        if _CACHE is None:
            _CACHE = _load_cache(cache_path())
        return _CACHE


def _normalise(symbols: Iterable[Any]) -> list[str]:
    wanted: list[str] = []
    for item in symbols:
        name = str(item or "").strip().upper()
        if name and name not in wanted:
            wanted.append(name)
    return wanted


def cached_profiles(symbols: Iterable[Any]) -> dict[str, dict]:
    """``{SYMBOL: {"industry", "marketCap"}}`` from the hand map + cache. No network.

    ``industry`` is None when nothing is known yet (or the provider has none);
    ``marketCap`` is USD or None.
    """
    cache = _loaded_cache()
    out: dict[str, dict] = {}
    for name in _normalise(symbols):
        with _LOCK:
            entry = cache.get(name)
        entry = entry if isinstance(entry, dict) else {}
        industry = industry_for(name) or str(entry.get("industry") or "") or None
        out[name] = {
            "industry": industry,
            "marketCap": _positive_float(entry.get("marketCap")),
        }
    return out


def _needs_market_cap(entry: Any) -> int:
    """0 = fresh, 1 = stale (refresh later), 2 = never fetched (fetch first)."""
    if not isinstance(entry, dict) or "marketCapAt" not in entry:
        return 2
    age = _stamp_age_seconds(entry.get("marketCapAt"))
    if age is None or age > MARKET_CAP_MAX_AGE_SECONDS:
        return 1
    return 0


def resolve_market_caps(
    symbols: Iterable[Any],
    *,
    budget: int = MARKET_CAP_FETCH_BUDGET,
    get: Callable[[str, float], Any] | None = None,
) -> int:
    """Fetch market cap for up to ``budget`` symbols that lack a fresh one.

    Never-fetched symbols go before stale ones. A provider answer with no cap
    (ETFs on Finnhub) is cached as None so it is not refetched for a week; a
    failed call is not cached at all. Returns how many entries were written.
    """
    cache = _loaded_cache()
    ranked: list[tuple[int, int, str]] = []
    for index, name in enumerate(_normalise(symbols)):
        with _LOCK:
            need = _needs_market_cap(cache.get(name))
        if need:
            ranked.append((-need, index, name))
    ranked.sort()

    written = 0
    for _, _, name in ranked[: max(0, int(budget))]:
        profile: dict = {}
        label = fetch_industry(name, get=get, profile=profile)
        if label is None:
            continue
        stamp = _now_stamp()
        with _LOCK:
            entry = dict(cache.get(name) or {})
            if "industry" not in entry:
                entry["industry"] = label
                entry["fetchedAt"] = stamp
            entry["marketCap"] = profile.get("marketCap")
            entry["marketCapAt"] = stamp
            cache[name] = entry
        written += 1

    if written:
        with _LOCK:
            snapshot = dict(cache)
        _save_cache(cache_path(), snapshot)
    return written


def schedule_market_cap_fill(
    symbols: Iterable[Any],
    *,
    start_thread: Callable[[Callable[[], None]], Any] | None = None,
) -> bool:
    """Start one budgeted fill pass on a daemon thread if one is due.

    Returns True when a pass was started. Cheap enough to call from every
    Sector-panel poll: it only checks a timestamp unless work is needed.
    """
    global _FILL_RUNNING, _LAST_FILL_MONOTONIC
    wanted = _normalise(symbols)
    if not wanted:
        return False
    cache = _loaded_cache()
    with _LOCK:
        due = any(_needs_market_cap(cache.get(name)) for name in wanted)
    if not due:
        return False
    with _FILL_LOCK:
        now = time.monotonic()
        if _FILL_RUNNING:
            return False
        if _LAST_FILL_MONOTONIC is not None and now - _LAST_FILL_MONOTONIC < FILL_MIN_INTERVAL_SECONDS:
            return False
        _FILL_RUNNING = True
        _LAST_FILL_MONOTONIC = now

    def run() -> None:
        global _FILL_RUNNING
        try:
            resolve_market_caps(wanted)
        except Exception:
            pass  # the panel just keeps its estimated sizes this round
        finally:
            with _FILL_LOCK:
                _FILL_RUNNING = False

    if start_thread is not None:
        start_thread(run)
    else:
        threading.Thread(target=run, name="sector-market-cap-fill", daemon=True).start()
    return True


def clear_memory_cache() -> None:
    """Testing hook: forget the in-process cache (the file is untouched)."""
    global _CACHE, _FILL_RUNNING, _LAST_FILL_MONOTONIC
    with _LOCK:
        _CACHE = None
    with _FILL_LOCK:
        _FILL_RUNNING = False
        _LAST_FILL_MONOTONIC = None


__all__ = [
    "FETCH_BUDGET_PER_CALL",
    "cache_path",
    "clear_memory_cache",
    "fetch_industry",
    "resolve",
]
