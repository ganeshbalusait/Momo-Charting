"""Unit: the News Feed's 'USD News Today' panel payload
(DashboardState.forex_factory_us_news_payload), 2026-11-04 coverage.

Forex Factory's feed is faked and the ET clock is pinned (the payload reads
pd.Timestamp.now inside), so every case is the same on any day: 'today' is
Wednesday 2026-11-04 10:00 ET and the week is Mon 11-02 .. Fri 11-06.
"""
from __future__ import annotations

import threading
from datetime import datetime
from urllib.error import URLError
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

import api_server

ET = ZoneInfo("America/New_York")
WEDNESDAY_10AM = datetime(2026, 11, 4, 10, 0, tzinfo=ET)


class PinnedPandas:
    """api_server.pd with Timestamp.now pinned to a movable instant."""

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


def ff(title, country, impact, date, forecast="", previous=""):
    return {"title": title, "country": country, "impact": impact, "date": date, "forecast": forecast, "previous": previous}


FEED = [
    ff("FOMC Statement", "USD", "High", "2026-11-05T19:00:00Z"),                    # Thu 2:00 PM ET
    ff("Crude Oil Inventories", "USD", "medium", "2026-11-04T15:30:00Z", "-1.2M", "0.5M"),   # today, later
    ff("CPI m/m", "USD", "High", "2026-11-04T13:30:00Z", "0.3%", "0.4%"),            # today, 8:30 AM (past)
    ff("Treasury Bill Auction", "USD", "Low", "2026-11-04T16:30:00Z"),              # Low -> dropped
    ff("Bank Holiday", "USD", "Holiday", "2026-11-04T05:00:00Z"),                   # Holiday -> dropped
    ff("ECB Press Conference", "EUR", "High", "2026-11-04T13:45:00Z"),              # EUR -> dropped
    ff("ISM Services PMI", "USD", "Medium", "2026-11-02T10:00:00"),                 # naive = ET, Monday
    ff("Unemployment Claims", "USD", "High", "2026-11-06T08:30:00-05:00"),          # Friday, offset form
    ff("Weekend Speech", "USD", "High", "2026-11-07T15:00:00Z"),                    # Saturday -> excluded
    ff("Next Week CPI", "USD", "High", "2026-11-10T13:30:00Z"),                     # next week -> excluded
    ff("Broken Row", "USD", "High", "not-a-date"),                                  # unparseable -> skipped
    "not a dict",
]


class Feed:
    def __init__(self, answer=FEED):
        self.answer = answer
        self.calls: list[str] = []

    def __call__(self, url):
        self.calls.append(url)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def clock(monkeypatch):
    pinned = PinnedPandas(WEDNESDAY_10AM)
    monkeypatch.setattr(api_server, "pd", pinned)
    return pinned


def _state(feed: Feed):
    state = object.__new__(api_server.DashboardState)
    state.forex_factory_us_news_lock = threading.Lock()
    state.forex_factory_us_news_cache = None
    state._earnings_calendar_request = feed
    return state


def test_only_usd_medium_and_high_events_of_this_week_survive_sorted_by_time(clock):
    feed = Feed()
    payload = _state(feed).forex_factory_us_news_payload()
    assert feed.calls == [api_server.FOREX_FACTORY_CALENDAR_URL]
    assert [event["title"] for event in payload["weeklyEvents"]] == [
        "ISM Services PMI", "CPI m/m", "Crude Oil Inventories", "FOMC Statement", "Unemployment Claims",
    ]
    assert [event["title"] for event in payload["events"]] == ["CPI m/m", "Crude Oil Inventories"]
    assert {event["impact"] for event in payload["weeklyEvents"]} == {"High", "Medium"}
    assert payload["weekStart"] == "2026-11-02" and payload["weekEnd"] == "2026-11-06"
    assert payload["date"] == "2026-11-04" and payload["dateLabel"] == "Wednesday, Nov 04"
    assert (payload["timezone"], payload["informationOnly"], payload["live"], payload["errors"]) == ("ET", True, True, [])


