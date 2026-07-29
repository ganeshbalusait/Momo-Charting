from __future__ import annotations

import threading
import time
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


if __name__ == "__main__":
    unittest.main()
