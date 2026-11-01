"""The MomX tab's news endpoints: stored headlines per ticker (DB only) and the
one-at-a-time background scrape.

Both run against a scratch TradingRepository and a fake engine, never the
network: the point of these tests is the contract the MomX panel reads
(``latest`` / ``feed`` / ``newsFeedMeta`` / ``refreshing``), not the scraping.
The store is exercised directly AND through DashboardState's delegation, so a
change to either side is caught.
"""
from __future__ import annotations

import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from news_feed_store import MomxNewsStore, dedupe_symbols, scrape_symbols


# Relative to the real clock: the store reads a 7-day window back from NOW, so a fixed
# date made 'hours ago' items fall out of the window a week after it was written.
NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def _item(symbol: str, headline: str, hours_ago: float, **extra) -> dict:
    base = {
        "symbol": symbol,
        "headline": headline,
        "source": "Reuters",
        "url": f"https://example.com/{symbol}/{abs(hash(headline))}",
        "published_at": _iso(hours_ago),
        "score": 3,
        "sentiment": "Strong",
        "tags": "Positive Catalyst",
        "via": "Yahoo Finance",
        "summary": "teaser",
        "related_symbols": f"{symbol},SPY",
        "article_id": "a1",
    }
    base.update(extra)
    return base


class FakeEngine:
    """Answers load_watchlist_news with canned rows, reports one ok source."""

    lookback_days = 7
    per_symbol_limit = 25

    def __init__(self, rows: list[dict], delay: float = 0.0) -> None:
        self.rows = rows
        self.delay = delay
        self.calls: list[list[str]] = []

    def load_watchlist_news(self, symbols, limit: int = 40):
        import time

        self.calls.append(list(symbols))
        if self.delay:
            time.sleep(self.delay)
        return [row for row in self.rows if row["symbol"] in set(symbols)]

    def source_status(self):
        return [{"name": "yahoo_rss", "label": "Yahoo Finance RSS", "status": "ok", "items": len(self.rows), "error": ""}]

    def available_sources(self):
        return [{"name": "yahoo_rss", "label": "Yahoo Finance RSS", "enabled": True, "reason": ""}]


class NewsSettings:
    momx_feed_per_symbol = 5
    lookback_days = 7
    momx_refresh_max_symbols = 60
    per_symbol_limit = 25


class MomxNewsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        from database.repository import TradingRepository

        self._tmp = tempfile.TemporaryDirectory()
        self.repository = TradingRepository(Path(self._tmp.name) / "news.db")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _store(self, engine, settings=None) -> MomxNewsStore:
        return MomxNewsStore(self.repository, engine, settings or NewsSettings())

    def test_dedupe_symbols(self) -> None:
        self.assertEqual(dedupe_symbols(["aapl", " MSFT", "AAPL", "", None]), ["AAPL", "MSFT"])
        self.assertEqual(dedupe_symbols("AAPL"), [])

    def test_latest_is_db_only_and_groups_newest_first(self) -> None:
        self.repository.log_catalysts([
            _item("AAPL", "Apple older story", 30),
            _item("AAPL", "Apple newest story", 2),
            _item("MSFT", "Microsoft story", 5, sentiment="Negative", score=0),
            _item("NVDA", "Nvidia story from last month", 24 * 20),  # outside the 7-day lookback
        ])
        engine = FakeEngine([])
        payload = self._store(engine).latest(["aapl", "MSFT", "NVDA", "TSLA", "AAPL"])
        self.assertEqual(engine.calls, [])  # never scrapes
        self.assertEqual(payload["symbols"], ["AAPL", "MSFT", "NVDA", "TSLA"])
        self.assertEqual(payload["count"], 2)
        self.assertEqual(payload["latest"]["AAPL"]["headline"], "Apple newest story")
        self.assertEqual(payload["latest"]["AAPL"]["publishedAt"], _iso(2))
        self.assertEqual(payload["latest"]["AAPL"]["relatedSymbols"], ["AAPL", "SPY"])
        self.assertEqual(payload["latest"]["AAPL"]["via"], "Yahoo Finance")
        self.assertEqual(payload["latest"]["MSFT"]["sentiment"], "Negative")
        self.assertNotIn("NVDA", payload["latest"])
        self.assertNotIn("TSLA", payload["latest"])
        aapl_feed = [row for row in payload["feed"] if row["symbol"] == "AAPL"]
        self.assertEqual([row["headline"] for row in aapl_feed], ["Apple newest story", "Apple older story"])
        self.assertFalse(payload["refreshing"])
        self.assertEqual(payload["newsFeedMeta"], {})
        self.assertIsNone(payload["storeError"])

    def test_latest_with_no_symbols_is_empty_not_an_error(self) -> None:
        payload = self._store(FakeEngine([])).latest([])
        self.assertEqual(payload["latest"], {})
        self.assertEqual(payload["feed"], [])
        self.assertEqual(payload["count"], 0)

    def test_refresh_runs_once_in_the_background_and_stores(self) -> None:
        engine = FakeEngine([_item("AAPL", "Apple beats", 1)], delay=0.2)
        store = self._store(engine)
        started = store.refresh(["AAPL", "MSFT"])
        self.assertTrue(started["started"])
        self.assertEqual(started["symbols"], ["AAPL", "MSFT"])
        # A second press while the first runs is refused, not stacked.
        again = store.refresh(["AAPL"])
        self.assertFalse(again["started"])
        self.assertEqual(again["reason"], "already running")
        self.assertTrue(store.latest(["AAPL"])["refreshing"])
        self.assertTrue(store.wait(5))
        self.assertEqual(engine.calls, [["AAPL", "MSFT"]])
        payload = store.latest(["AAPL", "MSFT"])
        self.assertFalse(payload["refreshing"])
        self.assertEqual(payload["latest"]["AAPL"]["headline"], "Apple beats")
        meta = payload["newsFeedMeta"]
        self.assertEqual(meta["symbolsScanned"], 2)
        self.assertEqual(meta["headlinesRefreshed"], 1)
        self.assertEqual(meta["headlinesStored"], 1)
        self.assertEqual(meta["sources"][0]["status"], "ok")
        self.assertNotIn("message", meta)
        self.assertIsNone(payload["refreshError"])
        # The same story is not stored twice on the next refresh.
        store.refresh(["AAPL"])
        self.assertTrue(store.wait(5))
        self.assertEqual(store.latest(["AAPL"])["newsFeedMeta"]["headlinesStored"], 0)

    def test_refresh_caps_the_symbol_list(self) -> None:
        settings = NewsSettings()
        settings.momx_refresh_max_symbols = 3
        engine = FakeEngine([])
        store = self._store(engine, settings)
        started = store.refresh([f"S{i}" for i in range(10)])
        self.assertEqual(started["symbols"], ["S0", "S1", "S2"])
        self.assertTrue(store.wait(5))
        self.assertEqual(engine.calls, [["S0", "S1", "S2"]])

    def test_refresh_failure_is_reported_not_raised(self) -> None:
        class Exploding(FakeEngine):
            def load_watchlist_news(self, symbols, limit: int = 40):
                raise RuntimeError("edge blocked")

        repository = types.SimpleNamespace(
            get_catalysts_for_symbols=lambda *a, **k: [],
            log_bot_event=lambda kind, message: None,
            log_catalysts=lambda items: 0,
        )
        store = MomxNewsStore(repository, Exploding([]), NewsSettings())
        self.assertTrue(store.refresh(["AAPL"])["started"])
        self.assertTrue(store.wait(5))
        payload = store.latest(["AAPL"])
        self.assertFalse(payload["refreshing"])
        self.assertIn("edge blocked", payload["refreshError"])

    def test_scrape_symbols_reports_unavailable_sources_in_the_message(self) -> None:
        class MixedEngine(FakeEngine):
            def source_status(self):
                return [
                    {"name": "yahoo_rss", "label": "Yahoo Finance RSS", "status": "ok", "items": 1, "error": ""},
                    {"name": "finviz", "label": "Finviz", "status": "blocked", "items": 0, "error": "HTTP 403"},
                ]

        meta = scrape_symbols(MixedEngine([_item("AAPL", "Apple beats", 1)]), self.repository, ["AAPL"], NewsSettings())
        self.assertIn("Sources ok: 1/2", meta["message"])
        self.assertIn("Unavailable: Finviz", meta["message"])
        self.assertEqual(meta["headlinesStored"], 1)
        self.assertEqual(meta["symbols"], ["AAPL"])