def test_times_are_converted_to_eastern_and_naive_times_are_read_as_eastern(clock):
    events = {event["title"]: event for event in _state(Feed()).forex_factory_us_news_payload()["weeklyEvents"]}
    assert (events["CPI m/m"]["timeET"], events["CPI m/m"]["date"]) == ("8:30 AM", "2026-11-04")
    assert (events["FOMC Statement"]["timeET"], events["FOMC Statement"]["date"]) == ("2:00 PM", "2026-11-05")
    assert (events["ISM Services PMI"]["timeET"], events["ISM Services PMI"]["date"]) == ("10:00 AM", "2026-11-02")
    assert events["Unemployment Claims"]["timeET"] == "8:30 AM"
    assert events["Crude Oil Inventories"]["forecast"] == "-1.2M" and events["Crude Oil Inventories"]["previous"] == "0.5M"


def test_is_past_is_true_only_for_events_before_now(clock):
    events = {event["title"]: event for event in _state(Feed()).forex_factory_us_news_payload()["weeklyEvents"]}
    assert events["CPI m/m"]["isPast"] is True                  # 8:30 < 10:00 today
    assert events["ISM Services PMI"]["isPast"] is True         # Monday
    assert events["Crude Oil Inventories"]["isPast"] is False   # 10:30 today
    assert events["FOMC Statement"]["isPast"] is False          # tomorrow


def test_the_cache_holds_ten_minutes_per_et_day_and_force_bypasses_it(clock):
    feed = Feed()
    state = _state(feed)
    first = state.forex_factory_us_news_payload()
    assert "cached" not in first
    clock.instant = datetime(2026, 11, 4, 10, 9, tzinfo=ET)
    again = state.forex_factory_us_news_payload()
    assert again["cached"] is True and len(feed.calls) == 1
    assert again["events"] == first["events"]
    state.forex_factory_us_news_payload(force=True)
    assert len(feed.calls) == 2, "Refresh calendar (force) always asks Forex Factory"
    clock.instant = datetime(2026, 11, 4, 10, 25, tzinfo=ET)
    state.forex_factory_us_news_payload()
    assert len(feed.calls) == 3, "after 10 minutes the calendar is fetched again"


def test_a_new_et_day_refetches_even_inside_ten_minutes(clock):
    feed = Feed()
    state = _state(feed)
    clock.instant = datetime(2026, 11, 4, 23, 58, tzinfo=ET)
    state.forex_factory_us_news_payload()
    clock.instant = datetime(2026, 11, 5, 0, 2, tzinfo=ET)
    payload = state.forex_factory_us_news_payload()
    assert len(feed.calls) == 2 and "cached" not in payload
    assert payload["date"] == "2026-11-05"
    assert [event["title"] for event in payload["events"]] == ["FOMC Statement"]


def test_a_provider_error_is_one_plain_line_and_an_empty_calendar(clock):
    payload = _state(Feed(URLError("timed out"))).forex_factory_us_news_payload()
    assert payload["live"] is False and payload["events"] == [] and payload["weeklyEvents"] == []
    assert payload["errors"] == ["Forex Factory calendar unavailable: URLError."]


def test_a_non_list_answer_is_reported_as_unavailable(clock):
    payload = _state(Feed({"error": "rate limited"})).forex_factory_us_news_payload()
    assert payload["live"] is False and payload["events"] == []
    assert payload["errors"] == ["Forex Factory returned an unavailable calendar response."]


def test_an_empty_feed_answers_no_events_and_is_not_marked_live(clock):
    payload = _state(Feed([])).forex_factory_us_news_payload()
    assert payload["events"] == [] and payload["errors"] == []
    assert payload["live"] is False, "an empty feed is not proof the calendar is live"
