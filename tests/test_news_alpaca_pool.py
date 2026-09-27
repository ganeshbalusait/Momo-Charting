"""Alpaca news with several configured key pairs: a dead pair (401/403) is that
pair's verdict, not the source's. The engine tries the pool in order, keeps the
first pair that answers, and only reports "blocked" when every pair fails.
Live lesson 2026-09-26: the active profile's key answered 401 while another
profile's key was fine."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from catalyst_engine import CatalystEngine, HttpResponse

NOW = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)
ARTICLE = {
    "news": [
        {
            "id": 1,
            "headline": "Apple Raises Price Target At Morgan Stanley",
            "created_at": (NOW - timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            "url": "https://www.benzinga.com/news/apple-pt",
            "symbols": ["AAPL"],
            "source": "benzinga",
        }
    ]
}


class PoolHttp:
    def __init__(self, live_keys: set[str]) -> None:
        self.live_keys = live_keys
        self.keys_tried: list[str] = []

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        assert urlsplit(url).netloc == "data.alpaca.markets", url
        key = headers.get("APCA-API-KEY-ID", "")
        self.keys_tried.append(key)
        if key in self.live_keys:
            return HttpResponse(status=200, body=json.dumps(ARTICLE).encode())
        return HttpResponse(status=401, body=b'{"message":"unauthorized"}')


class AlpacaPoolTests(unittest.TestCase):
    def test_dead_first_pair_falls_through_to_the_live_one_and_is_remembered(self) -> None:
        http = PoolHttp({"k-live"})
        engine = CatalystEngine(
            sources=("alpaca",),
            http_get=http,
            cache_ttl_seconds=0,
            alpaca_credentials=("k-dead", "s-dead"),
            alpaca_credential_pool=[("k-dead", "s-dead"), ("k-also-dead", "s2"), ("k-live", "s-live")],
        )
        self.assertEqual(engine.alpaca_credential_pool, [("k-dead", "s-dead"), ("k-also-dead", "s2"), ("k-live", "s-live")])
        items = engine.load_symbol_news("AAPL")
        self.assertEqual([item.headline for item in items], ["Apple Raises Price Target At Morgan Stanley"])
        self.assertEqual(http.keys_tried, ["k-dead", "k-also-dead", "k-live"])
        self.assertEqual(engine.source_status()[0]["status"], "ok")
        # The pair that answered now leads, so the next symbol costs one request.
        self.assertEqual(engine.alpaca_credential_pool[0], ("k-live", "s-live"))
        self.assertEqual(engine.alpaca_credentials, ("k-live", "s-live"))
        engine.load_symbol_news("MSFT")
        self.assertEqual(http.keys_tried[-1], "k-live")
        self.assertEqual(len(http.keys_tried), 4)

    def test_every_pair_dead_is_reported_as_blocked_with_the_http_reason(self) -> None:
        http = PoolHttp(set())
        engine = CatalystEngine(
            sources=("alpaca",),
            http_get=http,
            cache_ttl_seconds=0,
            alpaca_credentials=("k1", "s1"),
            alpaca_credential_pool=[("k2", "s2")],
        )
        self.assertEqual(engine.load_symbol_news("AAPL"), [])
        status = engine.source_status()[0]
        self.assertEqual(status["status"], "blocked")
        self.assertIn("HTTP 401", status["error"])
        self.assertIn("2", status["error"])

    def test_single_pair_keeps_the_old_behaviour_and_empty_pool_is_not_configured(self) -> None:
        http = PoolHttp({"key"})
        engine = CatalystEngine(sources=("alpaca",), http_get=http, cache_ttl_seconds=0, alpaca_credentials=("key", "secret"))
        self.assertEqual(engine.alpaca_credential_pool, [("key", "secret")])
        self.assertEqual(len(engine.load_symbol_news("AAPL")), 1)
        empty = CatalystEngine(sources=("alpaca",), http_get=http, cache_ttl_seconds=0, alpaca_credentials=("", ""), alpaca_credential_pool=[("", "x")])
        self.assertEqual(empty.alpaca_credential_pool, [])
        self.assertEqual(empty.load_symbol_news("AAPL"), [])
        self.assertIn("not configured", empty.source_status()[0]["error"])


if __name__ == "__main__":
    unittest.main()
