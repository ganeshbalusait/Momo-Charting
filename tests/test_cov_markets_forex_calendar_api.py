"""Integration: the News Feed's USD calendar through GET
/api/forex-factory-us-news over HTTP (2026-11-04 coverage).

Forex Factory (nfs.faireconomy.media) is faked at the fetcher the payload
uses, and the ET clock is pinned to Wednesday 2026-11-04 10:00 ET.
"""
from __future__ import annotations

from datetime import date, datetime
from urllib.error import URLError
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


FEED = [
    {"title": "CPI m/m", "country": "USD", "impact": "High", "date": "2026-11-04T13:30:00Z", "forecast": "0.3%", "previous": "0.4%"},
    {"title": "Crude Oil Inventories", "country": "USD", "impact": "Medium", "date": "2026-11-04T15:30:00Z", "forecast": "", "previous": ""},
    {"title": "FOMC Statement", "country": "USD", "impact": "High", "date": "2026-11-05T19:00:00Z", "forecast": "", "previous": ""},
    {"title": "German Factory Orders", "country": "EUR", "impact": "High", "date": "2026-11-04T07:00:00Z", "forecast": "", "previous": ""},
    {"title": "Mortgage Delinquencies", "country": "USD", "impact": "Low", "date": "2026-11-04T17:00:00Z", "forecast": "", "previous": ""},
]


class Fetcher:
    def __init__(self, answer=FEED):
        self.answer = answer
        self.urls: list[str] = []

    def __call__(self, url):
        self.urls.append(url)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def calendar(monkeypatch):
    fetcher = Fetcher()
    monkeypatch.setattr(api_server, "pd", PinnedPandas(datetime(2026, 11, 4, 10, 0, tzinfo=ET)))
    monkeypatch.setattr(api_server.DashboardState, "_earnings_calendar_request", staticmethod(fetcher))
    monkeypatch.setattr(api_server.STATE, "forex_factory_us_news_cache", None)
    return fetcher


def test_the_panel_payload_is_usd_medium_high_in_eastern_time(calendar):
    with running_api() as api:
        status, body = api.get("/api/forex-factory-us-news")
    assert status == 200, body
    assert body["informationOnly"] is True and body["timezone"] == "ET" and body["live"] is True
    assert date.fromisoformat(body["weekStart"]).weekday() == 0, "the week grid starts on a Monday"
    assert body["sourceUrl"] == "https://www.forexfactory.com/calendar"
    assert [(e["title"], e["timeET"], e["impact"]) for e in body["events"]] == [
        ("CPI m/m", "8:30 AM", "High"), ("Crude Oil Inventories", "10:30 AM", "Medium"),
    ]
    assert [e["title"] for e in body["weeklyEvents"]] == ["CPI m/m", "Crude Oil Inventories", "FOMC Statement"]
    assert [e["isPast"] for e in body["events"]] == [True, False]
    assert calendar.urls == [api_server.FOREX_FACTORY_CALENDAR_URL]


def test_the_ten_minute_poll_is_cached_and_refresh_forces_a_fetch(calendar):
    with running_api() as api:
        api.get("/api/forex-factory-us-news")
        status, cached = api.get("/api/forex-factory-us-news")
        assert status == 200 and cached["cached"] is True
        assert len(calendar.urls) == 1
        status, forced = api.get("/api/forex-factory-us-news?force=true")
    assert status == 200 and "cached" not in forced
    assert len(calendar.urls) == 2


def test_forex_factory_down_is_a_200_with_the_reason(calendar):
    calendar.answer = URLError("connection reset")
    with running_api() as api:
        status, body = api.get("/api/forex-factory-us-news")
    assert status == 200, body
    assert body["live"] is False and body["events"] == []
    assert body["errors"] == ["Forex Factory calendar unavailable: URLError."]