class DashboardDelegationTests(unittest.TestCase):
    """DashboardState routes the MomX calls through the store and its own
    _refresh_catalyst_information, so a swapped engine on the state is used."""

    def test_state_without_init_builds_its_store_lazily(self) -> None:
        import api_server
        from database.repository import TradingRepository

        with tempfile.TemporaryDirectory() as tmp:
            repository = TradingRepository(Path(tmp) / "news.db")
            state = api_server.DashboardState.__new__(api_server.DashboardState)
            state.repository = repository
            engine = FakeEngine([_item("AAPL", "Apple beats", 1)])
            state.catalysts = engine
            self.assertEqual(state.momx_news_latest(["AAPL"])["count"], 0)
            self.assertTrue(state.momx_news_refresh(["AAPL"])["started"])
            self.assertTrue(state.momx_news_store.wait(5))
            payload = state.momx_news_latest(["AAPL"])
            self.assertEqual(payload["latest"]["AAPL"]["headline"], "Apple beats")
            self.assertEqual(engine.calls, [["AAPL"]])
            self.assertEqual(payload["newsFeedMeta"]["sources"][0]["name"], "yahoo_rss")


if __name__ == "__main__":
    unittest.main()


def test_auto_refresh_batch_movers_first_then_rotation():
    from news_feed_store import auto_refresh_batch
    universe = [f"S{i}" for i in range(100)]
    movers = ["INOD", "S5", "S5", "LITE"]
    batch, cur = auto_refresh_batch(movers, universe, 0, size=10, mover_share=3)
    assert batch[:3] == ["INOD", "S5", "LITE"]
    assert batch[3:] == ["S0", "S1", "S2", "S3", "S4", "S6", "S7"]      # S5 not repeated
    assert cur == 8
    batch2, cur2 = auto_refresh_batch([], universe, 98, size=4)
    assert batch2 == ["S98", "S99", "S0", "S1"] and cur2 == 2           # wraps round
    assert auto_refresh_batch(["A"], [], 5, size=4) == (["A"], 0)


