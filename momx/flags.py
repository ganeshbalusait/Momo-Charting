"""MomoX lightning badge: the three "look at this one" signals behind the flag.

The badge lights when ANY of the trader's three reasons is true, in this order:

1. ``weeklies`` - the symbol trades WEEKLY option expiries
2. ``earnings`` - an earnings report is coming up soon (default: within 7 days)
3. ``rvol``     - unusual relative volume already on the row

:func:`build_badge` is the only thing the board needs::

    {"on": True, "reasons": ["weeklies", "earnings"],
     "tooltip": "Weekly options - earnings in 3 days"}

*** PERFORMANCE CONSTRAINTS -- SAME ONES momx.board LIVES UNDER ***

This module STARTS NO THREAD ON IMPORT, does NOT import ``api_server``, and
makes ZERO per-symbol network calls. The board runs hundreds of symbols on a
timer and this repository has a documented history of background collectors
starving the chart engine of CPU; a per-symbol broker or vendor call inside a
row builder is exactly that shape.

WHERE THE DATA COMES FROM (and what was deliberately not reused)
---------------------------------------------------------------

* WEEKLIES -- the only option-expiry source in this repository is
  ``api_server``'s ``market_data_client.get_option_chain(symbol, ...)`` (see
  ``_oi_finder_*`` and ``_option_oi_wall_plan``). That is a LIVE CHAIN FETCH
  PER SYMBOL. It is fine for the OI Finder, which looks at the one ticker the
  trader opened, and it is unusable here: 355 symbols times every board
  refresh is the CPU-saturation incident this project already had. So the
  chain is read ONCE per symbol, spaced, in the background, by
  :mod:`momx.optionable`, and stored; this module only reads that store
  (``optionable.weekly_table()``, re-read when the file changes). Until then
  the curated hand-edited table was used - it was wrong for 157 of 358
  Watchlist names (2026-09-26) and is gone. A symbol with no chain read yet is
  UNKNOWN (``None``), never ``False``: unknown must not light a badge and must
  not be remembered as a no.

* EARNINGS -- reuses the app's existing earnings calendar, the same one behind
  ``api_server.earnings_calendar_payload``: the trader's reviewed screenshot
  imports in ``artifacts/earnings_manual_imports.json`` (which win, exactly as
  they do there), layered on ONE bulk Finnhub/FMP ``from``/``to`` range
  request for the whole date window. That request is per BUILD, never per
  symbol, is TTL-cached, and backs off after a failure. Pass the result of
  :func:`load_earnings_calendar` into :func:`build_badge` as ``earnings_map``
  and the whole refresh costs nothing.

* RVOL -- read straight off the row the board already built
  (``row["rvol"][timeframe]["value"]``). Nothing is recomputed here. The
  trader's RVOL is a Z-SCORE, not a ratio, so the threshold is 2.0 = two
  standard deviations - the same rung his own colour ladder in
  ``momx.columns.rvol_cell`` paints green/red.

Nothing in here may raise into a row. Every signal degrades to "no reason".
One missing earnings date must never blank a row.
"""

from __future__ import annotations

import json
import math
import os
import threading
import time
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from config import ARTIFACTS_DIR
from momx import optionable

__all__ = [
    "EARNINGS_HORIZON_DAYS",
    "REASON_EARNINGS",
    "REASON_ORDER",
    "REASON_RVOL",
    "REASON_WEEKLIES",
    "RVOL_THRESHOLD",
    "build_badge",
    "earnings_within",
    "empty_badge",
    "has_weekly_options",
    "load_earnings_calendar",
    "reset_caches",
    "unusual_rvol",
]


# ---------------------------------------------------------------------------
# Contract vocabulary
# ---------------------------------------------------------------------------

REASON_WEEKLIES = "weeklies"
REASON_EARNINGS = "earnings"
REASON_RVOL = "rvol"

#: The order reasons appear in the payload. Fixed by the badge contract.
REASON_ORDER: tuple[str, ...] = (REASON_WEEKLIES, REASON_EARNINGS, REASON_RVOL)

#: "Soon" for the earnings reason.
EARNINGS_HORIZON_DAYS = 7

#: Two standard deviations. ``momx.columns.rvol_cell`` already paints
#: ``rel_vol >= 2`` green (bullish) or red (bearish); the badge uses the same
#: rung so the lightning bolt and the cell colour can never disagree.
RVOL_THRESHOLD = 2.0


