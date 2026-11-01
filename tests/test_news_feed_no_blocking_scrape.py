"""GET /api/news-feed must not scrape while the phone waits (2026-10-05: 75 s)."""
from __future__ import annotations

import threading
import time

import pandas as pd

import api_server
from api_server import DashboardState


def _state(scrape_seconds: float):
    state = DashboardState.__new__(DashboardState)
    calls = []

    def scrape(symbols=None):
        calls.append(symbols)
        time.sleep(scrape_seconds)
        return {"message": "refreshed", "sources": [{"label": "X", "status": "ok"}]}

    state._refresh_catalyst_information = scrape
    state._recent_catalysts_or_empty = lambda limit=200, force=False: pd.DataFrame([{"symbol": "AAPL", "headline": "Apple stored"}])
    state._latest_catalysts_or_empty = lambda force=False: pd.DataFrame()
    state.momx_jev_judge = type("J", (), {"annotate": lambda self, payload: None})()
    return state, calls


def test_get_returns_stored_rows_at_once_and_refreshes_in_the_background():
    state, calls = _state(scrape_seconds=1.0)
    started = time.monotonic()
    first = state.load_news(scrape_now=False)
    assert time.monotonic() - started < 0.5                       # did not wait for the 1 s scrape
    assert first["catalysts"][0]["headline"] == "Apple stored"
    for _ in range(40):                                          # the background scrape lands
        if getattr(state, "_news_feed_last_refresh", None):
            break
        time.sleep(0.05)
    assert calls == [None]
    second = state.load_news(scrape_now=False)                   # fresh: no second scrape
    assert second["newsFeedMeta"]["sources"][0]["status"] == "ok"
    assert calls == [None]


def test_only_one_background_scrape_at_a_time():
    state, calls = _state(scrape_seconds=0.5)
    threads = [threading.Thread(target=state.load_news, kwargs={"scrape_now": False}) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    time.sleep(0.7)
    assert len(calls) == 1


def test_the_refresh_button_still_scrapes_now():
    state, calls = _state(scrape_seconds=0.0)
    state.load_news(["AAPL"])
    assert calls == [["AAPL"]]
