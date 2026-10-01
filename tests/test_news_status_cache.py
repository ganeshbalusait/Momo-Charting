"""A watchlist run answered entirely from the per-symbol cache makes no request.
The source statuses must then carry the previous run's verdict (marked
fromCache), not "idle" - otherwise the MomX health line reads "0/8 sources OK"
about feeds that answered minutes ago (seen on the second NEWS press,
2026-09-26)."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone

from catalyst_engine import CatalystEngine, HttpResponse

NOW = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)


def _rss(symbol: str) -> str:
    stamp = (NOW - timedelta(hours=1)).strftime("%a, %d %b %Y %H:%M:%S +0000")
    return (
        '<?xml version="1.0"?><rss version="2.0"><channel>'
        f"<item><title>{symbol} story</title><link>https://example.com/{symbol}</link><pubDate>{stamp}</pubDate></item>"
        "</channel></rss>"
    )


class Http:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url, headers, timeout):
        self.calls += 1
        if "feeds.finance.yahoo.com" in url:
            symbol = url.split("s=")[1].split("&")[0]
            return HttpResponse(status=200, body=_rss(symbol).encode())
        return HttpResponse(status=403, body=b"")


class StatusCacheTests(unittest.TestCase):
    def test_cached_run_keeps_the_previous_verdict(self) -> None:
        http = Http()
        engine = CatalystEngine(sources=("yahoo_rss", "finviz"), http_get=http, cache_ttl_seconds=600)
        first = engine.load_watchlist_news(["AAPL", "MSFT"])
        self.assertEqual(len(first), 2)
        status = {entry["name"]: entry for entry in engine.source_status()}
        self.assertEqual(status["yahoo_rss"]["status"], "ok")
        self.assertEqual(status["finviz"]["status"], "blocked")
        calls = http.calls

        second = engine.load_watchlist_news(["AAPL", "MSFT"])  # all cached
        self.assertEqual(len(second), 2)
        self.assertEqual(http.calls, calls, "a cached run makes no request")
        status = {entry["name"]: entry for entry in engine.source_status()}
        self.assertEqual(status["yahoo_rss"]["status"], "ok")
        self.assertTrue(status["yahoo_rss"]["fromCache"])
        self.assertEqual(status["finviz"]["status"], "blocked")
        self.assertTrue(status["finviz"]["fromCache"])

        # A run that DOES ask the sources again reports fresh verdicts, unmarked.
        engine.load_watchlist_news(["NVDA"])
        status = {entry["name"]: entry for entry in engine.source_status()}
        self.assertEqual(status["yahoo_rss"]["status"], "ok")
        self.assertNotIn("fromCache", status["yahoo_rss"])

    def test_first_run_stays_idle_free_when_nothing_was_cached(self) -> None:
        engine = CatalystEngine(sources=("yahoo_rss",), http_get=Http(), cache_ttl_seconds=600)
        engine.load_watchlist_news(["AAPL"])
        self.assertEqual(engine.source_status()[0]["status"], "ok")
        self.assertNotIn("fromCache", engine.source_status()[0])
        self.assertEqual(json.loads(json.dumps(engine.source_status()))[0]["name"], "yahoo_rss")


if __name__ == "__main__":
    unittest.main()