# ---------------------------------------------------------------------------
# WEEKLIES -- read from REAL option chains (momx/optionable.py)
# ---------------------------------------------------------------------------
#
# 2026-09-26: the hand-edited table that lived here was checked against the
# real chain of every Watchlist name and was wrong for 157 of 358 (155 weekly
# names missing, MPC and NOC marked weekly with monthly-only chains). Ganesh:
# "no guess work". The answer now comes from momx/optionable.py's store,
# filled from api_server's option chain: True = weekly expiries listed,
# False = options but monthly only (or no options), missing = unknown.

# ---------------------------------------------------------------------------
# Earnings calendar plumbing (mirrors api_server, without importing it)
# ---------------------------------------------------------------------------

#: The trader's reviewed screenshot imports. Same file api_server writes.
EARNINGS_MANUAL_IMPORT_PATH = ARTIFACTS_DIR / "earnings_manual_imports.json"

#: How far ahead the bulk range request looks. api_server uses 45.
EARNINGS_CALENDAR_HORIZON_DAYS = 45

#: api_server caches the same payload for 15 minutes. So do we, so a 60s board
#: timer cannot turn into a vendor request every minute.
EARNINGS_CACHE_SECONDS = 15 * 60

#: After a provider failure, do not try again for this long. Without it a dead
#: key would mean a blocking socket timeout on every single board build.
EARNINGS_FAILURE_COOLDOWN_SECONDS = 5 * 60

#: Deliberately shorter than api_server's 15s: this one sits in front of a
#: board refresh, so it must give up long before the board feels slow.
EARNINGS_REQUEST_TIMEOUT = 8.0

_EARNINGS_LOCK = threading.Lock()
#: (today, horizon) -> (monotonic stamp, SYMBOL -> date). One entry, whole
#: universe. This module owns this dict and nothing else may reach into it.
_EARNINGS_CACHE: dict[tuple, tuple[float, dict[str, date]]] = {}
#: monotonic stamp of the last provider failure, for the cooldown above.
_EARNINGS_FAILED_AT: dict[str, float] = {}


def _monotonic() -> float:
    """Test seam for the TTL clock."""
    return time.monotonic()


def reset_caches() -> None:
    """Drop the earnings cache. For tests and for a manual "refresh now"."""
    with _EARNINGS_LOCK:
        _EARNINGS_CACHE.clear()
        _EARNINGS_FAILED_AT.clear()


# ---------------------------------------------------------------------------
# small helpers -- none of these may raise
# ---------------------------------------------------------------------------

def _norm(symbol: Any) -> str:
    """Uppercase ticker, or "" when there is nothing usable."""
    try:
        return str(symbol or "").strip().upper()
    except Exception:  # noqa: BLE001 - a weird __str__ must not blank a row
        return ""


