"""Finnhub /stock/metric extract for the Long Term value screen."""
from __future__ import annotations

import json
import logging
import math
import threading
import time
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import quote

log = logging.getLogger("long_term")

_URL = "https://finnhub.io/api/v1/stock/metric?symbol={symbol}&metric=all&token={token}"


@dataclass
class FinnhubExtract:
    """Values as Finnhub gives them, except market cap.

    roi_ttm / roe_ttm are PERCENTAGES (101.59 means 101.59%), not rescaled.
    roic_annual values are FRACTIONS (0.724), not rescaled.
    fcf_margin_annual values are FRACTIONS (series.annual.fcfMargin, 0.4477 = 44.77%).
    market_cap_usd is dollars (Finnhub reports millions; multiplied by 1e6).
    Annual series are keyed by period (YYYY-MM-DD); null points are dropped.
    """
    symbol: str
    eps_annual: dict = field(default_factory=dict)
    pe_annual: dict = field(default_factory=dict)
    roic_annual: dict = field(default_factory=dict)
    fcf_margin_annual: dict = field(default_factory=dict)
    eps_ttm: float | None = None
    eps_incl_ttm: float | None = None
    pe_ttm: float | None = None
    roi_ttm: float | None = None
    roe_ttm: float | None = None
    market_cap_usd: float | None = None
    debt_to_equity: float | None = None
    fetched_at: str = ""


def _default_get(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


default_get = _default_get    # public name (the Long Term service wraps it with 429 back-off)


def _dotted(symbol: str) -> str:
    return symbol.strip().upper().replace("-", ".")


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    v = float(v)
    return v if math.isfinite(v) else None


def _series(annual: dict, key: str) -> dict:
    out = {}
    for pt in (annual.get(key) or []):
        if not isinstance(pt, dict):
            continue
        v = _num(pt.get("v"))
        if v is not None and pt.get("period"):
            out[str(pt["period"])] = v
    return out


def fetch_metrics(symbol: str, api_key: str, *, get=None, fetched_at: str,
                  timeout: float = 15.0) -> FinnhubExtract | None:
    if not api_key:
        return None
    get = get or _default_get
    url = _URL.format(symbol=quote(_dotted(symbol)), token=quote(api_key))
    try:
        data = get(url, timeout)
    except Exception as exc:  # noqa: BLE001 - logged by class only: the URL carries the key
        log.warning("long_term: Finnhub fetch failed for %s (%s)", symbol, type(exc).__name__)
        return None
    if not isinstance(data, dict):
        return None
    metric = data.get("metric")
    if not isinstance(metric, dict) or not metric:
        return None
    series = data.get("series")
    annual = series.get("annual") if isinstance(series, dict) else None
    if not isinstance(annual, dict):
        annual = {}
    cap = _num(metric.get("marketCapitalization"))
    return FinnhubExtract(
        symbol=symbol,
        eps_annual=_series(annual, "eps"),
        pe_annual=_series(annual, "pe"),
        roic_annual=_series(annual, "roic"),
        fcf_margin_annual=_series(annual, "fcfMargin"),
        eps_ttm=_num(metric.get("epsTTM")),
        eps_incl_ttm=_num(metric.get("epsInclExtraItemsTTM")),
        pe_ttm=_num(metric.get("peTTM")),
        roi_ttm=_num(metric.get("roiTTM")),
        roe_ttm=_num(metric.get("roeTTM")),
        market_cap_usd=cap * 1_000_000 if cap is not None else None,
        debt_to_equity=_num(metric.get("totalDebt/totalEquityAnnual")),
        fetched_at=fetched_at,
    )


class FinnhubPacer:
    """Spaces calls at least 60/per_minute seconds apart; backoff sleeps 2**attempt."""

    def __init__(self, per_minute: int = 30, sleep=time.sleep, clock=time.monotonic):
        self._gap = 60.0 / per_minute
        self._sleep = sleep
        self._clock = clock
        self._last = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            if self._last is not None:
                remaining = self._gap - (self._clock() - self._last)
                if remaining > 0:
                    self._sleep(remaining)
            self._last = self._clock()

    def backoff(self, attempt: int) -> None:
        self._sleep(2 ** attempt)
