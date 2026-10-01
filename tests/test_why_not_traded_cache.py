from __future__ import annotations

import threading
import unittest

import api_server
from api_server import DashboardState


class WhyNotTradedCacheTests(unittest.TestCase):
    """The diagnostics call is on the app's critical load path.

    App.jsx runs it from `useEffect(..., [selectedSymbol])`, so it fires on
    every app load AND every ticker switch, panel open or not. Uncached it runs
    a full scanner diagnosis, enriches 200 trade-history rows and builds a
    strategy frame - measured 6.0s locally, 8.5s under concurrent load and
    16.9s through the Cloudflare tunnel, for a 3KB response. Being pure-Python
    CPU it also holds the GIL, which is why /api/auth/status (400 bytes, no
    work at all) took 2,025ms in the browser while this ran.
    """

    def setUp(self) -> None:
        self.state = DashboardState.__new__(DashboardState)
        self.state.why_not_traded_cache = {}
        self.state.why_not_traded_lock = threading.Lock()
        self.builds: list[str] = []

        def fake_build(symbol: str) -> dict:
            self.builds.append(symbol)
            return {"symbol": symbol, "diagnostics": {"n": len(self.builds)}}

        self.state._build_why_not_traded = fake_build  # type: ignore[method-assign]

    def test_repeat_calls_for_one_symbol_build_once(self) -> None:
        first = self.state.why_not_traded("AAPL")
        second = self.state.why_not_traded("AAPL")
        self.assertEqual(self.builds, ["AAPL"])
        self.assertEqual(first, second)

    def test_symbols_do_not_share_an_entry(self) -> None:
        self.state.why_not_traded("AAPL")
        self.state.why_not_traded("MSFT")
        self.state.why_not_traded("AAPL")
        self.assertEqual(self.builds, ["AAPL", "MSFT"])

    def test_symbol_is_normalised_before_lookup(self) -> None:
        self.state.why_not_traded("aapl")
        self.state.why_not_traded("  AAPL ")
        self.assertEqual(self.builds, ["AAPL"])

    def test_an_expired_entry_is_rebuilt(self) -> None:
        self.state.why_not_traded("AAPL")
        stamp, payload = self.state.why_not_traded_cache["AAPL"]
        self.state.why_not_traded_cache["AAPL"] = (
            stamp - api_server.WHY_NOT_TRADED_CACHE_TTL_SECONDS - 1.0,
            payload,
        )
        self.state.why_not_traded("AAPL")
        self.assertEqual(self.builds, ["AAPL", "AAPL"])

    def test_the_cache_is_bounded(self) -> None:
        for index in range(api_server.WHY_NOT_TRADED_CACHE_LIMIT + 8):
            self.state.why_not_traded("SYM%d" % index)
        self.assertLessEqual(
            len(self.state.why_not_traded_cache),
            api_server.WHY_NOT_TRADED_CACHE_LIMIT,
        )

    def test_a_blank_symbol_still_answers(self) -> None:
        self.assertEqual(self.state.why_not_traded("")["symbol"], "")


if __name__ == "__main__":
    unittest.main()
