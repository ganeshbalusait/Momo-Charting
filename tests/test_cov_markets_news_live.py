"""Unit: the live news ingest's status (what the MomX NEWS health line reads)
and its one-time start inside api_server (2026-11-04 coverage).

The ingest's transports are never started here: a fake LiveNewsIngest class
stands in for the start, and the real one is only fed rows directly.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import api_server
import news_live


class Repo:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.calls = []

    def log_catalysts(self, rows):
        if self.fail:
            raise RuntimeError("database is locked")
        self.calls.append(rows)
        return len(rows)


def _row(symbol, url, minutes_old=1):
    published = datetime.now(timezone.utc) - timedelta(minutes=minutes_old)
    return {"symbol": symbol, "url": url, "headline": "h", "published_at": published.isoformat()}


def score(headline, summary=""):
    return 1, "Neutral", []


def test_status_reports_every_feed_with_the_fields_the_health_line_reads():
    live = news_live.LiveNewsIngest(Repo(), score, lambda: [])
    status = live.status()
    assert set(status) == {"alpaca", "wires", "edgar", "media"}
    for feed in status.values():
        assert feed == {"status": "idle", "received": 0, "stored": 0, "lastAt": None, "error": None, "medianLagSec": None}


def test_status_after_a_delivery_carries_counts_time_and_no_private_fields():
    live = news_live.LiveNewsIngest(Repo(), score, lambda: [])
    live.store("alpaca", [_row("NVDA", "u1", 3), _row("AAPL", "u2", 5)])
    alpaca = live.status()["alpaca"]
    assert (alpaca["received"], alpaca["stored"]) == (2, 2)
    assert datetime.fromisoformat(alpaca["lastAt"]).tzinfo is not None
    assert 150 <= alpaca["medianLagSec"] <= 330, "alpaca is timed from the first message"
    assert not any(key.startswith("_") for key in alpaca)
    assert live.status()["wires"]["received"] == 0, "feeds are counted separately"


def test_a_store_failure_surfaces_as_the_feeds_error_and_stores_nothing():
    live = news_live.LiveNewsIngest(Repo(fail=True), score, lambda: [])
    assert live.store("edgar", [_row("MSFT", "u3")]) == 0
    assert live.status()["edgar"]["error"] == "store: RuntimeError"


class FakeIngest:
    built: list = []

    def __init__(self, repository, score_fn, pool, *, universe=None, sec_user_agent=""):
        self.started = 0
        self.universe = universe
        self.sec_user_agent = sec_user_agent
        FakeIngest.built.append(self)

    def start(self):
        self.started += 1


@pytest.fixture
def fresh_live(monkeypatch):
    FakeIngest.built = []
    monkeypatch.setattr(news_live, "LiveNewsIngest", FakeIngest)
    monkeypatch.setitem(api_server._NEWS_LIVE, "ingest", None)
    engine = SimpleNamespace(score_headline=score, sec_user_agent="AGX test agent")
    monkeypatch.setattr(api_server.STATE, "catalysts", engine)
    yield


def test_the_live_ingest_starts_once_however_often_main_asks(fresh_live):
    api_server._news_live_start()
    api_server._news_live_start()
    assert len(FakeIngest.built) == 1, "a second start would open a second Alpaca websocket"
    ingest = FakeIngest.built[0]
    assert ingest.started == 1
    assert api_server._NEWS_LIVE["ingest"] is ingest
    assert ingest.sec_user_agent == "AGX test agent", "EDGAR needs the engine's declared user agent"
    assert callable(ingest.universe)
