from __future__ import annotations

import unittest
import threading
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from api_server import DashboardState


class OiFinderTests(unittest.TestCase):
    def test_chain_rows_preserve_quotes_greeks_and_contract_identity(self) -> None:
        state = DashboardState.__new__(DashboardState)
        expiry = (datetime.now().date() + timedelta(days=2)).isoformat()

        def option(strike: float, delta: float, symbol: str, mark: float) -> list[dict]:
            return [{
                "symbol": symbol,
                "strikePrice": strike,
                "delta": delta,
                "gamma": 0.0123,
                "theta": -0.44,
                "vega": 0.21,
                "totalVolume": 1200,
                "openInterest": 2400,
                "daysToExpiration": 2,
                "expirationDate": expiry,
                "bid": 2.8,
                "ask": 3.0,
                "last": 2.85,
                "mark": mark,
            }]

        chain = {
            "underlyingPrice": 200.0,
            "callExpDateMap": {
                f"{expiry}:2": {
                    "200": option(200, 0.51, "TEST_ATM", 5.1),
                    "205": option(205, 0.40, "TEST_OTM", 2.95),
                }
            },
        }

        side_row = state._oi_finder_side_rows(chain, "CALL")[0]
        selected_row = next(
            row for row in state._oi_finder_selected_expiry_chain_rows(chain)
            if row["strike"] == 205
        )

        for row in (side_row, selected_row):
            self.assertEqual(row["symbol"], "TEST_OTM")
            self.assertEqual(row["bid"], 2.8)
            self.assertEqual(row["ask"], 3.0)
            self.assertEqual(row["last"], 2.85)
            self.assertEqual(row["mark"], 2.95)
            self.assertEqual(row["gamma"], 0.0123)
            self.assertEqual(row["theta"], -0.44)
            self.assertEqual(row["vega"], 0.21)

    def test_builds_representative_delta_rows_with_expected_move_and_tags(self) -> None:
        state = DashboardState.__new__(DashboardState)
        expiry_key = "2026-07-17:2"

        def option(strike: float, delta: float, volume: float, oi: float, bid: float, ask: float) -> list[dict]:
            return [{
                "strikePrice": strike,
                "delta": delta,
                "totalVolume": volume,
                "openInterest": oi,
                "daysToExpiration": 2,
                "expirationDate": "2026-07-17",
                "bid": bid,
                "ask": ask,
            }]

        chain = {
            "underlyingPrice": 200.0,
            "callExpDateMap": {
                expiry_key: {
                    "200": option(200, 0.51, 1000, 1500, 5.0, 5.2),
                    "202.5": option(202.5, 0.46, 900, 1200, 3.8, 4.0),
                    "205": option(205, 0.40, 2000, 1000, 2.8, 3.0),
                    "207.5": option(207.5, 0.34, 700, 2200, 2.0, 2.2),
                    "212.5": option(212.5, 0.21, 300, 1600, 1.0, 1.2),
                }
            },
            "putExpDateMap": {
                expiry_key: {
                    "200": option(200, -0.49, 1100, 1400, 4.8, 5.0),
                    "197.5": option(197.5, -0.44, 800, 1300, 3.6, 3.8),
                    "195": option(195, -0.39, 500, 1700, 2.6, 2.8),
                    "192.5": option(192.5, -0.35, 600, 1800, 1.9, 2.1),
                    "187.5": option(187.5, -0.20, 250, 2100, 0.9, 1.1),
                }
            },
        }

        calls = state._oi_finder_side_rows(chain, "CALL")
        puts = state._oi_finder_side_rows(chain, "PUT")

        self.assertEqual(len(calls), 4)
        self.assertEqual(len(puts), 4)
        self.assertTrue(all(row["delta"] >= 0.20 for row in calls + puts))
        self.assertEqual(calls[0]["expected_move"], 10.0)
        self.assertIn(calls[1]["flow_type"], {"Big real-time volume only", "Big OI only"})
        self.assertIn(" + ", calls[1]["scanner_tag"])
        self.assertIn(calls[1]["strength"], {"STRONG", "MODERATE", "WEAK"})

    def test_keeps_only_the_leading_expiry_for_each_otm_strike(self) -> None:
        state = DashboardState.__new__(DashboardState)

        def option(strike: float, delta: float, volume: float, oi: float, dte: int, expiry: str) -> list[dict]:
            return [{
                "strikePrice": strike,
                "delta": delta,
                "totalVolume": volume,
                "openInterest": oi,
                "daysToExpiration": dte,
                "expirationDate": expiry,
                "bid": 2.0,
                "ask": 2.2,
            }]

        chain = {
            "underlyingPrice": 200.0,
            "callExpDateMap": {
                "2026-07-17:2": {
                    "200": option(200, 0.51, 1000, 1500, 2, "2026-07-17"),
                    "205": option(205, 0.40, 8000, 2000, 2, "2026-07-17"),
                },
                "2026-07-24:9": {
                    "200": option(200, 0.51, 1000, 1500, 9, "2026-07-24"),
                    "205": option(205, 0.40, 700, 300, 9, "2026-07-24"),
                },
            },
        }

        calls = state._oi_finder_side_rows(chain, "CALL")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["strike"], 205.0)
        self.assertEqual(calls[0]["days_to_expiration"], 2)
        self.assertEqual(calls[0]["volume"], 8000)

    def test_current_atm_uses_the_nearest_expiry_for_both_sides(self) -> None:
        state = DashboardState.__new__(DashboardState)

        def option(strike: float, volume: float, oi: float, dte: int, expiry: str) -> list[dict]:
            return [{
                "strikePrice": strike,
                "totalVolume": volume,
                "openInterest": oi,
                "daysToExpiration": dte,
                "expirationDate": expiry,
                "bid": 2.0,
                "ask": 2.2,
            }]

        chain = {
            "underlyingPrice": 200.0,
            "callExpDateMap": {
                "2026-07-17:2": {"200": option(200, 1200, 2500, 2, "2026-07-17")},
                "2026-07-24:9": {"200": option(200, 9900, 9900, 9, "2026-07-24")},
            },
            "putExpDateMap": {
                "2026-07-17:2": {"200": option(200, 800, 3100, 2, "2026-07-17")},
                "2026-07-24:9": {"200": option(200, 8800, 8800, 9, "2026-07-24")},
            },
        }

        current_atm = state._oi_finder_current_atm(chain)

        self.assertEqual(current_atm["expiry"], "2026-07-17")
        self.assertEqual(current_atm["daysToExpiration"], 2)
        self.assertEqual(current_atm["call"], {"strike": 200.0, "volume": 1200, "openInterest": 2500})
        self.assertEqual(current_atm["put"], {"strike": 200.0, "volume": 800, "openInterest": 3100})

    def test_keeps_the_largest_liquidity_wall_when_it_misses_a_delta_target(self) -> None:
        state = DashboardState.__new__(DashboardState)

        def option(strike: float, delta: float, volume: float, oi: float) -> list[dict]:
            return [{
                "strikePrice": strike,
                "delta": delta,
                "totalVolume": volume,
                "openInterest": oi,
                "daysToExpiration": 2,
                "expirationDate": "2026-07-17",
                "bid": 2.0,
                "ask": 2.2,
            }]

        chain = {
            "underlyingPrice": 200.0,
            "callExpDateMap": {
                "2026-07-17:2": {
                    "200": option(200, 0.51, 100, 100),
                    "202.5": option(202.5, 0.50, 100, 100),
                    "205": option(205, 0.40, 100, 100),
                    "207.5": option(207.5, 0.35, 100, 100),
                    "210": option(210, 0.20, 100, 100),
                    "212.5": option(212.5, 0.27, 9000, 12000),
                }
            },
        }

        calls = state._oi_finder_side_rows(chain, "CALL")
        wall = next(row for row in calls if row["strike"] == 212.5)

        self.assertTrue(wall["is_liquidity_wall"])
        self.assertEqual(len(calls), 5)

    def test_uses_the_lower_tos_style_strike_as_the_atm_anchor(self) -> None:
        state = DashboardState.__new__(DashboardState)

        def option(strike: float, delta: float) -> list[dict]:
            return [{
                "strikePrice": strike,
                "delta": delta,
                "totalVolume": 100,
                "openInterest": 100,
                "daysToExpiration": 1,
                "expirationDate": "2026-07-17",
                "bid": 2.0,
                "ask": 2.2,
            }]

        chain = {
            "underlyingPrice": 115.54,
            "callExpDateMap": {
                "2026-07-17:1": {
                    "115": option(115, 0.54),
                    "116": option(116, 0.48),
                    "117": option(117, 0.40),
                    "118": option(118, 0.35),
                    "120": option(120, 0.20),
                }
            },
            "putExpDateMap": {
                "2026-07-17:1": {
                    "115": option(115, -0.46),
                    "114": option(114, -0.40),
                    "113": option(113, -0.35),
                    "112": option(112, -0.30),
                    "110": option(110, -0.20),
                }
            },
        }

        calls = state._oi_finder_side_rows(chain, "CALL")
        puts = state._oi_finder_side_rows(chain, "PUT")
        current_atm = state._oi_finder_current_atm(chain)

        self.assertTrue(all(row["atm_strike"] == 115.0 for row in calls + puts))
        self.assertNotIn(115.0, {row["strike"] for row in puts})
        self.assertEqual(current_atm["call"]["strike"], 115.0)
        self.assertEqual(current_atm["put"]["strike"], 115.0)

    def test_keeps_all_otm_strikes_that_exceed_atm_volume_or_oi(self) -> None:
        state = DashboardState.__new__(DashboardState)

        def option(strike: float, delta: float, volume: float, oi: float) -> list[dict]:
            return [{
                "strikePrice": strike,
                "delta": delta,
                "totalVolume": volume,
                "openInterest": oi,
                "daysToExpiration": 1,
                "expirationDate": "2026-07-17",
                "bid": 2.0,
                "ask": 2.2,
            }]

        chain = {
            "underlyingPrice": 200.0,
            "callExpDateMap": {
                "2026-07-17:1": {
                    "200": option(200, 0.54, 100, 100),
                    "202.5": option(202.5, 0.50, 10, 10),
                    "205": option(205, 0.40, 10, 10),
                    "207.5": option(207.5, 0.35, 10, 10),
                    "210": option(210, 0.20, 10, 10),
                    "212.5": option(212.5, 0.28, 500, 20),
                    "215": option(215, 0.24, 20, 500),
                }
            },
        }

        calls = state._oi_finder_side_rows(chain, "CALL")
        high_volume = next(row for row in calls if row["strike"] == 212.5)
        high_oi = next(row for row in calls if row["strike"] == 215.0)

        self.assertTrue(high_volume["above_atm_volume"])
        self.assertTrue(high_oi["above_atm_open_interest"])
        self.assertIn("HIGH VOL", high_volume["liquidity_labels"])
        self.assertIn("HIGH OI", high_oi["liquidity_labels"])

    def test_four_hour_source_keeps_twenty_year_archive_and_exact_recent_bars(self) -> None:
        daily = pd.DataFrame({
            "timestamp": pd.to_datetime([
                "2006-08-15T16:00:00Z",
                "2025-11-21T21:00:00Z",
                "2025-11-24T21:00:00Z",
            ]),
            "open": [10.0, 20.0, 30.0],
            "high": [11.0, 21.0, 31.0],
            "low": [9.0, 19.0, 29.0],
            "close": [10.5, 20.5, 30.5],
            "volume": [100, 200, 300],
        })
        intraday = pd.DataFrame({
            "timestamp": pd.to_datetime([
                "2025-11-23T01:00:00Z",
                "2025-11-23T01:30:00Z",
                "2025-11-24T14:30:00Z",
            ]),
            "open": [25.0, 25.5, 30.0],
            "high": [26.0, 26.5, 31.0],
            "low": [24.0, 24.5, 29.0],
            "close": [25.5, 26.0, 30.5],
            "volume": [50, 60, 70],
        })

        merged, coverage = DashboardState._four_hour_archive_frame(daily, intraday)

        self.assertEqual(len(merged), 5)
        self.assertEqual(pd.Timestamp(merged.iloc[0]["timestamp"]).year, 2006)
        self.assertEqual(coverage["mode"], "daily-archive+exact-30m")
        self.assertEqual(coverage["requestedYears"], 20)
        self.assertTrue(coverage["exactFrom"].startswith("2025-11-22"))

    def test_chart_and_chain_browser_caches_survive_restart(self) -> None:
        state = DashboardState.__new__(DashboardState)
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            state.oi_finder_chart_disk_cache_dir = root / "charts"
            state.oi_finder_chain_disk_cache_dir = root / "chains"
            chart = {
                "symbol": "NVDA",
                "live": True,
                "bars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
                "studyBars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
                "dailyBars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
                "fourHourCoverage": {"requestedYears": 20},
                "historyLoading": False,
            }
            chain = {
                "symbol": "NVDA",
                "live": True,
                "callRows": [{"strike": 100}],
                "putRows": [{"strike": 95}],
            }

            state._save_oi_finder_chart_disk_payload("NVDA", chart)
            state._save_oi_finder_chain_disk_payload("NVDA", chain)

            loaded_chart = state._load_oi_finder_chart_disk_payload("NVDA")
            loaded_chain = state._load_oi_finder_chain_disk_payload("NVDA")
            self.assertEqual(loaded_chart["studyBars"], chart["studyBars"])
            self.assertTrue(loaded_chart["diskCached"])
            self.assertEqual(loaded_chain["callRows"], chain["callRows"])
            self.assertTrue(loaded_chain["diskCached"])

    def test_initial_option_chain_keeps_only_the_nearest_expiry(self) -> None:
        payload = {
            "currentAtm": {"expiry": "2026-08-10"},
            "expiries": ["2026-08-10", "2026-08-14"],
            "callRows": [
                {"expiry": "2026-08-10", "strike": 100},
                {"expiry": "2026-08-14", "strike": 105},
            ],
            "putRows": [{"expiry": "2026-08-14", "strike": 95}],
            "selectedExpiryChainRows": [
                {"expiry": "2026-08-10", "strike": 100},
                {"expiry": "2026-08-14", "strike": 105},
            ],
            "tosScriptLevels": [
                {"expiry": "2026-08-10", "callLevels": []},
                {"expiry": "2026-08-14", "callLevels": []},
            ],
            "expiryExpectedMoves": {"2026-08-10": 2.0, "2026-08-14": 4.0},
        }

        slim = DashboardState._slim_initial_oi_finder_chain_payload(payload)

        self.assertEqual(slim["expiries"], ["2026-08-10"])
        self.assertEqual(len(slim["callRows"]), 1)
        self.assertEqual(slim["putRows"], [])
        self.assertEqual(len(slim["selectedExpiryChainRows"]), 1)
        self.assertEqual(len(slim["tosScriptLevels"]), 1)
        self.assertEqual(slim["expiryExpectedMoves"], {"2026-08-10": 2.0})
        self.assertTrue(slim["frontExpiryOnly"])

    def test_cached_initial_option_chain_is_also_nearest_expiry_only(self) -> None:
        state = DashboardState.__new__(DashboardState)
        state.oi_finder_lock = threading.RLock()
        state.oi_finder_chain_cache = {
            "NVDA": (datetime.now().astimezone(), {
                "live": True,
                "symbol": "NVDA",
                "currentAtm": {"expiry": "2026-08-10"},
                "expiries": ["2026-08-10", "2026-08-14"],
                "callRows": [
                    {"expiry": "2026-08-10", "strike": 100},
                    {"expiry": "2026-08-14", "strike": 105},
                ],
                "putRows": [{"expiry": "2026-08-14", "strike": 95}],
                "selectedExpiryChainRows": [],
                "tosScriptLevels": [],
                "expiryExpectedMoves": {},
            }),
        }
        state.oi_finder_cache = {}

        payload = state.oi_finder_payload("NVDA", compact=True, initial_paint=True)

        self.assertEqual(payload["expiries"], ["2026-08-10"])
        self.assertEqual(len(payload["callRows"]), 1)
        self.assertEqual(payload["putRows"], [])
        self.assertTrue(payload["frontExpiryOnly"])


if __name__ == "__main__":
    unittest.main()
