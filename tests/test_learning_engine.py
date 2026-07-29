import os
import tempfile
import unittest
from datetime import datetime, timezone

import pandas as pd

os.environ.setdefault("AUTO_START_BOT", "false")
os.environ.setdefault("ALPACA_STREAMING_ENABLED", "false")
os.environ.setdefault("ALPACA_TRADE_UPDATES_ENABLED", "false")

from database.repository import TradingRepository
from learning_engine import TradingLearningAgent


class FakeQuoteClient:
    def __init__(self, prices):
        self.prices = prices
        self.requests = []

    def get_quotes(self, symbols):
        self.requests.append(list(symbols))
        return {
            symbol: {"last_price": self.prices[symbol]}
            for symbol in symbols
            if symbol in self.prices
        }


class LearningAgentTests(unittest.TestCase):
    def test_daily_learning_report_tracks_books_predictions_and_calibration(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "learning.db"))
            observed_at = datetime(2026, 1, 5, 14, 30, tzinfo=timezone.utc)
            repository.log_learning_observations(
                pd.DataFrame(
                    [
                        {
                            "symbol": "AAPL",
                            "last_price": 100.0,
                            "setup_name": "EMA + VWAP + ORB",
                            "model_win_probability": 0.80,
                            "learning_cohort": "mag7",
                        },
                        {
                            "symbol": "MSFT",
                            "last_price": 200.0,
                            "setup_name": "EMA + VWAP",
                            "model_win_probability": 0.70,
                            "learning_cohort": "mag7",
                        },
                    ]
                ),
                source="stock_bot_candidate",
                product="stock",
                observed_at=observed_at,
            )
            with repository._connect() as connection:
                observation_ids = [
                    int(row["id"])
                    for row in connection.execute(
                        "SELECT id FROM learning_observations ORDER BY symbol"
                    ).fetchall()
                ]
                connection.execute(
                    "UPDATE learning_horizon_outcomes SET resolved_at = ?, return_pct = 2.0, label_win = 1 WHERE observation_id = ? AND horizon_minutes = 60",
                    ((observed_at.replace(hour=15, minute=30)).isoformat(), observation_ids[0]),
                )
                connection.execute(
                    "UPDATE learning_horizon_outcomes SET resolved_at = ?, return_pct = -1.0, label_win = 0 WHERE observation_id = ? AND horizon_minutes = 60",
                    ((observed_at.replace(hour=15, minute=30)).isoformat(), observation_ids[1]),
                )
                connection.execute(
                    """
                    INSERT INTO learning_trade_outcomes (
                        outcome_key, product, cohort, trade_row_id, symbol, opened_at,
                        closed_at, pnl, label_win, hold_minutes, exit_reason, analysis_json
                    ) VALUES (?, 'stock', 'mag7', 1, 'AAPL', ?, ?, 125.0, 1, 45.0, 'target', '{}')
                    """,
                    (
                        "stock:1",
                        observed_at.isoformat(),
                        observed_at.replace(hour=15, minute=15).isoformat(),
                    ),
                )

            status = repository.learning_status()
            report = next(row for row in status["dailyReports"] if row["date"] == "2026-01-05")
            mag7_stock = next(row for row in report["books"] if row["label"] == "Mag7 Stock")

            self.assertEqual(report["observations"], 2)
            self.assertEqual(report["resolved_60m"], 2)
            self.assertEqual(report["prediction_samples"], 2)
            self.assertEqual(report["predicted_win_rate"], 75.0)
            self.assertEqual(report["realized_prediction_win_rate"], 50.0)
            self.assertEqual(report["prediction_accuracy"], 50.0)
            self.assertEqual(report["calibration_gap"], 25.0)
            self.assertEqual(report["pnl"], 125.0)
            self.assertEqual(mag7_stock["trades"], 1)
            self.assertEqual(mag7_stock["win_rate"], 100.0)
            self.assertIn("Closed 1 paper trades", report["explanation"])
            self.assertTrue(any("Mag7 Stock" in item for item in report["what_worked"]))
            self.assertTrue(report["what_failed"])
            self.assertTrue(any("calibration gap" in item for item in report["next_session_focus"]))

    def test_cycle_resolves_due_outcomes_without_training_or_execution_authority(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "learning.db"))
            observed_at = datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc)
            repository.log_learning_observations(
                pd.DataFrame(
                    [
                        {
                            "symbol": "AAPL",
                            "last_price": 100.0,
                            "setup_name": "EMA + VWAP",
                            "fast_momentum_score": 2,
                        }
                    ]
                ),
                source="stock_bot_candidate",
                product="stock",
                observed_at=observed_at,
            )
            with repository._connect() as connection:
                connection.execute(
                    "UPDATE learning_horizon_outcomes SET due_at = ?",
                    (datetime(2026, 1, 5, 9, 31, tzinfo=timezone.utc).isoformat(),),
                )

            client = FakeQuoteClient({"AAPL": 102.0})
            agent = TradingLearningAgent(repository, client)
            result = agent.run_cycle()
            status = repository.learning_status()

            self.assertTrue(result["started"])
            self.assertEqual(result["resolvedThisCycle"], 4)
            self.assertEqual(status["resolvedOutcomes"], 4)
            self.assertEqual(status["winningOutcomes"], 4)
            self.assertFalse(status["canBlockTrades"])
            self.assertEqual(status["models"], [])
            self.assertEqual(agent.status, "Monitoring")
            self.assertEqual(client.requests, [["AAPL"]])

    def test_shadow_training_requires_fifty_closed_trades(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "learning.db"))
            agent = TradingLearningAgent(repository, FakeQuoteClient({}))
            payload = {
                "tradeOutcomes": 49,
                "resolved60mOutcomes": 1000,
                "models": [],
            }

            self.assertIsNone(agent._maybe_train_shadow(payload, force_training=True))

    def test_shadow_training_requires_one_hundred_scanner_outcomes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "learning.db"))
            agent = TradingLearningAgent(repository, FakeQuoteClient({}))
            payload = {
                "tradeOutcomes": 50,
                "resolved60mOutcomes": 99,
                "models": [],
            }

            self.assertIsNone(agent._maybe_train_shadow(payload, force_training=True))


if __name__ == "__main__":
    unittest.main(verbosity=2)