def _as_date(value: Any) -> date | None:
    """date / datetime / "YYYY-MM-DD..." -> date. None when unreadable."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        text = str(value or "").strip()
    except Exception:  # noqa: BLE001
        return None
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _today(now: Any = None) -> date:
    if now is None:
        return datetime.now().date()
    resolved = _as_date(now)
    return resolved if resolved is not None else datetime.now().date()


# ---------------------------------------------------------------------------
# 1. WEEKLIES
# ---------------------------------------------------------------------------

def has_weekly_options(symbol: Any, *, weekly_symbols: Any = None) -> bool | None:
    """True when ``symbol`` lists weekly option expiries, None when unknown.

    ``None`` is a first-class answer and means "no chain has been read for this
    ticker yet". An unknown must NOT render a badge and must NOT be remembered
    as ``False`` - only a chain that was actually read can say False.

    The default source is the real-chain store (``optionable.weekly_table()``,
    re-read only when the file changes - no network here). ``weekly_symbols``
    is the injection seam: a set/sequence (membership means yes, anything else
    unknown) or a mapping (``True``/``False``/``None`` per symbol).
    """
    key = _norm(symbol)
    if not key:
        return None
    try:
        table: Any = optionable.weekly_table() if weekly_symbols is None else weekly_symbols
        if isinstance(table, Mapping):
            if key not in table:
                return None
            value = table[key]
            return None if value is None else bool(value)
        return True if key in table else None
    except Exception:  # noqa: BLE001 - a broken table is an unknown, not a crash
        return None


# ---------------------------------------------------------------------------
# 2. EARNINGS
# ---------------------------------------------------------------------------

def _http_json(url: str, timeout: float = EARNINGS_REQUEST_TIMEOUT) -> Any:
    """One plain JSON GET. The ONLY network call this module can make."""
    request = Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "AgenticAI-Trading/1.0"},
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed vendor hosts
        return json.loads(response.read().decode("utf-8"))


def _manual_earnings_dates(path: Any = None) -> dict[str, date]:
    """The trader's reviewed screenshot imports: SYMBOL -> earliest date.

    Local file, no network. Never raises - a missing or corrupt file is simply
    "no manual dates".
    """
    target = EARNINGS_MANUAL_IMPORT_PATH if path is None else path
    try:
        with open(str(target), "r", encoding="utf-8") as handle:
            raw = json.loads(handle.read())
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(raw, list):
        return {}
    dates: dict[str, date] = {}
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        symbol = _norm(item.get("symbol"))
        when = _as_date(item.get("date"))
        if not symbol or when is None:
            continue
        current = dates.get(symbol)
        if current is None or when < current:
            dates[symbol] = when
    return dates


def _provider_earnings_dates(start: date, end: date, *, fetch: Any = None) -> dict[str, date]:
    """ONE bulk ``from``/``to`` range request. Never one per symbol.

    Same providers and same priority as ``api_server._fetch_watchlist_earnings``
    (Finnhub first, then Financial Modeling Prep). Returns {} when neither key
    is configured or the call fails - the badge simply loses its earnings
    reason, which is the correct degradation.
    """
    getter = _http_json if fetch is None else fetch
    query = {"from": start.isoformat(), "to": end.isoformat()}
    rows: Any = None

    finnhub_key = str(os.getenv("FINNHUB_API_KEY", "")).strip()
    if finnhub_key:
        try:
            payload = getter(
                "https://finnhub.io/api/v1/calendar/earnings?"
                + urlencode({**query, "token": finnhub_key})
            )
            if isinstance(payload, Mapping) and not payload.get("error"):
                candidate = payload.get("earningsCalendar")
                if isinstance(candidate, list):
                    rows = candidate
        except Exception:  # noqa: BLE001
            rows = None

    if rows is None:
        fmp_key = str(os.getenv("FMP_API_KEY", "")).strip()
        if fmp_key:
            try:
                payload = getter(
                    "https://financialmodelingprep.com/stable/earnings-calendar?"
                    + urlencode({**query, "apikey": fmp_key})
                )
                if isinstance(payload, list):
                    rows = payload
                elif isinstance(payload, Mapping) and isinstance(payload.get("data"), list):
                    rows = payload["data"]
            except Exception:  # noqa: BLE001
                rows = None

    if not isinstance(rows, list):
        return {}

    dates: dict[str, date] = {}
    for raw in rows:
        if not isinstance(raw, Mapping):
            continue
        symbol = _norm(raw.get("symbol") or raw.get("ticker"))
        when = _as_date(raw.get("date") or raw.get("earningsDate"))
        if not symbol or when is None:
            continue
        if when < start or when > end:
            continue
        current = dates.get(symbol)
        if current is None or when < current:
            dates[symbol] = when
    return dates


def _earnings_dates(
    today: date,
    horizon_days: int,
    *,
    fetch: Any = None,
    manual_path: Any = None,
    use_network: bool = True,
) -> dict[str, date]:
    """SYMBOL -> next earnings date for the WHOLE market, manual imports on top.

    Manual screenshot imports win over the provider, exactly as they do in
    ``api_server.earnings_calendar_payload``: the trader reviewed them himself.
    """
    end = today + timedelta(days=max(1, int(horizon_days)))
    dates: dict[str, date] = {}
    if use_network:
        try:
            dates.update(_provider_earnings_dates(today, end, fetch=fetch))
        except Exception:  # noqa: BLE001
            pass
    try:
        for symbol, when in _manual_earnings_dates(manual_path).items():
            if today <= when <= end:
                dates[symbol] = when
    except Exception:  # noqa: BLE001
        pass
    return dates


def _network_allowed() -> bool:
    """Off entirely with ``MOMX_FLAGS_EARNINGS_NETWORK=0``; otherwise needs a key."""
    disabled = str(os.getenv("MOMX_FLAGS_EARNINGS_NETWORK", "1")).strip().lower()
    if disabled in {"0", "false", "no", "off"}:
        return False
    return bool(
        str(os.getenv("FINNHUB_API_KEY", "")).strip()
        or str(os.getenv("FMP_API_KEY", "")).strip()
    )


def _cached_earnings_dates(today: date, horizon: int) -> dict[str, date]:
    """TTL-cached whole-market date table. One build, many symbols."""
    key = (today.isoformat(), horizon)
    stamp = _monotonic()
    with _EARNINGS_LOCK:
        cached = _EARNINGS_CACHE.get(key)
        if cached and (stamp - cached[0]) < EARNINGS_CACHE_SECONDS:
            return cached[1]
        failed_at = _EARNINGS_FAILED_AT.get("provider")
        cooling = (
            failed_at is not None
            and (stamp - failed_at) < EARNINGS_FAILURE_COOLDOWN_SECONDS
        )

    use_network = _network_allowed() and not cooling
    try:
        dates = _earnings_dates(today, horizon, use_network=use_network)
    except Exception:  # noqa: BLE001 - the calendar must never break a board
        dates = {}

    with _EARNINGS_LOCK:
        if use_network and not dates:
            _EARNINGS_FAILED_AT["provider"] = stamp
        elif dates:
            _EARNINGS_FAILED_AT.pop("provider", None)
        _EARNINGS_CACHE.clear()  # one entry only: today's table
        _EARNINGS_CACHE[key] = (stamp, dates)
    return dates


def load_earnings_calendar(
    symbols: Iterable[Any],
    *,
    days: int = EARNINGS_CALENDAR_HORIZON_DAYS,
    now: Any = None,
    fetch: Any = None,
    manual_path: Any = None,
) -> dict[str, int | None]:
    """SYMBOL -> days until its next earnings, ``None`` when unknown.

    THE ONCE-PER-BUILD LOADER. Call this ONE time for the whole universe and
    hand the result to :func:`build_badge` as ``earnings_map``. It costs at
    most one bulk range request (TTL-cached for 15 minutes, and skipped for 5
    minutes after a failure), never one per symbol.

    ``None`` means unknown - "no date inside the horizon" and "the provider is
    down" are the same answer here, and both correctly light no badge.

    Passing ``fetch=`` or ``manual_path=`` bypasses the cache entirely, so
    tests are deterministic and never see another test's data.
    """
    try:
        wanted = [_norm(item) for item in (symbols or [])]
    except Exception:  # noqa: BLE001
        return {}
    wanted = [item for item in wanted if item]
    if not wanted:
        return {}

    today = _today(now)
    try:
        horizon = max(1, int(days))
    except (TypeError, ValueError):
        horizon = EARNINGS_CALENDAR_HORIZON_DAYS

    if fetch is not None or manual_path is not None:
        dates = _earnings_dates(
            today,
            horizon,
            fetch=fetch,
            manual_path=manual_path,
            use_network=fetch is not None,
        )
    else:
        dates = _cached_earnings_dates(today, horizon)

    result: dict[str, int | None] = {}
    for symbol in wanted:
        when = dates.get(symbol)
        result[symbol] = None if when is None else (when - today).days
    return result


def _days_until(value: Any, *, today: date | None = None) -> int | None:
    """Read a day count out of whatever the calendar happened to hold.

    Accepts an int, a date/datetime, an ISO string, or one of the row dicts
    ``api_server.earnings_calendar_payload`` emits (``daysUntil`` / ``date``),
    so the wiring cannot get this wrong.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Mapping):
        for name in ("daysUntil", "days_until", "days"):
            if name in value:
                nested = _days_until(value.get(name), today=today)
                if nested is not None:
                    return nested
        return _days_until(value.get("date"), today=today)
    if isinstance(value, (int, float)):
        number = _finite(value)
        return None if number is None else int(number)
    when = _as_date(value)
    if when is None:
        return None
    return (when - (today or _today())).days


