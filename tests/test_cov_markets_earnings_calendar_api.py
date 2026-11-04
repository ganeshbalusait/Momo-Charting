"""Integration: the Earnings Calendar page's read, GET /api/earnings-calendar,
over HTTP (2026-11-04 coverage). Finnhub is faked at the fetcher the payload
uses (never Schwab, never the network) and the ET day is pinned.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from api_harness import api_server, running_api

ET = ZoneInfo("America/New_York")


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


class Finnhub:
    def __init__(self):
        self.urls: list[str] = []

    def __call__(self, url):
        self.urls.append(url)
        return {"earningsCalendar": [
            {"symbol": "NVDA", "date": "2026-11-19", "hour": "amc", "epsEstimate": 1.23, "quarter": 3, "year": 2027},
            {"symbol": "AAPL", "date": "2026-11-05", "hour": "bmo", "epsEstimate": 2.1, "quarter": 4, "year": 2026},
            {"symbol": "NOTWATCHED", "date": "2026-11-05", "hour": "bmo"},
        ]}


@pytest.fixture
def calendar(monkeypatch):
    finnhub = Finnhub()
    monkeypatch.setattr(api_server, "pd", PinnedPandas(datetime(2026, 11, 4, 10, 0, tzinfo=ET)))
    monkeypatch.setattr(api_server.DashboardState, "_earnings_calendar_request", staticmethod(finnhub))
    monkeypatch.setattr(api_server.settings.scanner, "default_universe", ["NVDA", "AAPL", "MSFT"])
    monkeypatch.setattr(api_server.STATE, "earnings_calendar_cache", None)
    monkeypatch.setattr(api_server.STATE, "earnings_manual_imports", [])
    monkeypatch.setenv("FINNHUB_API_KEY", "test-finnhub")
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    return finnhub


def test_the_page_read_lists_watchlist_releases_only(calendar):
    with running_api() as api:
        status, body = api.get("/api/earnings-calendar?days=45")
    assert status == 200, body
    assert [(row["symbol"], row["date"], row["timingCode"]) for row in body["rows"]] == [
        ("AAPL", "2026-11-05", "bmo"), ("NVDA", "2026-11-19", "amc"),
    ]
    assert body["informationOnly"] is True and body["live"] is True
    assert (body["watchlistCount"], body["horizonDays"], body["startDate"]) == (3, 45, "2026-11-04")
    assert body["rows"][0]["epsEstimate"] == 2.1
    assert body["source"] == "Finnhub earnings calendar"


@pytest.mark.parametrize("query,horizon", [("days=500", 90), ("days=abc", 45), ("", 45), ("days=3", 7)])
def test_odd_day_counts_are_clamped_never_a_500(calendar, query, horizon):
    with running_api() as api:
        status, body = api.get(f"/api/earnings-calendar?{query}")
    assert status == 200, body
    assert body["horizonDays"] == horizon


def test_the_15_minute_auto_refresh_is_cached_and_refresh_calendar_forces(calendar):
    with running_api() as api:
        api.get("/api/earnings-calendar?days=45")
        status, cached = api.get("/api/earnings-calendar?days=45")
        assert status == 200 and cached["cached"] is True and len(calendar.urls) == 1
        status, forced = api.get("/api/earnings-calendar?days=45&force=true")
    assert status == 200 and "cached" not in forced
    assert len(calendar.urls) == 2


def test_with_no_keys_the_page_gets_the_setup_line_and_no_rows(calendar, monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    with running_api() as api:
        status, body = api.get("/api/earnings-calendar?days=45")
    assert status == 200, body
    assert body["live"] is False and body["rows"] == []
    assert body["errors"] == ["Set FINNHUB_API_KEY or FMP_API_KEY to load a live earnings calendar."]
    assert calendar.urls == []
