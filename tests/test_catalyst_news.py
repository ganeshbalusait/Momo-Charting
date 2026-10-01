from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from catalyst_news import (
    CatalystCache,
    classify_headline,
    news_credentials,
    pick_catalyst,
)

NOW = datetime(2026, 8, 24, 10, 30, tzinfo=timezone.utc)  # 06:30 ET Monday


def _item(headline: str, *, hours_ago: float = 2.0, symbols: list | None = None,
          source: str = "benzinga") -> dict:
    return {
        "headline": headline,
        "created_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "symbols": ["NVDA"] if symbols is None else symbols,
        "source": source,
    }


class ClassifyHeadlineTests(unittest.TestCase):
    def test_tags_are_keyword_driven(self) -> None:
        self.assertEqual(classify_headline("Nvidia Beats Estimates, Raises Guidance"), "EARNINGS")
        self.assertEqual(classify_headline("Morgan Stanley Raises Price Target on NVDA"), "UPGRADE")
        self.assertEqual(classify_headline("FDA Grants Breakthrough Therapy Status"), "FDA")
        self.assertEqual(classify_headline("Microsoft To Buy Startup For $2B"), "M&A")
        self.assertEqual(classify_headline("Company Announces $500M Convertible Notes Offering"), "OFFERING")
        self.assertEqual(classify_headline("Bitcoin Tops $100K As Miners Rally"), "CRYPTO")
        self.assertIsNone(classify_headline("10 Stocks To Watch This Week"))
        self.assertIsNone(classify_headline(""))
        self.assertIsNone(classify_headline(None))

    def test_first_matching_rule_wins(self) -> None:
        # Earnings outranks the analyst chatter that follows a report.
        self.assertEqual(
            classify_headline("After Earnings Beat, Analysts Upgrade The Stock"),
            "EARNINGS",
        )


class PickCatalystTests(unittest.TestCase):
    def test_tagged_beats_untagged_regardless_of_recency(self) -> None:
        picked = pick_catalyst(
            [
                _item("Nvidia Mentioned In Market Wrap", hours_ago=0.5),
                _item("Nvidia Raises Guidance On AI Demand", hours_ago=6.0),
            ],
            NOW,
            "NVDA",
        )
        self.assertEqual(picked["tag"], "GUIDANCE")
        self.assertEqual(picked["ageMinutes"], 360)

    def test_focused_story_beats_listicle(self) -> None:
        picked = pick_catalyst(
            [
                _item("Chip Names Rally: 8 Stocks Moving", hours_ago=1.0,
                      symbols=["NVDA", "AMD", "AVGO", "MU", "INTC", "TSM", "ARM", "SMCI"]),
                _item("Nvidia Customers Notified About AI-Related Price Hikes", hours_ago=2.0,
                      symbols=["NVDA"]),
            ],
            NOW,
            "NVDA",
        )
        self.assertIn("Price Hikes", picked["headline"])

    def test_old_and_wrong_symbol_headlines_are_ignored(self) -> None:
        self.assertIsNone(pick_catalyst(
            [
                _item("Nvidia Beats Estimates", hours_ago=30.0),          # too old
                _item("Tesla Recalls Vehicles", symbols=["TSLA"]),        # not ours
                {"headline": "No timestamp"},                              # unparseable
            ],
            NOW,
            "NVDA",
        ))
        self.assertIsNone(pick_catalyst(None, NOW, "NVDA"))

    def test_untagged_story_still_surfaces_as_news(self) -> None:
        picked = pick_catalyst([_item("Nvidia In Focus Ahead Of Conference")], NOW, "NVDA")
        self.assertEqual(picked["tag"], "NEWS")


class CatalystCacheTests(unittest.TestCase):
    def test_ttl_gates_refetch_and_get_never_fetches(self) -> None:
        cache = CatalystCache(ttl_seconds=600.0)
        calls: list[str] = []

        def fetcher(symbol: str) -> list[dict]:
            calls.append(symbol)
            return [_item(f"{symbol} Raises Guidance")]

        self.assertEqual(cache.refresh(["NVDA", "TSLA"], fetcher, now=0.0, now_utc=NOW), 2)
        self.assertEqual(cache.refresh(["NVDA", "TSLA"], fetcher, now=300.0, now_utc=NOW), 0)
        self.assertEqual(cache.refresh(["NVDA"], fetcher, now=700.0, now_utc=NOW), 1)
        self.assertEqual(calls, ["NVDA", "TSLA", "NVDA"])
        self.assertEqual(cache.get("NVDA")["tag"], "GUIDANCE")
        self.assertIsNone(cache.get("MSFT"))

    def test_fetch_failure_keeps_previous_verdict_but_respects_ttl(self) -> None:
        cache = CatalystCache(ttl_seconds=600.0)
        cache.refresh(["NVDA"], lambda s: [_item("Nvidia Beats Estimates")], now=0.0, now_utc=NOW)

        def broken(symbol: str) -> list[dict]:
            raise OSError("news feed down")

        cache.refresh(["NVDA"], broken, now=700.0, now_utc=NOW)
        self.assertEqual(cache.get("NVDA")["tag"], "EARNINGS")  # stale beats blinking
        # The failed check re-stamped the TTL: no hammering while the feed is down.
        self.assertEqual(cache.refresh(["NVDA"], broken, now=800.0, now_utc=NOW), 0)


class NewsCredentialsTests(unittest.TestCase):
    def test_paper5_wins_and_missing_pairs_are_skipped(self) -> None:
        env = {
            "ALPACA_PROFILE_PAPER4_KEY_ID": "k4", "ALPACA_PROFILE_PAPER4_SECRET_KEY": "s4",
            "ALPACA_PROFILE_PAPER5_KEY_ID": "k5", "ALPACA_PROFILE_PAPER5_SECRET_KEY": "s5",
        }
        self.assertEqual(news_credentials(lambda k, d="": env.get(k, d)), ("k5", "s5"))
        self.assertIsNone(news_credentials(lambda k, d="": ""))


if __name__ == "__main__":
    unittest.main()