class MomxNewsLatestIsSharedAcrossPolls(unittest.TestCase):
    """2026-10-05 09:35 ET: every MomX screen's 15 s poll took the process-wide DB
    lock; 52 of 112 threads queued there and /api/news-feed hung 60 s+."""

    def setUp(self) -> None:
        import api_server
        self.state = api_server.STATE
        saved = self.state.__dict__.get("_momx_news_cache")
        self.addCleanup(lambda: self.state.__dict__.__setitem__("_momx_news_cache", saved or {}))
        self.state._momx_news_cache = {}

    def test_repeat_polls_inside_the_window_read_the_database_once(self) -> None:
        from unittest.mock import patch
        with patch.object(self.state, "_momx_news_latest_uncached", return_value={"items": []}) as read:
            self.state.momx_news_latest(["AAPL", "NVDA"])
            self.state.momx_news_latest(["NVDA", "AAPL"])
        self.assertEqual(read.call_count, 1)

    def test_a_different_board_gets_its_own_read(self) -> None:
        from unittest.mock import patch
        with patch.object(self.state, "_momx_news_latest_uncached", return_value={"items": []}) as read:
            self.state.momx_news_latest(["AAPL"])
            self.state.momx_news_latest(["TSLA"])
        self.assertEqual(read.call_count, 2)

    def test_an_expired_answer_is_read_again(self) -> None:
        from unittest.mock import patch
        with patch.object(self.state, "_momx_news_latest_uncached", return_value={"items": []}) as read:
            self.state.momx_news_latest(["AAPL"])
            self.state._momx_news_cache[("AAPL",)]["at"] -= self.state.MOMX_NEWS_LATEST_TTL_SECONDS + 1
            self.state.momx_news_latest(["AAPL"])
        self.assertEqual(read.call_count, 2)

    def test_concurrent_polls_share_one_read(self) -> None:
        import threading
        import time as _t
        from unittest.mock import patch
        calls = []

        def slow(symbols):
            calls.append(1)
            _t.sleep(0.2)
            return {"items": []}

        with patch.object(self.state, "_momx_news_latest_uncached", side_effect=slow):
            threads = [threading.Thread(target=self.state.momx_news_latest, args=(["AAPL"],)) for _ in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(5)
        self.assertEqual(len(calls), 1)


class MomxNewsLatestSeesAFinishedScrape(MomxNewsLatestIsSharedAcrossPolls):
    def test_a_finished_scrape_makes_the_next_poll_read_again(self) -> None:
        import threading
        import types as _types
        from unittest.mock import patch
        store = _types.SimpleNamespace(lock=threading.Lock(), state={"finishedAt": "t1"})
        saved = self.state.__dict__.get("momx_news_store")
        self.addCleanup(lambda: self.state.__dict__.__setitem__("momx_news_store", saved))
        self.state.momx_news_store = store
        with patch.object(self.state, "_momx_news_latest_uncached", return_value={"items": []}) as read:
            self.state.momx_news_latest(["AAPL"])
            self.state.momx_news_latest(["AAPL"])
            store.state["finishedAt"] = "t2"
            self.state.momx_news_latest(["AAPL"])
        self.assertEqual(read.call_count, 2)


class NewsListReadsAreShared(unittest.TestCase):
    """2026-10-05 19:50 ET: GET /api/news-feed took 10-60 s - its catalyst reads held the
    process-wide DB lock while waiting for the GIL; one 30 s answer is now shared."""

    def setUp(self) -> None:
        import api_server
        self.state = api_server.STATE
        saved = self.state.__dict__.get("_news_read_cache")
        self.addCleanup(lambda: self.state.__dict__.__setitem__("_news_read_cache", saved or {}))
        self.state._news_read_cache = {}

    def test_repeat_reads_inside_the_window_hit_the_database_once(self) -> None:
        import pandas as pd
        from unittest.mock import patch
        frame = pd.DataFrame([{"symbol": "AAPL", "headline": "x"}])
        with patch.object(self.state.repository, "get_recent_catalysts", return_value=frame) as read:
            first = self.state._recent_catalysts_or_empty(limit=200)
            second = self.state._recent_catalysts_or_empty(limit=200)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(len(first), 1)
        self.assertEqual(len(second), 1)

    def test_a_finished_scrape_drops_the_shared_answer(self) -> None:
        import pandas as pd
        from unittest.mock import patch
        frame = pd.DataFrame([{"symbol": "AAPL", "headline": "x"}])
        with patch.object(self.state.repository, "get_latest_catalysts_by_symbol", return_value=frame) as read, \
                patch.object(self.state, "_refresh_catalyst_information_impl", return_value={"message": ""}):
            self.state._latest_catalysts_or_empty()
            self.state._refresh_catalyst_information(["AAPL"])
            # The refresh button's own read is forced, so it sees the new rows.
            self.state._latest_catalysts_or_empty(force=True)
        self.assertEqual(read.call_count, 2)

    def test_a_finished_scrape_marks_the_answer_old_but_keeps_serving_it(self) -> None:
        import pandas as pd
        from unittest.mock import patch
        frame = pd.DataFrame([{"symbol": "AAPL", "headline": "x"}])
        with patch.object(self.state.repository, "get_latest_catalysts_by_symbol", return_value=frame),                 patch.object(self.state, "_refresh_catalyst_information_impl", return_value={"message": ""}):
            self.state._latest_catalysts_or_empty()
            self.state._refresh_catalyst_information(["AAPL"])
        self.assertEqual(self.state._news_read_cache["latest"]["at"], 0.0)
        self.assertIsNotNone(self.state._news_read_cache["latest"]["frame"])

    def test_a_failed_read_degrades_to_an_empty_frame(self) -> None:
        from unittest.mock import patch
        with patch.object(self.state.repository, "get_recent_catalysts", side_effect=RuntimeError("locked")):
            self.assertTrue(self.state._recent_catalysts_or_empty(limit=50).empty)


class NewsListServesTheHeldAnswerWhileRereading(NewsListReadsAreShared):
    def test_an_expired_answer_is_served_at_once_and_reread_in_the_background(self) -> None:
        import threading
        import pandas as pd
        from unittest.mock import patch
        old = pd.DataFrame([{"symbol": "OLD"}])
        new = pd.DataFrame([{"symbol": "NEW"}, {"symbol": "NEW2"}])
        gate = threading.Event()

        def slow_read(limit=200):
            gate.wait(5)
            return new

        with patch.object(self.state.repository, "get_recent_catalysts", return_value=old):
            self.state._recent_catalysts_or_empty(limit=200)
        self.state._news_read_cache["recent:200"]["at"] -= self.state.NEWS_READ_CACHE_SECONDS + 1
        with patch.object(self.state.repository, "get_recent_catalysts", side_effect=slow_read):
            served = self.state._recent_catalysts_or_empty(limit=200)
            self.assertEqual(list(served["symbol"]), ["OLD"])
            gate.set()
            slot = self.state._news_read_cache["recent:200"]
            with slot["lock"]:
                pass
        self.assertEqual(len(self.state._recent_catalysts_or_empty(limit=200)), 2)
