from __future__ import annotations

import threading
import unittest
from datetime import datetime, timedelta, timezone

from api_server import DashboardState


class OiFinderTests(unittest.TestCase):
    def test_chain_view_omits_analysis_heavy_finder_sections(self) -> None:
        compact = DashboardState._oi_finder_chain_view({
            "symbol": "AAPL",
            "live": True,
            "underlyingPrice": 200.0,
            "currentAtm": {"expiry": "2026-07-31"},
            "selectedExpiryChainRows": [{"side": "CALL", "strike": 205}],
            "callRows": [{"strike": 205}],
            "putRows": [],
            "dailyLiquidityHeatmap": {"call": [object()]},
            "unusualOtmDashboard": {"contracts": [object()]},
            "persistentActivity": {"contracts": [object()]},
            "errors": [],
        })

        self.assertEqual(compact["symbol"], "AAPL")
        self.assertEqual(compact["currentAtm"]["expiry"], "2026-07-31")
        self.assertEqual(len(compact["selectedExpiryChainRows"]), 1)
        self.assertNotIn("dailyLiquidityHeatmap", compact)
        self.assertNotIn("unusualOtmDashboard", compact)
        self.assertNotIn("persistentActivity", compact)

    def test_builds_compact_popup_chain_without_history_analysis(self) -> None:
        state = DashboardState.__new__(DashboardState)

        class FakeStream:
            def __init__(self) -> None:
                self.watched = []

            def watch(self, symbol, option_symbols=(), replace_options=False) -> None:
                self.watched.append((symbol, list(option_symbols), replace_options))

            @staticmethod
            def status() -> dict:
                return {"connected": False}

        state.live_market_stream = FakeStream()
        expiry = (datetime.now().date() + timedelta(days=2)).isoformat()
        expiry_key = f"{expiry}:2"

        def option(side: str, strike: float, delta: float, symbol: str) -> list[dict]:
            return [{
                "symbol": symbol,
                "putCall": side,
                "strikePrice": strike,
                "delta": delta,
                "gamma": 0.02,
                "totalVolume": 500,
                "openInterest": 1_000,
                "daysToExpiration": 2,
                "expirationDate": expiry,
                "bid": 2.0,
                "ask": 2.2,
                "mark": 2.1,
            }]

        chain = {
            "symbol": "AAPL",
            "underlyingPrice": 200.0,
            "callExpDateMap": {
                expiry_key: {
                    "200": option("CALL", 200, 0.51, "AAPL-C-200"),
                    "205": option("CALL", 205, 0.35, "AAPL-C-205"),
                },
            },
            "putExpDateMap": {
                expiry_key: {
                    "195": option("PUT", 195, -0.35, "AAPL-P-195"),
                    "200": option("PUT", 200, -0.49, "AAPL-P-200"),
                },
            },
        }

        payload = state._build_oi_finder_chain_payload(
            "AAPL",
            chain,
            {},
            "Schwab/TOS option chain",
            "",
            datetime.now().astimezone(),
        )

        self.assertTrue(payload["live"])
        self.assertEqual(payload["currentAtm"]["expiry"], expiry)
        self.assertEqual({row["side"] for row in payload["selectedExpiryChainRows"]}, {"CALL", "PUT"})
        self.assertEqual(payload["errors"], [])
        self.assertNotIn("dailyLiquidityHeatmap", payload)
        self.assertNotIn("unusualOtmDashboard", payload)
        self.assertTrue(state.live_market_stream.watched)

    def test_greek_roi_estimate_matches_delta_gamma_theta_example(self) -> None:
        state = DashboardState.__new__(DashboardState)

        result = state.option_roi_estimate(
            side="CALL",
            spot_price=158.29,
            strike=180,
            entry_price=2.07,
            target_price=165,
            delta=0.1941,
            gamma=0.012,
            theta=-0.18,
            vega=0.08,
            days_to_target=1,
            iv_change_points=0,
            days_to_expiration=5,
        )

        self.assertAlmostEqual(result["estimatedExitPrice"], 3.4626, places=4)
        self.assertAlmostEqual(result["roiPercent"], 67.27, places=2)
        self.assertAlmostEqual(result["profitPerContract"], 139.26, places=2)
        self.assertEqual(result["vegaEffect"], 0.0)

    def test_attaches_backend_chain_mark_roi_to_call_and_put_chain_rows(self) -> None:
        state = DashboardState.__new__(DashboardState)
        rows = [
            {"side": "CALL", "expiry": "2026-07-31", "days_to_expiration": 4, "strike": 170, "delta": 0.35, "gamma": 0.01, "theta": -0.15, "vega": 0.08, "mark": 4.0},
            {"side": "CALL", "expiry": "2026-07-31", "days_to_expiration": 4, "strike": 180, "delta": 0.19, "gamma": 0.012, "theta": -0.18, "vega": 0.08, "mark": 2.07},
            {"side": "PUT", "expiry": "2026-07-31", "days_to_expiration": 4, "strike": 140, "delta": 0.19, "gamma": 0.012, "theta": -0.18, "vega": 0.08, "mark": 2.10},
            {"side": "PUT", "expiry": "2026-07-31", "days_to_expiration": 4, "strike": 150, "delta": 0.35, "gamma": 0.01, "theta": -0.15, "vega": 0.08, "mark": 4.10},
        ]

        state._attach_option_chain_roi_estimates(rows, 158.29)

        self.assertEqual(rows[1]["roiEstimate"]["kind"], "entry")
        self.assertEqual(rows[0]["roiEstimate"]["kind"], "target")
        self.assertEqual(rows[0]["roiEstimate"]["source"], "chain-mark")
        self.assertAlmostEqual(rows[0]["roiEstimate"]["roiPercent"], 93.24, places=2)
        self.assertEqual(rows[2]["roiEstimate"]["kind"], "entry")
        self.assertEqual(rows[3]["roiEstimate"]["kind"], "target")
        self.assertEqual(rows[3]["roiEstimate"]["source"], "chain-mark")
        self.assertAlmostEqual(rows[3]["roiEstimate"]["roiPercent"], 95.24, places=2)

    def test_chain_mark_roi_matches_340_call_to_335_target_example(self) -> None:
        state = DashboardState.__new__(DashboardState)
        rows = [
            {"side": "CALL", "expiry": "2026-07-31", "days_to_expiration": 3, "strike": 335, "delta": 0.37, "gamma": 0.02, "theta": -0.12, "vega": 0.05, "mark": 2.06},
            {"side": "CALL", "expiry": "2026-07-31", "days_to_expiration": 3, "strike": 337.5, "delta": 0.26, "gamma": 0.02, "theta": -0.10, "vega": 0.04, "mark": 1.30},
            {"side": "CALL", "expiry": "2026-07-31", "days_to_expiration": 3, "strike": 340, "delta": 0.17, "gamma": 0.02, "theta": -0.08, "vega": 0.03, "mark": 0.79},
        ]

        state._attach_option_chain_roi_estimates(rows, 330)

        estimate = rows[0]["roiEstimate"]
        self.assertEqual(estimate["source"], "chain-mark")
        self.assertEqual(estimate["estimatedExitPrice"], 2.06)
        self.assertEqual(estimate["profitPerContract"], 127.0)
        self.assertAlmostEqual(estimate["roiPercent"], 160.76, places=2)

    def test_chain_mark_roi_always_uses_original_point_19_delta_entry(self) -> None:
        state = DashboardState.__new__(DashboardState)
        rows = [
            {"side": "CALL", "expiry": "2026-07-31", "strike": 160, "delta": 0.51, "mark": 4.50},
            {"side": "CALL", "expiry": "2026-07-31", "strike": 162.5, "delta": 0.42, "mark": 3.35},
            {"side": "CALL", "expiry": "2026-07-31", "strike": 185, "delta": 0.18, "mark": 2.20},
        ]

        state._attach_option_chain_roi_estimates(rows, 157.5)

        self.assertEqual(rows[2]["roiEstimate"]["kind"], "entry")
        self.assertAlmostEqual(rows[0]["roiEstimate"]["roiPercent"], 104.55, places=2)
        self.assertAlmostEqual(rows[1]["roiEstimate"]["roiPercent"], 52.27, places=2)

    def test_chain_exposes_chain_and_gamma_roi_side_by_side(self) -> None:
        state = DashboardState.__new__(DashboardState)
        rows = [
            {"side": "CALL", "expiry": "2026-07-31", "strike": 335, "delta": 0.41, "gamma": 0.032, "mark": 2.26},
            {"side": "CALL", "expiry": "2026-07-31", "strike": 337.5, "delta": 0.29, "gamma": 0.032, "mark": 1.41},
            {"side": "CALL", "expiry": "2026-07-31", "strike": 340, "delta": 0.20 - 1e-6, "gamma": 0.032, "mark": 0.85},
        ]

        state._attach_option_chain_roi_estimates(rows, 332.5)

        self.assertAlmostEqual(rows[0]["roiEstimate"]["roiPercent"], 165.88, places=2)
        self.assertAlmostEqual(rows[0]["gammaRoiEstimate"]["estimatedExitPrice"], 1.45, places=2)
        self.assertAlmostEqual(rows[0]["gammaRoiEstimate"]["roiPercent"], 70.59, places=2)
        self.assertAlmostEqual(rows[1]["roiEstimate"]["roiPercent"], 65.88, places=2)
        self.assertAlmostEqual(rows[1]["gammaRoiEstimate"]["estimatedExitPrice"], 2.25, places=2)
        self.assertAlmostEqual(rows[1]["gammaRoiEstimate"]["roiPercent"], 164.71, places=2)

    def test_live_heatmap_changes_compare_the_same_exact_contract(self) -> None:
        state = DashboardState.__new__(DashboardState)
        state.oi_finder_lock = threading.RLock()
        state.oi_finder_volume_history = {}
        state._oi_finder_intraday_volume_timeline = lambda _symbol, _snapshot: {}
        first_time = datetime(2026, 7, 24, 14, 30, tzinfo=timezone.utc)
        contract_key = state._oi_finder_volume_key("CALL", "2026-07-31", 205)

        first = {
            "recordedAt": first_time,
            "contracts": {
                contract_key: {
                    "side": "CALL",
                    "expiry": "2026-07-31",
                    "strike": 205,
                    "volume": 1_000,
                    "openInterest": 8_000,
                },
            },
            "aggregates": {},
        }
        second = {
            "recordedAt": first_time + timedelta(seconds=15),
            "contracts": {
                contract_key: {
                    "side": "CALL",
                    "expiry": "2026-07-31",
                    "strike": 205,
                    "volume": 6_000,
                    "openInterest": 7_750,
                },
            },
            "aggregates": {},
        }

        first_momentum = state._oi_finder_volume_momentum("AAPL", first)
        second_momentum = state._oi_finder_volume_momentum("AAPL", second)
        heatmap = {
            "call": [{"expiry": "2026-07-31", "strike": 205}],
            "put": [{"expiry": "2026-07-31", "strike": 205}],
        }
        state._attach_oi_finder_heatmap_live_changes(heatmap, second_momentum)

        self.assertNotIn("liveVolumeChange", first_momentum["contracts"][contract_key])
        self.assertEqual(heatmap["call"][0]["liveVolumeChange"], 5_000)
        self.assertEqual(heatmap["call"][0]["liveOpenInterestChange"], -250)
        self.assertEqual(heatmap["call"][0]["liveComparisonElapsedSeconds"], 15)
        self.assertNotIn("liveVolumeChange", heatmap["put"][0])

        reset_snapshot = {
            "recordedAt": first_time + timedelta(seconds=30),
            "contracts": {
                contract_key: {
                    "side": "CALL",
                    "expiry": "2026-07-31",
                    "strike": 205,
                    "volume": 500,
                    "openInterest": 7_750,
                },
            },
            "aggregates": {},
        }
        reset_momentum = state._oi_finder_volume_momentum("AAPL", reset_snapshot)
        reset_heatmap = {"call": [{"expiry": "2026-07-31", "strike": 205}], "put": []}
        state._attach_oi_finder_heatmap_live_changes(reset_heatmap, reset_momentum)

        self.assertIsNone(reset_heatmap["call"][0]["liveVolumeChange"])
        self.assertTrue(reset_heatmap["call"][0]["liveVolumeReset"])

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


if __name__ == "__main__":
    unittest.main()