def earnings_within(
    symbol: Any,
    days: int = EARNINGS_HORIZON_DAYS,
    *,
    calendar: Any = None,
) -> int | None:
    """Days until ``symbol``'s next earnings when it lands inside ``days``.

    ``None`` means "no reason to badge": either genuinely unknown, or the
    report is further out than ``days``, or it already happened. 0 is a real
    answer (earnings today) and is deliberately NOT collapsed into falsy.

    ALWAYS pass ``calendar=`` from :func:`load_earnings_calendar` in the board.
    Without it this falls back to the shared TTL cache, which is correct but is
    a per-symbol code path and only exists so the function works standalone.
    """
    key = _norm(symbol)
    if not key:
        return None
    try:
        horizon = max(0, int(days))
    except (TypeError, ValueError):
        horizon = EARNINGS_HORIZON_DAYS
    try:
        source = load_earnings_calendar([key]) if calendar is None else calendar
        raw = source.get(key) if isinstance(source, Mapping) else None
    except Exception:  # noqa: BLE001 - a broken calendar is an unknown
        return None
    count = _days_until(raw)
    if count is None or count < 0 or count > horizon:
        return None
    return count


# ---------------------------------------------------------------------------
# 3. RVOL
# ---------------------------------------------------------------------------

def _peak_rvol(row: Any) -> float | None:
    """The largest RVOL z-score ALREADY on the row. Computes nothing.

    Handles the board's shape (``row["rvol"]["5m"]["value"]``), a bare cell
    (``{"value": 2.4}``) and a bare number, and ignores blank cells.
    """
    if not isinstance(row, Mapping):
        return None
    block = row.get("rvol")
    if block is None:
        return None
    if isinstance(block, Mapping):
        cells: Iterable[Any] = (block,) if "value" in block else list(block.values())
    elif isinstance(block, (list, tuple)):
        cells = list(block)
    else:
        cells = (block,)

    peak: float | None = None
    for item in cells:
        raw = item.get("value") if isinstance(item, Mapping) else item
        number = _finite(raw)
        if number is None:
            continue
        if peak is None or number > peak:
            peak = number
    return peak


