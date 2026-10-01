import unittest
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from config import EASTERN_TZ
from ai_ensemble import HeavyTradingModel
from strategy import StrategyEngine


def momentum_row(score: int) -> dict:
    return {
        "symbol": "AMZN",
        "score": 90,
        "setup_name": "EMA + VWAP + ORB",
        "trigger_source": "opening_range_high",
        "last_price": 248.0,
        "rvol": 1.0,
        "volume_trend": True,
        "ema_stack": True,
        "above_vwap": True,
        "cloud_alignment_pass": True,
        "four_hour_cloud_bullish": True,
        "session_change_pass": True,
        "session_change_pct": 1.4,
        "tos_rvol_5m_early_alert": True,
        "tos_rvol_any_pass": False,
        "fast_momentum_score": score,
        "intraday_change_pct": 1.2,
        "close_near_high": True,
        "breakout_close_confirmed": True,
        "orb_breakout": True,
        "average_volume": 2_000_000,
        "entry": 248.0,
        "stop_loss": 243.04,
        "target": 252.96,
        "risk_per_share": 4.96,
    }


class StrategyFastEntryTests(unittest.TestCase):
    def test_two_of_three_fast_momentum_does_not_wait_for_15m_rvol(self):
        engine = StrategyEngine()
        candidates = engine.build_trade_candidates(
            pd.DataFrame([momentum_row(2)]),
            now=datetime(2026, 7, 14, 9, 32, tzinfo=ZoneInfo(EASTERN_TZ)),
        )

        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].allowed)
        self.assertEqual(candidates[0].rejection_reason, "")

    def test_one_of_three_fast_momentum_still_waits_for_confirmation(self):
        engine = StrategyEngine()
        candidates = engine.build_trade_candidates(
            pd.DataFrame([momentum_row(1)]),
            now=datetime(2026, 7, 14, 9, 32, tzinfo=ZoneInfo(EASTERN_TZ)),
        )

        self.assertEqual(len(candidates), 1)
        self.assertFalse(candidates[0].allowed)
        self.assertIn("15m or higher confirmation required", candidates[0].rejection_reason)

    def test_catalyst_changes_explanation_but_not_strategy_score(self):
        engine = StrategyEngine()
        now = datetime(2026, 7, 14, 9, 32, tzinfo=ZoneInfo(EASTERN_TZ))
        strong = engine.build_trade_candidates(
            pd.DataFrame([momentum_row(3)]),
            catalysts=pd.DataFrame([{"symbol": "AMZN", "score": 3, "sentiment": "strong"}]),
            now=now,
        )[0]
        negative = engine.build_trade_candidates(
            pd.DataFrame([momentum_row(3)]),
            catalysts=pd.DataFrame([{"symbol": "AMZN", "score": 1, "sentiment": "negative"}]),
            now=now,
        )[0]

        self.assertNotEqual(strong.catalyst_score, negative.catalyst_score)
        self.assertEqual(strong.final_score, negative.final_score)
        self.assertEqual(strong.allowed, negative.allowed)
        self.assertIn("information only", strong.rationale)

    def test_catalyst_does_not_change_heavy_model_score_or_decision(self):
        model = HeavyTradingModel.__new__(HeavyTradingModel)
        model.predictive_model = SimpleNamespace(predict_row=lambda row: None)
        model.reasoner = SimpleNamespace(explain=lambda row: "Technical explanation")
        row = {
            **momentum_row(3),
            "market_trend": True,
            "not_overextended": True,
            "first_5m_bullish": True,
            "first_5m_close_above_vwap": True,
        }

        strong = model._score_row(row, {}, {"score": 3, "sentiment": "strong", "recency_bonus": 10})
        negative = model._score_row(row, {}, {"score": 1, "sentiment": "negative", "recency_bonus": 0})

        self.assertNotEqual(strong.catalyst_score, negative.catalyst_score)
        self.assertEqual(strong.ai_trade_score, negative.ai_trade_score)
        self.assertEqual(strong.ai_confidence, negative.ai_confidence)
        self.assertEqual(strong.ai_decision, negative.ai_decision)
        self.assertIn("info only", strong.ai_reasoning)

    def test_catalyst_store_failure_does_not_abort_heavy_model_scoring(self):
        model = HeavyTradingModel.__new__(HeavyTradingModel)
        model.repository = SimpleNamespace(
            get_symbol_memory_snapshot=lambda symbols: {},
            get_catalyst_snapshot=lambda symbols: (_ for _ in ()).throw(RuntimeError("news unavailable")),
        )
        model.predictive_model = SimpleNamespace(predict_row=lambda row: None)
        model.reasoner = SimpleNamespace(explain=lambda row: "Technical explanation")

        scored = model.score_frame(pd.DataFrame([{**momentum_row(3), "market_trend": True}]))

        self.assertEqual(len(scored), 1)
        self.assertEqual(float(scored.iloc[0]["catalyst_score"]), 45.0)


if __name__ == "__main__":
    unittest.main()
