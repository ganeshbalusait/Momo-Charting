"""Integration: the ticker card's day chart route (/api/momx-scanner/daybars)
on the real scanner worker.

The card draws "today 04:00-20:00 ET"; a History snapshot's card asks for
its own day with ?day=YYYY-MM-DD (a weekend snapshot shows the last session
before it). The 5-minute tape is a fake feed and "now" is pinned, so nothing
here depends on the clock or reaches Alpaca.
"""
from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from momx import daybars, feed
from worker_harness import running_worker

ET = ZoneInfo("America/New_York")
DAYBARS = "/api/momx-scanner/daybars"


def at(day, hour, minute):
    return int(datetime(2026, 10 if day > 20 else 11, day, hour, minute, tzinfo=ET).timestamp())


# Friday 10-30, Monday 11-02, Tuesday 11-03. 03:55 and 20:00 are outside the card's window.
TIMES = [
    (at(30, 9, 30), 50.0), (at(30, 15, 55), 51.0),
    (at(2, 3, 55), 59.0), (at(2, 4, 0), 60.0), (at(2, 12, 0), 61.5), (at(2, 19, 55), 62.0), (at(2, 20, 0), 99.0),
    (at(3, 4, 0), 70.0), (at(3, 9, 35), 72.25), (at(3, 16, 0), 71.0),
]


class FixedNow(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 4, 12, 0, tzinfo=ET).astimezone(tz) if tz else datetime(2026, 11, 4, 17, 0)


@pytest.fixture
def tape(monkeypatch):
    calls = []

    def fetch_5m(symbols, **kwargs):
        calls.append((list(symbols), kwargs))
        frame = pd.DataFrame({"time": [t for t, _ in TIMES], "close": [c for _, c in TIMES]})
        return SimpleNamespace(bars={symbol: frame for symbol in symbols if symbol != "NODATA"}, errors={})

    monkeypatch.setattr(feed, "fetch_5m", fetch_5m)
    monkeypatch.setattr(daybars, "datetime", FixedNow)
    return calls


def test_the_card_gets_only_the_newest_day_inside_4am_to_8pm(tmp_path, monkeypatch, tape):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get(DAYBARS + "?symbol=aapl")
    assert status == 200, body
    assert body["symbol"] == "AAPL" and body["day"] == "2026-11-03"
    assert body["bars"] == [[at(3, 4, 0), 70.0], [at(3, 9, 35), 72.25], [at(3, 16, 0), 71.0]]
    assert (body["low"], body["high"]) == (70.0, 72.25)
    # One cached tape read, never the Schwab volume pass.
    assert tape == [(["AAPL"], {"days": 1, "schwab_volume": False})]


def test_a_history_snapshot_gets_its_own_session(tmp_path, monkeypatch, tape):
    with running_worker(tmp_path, monkeypatch) as worker:
        monday = worker.get(DAYBARS + "?symbol=AAPL&day=2026-11-02")[1]
        sunday = worker.get(DAYBARS + "?symbol=AAPL&day=2026-11-01")[1]
    assert monday["day"] == "2026-11-02"
    assert [close for _, close in monday["bars"]] == [60.0, 61.5, 62.0]      # 03:55 and 20:00 left out
    assert sunday["day"] == "2026-10-30"                                    # weekend -> the Friday before
    assert tape[0][1]["days"] == 2 + 4                                      # reaches back past a weekend


@pytest.mark.parametrize("query, error", [
    ("?symbol=%24%24%24", "bad symbol"),
    ("?symbol=", "bad symbol"),
    ("?symbol=AAPL&day=2026-13-45", "bad day"),
    ("?symbol=AAPL&day=2026-09-01", "day out of range"),
    ("?symbol=AAPL&day=2026-11-05", "day out of range"),
    ("?symbol=NODATA", "no bars"),
])
def test_junk_is_a_200_with_a_reason_never_a_crash(tmp_path, monkeypatch, tape, query, error):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get(DAYBARS + query)
    assert status == 200
    assert body["error"] == error and "bars" not in body


def test_a_feed_failure_is_named_and_the_worker_keeps_serving(tmp_path, monkeypatch, tape):
    def broken(symbols, **kwargs):
        raise TimeoutError("alpaca slow")

    monkeypatch.setattr(feed, "fetch_5m", broken)
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get(DAYBARS + "?symbol=NVDA")
        health = worker.get("/healthz")
    assert status == 200 and body == {"symbol": "NVDA", "error": "TimeoutError"}
    assert health == (200, {"ok": True})