def unusual_rvol(row: Any, threshold: float = RVOL_THRESHOLD) -> bool:
    """True when any RVOL cell on the row is at or above ``threshold``.

    The trader's RVOL is a Z-SCORE, not a ratio: the default 2.0 is TWO
    STANDARD DEVIATIONS, the same rung ``momx.columns.rvol_cell`` already
    paints green (bullish) or red (bearish). Do not "fix" this to 2x average.
    """
    limit = _finite(threshold)
    if limit is None:
        limit = RVOL_THRESHOLD
    try:
        peak = _peak_rvol(row)
    except Exception:  # noqa: BLE001 - a malformed row is not unusual volume
        return False
    return peak is not None and peak >= limit


# ---------------------------------------------------------------------------
# The badge
# ---------------------------------------------------------------------------

def empty_badge() -> dict:
    """A dark badge. Always a FRESH dict - badges travel into mutable rows."""
    return {"on": False, "reasons": [], "tooltip": ""}


def _earnings_phrase(count: int) -> str:
    if count <= 0:
        return "earnings today"
    if count == 1:
        return "earnings tomorrow"
    return f"earnings in {count} days"


def _rvol_phrase(peak: float | None) -> str:
    if peak is None:
        return "unusual volume"
    return f"unusual volume (RVOL {peak:.1f})"


def _tooltip(fragments: list[str]) -> str:
    if not fragments:
        return ""
    sentence = " - ".join(fragments)
    return sentence[:1].upper() + sentence[1:]


def build_badge(
    symbol: Any,
    row: Any,
    *,
    earnings_map: Any = None,
    weekly_symbols: Any = None,
    earnings_days: int = EARNINGS_HORIZON_DAYS,
    rvol_threshold: float = RVOL_THRESHOLD,
) -> dict:
    """The lightning badge for one row. ANY of the three reasons lights it.

    Returns exactly::

        {"on": bool, "reasons": [...], "tooltip": "..."}

    with ``reasons`` a subset of ``("weeklies", "earnings", "rvol")`` in that
    order. Every signal is evaluated behind its own guard: a source that
    raises, or an earnings date that is missing, loses ITS OWN reason and
    nothing else. This function never raises into a row.
    """
    reasons: list[str] = []
    fragments: list[str] = []

    try:
        if has_weekly_options(symbol, weekly_symbols=weekly_symbols) is True:
            reasons.append(REASON_WEEKLIES)
            fragments.append("weekly options")
    except Exception:  # noqa: BLE001
        pass

    try:
        count = earnings_within(symbol, earnings_days, calendar=earnings_map)
        if count is not None:
            reasons.append(REASON_EARNINGS)
            fragments.append(_earnings_phrase(count))
    except Exception:  # noqa: BLE001
        pass

    try:
        if unusual_rvol(row, rvol_threshold):
            reasons.append(REASON_RVOL)
            fragments.append(_rvol_phrase(_peak_rvol(row)))
    except Exception:  # noqa: BLE001
        pass

    if not reasons:
        return empty_badge()
    return {"on": True, "reasons": reasons, "tooltip": _tooltip(fragments)}
