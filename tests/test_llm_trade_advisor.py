import unittest

import pandas as pd

from llm_trade_advisor import LLMTradeAdvisor
from strategy import StrategyEngine


class LLMTradeAdvisorTests(unittest.TestCase):
    def test_advisor_enriches_without_changing_trade_decision_fields(self):
        frame = pd.DataFrame(
            [
                {
                    "symbol": "ASTS",
                    "allowed": True,
                    "rejection_reason": "",
                    "entry": 82.5,
                    "stop_loss": 80.85,
                    "target": 84.15,
                    "score": 90,
                    "final_score": 82,
                    "setup_name": "EMA + VWAP + ORB",
                    "strategy_family": "Momentum + Price Action Trend",
                    "ema_stack": True,
                    "above_vwap": True,
                    "four_hour_cloud_bullish": True,
                    "cloud_alignment_pass": True,
                    "cloud_alignment_action": "TRADE - 4H + 5M BULLISH",
                    "volume_trend": True,
                    "ema9_retest_5m": True,
                    "close_near_high": True,
                    "breakout_close_confirmed": True,
                    "market_trend": True,
                    "stock_all_conditions_pass": True,
                }
            ]
        )

        enriched = LLMTradeAdvisor().enrich_frame(frame)
        row = enriched.iloc[0].to_dict()

        self.assertTrue(row["allowed"])
        self.assertEqual(row["rejection_reason"], "")
        self.assertEqual(row["entry"], 82.5)
        self.assertEqual(row["stop_loss"], 80.85)
        self.assertEqual(row["target"], 84.15)
        self.assertTrue(row["llm_agent_non_blocking"])
        self.assertIn(row["llm_advice"], {"Strong context", "Qualified context", "Mixed context"})
        self.assertIn("informational only", row["llm_summary"])

    def test_strategy_handles_missing_model_probability_fields(self):
        frame = pd.DataFrame(
            [
                {
                    "symbol": "ASTS",
                    "score": 100,
                    "final_score": 82,
                    "rule_score": 100,
                    "ml_score": 80,
                    "catalyst_score": 45,
                    "regime_score": 100,
                    "liquidity_score": 80,
                    "ai_confidence": 82,
                    "setup_name": "EMA + VWAP + ORB",
                    "strategy_family": "Momentum + Price Action Trend",
                    "trigger_source": "premarket_high",
                    "last_price": 82.5,
                    "rvol": 3.2,
                    "entry": 82.5,
                    "stop_loss": 80.85,
                    "target": 84.15,
                    "risk_per_share": 1.65,
                    "ema_stack": True,
                    "above_vwap": True,
                    "four_hour_cloud_bullish": True,
                    "cloud_alignment_pass": True,
                    "cloud_alignment_action": "TRADE - 4H + 5M BULLISH",
                    "volume_trend": True,
                    "ema9_retest_5m": True,
                    "close_near_high": True,
                    "breakout_close_confirmed": True,
                    "market_trend": True,
                    "intraday_change_pct": 3.5,
                    "session_change_pct": 1.25,
                    "session_change_pass": True,
                }
            ]
        )

        candidates = StrategyEngine().build_trade_candidates(frame)

        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].allowed)

        frame.loc[0, "session_change_pct"] = 0.99
        frame.loc[0, "session_change_pass"] = False
        blocked = StrategyEngine().build_trade_candidates(frame)
        self.assertFalse(blocked[0].allowed)
        self.assertEqual(blocked[0].rejection_reason, "Live Change % below 1.00%")

    def test_strategy_scores_are_advisory_and_do_not_block_stock_entry(self):
        frame = pd.DataFrame(
            [
                {
                    "symbol": "ASTS",
                    "score": 1,
                    "final_score": 1,
                    "rule_score": 1,
                    "ml_score": 1,
                    "catalyst_score": 1,
                    "regime_score": 1,
                    "liquidity_score": 1,
                    "ai_confidence": 1,
                    "setup_name": "EMA + VWAP + ORB",
                    "trigger_source": "opening_range_high",
                    "last_price": 82.5,
                    "rvol": 1.5,
                    "entry": 82.5,
                    "stop_loss": 80.85,
                    "target": 84.15,
                    "risk_per_share": 1.65,
                    "ema_stack": True,
                    "above_vwap": True,
                    "volume_trend": True,
                    "ema9_retest_5m": False,
                    "close_near_high": True,
                    "breakout_close_confirmed": True,
                    "market_trend": False,
                    "intraday_change_pct": 3.5,
                    "session_change_pct": 1.25,
                    "session_change_pass": True,
                    "tos_rvol_5m_early_alert": False,
                    "tos_rvol_any_pass": True,
                    "four_hour_cloud_bullish": True,
                    "cloud_alignment_pass": True,
                    "cloud_alignment_action": "TRADE - 4H + 5M BULLISH",
                }
            ]
        )

        candidate = StrategyEngine().build_trade_candidates(frame)[0]

        self.assertTrue(candidate.allowed)
        self.assertEqual(candidate.rejection_reason, "")
        self.assertIn("Rule score 1 (advisory)", candidate.rationale)
        self.assertIn("Regime score 1 (advisory)", candidate.rationale)


if __name__ == "__main__":
    unittest.main(verbosity=2)
