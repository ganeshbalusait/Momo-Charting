import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

os.environ.setdefault("AUTO_START_BOT", "false")
os.environ.setdefault("ALPACA_STREAMING_ENABLED", "false")
os.environ.setdefault("ALPACA_TRADE_UPDATES_ENABLED", "false")

from config import EASTERN_TZ
from database.repository import TradingRepository


class RepositoryRollupTests(unittest.TestCase):
    def test_trade_journal_retention_prunes_completed_stock_and_option_rows(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            old_at = (datetime.now(tz=ZoneInfo("UTC")) - timedelta(days=184)).isoformat()

            with repository._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO trades (
                        client_order_id, symbol, side, quantity, entry_price, status,
                        opened_at, closed_at, account_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("old-stock", "OLD", "buy", 1, 10, "closed", old_at, old_at, "paper"),
                )
                connection.execute(
                    """
                    INSERT INTO option_trades (
                        client_order_id, underlying_symbol, structure, side, quantity,
                        status, opened_at, closed_at, account_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("old-option", "OLD", "Long Call", "buy", 1, "closed", old_at, old_at, "paper"),
                )

            repository.prune_trade_journals(retention_days=183)

            self.assertTrue(repository.get_trade_history(limit=100).empty)
            self.assertTrue(repository.get_option_trade_history(limit=100).empty)

    def test_trade_journal_retention_preserves_old_open_positions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            old_at = (datetime.now(tz=ZoneInfo("UTC")) - timedelta(days=184)).isoformat()

            with repository._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO trades (
                        client_order_id, symbol, side, quantity, entry_price, status,
                        opened_at, account_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("open-stock", "OPEN", "buy", 1, 10, "position_open", old_at, "paper"),
                )
                connection.execute(
                    """
                    INSERT INTO option_trades (
                        client_order_id, underlying_symbol, structure, side, quantity,
                        status, opened_at, account_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("open-option", "OPEN", "Long Call", "buy", 1, "position_open", old_at, "paper"),
                )

            repository.prune_trade_journals(retention_days=183)

            self.assertEqual(len(repository.get_trade_history(limit=100)), 1)
            self.assertEqual(len(repository.get_option_trade_history(limit=100)), 1)

    def test_scanner_history_keeps_quarter_hour_symbol_occurrences(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            eastern = ZoneInfo(EASTERN_TZ)
            frame = pd.DataFrame([{"symbol": "NVDA", "last_price": 200.0}])

            repository.log_scanner_history(
                frame,
                scanned_at=datetime(2026, 7, 10, 9, 33, tzinfo=eastern),
                source="MAG7 OI Scanner",
            )
            repository.log_scanner_history(
                frame.assign(last_price=201.0),
                scanned_at=datetime(2026, 7, 10, 9, 37, tzinfo=eastern),
                source="MAG7 OI Scanner",
            )
            repository.log_scanner_history(
                frame.assign(last_price=202.0),
                scanned_at=datetime(2026, 7, 10, 9, 45, tzinfo=eastern),
                source="MAG7 OI Scanner",
            )

            history_rows, history_days = repository.get_scanner_history()
            nvda_rows = history_rows[history_rows["symbol"] == "NVDA"]
            summary = history_days[history_days["source"] == "MAG7 OI Scanner"].iloc[0]

            self.assertEqual(len(nvda_rows), 2)
            self.assertEqual(
                set(pd.to_datetime(nvda_rows["scanned_at"]).dt.strftime("%H:%M")),
                {"09:33", "09:45"},
            )
            self.assertEqual(int(summary["ticker_count"]), 1)
            self.assertEqual(int(summary["entry_count"]), 2)

    def test_option_trade_rollups_use_scored_trades_for_win_rate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            rows = [
                ("opt-win", "closed", 100.0),
                ("opt-loss", "closed", -50.0),
                ("opt-pending-1", "orderstatus.pending_new", 0.0),
                ("opt-pending-2", "position_open", 0.0),
            ]
            for client_order_id, status, pnl in rows:
                repository.log_option_trade(
                    client_order_id=client_order_id,
                    broker_order_id=f"broker-{client_order_id}",
                    underlying_symbol="TEST",
                    option_symbol="TEST260710C00100000",
                    account_profile_id="paper3",
                    account_label="OPTION TRADE",
                    structure="Only Long Call",
                    side="buy",
                    quantity=1,
                    status=status,
                    entry_price=1.0,
                )
                repository.update_option_trade(client_order_id=client_order_id, pnl=pnl)

            daily = repository.option_trade_rollups(profile_id="paper3", broker_only=True)["daily"]

            self.assertEqual(int(daily.iloc[0]["trades"]), 2)
            self.assertEqual(int(daily.iloc[0]["scored_trades"]), 2)
            self.assertEqual(float(daily.iloc[0]["win_rate"]), 50.0)
            self.assertEqual(float(daily.iloc[0]["total_pnl"]), 50.0)

    def test_scanner_history_prunes_rows_older_than_retention_window(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            eastern = ZoneInfo(EASTERN_TZ)
            recent_at = datetime.now(tz=eastern)
            old_at = recent_at - timedelta(days=90)

            old_frame = pd.DataFrame([{"symbol": "OLD", "last_price": 10.0}])
            recent_frame = pd.DataFrame([{"symbol": "NEW", "last_price": 20.0}])

            repository.log_scanner_history(old_frame, scanned_at=old_at, source="Watchlist")
            repository.log_scanner_history(recent_frame, scanned_at=recent_at, source="Watchlist")

            history_rows, history_days = repository.get_scanner_history()

            symbols = set(history_rows["symbol"].tolist()) if not history_rows.empty else set()
            self.assertIn("NEW", symbols)
            self.assertNotIn("OLD", symbols)
            self.assertTrue((history_days["scan_date"] >= (recent_at - timedelta(days=60)).date().isoformat()).all())

    def test_learning_observations_dedupe_within_five_minute_source_bucket(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            eastern = ZoneInfo(EASTERN_TZ)
            first = pd.DataFrame(
                [
                    {
                        "symbol": "NVDA",
                        "last_price": 200.0,
                        "setup_name": "EMA + VWAP",
                        "fast_momentum_score": 2,
                        "tos_rvol_15m": 1.8,
                    }
                ]
            )
            updated = first.assign(last_price=201.0, fast_momentum_score=3)

            inserted_first = repository.log_learning_observations(
                first,
                source="stock_scanner:Watchlist",
                product="stock",
                observed_at=datetime(2026, 7, 10, 9, 31, tzinfo=eastern),
            )
            inserted_update = repository.log_learning_observations(
                updated,
                source="stock_scanner:Watchlist",
                product="stock",
                observed_at=datetime(2026, 7, 10, 9, 34, tzinfo=eastern),
            )

            with repository._connect() as connection:
                observation_count = connection.execute(
                    "SELECT COUNT(*) FROM learning_observations"
                ).fetchone()[0]
                outcome_count = connection.execute(
                    "SELECT COUNT(*) FROM learning_horizon_outcomes"
                ).fetchone()[0]
                stored = connection.execute(
                    "SELECT entry_price, fast_momentum_score, features_json FROM learning_observations"
                ).fetchone()

            self.assertEqual(inserted_first, 1)
            self.assertEqual(inserted_update, 0)
            self.assertEqual(observation_count, 1)
            self.assertEqual(outcome_count, 4)
            self.assertEqual(float(stored["entry_price"]), 200.0)
            self.assertEqual(int(stored["fast_momentum_score"]), 3)
            self.assertIn('"last_price": 201.0', stored["features_json"])

    def test_catalyst_snapshot_excludes_news_first_seen_after_as_of(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            as_of = datetime(2026, 7, 15, 9, 30, tzinfo=ZoneInfo("UTC"))
            with repository._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO catalyst_items (
                        created_at, symbol, headline, source, published_at, score, sentiment, tags
                    ) VALUES (?, 'PLTR', 'Known before observation', 'test', ?, 3, 'Strong', 'contract')
                    """,
                    (
                        (as_of - timedelta(minutes=5)).isoformat(),
                        (as_of - timedelta(minutes=20)).isoformat(),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO catalyst_items (
                        created_at, symbol, headline, source, published_at, score, sentiment, tags
                    ) VALUES (?, 'PLTR', 'Backfilled later', 'test', ?, 1, 'Negative', 'legal')
                    """,
                    (
                        (as_of + timedelta(minutes=5)).isoformat(),
                        (as_of - timedelta(minutes=30)).isoformat(),
                    ),
                )

            snapshot = repository.get_catalyst_snapshot(["PLTR"], as_of=as_of)

            self.assertEqual(snapshot["PLTR"]["headline"], "Known before observation")
            self.assertLessEqual(
                datetime.fromisoformat(snapshot["PLTR"]["first_seen_at"]),
                as_of,
            )

    def test_catalyst_shadow_fields_remain_frozen_within_observation_bucket(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            eastern = ZoneInfo(EASTERN_TZ)
            first = pd.DataFrame(
                [{"symbol": "PLTR", "last_price": 100.0, "catalyst_shadow_coverage": "none", "catalyst_shadow_score": 0}]
            )
            updated = pd.DataFrame(
                [{"symbol": "PLTR", "last_price": 101.0, "catalyst_shadow_coverage": "present", "catalyst_shadow_score": 3}]
            )
            repository.log_learning_observations(
                first,
                source="stock_scanner:Watchlist",
                product="stock",
                observed_at=datetime(2026, 7, 15, 9, 31, tzinfo=eastern),
            )
            repository.log_learning_observations(
                updated,
                source="stock_scanner:Watchlist",
                product="stock",
                observed_at=datetime(2026, 7, 15, 9, 34, tzinfo=eastern),
            )

            with repository._connect() as connection:
                raw = connection.execute(
                    "SELECT features_json FROM learning_observations"
                ).fetchone()[0]
            features = json.loads(raw)

            self.assertEqual(features["last_price"], 101.0)
            self.assertEqual(features["catalyst_shadow_coverage"], "none")
            self.assertEqual(features["catalyst_shadow_score"], 0)

    def test_learning_observations_keep_new_five_minute_occurrences(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            eastern = ZoneInfo(EASTERN_TZ)
            frame = pd.DataFrame([{"symbol": "AAPL", "last_price": 210.0}])

            repository.log_learning_observations(
                frame,
                source="oi_scanner:mag7",
                product="option",
                observed_at=datetime(2026, 7, 10, 9, 34, tzinfo=eastern),
            )
            repository.log_learning_observations(
                frame.assign(last_price=211.0),
                source="oi_scanner:mag7",
                product="option",
                observed_at=datetime(2026, 7, 10, 9, 35, tzinfo=eastern),
            )

            status = repository.learning_status()

            self.assertEqual(status["observations"], 2)
            self.assertEqual(status["scheduledOutcomes"], 8)
            self.assertFalse(status["canBlockTrades"])
            self.assertEqual(status["retention"]["policy"], "indefinite")
            self.assertEqual(status["sources"][0]["source"], "oi_scanner:mag7")

    def test_learning_status_explains_each_ticker_with_forward_outcomes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            observed_at = datetime(2026, 7, 13, 9, 35, tzinfo=ZoneInfo(EASTERN_TZ))
            repository.log_learning_observations(
                pd.DataFrame([
                    {
                        "symbol": "PYPL",
                        "last_price": 70.0,
                        "setup_name": "EMA + VWAP + ORB",
                        "session_change_pct": 4.25,
                        "tos_rvol_15m": 2.4,
                        "tos_rvol_30m": 1.8,
                        "fast_momentum_score": 3,
                        "learning_cohort": "watchlist",
                    }
                ]),
                source="stock_scanner:Watchlist",
                product="stock",
                observed_at=observed_at,
            )
            with repository._connect() as connection:
                outcomes = connection.execute(
                    "SELECT id, horizon_minutes FROM learning_horizon_outcomes ORDER BY horizon_minutes"
                ).fetchall()
            for outcome in outcomes:
                repository.resolve_learning_outcome(
                    int(outcome["id"]),
                    70.0 * (1 + (int(outcome["horizon_minutes"]) / 1000)),
                    resolved_at=observed_at + timedelta(minutes=int(outcome["horizon_minutes"])),
                )

            status = repository.learning_status()
            ticker = next(row for row in status["tickerAnalysis"] if row["symbol"] == "PYPL")

            self.assertEqual(ticker["cohort"], "watchlist")
            self.assertEqual(ticker["product"], "stock")
            self.assertEqual(ticker["observations"], 1)
            self.assertEqual(ticker["horizons"]["60"]["resolved"], 1)
            self.assertAlmostEqual(ticker["latest_outcomes"]["60"]["return_pct"], 6.0)
            self.assertEqual(ticker["move_label"], "Explosive")
            self.assertIn("EMA + VWAP + ORB", ticker["why_tags"])
            self.assertTrue(any(tag.startswith("RVOL") for tag in ticker["why_tags"]))

    def test_learning_trade_outcomes_sync_closed_stock_and_option_trades(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            opened_at = datetime(2026, 7, 10, 9, 30, tzinfo=ZoneInfo("UTC")).isoformat()
            closed_at = datetime(2026, 7, 10, 10, 0, tzinfo=ZoneInfo("UTC")).isoformat()
            with repository._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO trades (
                        client_order_id, symbol, side, quantity, entry_price, status,
                        opened_at, closed_at, pnl, notes, analysis_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("learn-stock", "NVDA", "buy", 1, 200, "closed", opened_at, closed_at, 10, "target", "{}"),
                )
                connection.execute(
                    """
                    INSERT INTO option_trades (
                        client_order_id, underlying_symbol, option_symbol, structure,
                        side, quantity, entry_price, status, opened_at, closed_at,
                        pnl, notes, analysis_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "learn-option",
                        "AAPL",
                        "AAPL260710C00210000",
                        "Only Long Call",
                        "buy_to_open",
                        1,
                        2.0,
                        "closed",
                        opened_at,
                        closed_at,
                        -25,
                        "stop",
                        "{}",
                    ),
                )

            inserted = repository.sync_learning_trade_outcomes()
            second_pass = repository.sync_learning_trade_outcomes()
            status = repository.learning_status()
            training = repository.learning_training_frame(horizon_minutes=60)

            self.assertEqual(inserted, 2)
            self.assertEqual(second_pass, 0)
            self.assertEqual(status["tradeOutcomes"], 2)
            self.assertEqual(status["winningTrades"], 1)
            self.assertEqual(status["tradePnl"], -15.0)
            self.assertEqual(status["baselineTargetTrades"], 50)
            self.assertEqual(status["sizingReviewTargetTrades"], 100)
            self.assertEqual(status["requirements"]["scannerOutcomes"]["target"], 100)
            self.assertEqual({row["product"] for row in status["tradeBreakdown"]}, {"stock", "option"})
            self.assertEqual(set(training["product"]), {"stock", "option"})
            self.assertEqual(set(training["label_source"]), {"closed_trade_outcome"})
            self.assertEqual(set(training["label_win"]), {0, 1})

    def test_learning_scope_prunes_and_excludes_non_mag7_symbols(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            observed_at = datetime(2026, 7, 10, 9, 30, tzinfo=ZoneInfo("UTC"))
            repository.log_learning_observations(
                pd.DataFrame([
                    {"symbol": "NVDA", "last_price": 200.0},
                    {"symbol": "CART", "last_price": 40.0},
                ]),
                source="stock_scanner:Watchlist",
                product="stock",
                observed_at=observed_at,
            )
            with repository._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO trades (
                        client_order_id, symbol, side, quantity, entry_price, status,
                        opened_at, closed_at, pnl, account_mode
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?), (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "mag7-trade", "NVDA", "buy", 1, 200, "closed", observed_at.isoformat(), observed_at.isoformat(), 5, "paper",
                        "other-trade", "CART", "buy", 1, 40, "closed", observed_at.isoformat(), observed_at.isoformat(), -2, "paper",
                    ),
                )

            repository.prune_learning_to_symbols(["NVDA"])
            inserted = repository.sync_learning_trade_outcomes(allowed_symbols=["NVDA"])
            status = repository.learning_status()

            self.assertEqual(inserted, 1)
            self.assertEqual(status["observations"], 1)
            self.assertEqual(status["tradeOutcomes"], 1)
            self.assertEqual({row["symbol"] for row in status["recentObservations"]}, {"NVDA"})
            self.assertEqual({row["symbol"] for row in status["recentTradeOutcomes"]}, {"NVDA"})

    def test_learning_cohorts_keep_mag7_and_watchlist_separate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            observed_at = datetime(2026, 7, 10, 9, 30, tzinfo=ZoneInfo("UTC"))
            repository.log_learning_observations(
                pd.DataFrame([
                    {"symbol": "NVDA", "last_price": 200.0, "learning_cohort": "mag7"},
                    {"symbol": "CART", "last_price": 40.0, "learning_cohort": "watchlist"},
                ]),
                source="oi_scanner",
                product="option",
                observed_at=observed_at,
            )
            status = repository.learning_status()
            comparison = {row["cohort"]: row for row in status["cohortComparison"]}

            self.assertEqual(comparison["mag7"]["observations"], 1)
            self.assertEqual(comparison["watchlist"]["observations"], 1)
            self.assertEqual(
                {row["symbol"]: row["cohort"] for row in status["recentObservations"]},
                {"NVDA": "mag7", "CART": "watchlist"},
            )
            books = {row["label"]: row for row in status["bookComparison"]}
            self.assertEqual(books["Mag7 Stock"]["advanced_target"], 100)
            self.assertEqual(books["Mag7 Option"]["advanced_target"], 100)
            self.assertEqual(books["Watchlist Stock"]["advanced_target"], 100)
            self.assertEqual(books["Watchlist 400 Option"]["initial_target"], 50)
            self.assertEqual(books["Watchlist 400 Option"]["advanced_target"], 150)

    def test_learning_rejects_explicit_cross_account_trade_leakage(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
            timestamp = datetime(2026, 7, 10, 10, 0, tzinfo=ZoneInfo("UTC")).isoformat()
            with repository._connect() as connection:
                for client_id, symbol, profile_id in (
                    ("correct-mag7", "AAPL", "paper3"),
                    ("wrong-mag7", "AAPL", "paper5"),
                    ("correct-watchlist", "CART", "paper5"),
                    ("wrong-watchlist", "CART", "paper3"),
                ):
                    connection.execute(
                        """
                        INSERT INTO option_trades (
                            client_order_id, underlying_symbol, option_symbol, account_profile_id,
                            structure, side, quantity, entry_price, status, opened_at, closed_at,
                            pnl, account_mode
                        ) VALUES (?, ?, ?, ?, 'Only Long Call', 'buy_to_open', 1, 1, 'closed', ?, ?, 5, 'paper')
                        """,
                        (client_id, symbol, f"{symbol}260710C00050000", profile_id, timestamp, timestamp),
                    )

            inserted = repository.sync_learning_trade_outcomes(
                allowed_symbols=["AAPL", "CART"],
                symbol_cohorts={"AAPL": "mag7", "CART": "watchlist"},
            )
            status = repository.learning_status()

            self.assertEqual(inserted, 2)
            self.assertEqual(status["tradeOutcomes"], 2)
            self.assertEqual(
                {(row["symbol"], row["cohort"]) for row in status["recentTradeOutcomes"]},
                {("AAPL", "mag7"), ("CART", "watchlist")},
            )

if __name__ == "__main__":
    unittest.main(verbosity=2)
