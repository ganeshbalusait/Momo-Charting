from __future__ import annotations

import threading
import time
import types
import unittest
from datetime import datetime, timezone

from catalyst_engine import CatalystEngine, CatalystItem


class CatalystEngineTests(unittest.TestCase):
    def test_watchlist_news_loads_symbols_concurrently_and_sorts_newest_first(self) -> None:
        engine = CatalystEngine(max_workers=4)
        active = 0
        peak_active = 0
        lock = threading.Lock()

        def fake_load(symbol: str, limit: int = 5) -> list[CatalystItem]:
            nonlocal active, peak_active
            with lock:
                active += 1
                peak_active = max(peak_active, active)
            time.sleep(0.03)
            with lock:
                active -= 1
            minute = {"AAPL": 1, "MSFT": 2, "NVDA": 3, "AMZN": 4}[symbol]
            return [CatalystItem(
                symbol=symbol,
                headline=f"{symbol} headline",
                source="Test",
                url=f"https://example.com/{symbol}",
                published_at=datetime(2026, 7, 14, 14, minute, tzinfo=timezone.utc).isoformat(),
                score=3,
                sentiment="Strong",
                tags="Positive Catalyst",
            )]

        engine.load_symbol_news = fake_load  # type: ignore[method-assign]
        rows = engine.load_watchlist_news(["AAPL", "MSFT", "NVDA", "AMZN"], limit=4)

        self.assertGreater(peak_active, 1)
        self.assertEqual([row["symbol"] for row in rows], ["AMZN", "NVDA", "MSFT", "AAPL"])

    def test_watchlist_news_deduplicates_symbols(self) -> None:
        engine = CatalystEngine(max_workers=4)
        calls: list[str] = []

        def fake_load(symbol: str, limit: int = 5) -> list[CatalystItem]:
            calls.append(symbol)
            return []

        engine.load_symbol_news = fake_load  # type: ignore[method-assign]
        engine.load_watchlist_news(["aapl", "AAPL", " msft "], limit=40)

        self.assertCountEqual(calls, ["AAPL", "MSFT"])




class _StubSource:
    """A NewsSource stand-in that returns canned RawArticles."""

    name = "stub"
    label = "Stub"
    homepage = ""
    ticker_tagged = True
    requires_key = ""

    def __init__(self, articles):
        self.articles = articles

    def fetch(self, symbol, limit):
        return list(self.articles)


class _StubClassifier:
    def __init__(self, verdicts, available=True):
        self.verdicts = verdicts
        self.available = available
        self.requests = []

    def classify(self, headlines):
        self.requests.append(headlines)
        out = {}
        for entry in headlines:
            label, reason = self.verdicts.get(entry["headline"], (None, ""))
            if label:
                out[entry["key"]] = types.SimpleNamespace(label=label, reason=reason)
        return out

    def status(self):
        return {"enabled": True, "available": self.available, "model": "stub"}


def _article(headline, url, minute):
    from catalyst_engine import RawArticle
    return RawArticle(headline=headline, url=url, published_at=datetime.now(timezone.utc).replace(minute=minute, second=0, microsecond=0), publisher="Stub")


class CatalystEngineAiSentimentTests(unittest.TestCase):
    def _engine(self, classifier, articles):
        engine = CatalystEngine(sources=(), cache_ttl_seconds=0, sentiment_classifier=classifier)
        engine.sources = [_StubSource(articles)]
        return engine

    def test_ai_labels_override_keyword_labels_and_are_marked_as_ai(self) -> None:
        articles = [
            _article("Apple beats on iPhone demand", "https://example.com/1", 1),
            _article("Tesla recalls 300k vehicles over steering fault", "https://example.com/2", 2),
            # No keyword hit; the AI confirms it is routine.
            _article("Nvidia to present at industry conference", "https://example.com/3", 3),
            # The AI did not answer for this one: keyword label stays.
            _article("Amazon shares fall after downgrade", "https://example.com/4", 4),
        ]
        classifier = _StubClassifier({
            "Apple beats on iPhone demand": ("Positive", "Beat and raised guidance"),
            "Tesla recalls 300k vehicles over steering fault": ("Negative", "Costly safety recall"),
            "Nvidia to present at industry conference": ("Neutral", "Routine appearance"),
        })
        engine = self._engine(classifier, articles)
        rows = {item.headline: item for item in engine.load_symbol_news("AAPL", limit=10)}

        apple = rows["Apple beats on iPhone demand"]
        self.assertIn(apple.sentiment, {"Positive", "Strong"})
        self.assertGreaterEqual(apple.score, 2)
        self.assertEqual(apple.sentiment_source, "ai")
        self.assertEqual(apple.sentiment_reason, "Beat and raised guidance")
        self.assertIn("Positive Catalyst", apple.tags)

        tesla = rows["Tesla recalls 300k vehicles over steering fault"]
        self.assertEqual((tesla.sentiment, tesla.score, tesla.sentiment_source), ("Negative", 0, "ai"))
        self.assertTrue(tesla.tags.startswith("Risk Headline"))

        nvidia = rows["Nvidia to present at industry conference"]
        self.assertEqual((nvidia.sentiment, nvidia.score, nvidia.sentiment_source), ("Neutral", 1, "ai"))

        amazon = rows["Amazon shares fall after downgrade"]
        self.assertEqual(amazon.sentiment_source, "keywords")
        self.assertEqual(amazon.sentiment_reason, "")
        # Every headline was offered to the AI once, with its summary slot.
        self.assertEqual(len(classifier.requests), 1)
        self.assertEqual(sorted(entry["headline"] for entry in classifier.requests[0]), sorted(rows))

    def test_keyword_labels_stay_when_no_classifier_or_it_is_unavailable(self) -> None:
        headline = "Tesla recalls 300k vehicles over steering fault"
        articles = [_article(headline, "https://example.com/2", 2)]
        _, keyword_sentiment, _ = CatalystEngine(sources=()).score_headline(headline)
        for classifier in (None, _StubClassifier({}, available=False)):
            engine = self._engine(classifier, articles)
            [item] = engine.load_symbol_news("TSLA", limit=10)
            self.assertEqual((item.sentiment, item.sentiment_source), (keyword_sentiment, "keywords"))
            if classifier is not None:
                self.assertEqual(classifier.requests, [])
        self.assertEqual(self._engine(None, articles).sentiment_status()["reason"], "not configured")

    def test_classifier_exception_never_breaks_the_feed(self) -> None:
        class Boom(_StubClassifier):
            def classify(self, headlines):
                raise RuntimeError("offline")

        headline = "Apple beats on iPhone demand"
        _, keyword_sentiment, _ = CatalystEngine(sources=()).score_headline(headline)
        engine = self._engine(Boom({}), [_article(headline, "https://example.com/1", 1)])
        [item] = engine.load_symbol_news("AAPL", limit=10)
        self.assertEqual((item.sentiment, item.sentiment_source), (keyword_sentiment, "keywords"))


if __name__ == "__main__":
    unittest.main()
