"""Unit: the Earnings Calendar page's payload (DashboardState.
earnings_calendar_payload and its helpers), 2026-11-04 coverage.

Finnhub and FMP are a fake fetcher, the API keys are set per test, the ET
'today' is pinned to Wednesday 2026-11-04, and the screenshot imports start
empty (the live STATE loads the trader's real imports file at import time).
"""
from __future__ import annotations

import threading
from datetime import datetime
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import api_server

ET = ZoneInfo("America/New_York")
WATCHLIST = ["NVDA", "AAPL", "MSFT", "TSLA"]


class PinnedPandas:
    def __init__(self, instant: datetime):
        self.instant = instant
        owner = self

        class PinnedTimestamp(pd.Timestamp):
            @classmethod
            def now(cls, tz=None):
                stamp = pd.Timestamp(owner.instant)
                return stamp.tz_convert(tz) if tz else stamp.tz_convert(None)

        self.Timestamp = PinnedTimestamp

    def __getattr__(self, name):
        return getattr(pd, name)


def finnhub_row(symbol, day, hour="", eps=None, quarter=4, year=2026):
    return {"symbol": symbol, "date": day, "hour": hour, "epsEstimate": eps, "quarter": quarter, "year": year,
            "revenueEstimate": None, "epsActual": None, "revenueActual": None}


FINNHUB_ROWS = [
    finnhub_row("NVDA", "2026-11-19", "amc", "1.23"),
    finnhub_row("NVDA", "2027-02-25", "amc"),          # later report: the nearest one wins
    finnhub_row("AAPL", "2026-11-05", "bmo", 2.1),
    finnhub_row("MSFT", "2026-11-05", ""),             # no timing -> tbd
    finnhub_row("ZZZZ", "2026-11-06", "bmo"),          # not on the watchlist
    finnhub_row("TSLA", "2026-11-03", "amc"),          # yesterday -> outside the window
    finnhub_row("TSLA", "not-a-date", "amc"),
    "garbage",
]


class Provider:
    """Answers by host: finnhub.io and financialmodelingprep.com."""

    def __init__(self, finnhub=None, fmp=None):
        self.finnhub = {"earningsCalendar": FINNHUB_ROWS} if finnhub is None else finnhub
        self.fmp = fmp
        self.urls: list[str] = []

    def __call__(self, url):
        self.urls.append(url)
        answer = self.finnhub if "finnhub.io" in url else self.fmp
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(api_server, "pd", PinnedPandas(datetime(2026, 11, 4, 10, 0, tzinfo=ET)))
    monkeypatch.setattr(api_server.settings.scanner, "default_universe", list(WATCHLIST))
    monkeypatch.setenv("FINNHUB_API_KEY", "test-finnhub")
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    return monkeypatch


def _state(provider, manual=None):
    state = object.__new__(api_server.DashboardState)
    state.earnings_calendar_lock = threading.Lock()
    state.earnings_calendar_cache = None
    state.earnings_manual_lock = threading.Lock()
    state.earnings_manual_imports = list(manual or [])
    state._earnings_calendar_request = provider
    return state


def test_only_watchlist_names_inside_the_window_nearest_date_first(env):
    provider = Provider()
    payload = _state(provider).earnings_calendar_payload()
    assert [(row["symbol"], row["date"]) for row in payload["rows"]] == [
        ("AAPL", "2026-11-05"), ("MSFT", "2026-11-05"), ("NVDA", "2026-11-19"),
    ]
    nvda = payload["rows"][2]
    assert (nvda["daysUntil"], nvda["timingCode"], nvda["timing"]) == (15, "amc", "After market close")
    assert nvda["epsEstimate"] == 1.23 and nvda["quarter"] == 4 and nvda["manualImport"] is False
    assert payload["rows"][1]["timingCode"] == "tbd" and payload["rows"][1]["timing"] == "Time not confirmed"
    assert (payload["startDate"], payload["endDate"], payload["horizonDays"]) == ("2026-11-04", "2026-12-19", 45)
    assert payload["watchlistCount"] == 4 and payload["upcomingCount"] == 3
    assert (payload["live"], payload["informationOnly"], payload["errors"]) == (True, True, [])
    query = parse_qs(urlparse(provider.urls[0]).query)
    assert query["from"] == ["2026-11-04"] and query["to"] == ["2026-12-19"] and query["token"] == ["test-finnhub"]


@pytest.mark.parametrize("asked,horizon", [(500, 90), (1, 7), (0, 45), (45, 45), (60, 60)])
def test_the_horizon_is_clamped_to_7_through_90_days(env, asked, horizon):
    assert _state(Provider()).earnings_calendar_payload(days=asked)["horizonDays"] == horizon


def test_a_reviewed_screenshot_import_overrides_the_provider_date(env):
    manual = [{"symbol": "NVDA", "date": "2026-11-18", "timingCode": "bmo", "importedAt": "2026-11-03T20:00:00-05:00"},
              {"symbol": "ZZZZ", "date": "2026-11-18", "timingCode": "bmo"},          # not on the watchlist
              {"symbol": "AAPL", "date": "2027-06-01", "timingCode": "amc"}]          # beyond the horizon
    payload = _state(Provider(), manual).earnings_calendar_payload()
    nvda = next(row for row in payload["rows"] if row["symbol"] == "NVDA")
    assert (nvda["date"], nvda["timingCode"], nvda["manualImport"]) == ("2026-11-18", "bmo", True)
    assert nvda["source"] == "Manual image import"
    assert payload["source"] == "Finnhub earnings calendar + manual image import"
    assert payload["manualImportCount"] == 1
    aapl = next(row for row in payload["rows"] if row["symbol"] == "AAPL")
    assert aapl["date"] == "2026-11-05", "an out-of-window import does not replace the provider row"


def test_the_cache_holds_for_the_same_watchlist_and_a_change_or_force_refetches(env):
    provider = Provider()
    state = _state(provider)
    state.earnings_calendar_payload()
    assert state.earnings_calendar_payload()["cached"] is True and len(provider.urls) == 1
    state.earnings_calendar_payload(force=True)
    assert len(provider.urls) == 2
    env.setattr(api_server.settings.scanner, "default_universe", WATCHLIST + ["AMD"])
    payload = state.earnings_calendar_payload()
    assert len(provider.urls) == 3 and "cached" not in payload and payload["watchlistCount"] == 5


def test_a_finnhub_failure_falls_through_to_fmp(env):
    from urllib.error import HTTPError

    env.setenv("FMP_API_KEY", "test-fmp")
    provider = Provider(finnhub=HTTPError("https://finnhub.io", 429, "Too Many", None, None),
                        fmp=[{"symbol": "AAPL", "date": "2026-11-06", "epsEstimated": 2.0, "time": "bmo"}])
    payload = _state(provider).earnings_calendar_payload()
    assert [("finnhub.io" in url, "financialmodelingprep.com" in url) for url in provider.urls] == [(True, False), (False, True)]
    assert payload["source"] == "Financial Modeling Prep earnings calendar"
    assert [(row["symbol"], row["timingCode"], row["epsEstimate"]) for row in payload["rows"]] == [("AAPL", "bmo", 2.0)]
    assert payload["live"] is True and payload["errors"] == []


def test_a_finnhub_error_body_with_no_fmp_key_is_reported_not_live(env):
    payload = _state(Provider(finnhub={"error": "Invalid API key"})).earnings_calendar_payload()
    assert payload["live"] is False and payload["rows"] == []
    assert payload["errors"] == ["Finnhub returned an unavailable earnings calendar response."]


def test_without_any_key_the_page_says_how_to_set_one_up(env):
    env.delenv("FINNHUB_API_KEY", raising=False)
    provider = Provider()
    payload = _state(provider).earnings_calendar_payload()
    assert provider.urls == [], "no key, no call"
    assert payload["live"] is False and payload["rows"] == []
    assert payload["errors"] == ["Set FINNHUB_API_KEY or FMP_API_KEY to load a live earnings calendar."]
    assert payload["source"] == "Earnings calendar unavailable"


@pytest.mark.parametrize("raw,code,label", [
    ("bmo", "bmo", "Before market open"), ("Before Open", "bmo", "Before market open"),
    ("after market close", "amc", "After market close"), ("AMC", "amc", "After market close"),
    ("during market hours", "dmh", "During market hours"), ("", "tbd", "Time not confirmed"),
    ("lunch", "tbd", "Time not confirmed"), (None, "tbd", "Time not confirmed"),
])
def test_timing_aliases(raw, code, label):
    assert api_server.DashboardState._earnings_timing_code(raw) == code
    assert api_server.DashboardState._earnings_timing_label(code) == label
