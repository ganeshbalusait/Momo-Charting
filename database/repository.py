from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Iterator
from zoneinfo import ZoneInfo

import pandas as pd

from config import DATABASE_PATH, EASTERN_TZ, settings


class TradingRepository:
    def __init__(self, db_path=DATABASE_PATH) -> None:
        self.db_path = db_path
        self._market_tz = ZoneInfo(EASTERN_TZ)
        # SQLite permits one writer at a time. The dashboard, scanner, and OI
        # Finder share this repository in background threads, so keep each
        # connection transaction local to one thread rather than letting a
        # snapshot write fail with "database is locked".
        self._connection_lock = threading.RLock()
        self._catalyst_shadow_cache: dict | None = None
        self._catalyst_shadow_cache_at: datetime | None = None
        self.initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._connection_lock:
            connection = sqlite3.connect(self.db_path, timeout=30.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                yield connection
                connection.commit()
            finally:
                connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scan_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbols_scanned INTEGER NOT NULL,
                    candidates_found INTEGER NOT NULL,
                    top_symbol TEXT,
                    notes TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_order_id TEXT UNIQUE,
                    symbol TEXT NOT NULL,
                    account_profile_id TEXT,
                    account_label TEXT,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    stop_price REAL,
                    target_price REAL,
                    risk_amount REAL DEFAULT 0,
                    status TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    pnl REAL DEFAULT 0,
                    setup_name TEXT,
                    strategy_family TEXT,
                    score REAL DEFAULT 0,
                    trigger_source TEXT,
                    policy_status TEXT,
                    execution_route TEXT,
                    trade_blueprint TEXT,
                    session_name TEXT,
                    entry_reason TEXT,
                    analysis_json TEXT,
                    notes TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS backtest_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbols TEXT NOT NULL,
                    total_trades INTEGER NOT NULL,
                    win_rate REAL NOT NULL,
                    total_pnl REAL NOT NULL,
                    summary_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS option_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_order_id TEXT UNIQUE,
                    underlying_symbol TEXT NOT NULL,
                    option_symbol TEXT,
                    account_profile_id TEXT,
                    account_label TEXT,
                    structure TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    entry_price REAL,
                    exit_price REAL,
                    stop_price REAL,
                    target_price REAL,
                    max_loss_amount REAL DEFAULT 0,
                    approval_mode TEXT,
                    status TEXT NOT NULL,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    pnl REAL DEFAULT 0,
                    trigger_source TEXT,
                    analysis_json TEXT,
                    notes TEXT,
                    account_mode TEXT DEFAULT 'paper'
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS catalyst_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    headline TEXT NOT NULL,
                    source TEXT,
                    url TEXT,
                    published_at TEXT,
                    score INTEGER DEFAULT 0,
                    sentiment TEXT,
                    tags TEXT,
                    UNIQUE(symbol, headline, published_at)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    message TEXT NOT NULL,
                    payload_json TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS scanner_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scan_date TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'Watchlist',
                    scanned_at TEXT NOT NULL,
                    scan_bucket TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    last_price REAL,
                    one_hour_close_change_pct REAL,
                    four_hour_volume_change_pct REAL,
                    four_hour_current_volume REAL,
                    four_hour_volume_2_bars_ago REAL,
                    trigger_source TEXT,
                    setup_name TEXT,
                    raw_json TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS symbol_memory (
                    symbol TEXT PRIMARY KEY,
                    observations INTEGER DEFAULT 0,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    total_pnl REAL DEFAULT 0,
                    total_r REAL DEFAULT 0,
                    confidence REAL DEFAULT 50,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS option_chain_daily_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_date TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    expiry TEXT NOT NULL,
                    side TEXT NOT NULL,
                    strike REAL NOT NULL,
                    volume REAL NOT NULL DEFAULT 0,
                    open_interest REAL NOT NULL DEFAULT 0,
                    gamma REAL NOT NULL DEFAULT 0,
                    delta REAL NOT NULL DEFAULT 0,
                    UNIQUE(snapshot_date, symbol, expiry, side, strike)
                )
                """
            )
            option_snapshot_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(option_chain_daily_snapshots)").fetchall()
            }
            if "gamma" not in option_snapshot_columns:
                connection.execute("ALTER TABLE option_chain_daily_snapshots ADD COLUMN gamma REAL NOT NULL DEFAULT 0")
            if "delta" not in option_snapshot_columns:
                connection.execute("ALTER TABLE option_chain_daily_snapshots ADD COLUMN delta REAL NOT NULL DEFAULT 0")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS option_wall_strength_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bucket_time INTEGER NOT NULL,
                    captured_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    strike REAL NOT NULL,
                    wall_strength REAL NOT NULL DEFAULT 0,
                    volume_strength REAL NOT NULL DEFAULT 0,
                    volume REAL NOT NULL DEFAULT 0,
                    open_interest REAL NOT NULL DEFAULT 0,
                    UNIQUE(bucket_time, symbol, side, strike)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS option_chain_intraday_volume_buckets (
                    symbol TEXT NOT NULL,
                    front_expiry TEXT NOT NULL,
                    bucket_time INTEGER NOT NULL,
                    recorded_at TEXT NOT NULL,
                    spot REAL NOT NULL DEFAULT 0,
                    calls_json TEXT NOT NULL DEFAULT '{}',
                    puts_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(symbol, front_expiry, bucket_time)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_option_chain_intraday_volume_lookup ON option_chain_intraday_volume_buckets(symbol, front_expiry, bucket_time)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observation_key TEXT NOT NULL UNIQUE,
                    observed_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    bucket_at TEXT NOT NULL,
                    source TEXT NOT NULL,
                    cohort TEXT NOT NULL DEFAULT 'mag7',
                    product TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    contract TEXT,
                    setup_name TEXT,
                    session_name TEXT,
                    entry_price REAL,
                    stop_price REAL,
                    target_price REAL,
                    model_name TEXT,
                    model_probability REAL,
                    fast_momentum_score INTEGER DEFAULT 0,
                    traded INTEGER DEFAULT 0,
                    trade_reference TEXT,
                    features_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_horizon_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    observation_id INTEGER NOT NULL,
                    horizon_minutes INTEGER NOT NULL,
                    due_at TEXT NOT NULL,
                    resolved_at TEXT,
                    future_price REAL,
                    return_pct REAL,
                    label_win INTEGER,
                    label_method TEXT DEFAULT 'horizon_mark',
                    UNIQUE(observation_id, horizon_minutes),
                    FOREIGN KEY(observation_id) REFERENCES learning_observations(id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_trade_outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    outcome_key TEXT NOT NULL UNIQUE,
                    product TEXT NOT NULL,
                    cohort TEXT NOT NULL DEFAULT 'mag7',
                    trade_row_id INTEGER NOT NULL,
                    symbol TEXT NOT NULL,
                    contract TEXT,
                    opened_at TEXT,
                    closed_at TEXT,
                    pnl REAL DEFAULT 0,
                    label_win INTEGER NOT NULL,
                    hold_minutes REAL,
                    exit_reason TEXT,
                    analysis_json TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_models (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_name TEXT NOT NULL,
                    version TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    trained_at TEXT NOT NULL,
                    training_rows INTEGER DEFAULT 0,
                    validation_rows INTEGER DEFAULT 0,
                    accuracy REAL,
                    brier_score REAL,
                    win_rate REAL,
                    artifact_path TEXT,
                    metrics_json TEXT,
                    is_active INTEGER DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS learning_cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    status TEXT NOT NULL,
                    observations INTEGER DEFAULT 0,
                    resolved_outcomes INTEGER DEFAULT 0,
                    trade_outcomes INTEGER DEFAULT 0,
                    message TEXT,
                    metrics_json TEXT
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_learning_observed_at ON learning_observations(observed_at)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_learning_source ON learning_observations(source, product)")
            connection.execute("CREATE INDEX IF NOT EXISTS idx_learning_due ON learning_horizon_outcomes(due_at, resolved_at)")
            self._ensure_column(connection, "scan_runs", "top_symbol", "TEXT")
            self._ensure_column(connection, "learning_observations", "cohort", "TEXT NOT NULL DEFAULT 'mag7'")
            self._ensure_column(connection, "learning_trade_outcomes", "cohort", "TEXT NOT NULL DEFAULT 'mag7'")
            self._ensure_column(connection, "scan_runs", "notes", "TEXT")
            self._ensure_column(connection, "scan_runs", "account_mode", "TEXT DEFAULT 'paper'")
            self._ensure_column(connection, "trades", "client_order_id", "TEXT")
            self._ensure_column(connection, "trades", "risk_amount", "REAL DEFAULT 0")
            self._ensure_column(connection, "trades", "notes", "TEXT")
            self._ensure_column(connection, "trades", "account_mode", "TEXT DEFAULT 'paper'")
            self._ensure_column(connection, "trades", "setup_name", "TEXT")
            self._ensure_column(connection, "trades", "strategy_family", "TEXT")
            self._ensure_column(connection, "trades", "score", "REAL DEFAULT 0")
            self._ensure_column(connection, "trades", "trigger_source", "TEXT")
            self._ensure_column(connection, "trades", "policy_status", "TEXT")
            self._ensure_column(connection, "trades", "execution_route", "TEXT")
            self._ensure_column(connection, "trades", "trade_blueprint", "TEXT")
            self._ensure_column(connection, "trades", "session_name", "TEXT")
            self._ensure_column(connection, "trades", "entry_reason", "TEXT")
            self._ensure_column(connection, "trades", "analysis_json", "TEXT")
            self._ensure_column(connection, "trades", "account_profile_id", "TEXT")
            self._ensure_column(connection, "trades", "account_label", "TEXT")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_client_order_id ON trades(client_order_id)"
            )
            self._ensure_column(connection, "option_trades", "client_order_id", "TEXT")
            self._ensure_column(connection, "option_trades", "broker_order_id", "TEXT")
            self._ensure_column(connection, "option_trades", "option_symbol", "TEXT")
            self._ensure_column(connection, "option_trades", "account_profile_id", "TEXT")
            self._ensure_column(connection, "option_trades", "account_label", "TEXT")
            self._ensure_column(connection, "option_trades", "entry_price", "REAL")
            self._ensure_column(connection, "option_trades", "exit_price", "REAL")
            self._ensure_column(connection, "option_trades", "stop_price", "REAL")
            self._ensure_column(connection, "option_trades", "target_price", "REAL")
            self._ensure_column(connection, "option_trades", "max_loss_amount", "REAL DEFAULT 0")
            self._ensure_column(connection, "option_trades", "approval_mode", "TEXT")
            self._ensure_column(connection, "option_trades", "trigger_source", "TEXT")
            self._ensure_column(connection, "option_trades", "analysis_json", "TEXT")
            self._ensure_column(connection, "option_trades", "notes", "TEXT")
            self._ensure_column(connection, "option_trades", "account_mode", "TEXT DEFAULT 'paper'")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_option_trades_client_order_id ON option_trades(client_order_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_trades_retention ON trades(account_mode, opened_at, closed_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_option_trades_retention ON option_trades(account_mode, opened_at, closed_at)"
            )
            self._ensure_column(connection, "scanner_history", "source", "TEXT DEFAULT 'Watchlist'")
            self._ensure_column(connection, "scanner_history", "last_price", "REAL")
            self._ensure_column(connection, "scanner_history", "one_hour_close_change_pct", "REAL")
            self._ensure_column(connection, "scanner_history", "four_hour_volume_change_pct", "REAL")
            self._ensure_column(connection, "scanner_history", "four_hour_current_volume", "REAL")
            self._ensure_column(connection, "scanner_history", "four_hour_volume_2_bars_ago", "REAL")
            self._ensure_column(connection, "scanner_history", "trigger_source", "TEXT")
            self._ensure_column(connection, "scanner_history", "setup_name", "TEXT")
            self._ensure_column(connection, "scanner_history", "raw_json", "TEXT")
            self._ensure_column(connection, "scanner_history", "scan_bucket", "TEXT")
            connection.execute(
                "UPDATE scanner_history SET scan_bucket = scanned_at WHERE scan_bucket IS NULL OR TRIM(scan_bucket) = ''"
            )
            self._migrate_scanner_history_source(connection)
            self._migrate_scanner_history_events(connection)
            connection.execute("DROP INDEX IF EXISTS idx_scanner_history_date_source_symbol")
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_scanner_history_event ON scanner_history(scan_date, source, symbol, scan_bucket)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_option_chain_daily_lookup ON option_chain_daily_snapshots(symbol, expiry, side, snapshot_date, strike)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_option_wall_strength_lookup ON option_wall_strength_snapshots(symbol, bucket_time, side, strike)"
            )
            self._deduplicate_option_chain_snapshot_expiries(connection)
            option_chain_cutoff = (datetime.now(self._market_tz).date() - timedelta(days=183)).isoformat()
            connection.execute("DELETE FROM option_chain_daily_snapshots WHERE snapshot_date < ?", (option_chain_cutoff,))
            wall_strength_cutoff = int((datetime.now(timezone.utc) - timedelta(days=183)).timestamp())
            connection.execute("DELETE FROM option_wall_strength_snapshots WHERE bucket_time < ?", (wall_strength_cutoff,))
            self._prune_trade_journals(connection)

    def _prune_trade_journals(
        self,
        connection: sqlite3.Connection,
        retention_days: int | None = None,
    ) -> None:
        """Keep six months of completed journal rows without removing open positions."""
        keep_days = max(int(retention_days or settings.trading.journal_retention_days), 1)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat()
        final_status_sql = """
            lower(COALESCE(status, '')) LIKE '%closed%'
            OR lower(COALESCE(status, '')) LIKE '%filled_exit%'
            OR lower(COALESCE(status, '')) LIKE '%canceled%'
            OR lower(COALESCE(status, '')) LIKE '%cancelled%'
            OR lower(COALESCE(status, '')) LIKE '%rejected%'
            OR lower(COALESCE(status, '')) LIKE '%expired%'
        """
        for table in ("trades", "option_trades"):
            connection.execute(
                f"""
                DELETE FROM {table}
                WHERE COALESCE(closed_at, opened_at) < ?
                  AND (closed_at IS NOT NULL OR ({final_status_sql}))
                """,
                (cutoff,),
            )

    def prune_trade_journals(self, retention_days: int | None = None) -> None:
        with self._connect() as connection:
            self._prune_trade_journals(connection, retention_days=retention_days)

    def get_app_settings(self) -> dict[str, str]:
        with self._connect() as connection:
            rows = connection.execute("SELECT key, value FROM app_settings").fetchall()
            return {str(row["key"]): str(row["value"]) for row in rows}

    def set_app_setting(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, datetime.utcnow().isoformat()),
            )

    @staticmethod
    def _canonical_option_expiry(value: object) -> str:
        text = str(value or "").strip()
        date_part = text[:10]
        return date_part if len(date_part) == 10 and date_part[4:5] == "-" and date_part[7:8] == "-" else text

    def _deduplicate_option_chain_snapshot_expiries(self, connection: sqlite3.Connection) -> None:
        """Collapse legacy ISO-timestamp and date-only copies of the same option expiry."""
        rows = connection.execute(
            """
            SELECT id, snapshot_date, captured_at, symbol, expiry, side, strike, volume, open_interest, gamma, delta
            FROM option_chain_daily_snapshots
            """
        ).fetchall()
        grouped: dict[tuple[str, str, str, str, float], list[sqlite3.Row]] = {}
        for row in rows:
            canonical_expiry = self._canonical_option_expiry(row["expiry"])
            key = (row["snapshot_date"], row["symbol"], canonical_expiry, row["side"], float(row["strike"]))
            grouped.setdefault(key, []).append(row)
        for (snapshot_date, symbol, expiry, side, strike), duplicates in grouped.items():
            if len(duplicates) == 1 and duplicates[0]["expiry"] == expiry:
                continue
            winner = max(duplicates, key=lambda row: (str(row["captured_at"]), int(row["id"])))
            connection.executemany(
                "DELETE FROM option_chain_daily_snapshots WHERE id = ?",
                [(int(row["id"]),) for row in duplicates],
            )
            connection.execute(
                """
                INSERT INTO option_chain_daily_snapshots (
                    snapshot_date, captured_at, symbol, expiry, side, strike, volume, open_interest, gamma, delta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_date,
                    winner["captured_at"],
                    symbol,
                    expiry,
                    side,
                    strike,
                    winner["volume"],
                    winner["open_interest"],
                    winner["gamma"],
                    winner["delta"],
                ),
            )

    def upsert_option_chain_daily_snapshots(self, snapshots: list[dict]) -> None:
        """Persist the latest daily option-chain values for the Finder heatmap."""
        normalized_records: dict[tuple[str, str, str, str, float], tuple] = {}
        for snapshot in snapshots or []:
            snapshot_date = str(snapshot.get("snapshot_date") or "").strip()
            captured_at = str(snapshot.get("captured_at") or "").strip()
            symbol = str(snapshot.get("symbol") or "").strip().upper()
            expiry = self._canonical_option_expiry(snapshot.get("expiry"))
            side = str(snapshot.get("side") or "").strip().upper()
            try:
                strike = float(snapshot.get("strike") or 0)
                volume = float(snapshot.get("volume") or 0)
                open_interest = float(snapshot.get("open_interest") or 0)
                gamma = float(snapshot.get("gamma") or 0)
                delta = float(snapshot.get("delta") or 0)
            except (TypeError, ValueError):
                continue
            if not snapshot_date or not captured_at or not symbol or not expiry or side not in {"CALL", "PUT"} or strike <= 0:
                continue
            record = (snapshot_date, captured_at, symbol, expiry, side, strike, max(volume, 0), max(open_interest, 0), gamma, delta)
            key = (snapshot_date, symbol, expiry, side, strike)
            existing = normalized_records.get(key)
            if existing is None or (record[1], record[6] + record[7]) >= (existing[1], existing[6] + existing[7]):
                normalized_records[key] = record
        records = list(normalized_records.values())
        if not records:
            return
        with self._connect() as connection:
            for snapshot_date, _, symbol, expiry, side, strike, _, _, _, _ in records:
                connection.execute(
                    """
                    DELETE FROM option_chain_daily_snapshots
                    WHERE snapshot_date = ? AND symbol = ? AND side = ? AND strike = ?
                      AND substr(expiry, 1, 10) = ? AND expiry <> ?
                    """,
                    (snapshot_date, symbol, side, strike, expiry, expiry),
                )
            connection.executemany(
                """
                INSERT INTO option_chain_daily_snapshots (
                    snapshot_date, captured_at, symbol, expiry, side, strike, volume, open_interest, gamma, delta
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(snapshot_date, symbol, expiry, side, strike) DO UPDATE SET
                    captured_at = excluded.captured_at,
                    volume = excluded.volume,
                    open_interest = excluded.open_interest,
                    gamma = excluded.gamma,
                    delta = excluded.delta
                """,
                records,
            )
            cutoff = (datetime.now(self._market_tz).date() - timedelta(days=183)).isoformat()
            connection.execute("DELETE FROM option_chain_daily_snapshots WHERE snapshot_date < ?", (cutoff,))

    def option_chain_daily_snapshots(self, symbol: str, expiry: str | None = None, days: int = 183) -> list[dict]:
        """Return six months at most of daily option-chain snapshots for a symbol."""
        target_symbol = str(symbol or "").strip().upper()
        target_expiry = str(expiry or "").strip()
        if not target_symbol:
            return []
        limit_days = max(1, min(int(days or 183), 183))
        cutoff = (datetime.now(self._market_tz).date() - timedelta(days=limit_days)).isoformat()
        with self._connect() as connection:
            if target_expiry:
                rows = connection.execute(
                    """
                    SELECT snapshot_date, captured_at, symbol, expiry, side, strike, volume, open_interest, gamma, delta
                    FROM option_chain_daily_snapshots
                    WHERE symbol = ? AND expiry = ? AND snapshot_date >= ?
                    ORDER BY snapshot_date ASC, side ASC, strike DESC
                    """,
                    (target_symbol, target_expiry, cutoff),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT snapshot_date, captured_at, symbol, expiry, side, strike, volume, open_interest, gamma, delta
                    FROM option_chain_daily_snapshots
                    WHERE symbol = ? AND snapshot_date >= ?
                    ORDER BY snapshot_date ASC, side ASC, expiry ASC, strike DESC
                    """,
                    (target_symbol, cutoff),
                ).fetchall()
        return [dict(row) for row in rows]

    def upsert_option_chain_intraday_volume_bucket(self, bucket: dict) -> None:
        """Persist one cumulative-volume reading for the Finder rate card.

        These are minute buckets, not historical trade prints.  Keeping the
        raw cumulative call/put volumes lets the browser calculate the same
        counter-safe delta after a backend restart.
        """
        symbol = str(bucket.get("symbol") or "").strip().upper()
        front_expiry = self._canonical_option_expiry(bucket.get("front_expiry"))
        recorded_at = str(bucket.get("recorded_at") or "").strip()
        try:
            bucket_time = int(bucket.get("bucket_time") or 0)
            spot = float(bucket.get("spot") or 0)
        except (TypeError, ValueError):
            return
        calls = bucket.get("calls") if isinstance(bucket.get("calls"), dict) else {}
        puts = bucket.get("puts") if isinstance(bucket.get("puts"), dict) else {}
        if not symbol or not front_expiry or bucket_time <= 0 or not recorded_at:
            return
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO option_chain_intraday_volume_buckets (
                    symbol, front_expiry, bucket_time, recorded_at, spot, calls_json, puts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, front_expiry, bucket_time) DO UPDATE SET
                    recorded_at = excluded.recorded_at,
                    spot = excluded.spot,
                    calls_json = excluded.calls_json,
                    puts_json = excluded.puts_json
                """,
                (
                    symbol,
                    front_expiry,
                    bucket_time,
                    recorded_at,
                    spot,
                    json.dumps(calls, separators=(",", ":")),
                    json.dumps(puts, separators=(",", ":")),
                ),
            )
            # Intraday rate bars are useful for the current and immediately
            # preceding session.  Daily OI/volume history remains in its own
            # six-month table above.
            cutoff = int((datetime.now(self._market_tz) - timedelta(hours=36)).timestamp())
            connection.execute("DELETE FROM option_chain_intraday_volume_buckets WHERE bucket_time < ?", (cutoff,))

    def option_chain_intraday_volume_buckets(self, symbol: str, front_expiry: str, hours: int = 18) -> list[dict]:
        """Restore compact cumulative-volume minute buckets for one front expiry."""
        target_symbol = str(symbol or "").strip().upper()
        target_expiry = self._canonical_option_expiry(front_expiry)
        if not target_symbol or not target_expiry:
            return []
        cutoff = int((datetime.now(self._market_tz) - timedelta(hours=max(1, min(int(hours), 36)))).timestamp())
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT bucket_time, recorded_at, spot, calls_json, puts_json
                FROM option_chain_intraday_volume_buckets
                WHERE symbol = ? AND front_expiry = ? AND bucket_time >= ?
                ORDER BY bucket_time ASC
                """,
                (target_symbol, target_expiry, cutoff),
            ).fetchall()
        restored: list[dict] = []
        for row in rows:
            try:
                calls = json.loads(row["calls_json"] or "{}")
                puts = json.loads(row["puts_json"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            restored.append({
                "time": int(row["bucket_time"]),
                "recordedAt": row["recorded_at"],
                "frontExpiry": target_expiry,
                "spot": float(row["spot"] or 0) or None,
                "calls": calls if isinstance(calls, dict) else {},
                "puts": puts if isinstance(puts, dict) else {},
            })
        return restored

    def upsert_option_wall_strength_snapshots(self, snapshots: list[dict]) -> None:
        """Store the leading Finder walls in five-minute buckets for a rolling six months."""
        records: list[tuple] = []
        for snapshot in snapshots or []:
            bucket_time = snapshot.get("bucket_time")
            captured_at = str(snapshot.get("captured_at") or "").strip()
            symbol = str(snapshot.get("symbol") or "").strip().upper()
            side = str(snapshot.get("side") or "").strip().upper()
            try:
                bucket_time = int(bucket_time)
                strike = float(snapshot.get("strike") or 0)
                wall_strength = float(snapshot.get("wall_strength") or 0)
                volume_strength = float(snapshot.get("volume_strength") or 0)
                volume = float(snapshot.get("volume") or 0)
                open_interest = float(snapshot.get("open_interest") or 0)
            except (TypeError, ValueError):
                continue
            if bucket_time <= 0 or not captured_at or not symbol or side not in {"CALL", "PUT"} or strike <= 0:
                continue
            records.append((
                bucket_time,
                captured_at,
                symbol,
                side,
                strike,
                max(wall_strength, 0),
                max(volume_strength, 0),
                max(volume, 0),
                max(open_interest, 0),
            ))
        if not records:
            return
        with self._connect() as connection:
            connection.executemany(
                """
                INSERT INTO option_wall_strength_snapshots (
                    bucket_time, captured_at, symbol, side, strike, wall_strength,
                    volume_strength, volume, open_interest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bucket_time, symbol, side, strike) DO UPDATE SET
                    captured_at = excluded.captured_at,
                    wall_strength = excluded.wall_strength,
                    volume_strength = excluded.volume_strength,
                    volume = excluded.volume,
                    open_interest = excluded.open_interest
                """,
                records,
            )
            cutoff = int((datetime.now(timezone.utc) - timedelta(days=183)).timestamp())
            connection.execute("DELETE FROM option_wall_strength_snapshots WHERE bucket_time < ?", (cutoff,))

    def option_wall_strength_snapshots(self, symbol: str, days: int = 7) -> list[dict]:
        """Return a compact recent wall-strength history for one Finder ticker."""
        target_symbol = str(symbol or "").strip().upper()
        if not target_symbol:
            return []
        limit_days = max(1, min(int(days or 7), 183))
        cutoff = int((datetime.now(timezone.utc) - timedelta(days=limit_days)).timestamp())
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT bucket_time, captured_at, symbol, side, strike, wall_strength,
                       volume_strength, volume, open_interest
                FROM option_wall_strength_snapshots
                WHERE symbol = ? AND bucket_time >= ?
                ORDER BY bucket_time ASC, side ASC, strike ASC
                """,
                (target_symbol, cutoff),
            ).fetchall()
        return [dict(row) for row in rows]

    def log_scan_run(self, symbols_scanned: int, candidates_found: int, top_symbol: str | None, notes: str = "") -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scan_runs (created_at, symbols_scanned, candidates_found, top_symbol, notes, account_mode)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (datetime.utcnow().isoformat(), symbols_scanned, candidates_found, top_symbol, notes, settings.execution_mode),
            )

    def log_scanner_history(self, scan_results: pd.DataFrame, scanned_at: datetime | None = None, source: str = "Watchlist") -> None:
        if scan_results is None or scan_results.empty or "symbol" not in scan_results.columns:
            return
        timestamp = scanned_at or datetime.now(tz=self._market_tz)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc).astimezone(self._market_tz)
        else:
            timestamp = timestamp.astimezone(self._market_tz)
        scan_date = timestamp.date().isoformat()
        bucket_minute = (timestamp.minute // 15) * 15
        event_timestamp = timestamp.replace(minute=bucket_minute, second=0, microsecond=0)
        # Keep the real first-observed time visible to the user. The rounded
        # bucket remains a separate deduplication key for repeat scan events.
        scanned_at_text = timestamp.isoformat()
        scan_bucket = event_timestamp.isoformat()
        source_text = str(source or "Watchlist").strip() or "Watchlist"
        with self._connect() as connection:
            for row in scan_results.to_dict("records"):
                symbol = str(row.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                connection.execute(
                    """
                    INSERT INTO scanner_history (
                        scan_date, source, scanned_at, scan_bucket, symbol, last_price,
                        one_hour_close_change_pct, four_hour_volume_change_pct,
                        four_hour_current_volume, four_hour_volume_2_bars_ago,
                        trigger_source, setup_name, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(scan_date, source, symbol, scan_bucket) DO UPDATE SET
                        last_price = excluded.last_price,
                        one_hour_close_change_pct = excluded.one_hour_close_change_pct,
                        four_hour_volume_change_pct = excluded.four_hour_volume_change_pct,
                        four_hour_current_volume = excluded.four_hour_current_volume,
                        four_hour_volume_2_bars_ago = excluded.four_hour_volume_2_bars_ago,
                        trigger_source = excluded.trigger_source,
                        setup_name = excluded.setup_name,
                        raw_json = excluded.raw_json
                    """,
                    (
                        scan_date,
                        source_text,
                        scanned_at_text,
                        scan_bucket,
                        symbol,
                        row.get("last_price"),
                        row.get("one_hour_price_change_pct", row.get("one_hour_close_change_pct")),
                        row.get("four_hour_volume_change_pct"),
                        row.get("four_hour_current_volume"),
                        row.get("four_hour_volume_2_bars_ago"),
                        row.get("trigger_source", ""),
                        row.get("setup_name", ""),
                        pd.Series(row).to_json(),
                    ),
                )
            self._prune_scanner_history(connection)

    def get_scanner_history(self, days: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
        retention_days = max(int(days or settings.scanner.history_retention_days), 1)
        cutoff = (datetime.now(tz=self._market_tz) - timedelta(days=retention_days)).date().isoformat()
        rows = self._query_frame(
            """
            SELECT *
            FROM scanner_history
            WHERE scan_date >= ?
            ORDER BY scan_date DESC, scanned_at DESC, symbol ASC
            """,
            (cutoff,),
        )
        summary = self._query_frame(
            """
            SELECT scan_date, source, COUNT(DISTINCT symbol) AS ticker_count,
                   COUNT(*) AS entry_count, MAX(scanned_at) AS last_scanned_at
            FROM scanner_history
            WHERE scan_date >= ?
            GROUP BY scan_date, source
            ORDER BY scan_date DESC, source ASC
            """,
            (cutoff,),
        )
        return rows, summary

    def _prune_scanner_history(self, connection: sqlite3.Connection, retention_days: int | None = None) -> None:
        keep_days = max(int(retention_days or settings.scanner.history_retention_days), 1)
        cutoff = (datetime.now(tz=self._market_tz) - timedelta(days=keep_days)).date().isoformat()
        connection.execute(
            "DELETE FROM scanner_history WHERE scan_date < ?",
            (cutoff,),
        )

    def log_trade(
        self,
        client_order_id: str,
        symbol: str,
        account_profile_id: str,
        account_label: str,
        side: str,
        quantity: float,
        entry_price: float,
        stop_price: float,
        target_price: float,
        risk_amount: float,
        status: str,
        setup_name: str = "",
        strategy_family: str = "",
        score: float = 0,
        trigger_source: str = "",
        policy_status: str = "",
        execution_route: str = "",
        trade_blueprint: str = "",
        session_name: str = "",
        entry_reason: str = "",
        analysis_json: str = "",
        notes: str = "",
    ) -> None:
        with self._connect() as connection:
            self._prune_trade_journals(connection)
            connection.execute(
                """
                INSERT OR REPLACE INTO trades (
                    client_order_id, symbol, account_profile_id, account_label, side, quantity, entry_price, stop_price,
                    target_price, risk_amount, status, opened_at, setup_name, strategy_family, score,
                    trigger_source, policy_status, execution_route, trade_blueprint, session_name, entry_reason, analysis_json, notes, account_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_order_id,
                    symbol,
                    account_profile_id,
                    account_label,
                    side,
                    quantity,
                    entry_price,
                    stop_price,
                    target_price,
                    risk_amount,
                    status,
                    datetime.now(timezone.utc).isoformat(),
                    setup_name,
                    strategy_family,
                    score,
                    trigger_source,
                    policy_status,
                    execution_route,
                    trade_blueprint,
                    session_name,
                    entry_reason,
                    analysis_json,
                    notes,
                    settings.execution_mode,
                ),
            )

    def update_trade_status(
        self,
        client_order_id: str,
        status: str,
        pnl: float | None = None,
        closed_at: str | None = None,
        notes: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE trades
                SET status = ?, pnl = COALESCE(?, pnl), closed_at = COALESCE(?, closed_at), notes = COALESCE(?, notes)
                WHERE client_order_id = ?
                """,
                (status, pnl, closed_at, notes, client_order_id),
            )

    def update_trade_plan(
        self,
        client_order_id: str,
        stop_price: float | None = None,
        target_price: float | None = None,
        notes: str | None = None,
        analysis_json: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE trades
                SET stop_price = COALESCE(?, stop_price),
                    target_price = COALESCE(?, target_price),
                    notes = COALESCE(?, notes),
                    analysis_json = COALESCE(?, analysis_json)
                WHERE client_order_id = ?
                """,
                (stop_price, target_price, notes, analysis_json, client_order_id),
            )

    def trades_today_count(self, profile_id: str | None = None) -> int:
        history = self.get_trade_history(limit=5000, profile_id=profile_id)
        if history.empty:
            return 0
        frame = self._normalize_trade_times(history)
        today_market = datetime.now(self._market_tz).date()
        opened_dates = frame["opened_at_market"].dt.date
        return int((opened_dates == today_market).sum())

    def option_trades_today_count(self, profile_id: str | None = None, broker_only: bool = False) -> int:
        history = self.get_option_trade_history(limit=5000, profile_id=profile_id, broker_only=broker_only)
        if history.empty:
            return 0
        frame = self._normalize_option_trade_times(history)
        today_market = datetime.now(self._market_tz).date()
        opened_dates = frame["opened_at_market"].dt.date
        return int((opened_dates == today_market).sum())

    def get_open_trades(self, profile_id: str | None = None) -> pd.DataFrame:
        open_where = """
            closed_at IS NULL
            AND account_mode = ?
            AND (
                status IS NULL
                OR (
                    lower(status) NOT LIKE '%canceled%'
                    AND lower(status) NOT LIKE '%cancelled%'
                    AND lower(status) NOT LIKE '%rejected%'
                    AND lower(status) NOT LIKE '%expired%'
                    AND lower(status) NOT LIKE '%closed_or_filled%'
                    AND lower(status) NOT LIKE '%filled_exit%'
                )
            )
        """
        if profile_id:
            return self._query_frame(
                f"SELECT * FROM trades WHERE {open_where} AND COALESCE(account_profile_id, '') IN (?, '') ORDER BY opened_at DESC",
                (settings.execution_mode, profile_id),
            )
        return self._query_frame(
            f"SELECT * FROM trades WHERE {open_where} ORDER BY opened_at DESC",
            (settings.execution_mode,),
        )

    def get_trade_history(self, limit: int = 50, profile_id: str | None = None) -> pd.DataFrame:
        if profile_id:
            return self._query_frame(
                f"SELECT * FROM trades WHERE account_mode = ? AND COALESCE(account_profile_id, '') IN (?, '') ORDER BY opened_at DESC LIMIT {int(limit)}",
                (settings.execution_mode, profile_id),
            )
        return self._query_frame(
            f"SELECT * FROM trades WHERE account_mode = ? ORDER BY opened_at DESC LIMIT {int(limit)}",
            (settings.execution_mode,),
        )

    def daily_pnl(self, profile_id: str | None = None) -> float:
        history = self.get_trade_history(limit=5000, profile_id=profile_id)
        if history.empty:
            return 0.0
        frame = self._normalize_trade_times(history)
        frame["pnl"] = pd.to_numeric(frame["pnl"], errors="coerce").fillna(0.0)
        today_market = datetime.now(self._market_tz).date()
        effective_dates = frame["effective_time_market"].dt.date
        return float(frame.loc[effective_dates == today_market, "pnl"].sum())

    def trade_rollups(self, profile_id: str | None = None) -> dict[str, pd.DataFrame]:
        history = self.get_trade_history(limit=2000, profile_id=profile_id)
        if history.empty:
            return {"daily": pd.DataFrame(), "weekly": pd.DataFrame(), "monthly": pd.DataFrame()}

        frame = self._normalize_trade_times(history)
        frame["pnl"] = pd.to_numeric(frame["pnl"], errors="coerce").fillna(0.0)
        frame["is_win"] = frame["pnl"] > 0
        frame["is_loss"] = frame["pnl"] < 0

        def summarize(freq: str, label: str) -> pd.DataFrame:
            grouped = (
                frame.groupby(pd.Grouper(key="effective_time_market", freq=freq))
                .agg(
                    trades=("client_order_id", "count"),
                    wins=("is_win", "sum"),
                    losses=("is_loss", "sum"),
                    total_pnl=("pnl", "sum"),
                )
                .reset_index()
            )
            grouped = grouped.dropna(subset=["effective_time_market"])
            if grouped.empty:
                return grouped
            grouped["effective_time"] = grouped["effective_time_market"].dt.strftime("%Y-%m-%d %H:%M:%S")
            grouped["period"] = grouped["effective_time_market"].dt.strftime("%Y-%m-%d" if label == "daily" else "%Y-%m-%d")
            grouped["win_rate"] = ((grouped["wins"] / grouped["trades"].replace(0, pd.NA)) * 100).round(2)
            return grouped.sort_values("effective_time_market", ascending=False).reset_index(drop=True)

        return {
            "daily": summarize("D", "daily"),
            "weekly": summarize("W-MON", "weekly"),
            "monthly": summarize("MS", "monthly"),
        }

    def log_option_trade(
        self,
        client_order_id: str,
        broker_order_id: str,
        underlying_symbol: str,
        option_symbol: str,
        account_profile_id: str,
        account_label: str,
        structure: str,
        side: str,
        quantity: float,
        status: str,
        entry_price: float | None = None,
        exit_price: float | None = None,
        stop_price: float | None = None,
        target_price: float | None = None,
        max_loss_amount: float = 0.0,
        approval_mode: str = "",
        trigger_source: str = "",
        analysis_json: str = "",
        notes: str = "",
    ) -> None:
        with self._connect() as connection:
            self._prune_trade_journals(connection)
            connection.execute(
                """
                INSERT OR REPLACE INTO option_trades (
                    client_order_id, broker_order_id, underlying_symbol, option_symbol, account_profile_id, account_label,
                    structure, side, quantity, entry_price, exit_price, stop_price, target_price,
                    max_loss_amount, approval_mode, status, opened_at, trigger_source, analysis_json, notes, account_mode
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    client_order_id,
                    broker_order_id,
                    underlying_symbol,
                    option_symbol,
                    account_profile_id,
                    account_label,
                    structure,
                    side,
                    quantity,
                    entry_price,
                    exit_price,
                    stop_price,
                    target_price,
                    max_loss_amount,
                    approval_mode,
                    status,
                    datetime.now(timezone.utc).isoformat(),
                    trigger_source,
                    analysis_json,
                    notes,
                    settings.execution_mode,
                ),
            )

    def update_option_trade(
        self,
        client_order_id: str,
        *,
        broker_order_id: str | None = None,
        option_symbol: str | None = None,
        quantity: float | None = None,
        entry_price: float | None = None,
        exit_price: float | None = None,
        stop_price: float | None = None,
        target_price: float | None = None,
        status: str | None = None,
        pnl: float | None = None,
        closed_at: str | None = None,
        analysis_json: str | None = None,
        notes: str | None = None,
    ) -> None:
        updates = {
            "broker_order_id": broker_order_id,
            "option_symbol": option_symbol,
            "quantity": quantity,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "status": status,
            "pnl": pnl,
            "closed_at": closed_at,
            "analysis_json": analysis_json,
            "notes": notes,
        }
        assignments = []
        values = []
        for column, value in updates.items():
            if value is None:
                continue
            assignments.append(f"{column} = ?")
            values.append(value)
        if not assignments:
            return
        values.append(client_order_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE option_trades SET {', '.join(assignments)} WHERE client_order_id = ?",
                tuple(values),
            )

    def update_option_trade_status(
        self,
        client_order_id: str,
        status: str,
        pnl: float | None = None,
        exit_price: float | None = None,
        closed_at: str | None = None,
        notes: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE option_trades
                SET status = ?,
                    pnl = COALESCE(?, pnl),
                    exit_price = COALESCE(?, exit_price),
                    closed_at = COALESCE(?, closed_at),
                    notes = COALESCE(?, notes)
                WHERE client_order_id = ?
                """,
                (status, pnl, exit_price, closed_at, notes, client_order_id),
            )

    def update_option_trade_plan(
        self,
        client_order_id: str,
        stop_price: float | None = None,
        target_price: float | None = None,
        notes: str | None = None,
        analysis_json: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE option_trades
                SET stop_price = COALESCE(?, stop_price),
                    target_price = COALESCE(?, target_price),
                    notes = COALESCE(?, notes),
                    analysis_json = COALESCE(?, analysis_json)
                WHERE client_order_id = ?
                """,
                (stop_price, target_price, notes, analysis_json, client_order_id),
            )

    def get_open_option_trades(self, profile_id: str | None = None) -> pd.DataFrame:
        open_where = """
            closed_at IS NULL
            AND account_mode = ?
            AND (
                status IS NULL
                OR lower(status) IN (
                    'auto_mode_preview',
                    'awaiting_approval',
                    'planned',
                    'queued',
                    'submitted',
                    'new',
                    'accepted',
                    'accepted_for_bidding',
                    'pending_new',
                    'partially_filled',
                    'position_open',
                    'exit_pending',
                    'manual_exit_pending',
                    'open'
                )
            )
        """
        if profile_id:
            return self._query_frame(
                f"SELECT * FROM option_trades WHERE {open_where} AND account_profile_id = ? ORDER BY opened_at DESC",
                (settings.execution_mode, profile_id),
            )
        return self._query_frame(
            f"SELECT * FROM option_trades WHERE {open_where} ORDER BY opened_at DESC",
            (settings.execution_mode,),
        )

    def get_option_trade_history(
        self,
        limit: int = 50,
        profile_id: str | None = None,
        profile_ids: list[str] | set[str] | tuple[str, ...] | None = None,
        broker_only: bool = False,
    ) -> pd.DataFrame:
        where_clauses = ["account_mode = ?"]
        params: list[str] = [settings.execution_mode]
        if profile_id:
            where_clauses.append("account_profile_id = ?")
            params.append(profile_id)
        elif profile_ids:
            normalized_profiles = sorted({str(item or "").strip().lower() for item in profile_ids if str(item or "").strip()})
            if normalized_profiles:
                placeholders = ",".join("?" for _ in normalized_profiles)
                where_clauses.append(f"lower(account_profile_id) IN ({placeholders})")
                params.extend(normalized_profiles)
        if broker_only:
            where_clauses.append("COALESCE(broker_order_id, '') <> ''")
        where_sql = " AND ".join(where_clauses)
        return self._query_frame(
            f"SELECT * FROM option_trades WHERE {where_sql} ORDER BY opened_at DESC LIMIT {int(limit)}",
            tuple(params),
        )

    def option_trade_rollups(
        self,
        profile_id: str | None = None,
        profile_ids: list[str] | set[str] | tuple[str, ...] | None = None,
        broker_only: bool = False,
    ) -> dict[str, pd.DataFrame]:
        history = self.get_option_trade_history(
            limit=2000,
            profile_id=profile_id,
            profile_ids=profile_ids,
            broker_only=broker_only,
        )
        if history.empty:
            return {"daily": pd.DataFrame(), "weekly": pd.DataFrame(), "monthly": pd.DataFrame()}

        frame = self._normalize_option_trade_times(history)
        status = frame["status"].fillna("").astype(str).str.lower()
        closed_at = frame["closed_at"] if "closed_at" in frame.columns else pd.Series(index=frame.index, dtype="object")
        closed_mask = closed_at.notna() | status.isin({
            "closed",
            "filled_exit",
            "closed_or_filled",
            "cancelled_after_fill",
        })
        frame = frame.loc[closed_mask].copy()
        if frame.empty:
            return {"daily": pd.DataFrame(), "weekly": pd.DataFrame(), "monthly": pd.DataFrame()}
        frame["pnl"] = pd.to_numeric(frame["pnl"], errors="coerce").fillna(0.0)
        frame["is_win"] = frame["pnl"] > 0
        frame["is_loss"] = frame["pnl"] < 0

        def summarize(freq: str) -> pd.DataFrame:
            grouped = (
                frame.groupby(pd.Grouper(key="effective_time_market", freq=freq))
                .agg(
                    trades=("client_order_id", "count"),
                    wins=("is_win", "sum"),
                    losses=("is_loss", "sum"),
                    total_pnl=("pnl", "sum"),
                )
                .reset_index()
            )
            grouped = grouped.dropna(subset=["effective_time_market"])
            if grouped.empty:
                return grouped
            grouped["effective_time"] = grouped["effective_time_market"].dt.strftime("%Y-%m-%d %H:%M:%S")
            grouped["period"] = grouped["effective_time_market"].dt.strftime("%Y-%m-%d")
            grouped["scored_trades"] = grouped["wins"] + grouped["losses"]
            grouped["win_rate"] = ((grouped["wins"] / grouped["scored_trades"].replace(0, pd.NA)) * 100).fillna(0).round(2)
            return grouped.sort_values("effective_time_market", ascending=False).reset_index(drop=True)

        return {
            "daily": summarize("D"),
            "weekly": summarize("W-MON"),
            "monthly": summarize("MS"),
        }

    def _normalize_trade_times(self, frame: pd.DataFrame) -> pd.DataFrame:
        normalized = frame.copy()
        normalized["opened_at"] = pd.to_datetime(normalized["opened_at"], errors="coerce", utc=True)
        normalized["closed_at"] = pd.to_datetime(normalized["closed_at"], errors="coerce", utc=True)
        normalized["opened_at_market"] = normalized["opened_at"].dt.tz_convert(self._market_tz)
        normalized["closed_at_market"] = normalized["closed_at"].dt.tz_convert(self._market_tz)
        normalized["effective_time_market"] = normalized["closed_at_market"].fillna(normalized["opened_at_market"])
        return normalized

    def _normalize_option_trade_times(self, frame: pd.DataFrame) -> pd.DataFrame:
        normalized = frame.copy()
        normalized["opened_at"] = pd.to_datetime(normalized["opened_at"], errors="coerce", utc=True)
        normalized["closed_at"] = pd.to_datetime(normalized["closed_at"], errors="coerce", utc=True)
        normalized["opened_at_market"] = normalized["opened_at"].dt.tz_convert(self._market_tz)
        normalized["closed_at_market"] = normalized["closed_at"].dt.tz_convert(self._market_tz)
        normalized["effective_time_market"] = normalized["closed_at_market"].fillna(normalized["opened_at_market"])
        return normalized

    def log_backtest_run(self, symbols: list[str], summary_frame: pd.DataFrame) -> None:
        if summary_frame.empty:
            return
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO backtest_runs (created_at, symbols, total_trades, win_rate, total_pnl, summary_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.utcnow().isoformat(),
                    ",".join(symbols),
                    int(summary_frame["trades"].sum()),
                    float(summary_frame["win_rate"].mean()),
                    float(summary_frame["total_pnl"].sum()),
                    summary_frame.to_json(orient="records"),
                ),
            )

    def get_recent_backtests(self, limit: int = 10) -> pd.DataFrame:
        return self._query_frame(
            f"SELECT * FROM backtest_runs ORDER BY created_at DESC LIMIT {int(limit)}"
        )

    def log_catalysts(self, items: list[dict]) -> None:
        if not items:
            return
        with self._connect() as connection:
            for item in items:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO catalyst_items (
                        created_at, symbol, headline, source, url, published_at,
                        score, sentiment, tags
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        datetime.utcnow().isoformat(),
                        item.get("symbol", ""),
                        item.get("headline", ""),
                        item.get("source", ""),
                        item.get("url", ""),
                        item.get("published_at", ""),
                        int(item.get("score") or 0),
                        item.get("sentiment", ""),
                        item.get("tags", ""),
                    ),
                )

    def get_recent_catalysts(self, limit: int = 50) -> pd.DataFrame:
        return self._query_frame(
            f"SELECT * FROM catalyst_items ORDER BY published_at DESC, created_at DESC LIMIT {int(limit)}"
        )

    def get_latest_catalysts_by_symbol(self) -> pd.DataFrame:
        return self._query_frame(
            """
            SELECT id, created_at, symbol, headline, source, url, published_at,
                   score, sentiment, tags
            FROM (
                SELECT catalyst_items.*,
                       ROW_NUMBER() OVER (
                           PARTITION BY UPPER(symbol)
                           ORDER BY published_at DESC, created_at DESC, id DESC
                       ) AS symbol_rank
                FROM catalyst_items
            )
            WHERE symbol_rank = 1
            ORDER BY published_at DESC, created_at DESC
            """
        )

    def log_learning_observations(
        self,
        frame: pd.DataFrame,
        source: str,
        product: str,
        observed_at: datetime | None = None,
        traded: bool = False,
        trade_reference: str | None = None,
    ) -> int:
        """Persist one full-fidelity signal snapshot per five-minute source bucket."""
        if frame is None or frame.empty:
            return 0
        timestamp = observed_at or datetime.now(tz=self._market_tz)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc).astimezone(self._market_tz)
        else:
            timestamp = timestamp.astimezone(self._market_tz)
        bucket = timestamp.replace(minute=(timestamp.minute // 5) * 5, second=0, microsecond=0)
        source_text = str(source or "unknown").strip() or "unknown"
        product_text = str(product or "stock").strip().lower() or "stock"
        inserted = 0

        def safe_float(value):
            try:
                numeric = float(value)
                return None if pd.isna(numeric) else numeric
            except (TypeError, ValueError):
                return None

        with self._connect() as connection:
            for raw in frame.to_dict("records"):
                symbol = str(
                    raw.get("symbol")
                    or raw.get("underlying")
                    or raw.get("underlying_symbol")
                    or ""
                ).strip().upper()
                if not symbol:
                    continue
                contract = str(
                    raw.get("contract")
                    or raw.get("option_symbol")
                    or raw.get("selected_option_symbol")
                    or ""
                ).strip().upper()
                setup_name = str(
                    raw.get("setup_name")
                    or raw.get("stock_setup_name")
                    or raw.get("setup_type")
                    or ""
                ).strip()
                session_name = str(raw.get("session_name") or raw.get("session") or "").strip()
                if product_text == "option":
                    entry_price = safe_float(
                        raw.get("underlying_price")
                        or raw.get("last_price")
                        or raw.get("entry")
                    )
                else:
                    entry_price = safe_float(
                        raw.get("entry")
                        or raw.get("last_price")
                        or raw.get("underlying_price")
                        or raw.get("entry_price")
                    )
                stop_price = safe_float(raw.get("stop_loss") or raw.get("stop_price"))
                target_price = safe_float(
                    raw.get("target")
                    or raw.get("target_price")
                    or raw.get("underlying_target_1_strike")
                )
                probability = safe_float(raw.get("model_win_probability"))
                model_name = str(
                    raw.get("predictive_model_name")
                    or raw.get("ai_model_name")
                    or "shadow-untrained"
                ).strip()
                fast_score = int(
                    safe_float(raw.get("fast_momentum_score") or raw.get("stock_fast_momentum_score")) or 0
                )
                cohort = str(raw.get("learning_cohort") or raw.get("cohort") or "mag7").strip().lower()
                cohort = cohort if cohort in {"mag7", "watchlist"} else "watchlist"
                key = "|".join([bucket.isoformat(), cohort, source_text, product_text, symbol, contract or "-"])
                existing = connection.execute(
                    "SELECT id, features_json FROM learning_observations WHERE observation_key = ?",
                    (key,),
                ).fetchone()
                feature_record = dict(raw)
                if existing is not None:
                    try:
                        original_features = json.loads(existing["features_json"] or "{}")
                    except Exception:
                        original_features = {}
                    for field, value in original_features.items():
                        if str(field).startswith("catalyst_shadow_"):
                            feature_record[field] = value
                features_json = json.dumps(feature_record, default=str)
                connection.execute(
                    """
                    INSERT INTO learning_observations (
                        observation_key, observed_at, last_seen_at, bucket_at, source, cohort, product,
                        symbol, contract, setup_name, session_name, entry_price, stop_price,
                        target_price, model_name, model_probability, fast_momentum_score,
                        traded, trade_reference, features_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(observation_key) DO UPDATE SET
                        last_seen_at = excluded.last_seen_at,
                        traded = MAX(learning_observations.traded, excluded.traded),
                        trade_reference = COALESCE(excluded.trade_reference, learning_observations.trade_reference),
                        model_name = excluded.model_name,
                        model_probability = excluded.model_probability,
                        fast_momentum_score = excluded.fast_momentum_score,
                        features_json = excluded.features_json
                    """,
                    (
                        key,
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                        bucket.isoformat(),
                        source_text,
                        cohort,
                        product_text,
                        symbol,
                        contract or None,
                        setup_name or None,
                        session_name or None,
                        entry_price,
                        stop_price,
                        target_price,
                        model_name or None,
                        probability,
                        fast_score,
                        int(bool(traded)),
                        trade_reference,
                        features_json,
                    ),
                )
                stored = connection.execute(
                    "SELECT id, entry_price FROM learning_observations WHERE observation_key = ?",
                    (key,),
                ).fetchone()
                if existing is None:
                    inserted += 1
                if stored is not None and float(stored["entry_price"] or 0.0) > 0:
                    for horizon in (5, 15, 30, 60):
                        due_at = timestamp + timedelta(minutes=horizon)
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO learning_horizon_outcomes (
                                observation_id, horizon_minutes, due_at, label_method
                            ) VALUES (?, ?, ?, 'horizon_mark')
                            """,
                            (int(stored["id"]), horizon, due_at.isoformat()),
                        )
        return inserted
    def get_due_learning_outcomes(self, limit: int = 500) -> pd.DataFrame:
        return self._query_frame(
            """
            SELECT h.id AS outcome_id, h.observation_id, h.horizon_minutes, h.due_at,
                   o.symbol, o.entry_price, o.target_price, o.stop_price, o.product
            FROM learning_horizon_outcomes h
            JOIN learning_observations o ON o.id = h.observation_id
            WHERE h.resolved_at IS NULL AND h.due_at <= ? AND o.entry_price > 0
            ORDER BY h.due_at ASC
            LIMIT ?
            """,
            (datetime.now(tz=self._market_tz).isoformat(), max(int(limit), 1)),
        )

    def resolve_learning_outcome(self, outcome_id: int, future_price: float, resolved_at: datetime | None = None) -> None:
        resolved = resolved_at or datetime.now(tz=self._market_tz)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT h.id, o.entry_price FROM learning_horizon_outcomes h
                JOIN learning_observations o ON o.id = h.observation_id
                WHERE h.id = ?
                """,
                (int(outcome_id),),
            ).fetchone()
            if row is None or float(row["entry_price"] or 0) <= 0:
                return
            entry = float(row["entry_price"])
            price = float(future_price)
            return_pct = ((price - entry) / entry) * 100.0
            connection.execute(
                """
                UPDATE learning_horizon_outcomes
                SET resolved_at = ?, future_price = ?, return_pct = ?, label_win = ?
                WHERE id = ?
                """,
                (resolved.isoformat(), price, round(return_pct, 6), int(return_pct > 0), int(outcome_id)),
            )

    def prune_learning_to_symbols(self, allowed_symbols: list[str] | None) -> int:
        symbols = sorted({str(symbol or "").strip().upper() for symbol in (allowed_symbols or []) if str(symbol or "").strip()})
        if not symbols:
            return 0
        placeholders = ",".join("?" for _ in symbols)
        with self._connect() as connection:
            observation_ids = connection.execute(
                f"SELECT id FROM learning_observations WHERE UPPER(symbol) NOT IN ({placeholders})",
                symbols,
            ).fetchall()
            ids = [int(row["id"]) for row in observation_ids]
            if ids:
                id_placeholders = ",".join("?" for _ in ids)
                connection.execute(
                    f"DELETE FROM learning_horizon_outcomes WHERE observation_id IN ({id_placeholders})",
                    ids,
                )
                connection.execute(
                    f"DELETE FROM learning_observations WHERE id IN ({id_placeholders})",
                    ids,
                )
            trade_cursor = connection.execute(
                f"DELETE FROM learning_trade_outcomes WHERE UPPER(symbol) NOT IN ({placeholders})",
                symbols,
            )
            removed = len(ids) + max(int(trade_cursor.rowcount or 0), 0)
            if removed:
                connection.execute("DELETE FROM learning_models")
            return removed

    def sync_learning_trade_outcomes(
        self,
        allowed_symbols: list[str] | None = None,
        symbol_cohorts: dict[str, str] | None = None,
    ) -> int:
        inserted = 0
        symbol_scope = {
            str(symbol or "").strip().upper()
            for symbol in (allowed_symbols or [])
            if str(symbol or "").strip()
        }
        cohort_map = {
            str(symbol or "").strip().upper(): str(cohort or "watchlist").strip().lower()
            for symbol, cohort in (symbol_cohorts or {}).items()
            if str(symbol or "").strip()
        }
        sources = [
            ("stock", "trades", "symbol", None),
            ("option", "option_trades", "underlying_symbol", "option_symbol"),
        ]
        with self._connect() as connection:
            for product, table, symbol_column, contract_column in sources:
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE closed_at IS NOT NULL AND COALESCE(account_mode, 'paper') = 'paper' ORDER BY closed_at ASC"
                ).fetchall()
                for row in rows:
                    symbol = str(row[symbol_column] or "").strip().upper()
                    if symbol_scope and symbol not in symbol_scope:
                        continue
                    cohort = cohort_map.get(symbol, "mag7")
                    account_profile_id = str(row["account_profile_id"] or "").strip().lower() if "account_profile_id" in row.keys() else ""
                    if account_profile_id:
                        expected_profile_id = (
                            settings.stock_account_profile_id("paper")
                            if product == "stock"
                            else (
                                settings.option_account_profile_id("paper")
                                if cohort == "mag7"
                                else settings.watchlist_option_account_profile_id("paper")
                            )
                        )
                        if account_profile_id != expected_profile_id:
                            continue
                    trade_id = int(row["id"])
                    opened_at = row["opened_at"]
                    closed_at = row["closed_at"]
                    hold_minutes = None
                    try:
                        opened = datetime.fromisoformat(str(opened_at).replace("Z", "+00:00"))
                        closed = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
                        hold_minutes = max((closed - opened).total_seconds() / 60.0, 0.0)
                    except Exception:
                        pass
                    pnl = float(row["pnl"] or 0.0)
                    outcome_key = f"{product}:{trade_id}"
                    existing = connection.execute(
                        "SELECT id FROM learning_trade_outcomes WHERE outcome_key = ?",
                        (outcome_key,),
                    ).fetchone()
                    connection.execute(
                        """
                        INSERT INTO learning_trade_outcomes (
                            outcome_key, product, cohort, trade_row_id, symbol, contract, opened_at,
                            closed_at, pnl, label_win, hold_minutes, exit_reason, analysis_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(outcome_key) DO UPDATE SET
                            cohort = excluded.cohort,
                            closed_at = excluded.closed_at,
                            pnl = excluded.pnl,
                            label_win = excluded.label_win,
                            hold_minutes = excluded.hold_minutes,
                            exit_reason = excluded.exit_reason,
                            analysis_json = excluded.analysis_json
                        """,
                        (
                            outcome_key, product, cohort, trade_id, symbol,
                            str(row[contract_column] or "").upper() if contract_column else None,
                            opened_at, closed_at, pnl, int(pnl > 0), hold_minutes,
                            row["notes"] if "notes" in row.keys() else None,
                            row["analysis_json"] if "analysis_json" in row.keys() else None,
                        ),
                    )
                    if existing is None:
                        inserted += 1
        return inserted

    def learning_training_frame(self, horizon_minutes: int = 60) -> pd.DataFrame:
        signal_frame = self._query_frame(
            """
            SELECT o.*, h.return_pct, h.label_win, h.future_price, h.resolved_at
            FROM learning_observations o
            JOIN learning_horizon_outcomes h ON h.observation_id = o.id
            WHERE h.horizon_minutes = ?
              AND h.resolved_at IS NOT NULL
              AND COALESCE(o.traded, 0) = 0
            ORDER BY o.observed_at ASC
            """,
            (int(horizon_minutes),),
        )
        stock_trade_frame = self._query_frame(
            """
            SELECT ('stock-trade:' || id) AS observation_key,
                   opened_at AS observed_at,
                   closed_at AS last_seen_at,
                   opened_at AS bucket_at,
                   'stock_bot_trade' AS source,
                   'stock' AS product,
                   symbol,
                   NULL AS contract,
                   setup_name,
                   session_name,
                   entry_price,
                   stop_price,
                   target_price,
                   NULL AS model_name,
                   NULL AS model_probability,
                   0 AS fast_momentum_score,
                   1 AS traded,
                   client_order_id AS trade_reference,
                   analysis_json AS features_json,
                   pnl AS actual_pnl,
                   risk_amount AS actual_risk_amount,
                   closed_at AS resolved_at
            FROM trades
            WHERE closed_at IS NOT NULL
              AND COALESCE(account_mode, 'paper') = 'paper'
            ORDER BY opened_at ASC
            """
        )
        option_trade_frame = self._query_frame(
            """
            SELECT ('option-trade:' || id) AS observation_key,
                   opened_at AS observed_at,
                   closed_at AS last_seen_at,
                   opened_at AS bucket_at,
                   'option_bot_trade' AS source,
                   'option' AS product,
                   underlying_symbol AS symbol,
                   option_symbol AS contract,
                   structure AS setup_name,
                   NULL AS session_name,
                   entry_price,
                   stop_price,
                   target_price,
                   NULL AS model_name,
                   NULL AS model_probability,
                   0 AS fast_momentum_score,
                   1 AS traded,
                   client_order_id AS trade_reference,
                   analysis_json AS features_json,
                   pnl AS actual_pnl,
                   max_loss_amount AS actual_risk_amount,
                   closed_at AS resolved_at
            FROM option_trades
            WHERE closed_at IS NOT NULL
              AND COALESCE(account_mode, 'paper') = 'paper'
            ORDER BY opened_at ASC
            """
        )

        def expand_features(frame: pd.DataFrame) -> pd.DataFrame:
            if frame is None or frame.empty:
                return pd.DataFrame()
            feature_rows = []
            for row in frame.to_dict("records"):
                try:
                    features = json.loads(row.get("features_json") or "{}")
                except Exception:
                    features = {}
                feature_rows.append({**features, **row})
            return pd.DataFrame(feature_rows)

        datasets: list[pd.DataFrame] = []
        signals = expand_features(signal_frame)
        if not signals.empty:
            signals["pnl"] = pd.to_numeric(signals["return_pct"], errors="coerce").fillna(0.0)
            signals["r_multiple"] = signals["pnl"] / 2.0
            signals["label_win"] = (signals["pnl"] > 0).astype(int)
            signals["label_source"] = "scanner_60m_outcome"
            datasets.append(signals)

        for trade_frame in (stock_trade_frame, option_trade_frame):
            trades = expand_features(trade_frame)
            if trades.empty:
                continue
            trades["pnl"] = pd.to_numeric(trades["actual_pnl"], errors="coerce").fillna(0.0)
            risk = pd.to_numeric(trades["actual_risk_amount"], errors="coerce").fillna(0.0)
            trades["r_multiple"] = [
                (float(pnl) / float(risk_value)) if float(risk_value) > 0 else 0.0
                for pnl, risk_value in zip(trades["pnl"], risk)
            ]
            trades["label_win"] = (trades["pnl"] > 0).astype(int)
            trades["label_source"] = "closed_trade_outcome"
            datasets.append(trades)

        if not datasets:
            return pd.DataFrame()
        dataset = pd.concat(datasets, ignore_index=True, sort=False)
        dataset["observed_at_sort"] = pd.to_datetime(dataset["observed_at"], utc=True, errors="coerce")
        return dataset.sort_values("observed_at_sort").drop(columns=["observed_at_sort"]).reset_index(drop=True)
    def register_learning_model(self, payload: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO learning_models (
                    model_name, version, status, trained_at, training_rows, validation_rows,
                    accuracy, brier_score, win_rate, artifact_path, metrics_json, is_active
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(version) DO UPDATE SET
                    status = excluded.status,
                    training_rows = excluded.training_rows,
                    validation_rows = excluded.validation_rows,
                    accuracy = excluded.accuracy,
                    brier_score = excluded.brier_score,
                    win_rate = excluded.win_rate,
                    artifact_path = excluded.artifact_path,
                    metrics_json = excluded.metrics_json
                """,
                (
                    payload.get("model_name", "PredictiveTradeModel"), payload["version"],
                    payload.get("status", "shadow"), payload.get("trained_at") or datetime.now(timezone.utc).isoformat(),
                    int(payload.get("training_rows") or 0), int(payload.get("validation_rows") or 0),
                    payload.get("accuracy"), payload.get("brier_score"), payload.get("win_rate"),
                    payload.get("artifact_path"), json.dumps(payload.get("metrics") or {}, default=str),
                    int(bool(payload.get("is_active"))),
                ),
            )

    def start_learning_cycle(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO learning_cycles (started_at, status, message) VALUES (?, 'running', 'Learning cycle started.')",
                (datetime.now(timezone.utc).isoformat(),),
            )
            return int(cursor.lastrowid)

    def finish_learning_cycle(self, cycle_id: int, status: str, message: str, metrics: dict) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE learning_cycles SET completed_at = ?, status = ?, observations = ?,
                    resolved_outcomes = ?, trade_outcomes = ?, message = ?, metrics_json = ?
                WHERE id = ?
                """,
                (
                    datetime.now(timezone.utc).isoformat(), status,
                    int(metrics.get("observations") or 0), int(metrics.get("resolvedOutcomes") or 0),
                    int(metrics.get("tradeOutcomes") or 0), message,
                    json.dumps(metrics, default=str), int(cycle_id),
                ),
            )

    def _learning_daily_reports(self, limit_days: int = 90) -> dict:
        """Build daily ET performance and prediction-calibration reports from retained learning data."""
        observation_rows = self._query_frame(
            """
            SELECT observed_at, cohort, product, symbol, setup_name, model_probability
            FROM learning_observations
            ORDER BY observed_at DESC
            """
        )
        outcome_rows = self._query_frame(
            """
            SELECT o.observed_at, o.cohort, o.product, o.symbol, o.setup_name,
                   o.model_probability, h.return_pct, h.label_win
            FROM learning_observations o
            JOIN learning_horizon_outcomes h ON h.observation_id = o.id
            WHERE h.horizon_minutes = 60 AND h.resolved_at IS NOT NULL
            ORDER BY o.observed_at DESC
            """
        )
        trade_rows = self._query_frame(
            """
            SELECT cohort, product, symbol, contract, closed_at, pnl, label_win,
                   hold_minutes, exit_reason, analysis_json
            FROM learning_trade_outcomes
            WHERE closed_at IS NOT NULL
            ORDER BY closed_at DESC
            """
        )

        def market_date(raw_value) -> str | None:
            if raw_value in (None, "") or pd.isna(raw_value):
                return None
            try:
                timestamp = pd.Timestamp(raw_value)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.tz_localize("UTC")
                return timestamp.tz_convert(self._market_tz).date().isoformat()
            except (TypeError, ValueError):
                return None

        book_definitions = (
            ("mag7", "stock", "Mag7 Stock"),
            ("mag7", "option", "Mag7 Option"),
            ("watchlist", "stock", "Watchlist Stock"),
            ("watchlist", "option", "Watchlist 400 Option"),
        )
        daily: dict[str, dict] = {}

        def ensure_day(day_key: str) -> dict:
            if day_key not in daily:
                daily[day_key] = {
                    "date": day_key,
                    "observations": 0,
                    "resolved_60m": 0,
                    "signal_wins": 0,
                    "signal_return_total": 0.0,
                    "trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "pnl": 0.0,
                    "prediction_samples": 0,
                    "prediction_label_wins": 0,
                    "prediction_probability_total": 0.0,
                    "prediction_correct": 0,
                    "brier_total": 0.0,
                    "symbol_pnl": {},
                    "symbol_returns": {},
                    "setup_returns": {},
                    "books": {
                        f"{cohort}:{product}": {
                            "cohort": cohort,
                            "product": product,
                            "label": label,
                            "trades": 0,
                            "wins": 0,
                            "losses": 0,
                            "pnl": 0.0,
                            "resolved_60m": 0,
                            "signal_wins": 0,
                            "return_total": 0.0,
                        }
                        for cohort, product, label in book_definitions
                    },
                }
            return daily[day_key]

        for row in observation_rows.to_dict("records") if not observation_rows.empty else []:
            day_key = market_date(row.get("observed_at"))
            if day_key:
                ensure_day(day_key)["observations"] += 1

        for row in outcome_rows.to_dict("records") if not outcome_rows.empty else []:
            day_key = market_date(row.get("observed_at"))
            if not day_key:
                continue
            report = ensure_day(day_key)
            cohort = str(row.get("cohort") or "mag7")
            product = str(row.get("product") or "stock")
            book = report["books"].get(f"{cohort}:{product}")
            label_win = int(row.get("label_win") or 0)
            return_pct = float(row.get("return_pct") or 0.0)
            report["resolved_60m"] += 1
            report["signal_wins"] += label_win
            report["signal_return_total"] += return_pct
            if book is not None:
                book["resolved_60m"] += 1
                book["signal_wins"] += label_win
                book["return_total"] += return_pct
            symbol = str(row.get("symbol") or "").upper()
            if symbol:
                report["symbol_returns"].setdefault(symbol, []).append(return_pct)
            setup_name = str(row.get("setup_name") or "Unclassified").strip() or "Unclassified"
            report["setup_returns"].setdefault(setup_name, []).append(return_pct)
            probability = row.get("model_probability")
            if probability is not None and not pd.isna(probability):
                probability_value = min(max(float(probability), 0.0), 1.0)
                report["prediction_samples"] += 1
                report["prediction_label_wins"] += label_win
                report["prediction_probability_total"] += probability_value
                report["prediction_correct"] += int((probability_value >= 0.5) == bool(label_win))
                report["brier_total"] += (probability_value - label_win) ** 2

        for row in trade_rows.to_dict("records") if not trade_rows.empty else []:
            day_key = market_date(row.get("closed_at"))
            if not day_key:
                continue
            report = ensure_day(day_key)
            cohort = str(row.get("cohort") or "mag7")
            product = str(row.get("product") or "stock")
            book = report["books"].get(f"{cohort}:{product}")
            pnl = float(row.get("pnl") or 0.0)
            is_win = int(row.get("label_win") or 0)
            report["trades"] += 1
            report["wins"] += is_win
            report["losses"] += int(not is_win)
            report["pnl"] += pnl
            if book is not None:
                book["trades"] += 1
                book["wins"] += is_win
                book["losses"] += int(not is_win)
                book["pnl"] += pnl
            symbol = str(row.get("symbol") or "").upper()
            if symbol:
                report["symbol_pnl"][symbol] = report["symbol_pnl"].get(symbol, 0.0) + pnl

        reports: list[dict] = []
        for day_key in sorted(daily, reverse=True)[:max(int(limit_days), 1)]:
            raw = daily[day_key]
            resolved = int(raw["resolved_60m"])
            trades = int(raw["trades"])
            samples = int(raw["prediction_samples"])
            predicted_win_rate = (
                (float(raw["prediction_probability_total"]) / samples) * 100.0 if samples else None
            )
            realized_prediction_win_rate = (
                (float(raw["prediction_label_wins"]) / samples) * 100.0 if samples else None
            )
            symbol_scores = raw["symbol_pnl"] or {
                symbol: sum(values) / max(len(values), 1)
                for symbol, values in raw["symbol_returns"].items()
            }
            setup_scores = {
                setup: sum(values) / max(len(values), 1)
                for setup, values in raw["setup_returns"].items()
            }
            best_symbol = max(symbol_scores, key=symbol_scores.get) if symbol_scores else None
            worst_symbol = min(symbol_scores, key=symbol_scores.get) if symbol_scores else None
            best_setup = max(setup_scores, key=setup_scores.get) if setup_scores else None
            worst_setup = min(setup_scores, key=setup_scores.get) if setup_scores else None
            book_rows: list[dict] = []
            for cohort, product, _label in book_definitions:
                book = raw["books"][f"{cohort}:{product}"]
                book_trades = int(book["trades"])
                book_resolved = int(book["resolved_60m"])
                book_rows.append({
                    **{key: value for key, value in book.items() if key != "return_total"},
                    "pnl": round(float(book["pnl"]), 2),
                    "win_rate": round((float(book["wins"]) / book_trades) * 100.0, 2) if book_trades else None,
                    "signal_win_rate": round((float(book["signal_wins"]) / book_resolved) * 100.0, 2) if book_resolved else None,
                    "avg_return_60m": round(float(book["return_total"]) / book_resolved, 4) if book_resolved else None,
                })
            calibration_gap = (
                abs(predicted_win_rate - realized_prediction_win_rate)
                if predicted_win_rate is not None and realized_prediction_win_rate is not None
                else None
            )
            verdict = "Collecting evidence"
            trade_win_rate_value = (float(raw["wins"]) / trades) * 100.0 if trades else None
            signal_win_rate_value = (float(raw["signal_wins"]) / resolved) * 100.0 if resolved else None
            average_signal_return = float(raw["signal_return_total"]) / resolved if resolved else None
            if trades >= 5:
                verdict = "Strong session" if trade_win_rate_value >= 60 and raw["pnl"] > 0 else "Mixed session" if raw["pnl"] >= 0 else "Needs review"
            elif resolved >= 10:
                verdict = "Signals followed through" if realized_prediction_win_rate >= 55 else "Signal quality needs review"

            def signed_money(value: float) -> str:
                amount = float(value or 0.0)
                sign = "+" if amount > 0 else "-" if amount < 0 else ""
                return f"{sign}${abs(amount):,.2f}"

            active_books = [row for row in book_rows if int(row.get("trades") or 0) > 0]
            strongest_book = max(active_books, key=lambda row: float(row.get("pnl") or 0.0)) if active_books else None
            weakest_book = min(active_books, key=lambda row: float(row.get("pnl") or 0.0)) if active_books else None
            explanation_parts: list[str] = []
            if trades:
                explanation_parts.append(
                    f"Closed {trades} paper trades with {int(raw['wins'])} wins and {int(raw['losses'])} losses "
                    f"({trade_win_rate_value:.2f}% win rate) for {signed_money(raw['pnl'])}."
                )
            else:
                explanation_parts.append("No paper trades closed during this learning day.")
            if resolved:
                explanation_parts.append(
                    f"Scanner signals followed through after 60 minutes {signal_win_rate_value:.2f}% of the time "
                    f"with an average return of {average_signal_return:+.4f}%."
                )
            if weakest_book and float(weakest_book.get("pnl") or 0.0) < 0:
                explanation_parts.append(
                    f"{weakest_book['label']} was the largest drag at {signed_money(weakest_book['pnl'])}."
                )
            elif strongest_book and float(strongest_book.get("pnl") or 0.0) > 0:
                explanation_parts.append(
                    f"{strongest_book['label']} led realized performance at {signed_money(strongest_book['pnl'])}."
                )

            what_worked: list[str] = []
            if strongest_book and float(strongest_book.get("pnl") or 0.0) > 0:
                what_worked.append(
                    f"{strongest_book['label']}: {int(strongest_book['wins'])} wins from {int(strongest_book['trades'])} trades, "
                    f"{signed_money(strongest_book['pnl'])}."
                )
            if best_symbol and float(symbol_scores[best_symbol]) > 0:
                if raw["symbol_pnl"]:
                    what_worked.append(f"Best symbol {best_symbol}: {signed_money(symbol_scores[best_symbol])}.")
                else:
                    what_worked.append(f"Best symbol {best_symbol}: {float(symbol_scores[best_symbol]):+.4f}% average 60-minute return.")
            if best_setup and float(setup_scores[best_setup]) > 0:
                what_worked.append(
                    f"Best setup {best_setup}: {float(setup_scores[best_setup]):+.4f}% average 60-minute return."
                )
            if not what_worked:
                what_worked.append("Evidence is still being collected; no repeatable winner is confirmed yet.")

            what_failed: list[str] = []
            if weakest_book and float(weakest_book.get("pnl") or 0.0) < 0:
                what_failed.append(
                    f"{weakest_book['label']}: {int(weakest_book['losses'])} losses from {int(weakest_book['trades'])} trades, "
                    f"{signed_money(weakest_book['pnl'])}."
                )
            if worst_symbol and worst_symbol != best_symbol and float(symbol_scores[worst_symbol]) < 0:
                if raw["symbol_pnl"]:
                    what_failed.append(f"Review symbol {worst_symbol}: {signed_money(symbol_scores[worst_symbol])}.")
                else:
                    what_failed.append(f"Review symbol {worst_symbol}: {float(symbol_scores[worst_symbol]):+.4f}% average 60-minute return.")
            if worst_setup and worst_setup != best_setup and float(setup_scores[worst_setup]) < 0:
                what_failed.append(
                    f"Setup to review {worst_setup}: {float(setup_scores[worst_setup]):+.4f}% average 60-minute return."
                )
            execution_gap = (
                signal_win_rate_value - trade_win_rate_value
                if signal_win_rate_value is not None and trade_win_rate_value is not None
                else None
            )
            if execution_gap is not None and execution_gap >= 10:
                what_failed.append(
                    f"Execution trailed scanner follow-through by {execution_gap:.2f} percentage points, indicating entry, contract, or exit quality needs review."
                )
            if not what_failed:
                what_failed.append("No dominant failure pattern was confirmed for this day.")

            next_session_focus: list[str] = []
            if weakest_book and str(weakest_book.get("product")) == "option" and float(weakest_book.get("pnl") or 0.0) < 0:
                next_session_focus.append(
                    "Review option strike, delta, bid/ask spread, volume, open interest, and exit handling before changing the underlying signal rules."
                )
            if execution_gap is not None and execution_gap >= 10:
                next_session_focus.append(
                    "Compare each submitted trade with its scanner snapshot to isolate where valid signals became losing executions."
                )
            if calibration_gap is not None and calibration_gap >= 10:
                next_session_focus.append(
                    f"Keep the shadow model advisory; its prediction calibration gap was {calibration_gap:.2f} percentage points."
                )
            if worst_setup:
                next_session_focus.append(f"Review {worst_setup} before allowing it to contribute more tickets.")
            if not next_session_focus:
                next_session_focus.append("Continue collecting outcomes without changing live rules from a single day of evidence.")

            reports.append({
                "date": day_key,
                "verdict": verdict,
                "explanation": " ".join(explanation_parts),
                "what_worked": what_worked[:4],
                "what_failed": what_failed[:4],
                "next_session_focus": next_session_focus[:4],
                "observations": int(raw["observations"]),
                "resolved_60m": resolved,
                "signal_wins": int(raw["signal_wins"]),
                "signal_win_rate": round((float(raw["signal_wins"]) / resolved) * 100.0, 2) if resolved else None,
                "avg_return_60m": round(float(raw["signal_return_total"]) / resolved, 4) if resolved else None,
                "trades": trades,
                "wins": int(raw["wins"]),
                "losses": int(raw["losses"]),
                "trade_win_rate": round(trade_win_rate_value, 2) if trade_win_rate_value is not None else None,
                "pnl": round(float(raw["pnl"]), 2),
                "prediction_samples": samples,
                "prediction_coverage_pct": round((samples / resolved) * 100.0, 2) if resolved else 0.0,
                "predicted_win_rate": round(predicted_win_rate, 2) if predicted_win_rate is not None else None,
                "realized_prediction_win_rate": round(realized_prediction_win_rate, 2) if realized_prediction_win_rate is not None else None,
                "prediction_accuracy": round((float(raw["prediction_correct"]) / samples) * 100.0, 2) if samples else None,
                "calibration_gap": round(calibration_gap, 2) if calibration_gap is not None else None,
                "brier_score": round(float(raw["brier_total"]) / samples, 4) if samples else None,
                "best_symbol": best_symbol,
                "best_symbol_value": round(float(symbol_scores.get(best_symbol, 0.0)), 2) if best_symbol else None,
                "worst_symbol": worst_symbol,
                "worst_symbol_value": round(float(symbol_scores.get(worst_symbol, 0.0)), 2) if worst_symbol else None,
                "symbol_value_type": "pnl" if raw["symbol_pnl"] else "return_pct",
                "best_setup": best_setup,
                "best_setup_return": round(float(setup_scores.get(best_setup, 0.0)), 4) if best_setup else None,
                "worst_setup": worst_setup,
                "worst_setup_return": round(float(setup_scores.get(worst_setup, 0.0)), 4) if worst_setup else None,
                "books": book_rows,
            })

        prediction_reports = [row for row in reports if row["prediction_samples"] > 0]
        prediction_samples = sum(int(row["prediction_samples"]) for row in prediction_reports)
        weighted_brier = (
            sum(float(row["brier_score"]) * int(row["prediction_samples"]) for row in prediction_reports)
            / prediction_samples
            if prediction_samples else None
        )
        return {
            "dailyReports": reports,
            "modelDiagnostics": {
                "predictionDays": len(prediction_reports),
                "predictionSamples": prediction_samples,
                "brierScore": round(weighted_brier, 4) if weighted_brier is not None else None,
                "latestCalibrationGap": prediction_reports[0].get("calibration_gap") if prediction_reports else None,
                "latestPredictionAccuracy": prediction_reports[0].get("prediction_accuracy") if prediction_reports else None,
                "status": "Measured" if prediction_samples >= 25 else "Collecting predictions",
                "advisoryOnly": True,
            },
        }

    def learning_status(self) -> dict:
        summary = self._query_frame(
            """
            SELECT COUNT(*) AS observations,
                   COUNT(DISTINCT CASE
                       WHEN CAST(strftime('%w', observed_at) AS INTEGER) BETWEEN 1 AND 5
                       THEN substr(observed_at, 1, 10)
                   END) AS collection_days,
                   MIN(observed_at) AS first_observation,
                   MAX(observed_at) AS last_observation,
                   SUM(CASE WHEN traded = 1 THEN 1 ELSE 0 END) AS traded_observations
            FROM learning_observations
            """
        ).iloc[0].to_dict()
        outcome_summary = self._query_frame(
            """
            SELECT COUNT(*) AS scheduled_outcomes,
                   SUM(CASE WHEN resolved_at IS NOT NULL THEN 1 ELSE 0 END) AS resolved_outcomes,
                   SUM(CASE WHEN resolved_at IS NOT NULL AND label_win = 1 THEN 1 ELSE 0 END) AS winning_outcomes,
                   SUM(CASE WHEN horizon_minutes = 60 AND resolved_at IS NOT NULL THEN 1 ELSE 0 END) AS resolved_60m
            FROM learning_horizon_outcomes
            """
        ).iloc[0].to_dict()
        trade_summary = self._query_frame(
            """
            SELECT COUNT(*) AS trade_outcomes,
                   SUM(CASE WHEN label_win = 1 THEN 1 ELSE 0 END) AS winning_trades,
                   SUM(pnl) AS total_pnl
            FROM learning_trade_outcomes
            """
        ).iloc[0].to_dict()
        cohort_signals = self._query_frame(
            """
            SELECT o.cohort,
                   COUNT(DISTINCT o.id) AS observations,
                   SUM(CASE WHEN h.horizon_minutes = 60 AND h.resolved_at IS NOT NULL THEN 1 ELSE 0 END) AS resolved_60m,
                   SUM(CASE WHEN h.horizon_minutes = 60 AND h.resolved_at IS NOT NULL AND h.label_win = 1 THEN 1 ELSE 0 END) AS wins_60m,
                   AVG(CASE WHEN h.horizon_minutes = 60 AND h.resolved_at IS NOT NULL THEN h.return_pct END) AS avg_return_60m
            FROM learning_observations o
            LEFT JOIN learning_horizon_outcomes h ON h.observation_id = o.id
            GROUP BY o.cohort
            """
        )
        cohort_trades = self._query_frame(
            """
            SELECT cohort, COUNT(*) AS trades,
                   SUM(CASE WHEN label_win = 1 THEN 1 ELSE 0 END) AS trade_wins,
                   SUM(pnl) AS trade_pnl,
                   AVG(hold_minutes) AS avg_hold_minutes
            FROM learning_trade_outcomes
            GROUP BY cohort
            """
        )
        book_trades = self._query_frame(
            """
            SELECT cohort, product, COUNT(*) AS trades,
                   SUM(CASE WHEN label_win = 1 THEN 1 ELSE 0 END) AS wins,
                   SUM(pnl) AS pnl,
                   AVG(hold_minutes) AS avg_hold_minutes
            FROM learning_trade_outcomes
            GROUP BY cohort, product
            """
        )
        cohort_records: list[dict] = []
        signal_by_cohort = {
            str(row.get("cohort") or "mag7"): row
            for row in cohort_signals.to_dict("records")
        } if not cohort_signals.empty else {}
        trade_by_cohort = {
            str(row.get("cohort") or "mag7"): row
            for row in cohort_trades.to_dict("records")
        } if not cohort_trades.empty else {}
        for cohort in ("mag7", "watchlist"):
            signal = signal_by_cohort.get(cohort, {})
            trade = trade_by_cohort.get(cohort, {})
            resolved_count = int(signal.get("resolved_60m") or 0)
            trade_count = int(trade.get("trades") or 0)
            cohort_records.append({
                "cohort": cohort,
                "label": "Mag7" if cohort == "mag7" else "Watchlist 400",
                "observations": int(signal.get("observations") or 0),
                "resolved_60m": resolved_count,
                "wins_60m": int(signal.get("wins_60m") or 0),
                "win_rate_60m": round((float(signal.get("wins_60m") or 0) / resolved_count) * 100, 2) if resolved_count else None,
                "avg_return_60m": round(float(signal.get("avg_return_60m") or 0.0), 4) if resolved_count else None,
                "trades": trade_count,
                "trade_wins": int(trade.get("trade_wins") or 0),
                "trade_win_rate": round((float(trade.get("trade_wins") or 0) / trade_count) * 100, 2) if trade_count else None,
                "trade_pnl": round(float(trade.get("trade_pnl") or 0.0), 2),
                "avg_hold_minutes": round(float(trade.get("avg_hold_minutes") or 0.0), 2) if trade_count else None,
            })
        book_trade_map = {
            (str(row.get("cohort") or "mag7"), str(row.get("product") or "stock")): row
            for row in book_trades.to_dict("records")
        } if not book_trades.empty else {}
        learning_books: list[dict] = []
        for cohort, product, label, initial_target, advanced_target in (
            ("mag7", "stock", "Mag7 Stock", 100, 100),
            ("mag7", "option", "Mag7 Option", 100, 100),
            ("watchlist", "stock", "Watchlist Stock", 100, 100),
            ("watchlist", "option", "Watchlist 400 Option", 50, 150),
        ):
            row = book_trade_map.get((cohort, product), {})
            count = int(row.get("trades") or 0)
            wins = int(row.get("wins") or 0)
            learning_books.append({
                "cohort": cohort,
                "product": product,
                "label": label,
                "trades": count,
                "wins": wins,
                "win_rate": round((wins / count) * 100, 2) if count else None,
                "pnl": round(float(row.get("pnl") or 0.0), 2),
                "avg_hold_minutes": round(float(row.get("avg_hold_minutes") or 0.0), 2) if count else None,
                "initial_target": initial_target,
                "advanced_target": advanced_target,
                "initial_ready": count >= initial_target,
                "advanced_ready": count >= advanced_target,
                "progress_pct": min(round((count / max(advanced_target, 1)) * 100, 2), 100.0),
            })
        source_rows = self._query_frame(
            """
            SELECT o.source, o.product, COUNT(DISTINCT o.id) AS observations,
                   SUM(CASE WHEN h.horizon_minutes = 60 AND h.resolved_at IS NOT NULL THEN 1 ELSE 0 END) AS resolved_60m,
                   SUM(CASE WHEN h.horizon_minutes = 60 AND h.resolved_at IS NOT NULL AND h.label_win = 1 THEN 1 ELSE 0 END) AS wins_60m,
                   AVG(CASE WHEN h.horizon_minutes = 60 AND h.resolved_at IS NOT NULL THEN h.return_pct END) AS avg_return_60m
            FROM learning_observations o
            LEFT JOIN learning_horizon_outcomes h ON h.observation_id = o.id
            GROUP BY o.source, o.product
            ORDER BY observations DESC
            """
        )
        if not source_rows.empty:
            source_rows["win_rate_60m"] = source_rows.apply(
                lambda row: round(
                    (float(row.get("wins_60m") or 0) / float(row.get("resolved_60m") or 1)) * 100,
                    2,
                ) if float(row.get("resolved_60m") or 0) > 0 else None,
                axis=1,
            )
            source_rows["avg_return_60m"] = pd.to_numeric(
                source_rows["avg_return_60m"], errors="coerce"
            ).round(4)
        horizon_rows = self._query_frame(
            """
            SELECT horizon_minutes,
                   COUNT(*) AS scheduled,
                   SUM(CASE WHEN resolved_at IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
                   SUM(CASE WHEN resolved_at IS NOT NULL AND label_win = 1 THEN 1 ELSE 0 END) AS wins,
                   AVG(CASE WHEN resolved_at IS NOT NULL THEN return_pct END) AS avg_return_pct
            FROM learning_horizon_outcomes
            GROUP BY horizon_minutes
            ORDER BY horizon_minutes
            """
        )
        if not horizon_rows.empty:
            horizon_rows["win_rate"] = horizon_rows.apply(
                lambda row: round(
                    (float(row.get("wins") or 0) / float(row.get("resolved") or 1)) * 100,
                    2,
                ) if float(row.get("resolved") or 0) > 0 else None,
                axis=1,
            )
            horizon_rows["avg_return_pct"] = pd.to_numeric(
                horizon_rows["avg_return_pct"], errors="coerce"
            ).round(4)
        trend_rows = self._query_frame(
            """
            SELECT substr(o.observed_at, 1, 10) AS observation_date,
                   COUNT(*) AS resolved,
                   SUM(CASE WHEN h.label_win = 1 THEN 1 ELSE 0 END) AS wins,
                   AVG(h.return_pct) AS avg_return_pct
            FROM learning_observations o
            JOIN learning_horizon_outcomes h ON h.observation_id = o.id
            WHERE h.horizon_minutes = 60 AND h.resolved_at IS NOT NULL
            GROUP BY substr(o.observed_at, 1, 10)
            ORDER BY observation_date DESC
            LIMIT 30
            """
        )
        if not trend_rows.empty:
            trend_rows["win_rate"] = trend_rows.apply(
                lambda row: round(
                    (float(row.get("wins") or 0) / float(row.get("resolved") or 1)) * 100,
                    2,
                ),
                axis=1,
            )
            trend_rows["avg_return_pct"] = pd.to_numeric(
                trend_rows["avg_return_pct"], errors="coerce"
            ).round(4)
            trend_rows = trend_rows.iloc[::-1].reset_index(drop=True)
        trade_breakdown = self._query_frame(
            """
            SELECT product, COUNT(*) AS trades,
                   SUM(CASE WHEN label_win = 1 THEN 1 ELSE 0 END) AS wins,
                   SUM(pnl) AS pnl,
                   AVG(hold_minutes) AS avg_hold_minutes
            FROM learning_trade_outcomes
            GROUP BY product
            ORDER BY product
            """
        )
        if not trade_breakdown.empty:
            trade_breakdown["win_rate"] = trade_breakdown.apply(
                lambda row: round(
                    (float(row.get("wins") or 0) / float(row.get("trades") or 1)) * 100,
                    2,
                ),
                axis=1,
            )
        momentum_rows = self._query_frame(
            """
            SELECT fast_momentum_score, COUNT(*) AS observations
            FROM learning_observations
            GROUP BY fast_momentum_score
            ORDER BY fast_momentum_score DESC
            """
        )
        recent = self._query_frame(
            """
            SELECT id, observed_at, last_seen_at, source, cohort, product, symbol, contract,
                   setup_name, entry_price, model_name, model_probability,
                   fast_momentum_score, traded
            FROM learning_observations
            ORDER BY observed_at DESC
            LIMIT 50
            """
        )
        recent_trades = self._query_frame(
            """
            SELECT cohort, product, symbol, contract, opened_at, closed_at, pnl,
                   label_win, hold_minutes, exit_reason
            FROM learning_trade_outcomes
            ORDER BY closed_at DESC
            LIMIT 30
            """
        )
        ticker_observations = self._query_frame(
            """
            SELECT cohort, product, symbol,
                   COUNT(*) AS observations,
                   SUM(CASE WHEN traded = 1 THEN 1 ELSE 0 END) AS traded_observations,
                   MIN(observed_at) AS first_observed_at,
                   MAX(last_seen_at) AS last_observed_at
            FROM learning_observations
            GROUP BY cohort, product, symbol
            """
        )
        ticker_horizons = self._query_frame(
            """
            SELECT o.cohort, o.product, o.symbol, h.horizon_minutes,
                   SUM(CASE WHEN h.resolved_at IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
                   SUM(CASE WHEN h.resolved_at IS NOT NULL AND h.label_win = 1 THEN 1 ELSE 0 END) AS wins,
                   AVG(CASE WHEN h.resolved_at IS NOT NULL THEN h.return_pct END) AS avg_return_pct,
                   MAX(CASE WHEN h.resolved_at IS NOT NULL THEN h.return_pct END) AS best_return_pct
            FROM learning_observations o
            JOIN learning_horizon_outcomes h ON h.observation_id = o.id
            GROUP BY o.cohort, o.product, o.symbol, h.horizon_minutes
            """
        )
        ticker_trades = self._query_frame(
            """
            SELECT cohort, product, symbol,
                   COUNT(*) AS trades,
                   SUM(CASE WHEN label_win = 1 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN label_win = 0 THEN 1 ELSE 0 END) AS losses,
                   SUM(pnl) AS pnl,
                   AVG(hold_minutes) AS avg_hold_minutes,
                   MAX(closed_at) AS last_trade_at
            FROM learning_trade_outcomes
            GROUP BY cohort, product, symbol
            """
        )
        ticker_latest = self._query_frame(
            """
            WITH ranked AS (
                SELECT id, observed_at, last_seen_at, source, cohort, product, symbol,
                       contract, setup_name, session_name, entry_price,
                       fast_momentum_score, traded, features_json,
                       ROW_NUMBER() OVER (
                           PARTITION BY cohort, product, symbol
                           ORDER BY julianday(last_seen_at) DESC, id DESC
                       ) AS row_number
                FROM learning_observations
            )
            SELECT * FROM ranked WHERE row_number = 1
            """
        )
        ticker_latest_outcomes = self._query_frame(
            """
            WITH ranked AS (
                SELECT id, cohort, product, symbol,
                       ROW_NUMBER() OVER (
                           PARTITION BY cohort, product, symbol
                           ORDER BY julianday(last_seen_at) DESC, id DESC
                       ) AS row_number
                FROM learning_observations
            )
            SELECT r.cohort, r.product, r.symbol, h.horizon_minutes,
                   h.return_pct, h.label_win, h.resolved_at
            FROM ranked r
            JOIN learning_horizon_outcomes h ON h.observation_id = r.id
            WHERE r.row_number = 1
            """
        )

        def ticker_key(row: dict) -> tuple[str, str, str]:
            return (
                str(row.get("cohort") or "mag7"),
                str(row.get("product") or "stock"),
                str(row.get("symbol") or "").upper(),
            )

        def finite_number(value, digits: int = 2):
            if value is None or pd.isna(value):
                return None
            return round(float(value), digits)

        horizon_map: dict[tuple[str, str, str], dict[int, dict]] = {}
        for raw in ticker_horizons.to_dict("records") if not ticker_horizons.empty else []:
            resolved_count = int(raw.get("resolved") or 0)
            wins_count = int(raw.get("wins") or 0)
            horizon_map.setdefault(ticker_key(raw), {})[int(raw.get("horizon_minutes") or 0)] = {
                "resolved": resolved_count,
                "wins": wins_count,
                "win_rate": round((wins_count / resolved_count) * 100, 2) if resolved_count else None,
                "avg_return_pct": finite_number(raw.get("avg_return_pct"), 4),
                "best_return_pct": finite_number(raw.get("best_return_pct"), 4),
            }
        latest_outcome_map: dict[tuple[str, str, str], dict[int, dict]] = {}
        for raw in ticker_latest_outcomes.to_dict("records") if not ticker_latest_outcomes.empty else []:
            latest_outcome_map.setdefault(ticker_key(raw), {})[int(raw.get("horizon_minutes") or 0)] = {
                "return_pct": finite_number(raw.get("return_pct"), 4),
                "label_win": int(raw.get("label_win")) if raw.get("label_win") is not None and not pd.isna(raw.get("label_win")) else None,
                "resolved_at": raw.get("resolved_at"),
            }
        trade_map = {
            ticker_key(row): row
            for row in ticker_trades.to_dict("records")
        } if not ticker_trades.empty else {}
        latest_map = {
            ticker_key(row): row
            for row in ticker_latest.to_dict("records")
        } if not ticker_latest.empty else {}

        def explain_ticker(latest: dict) -> tuple[list[str], str]:
            try:
                features = json.loads(str(latest.get("features_json") or "{}"))
                if not isinstance(features, dict):
                    features = {}
            except (TypeError, ValueError, json.JSONDecodeError):
                features = {}
            tags: list[str] = []
            setup_name = str(latest.get("setup_name") or features.get("setup_name") or "").strip()
            if setup_name:
                tags.append(setup_name)
            else:
                if bool(features.get("ema_stack")):
                    tags.append("EMA trend")
                if bool(features.get("above_vwap")):
                    tags.append("Above VWAP")
            price_change = features.get("session_change_pct")
            if price_change is None:
                price_change = features.get("change_pct", features.get("intraday_change_pct"))
            if price_change is not None and not pd.isna(price_change):
                tags.append(f"Live change {float(price_change):+.2f}%")
            rvol_values: list[str] = []
            for label, field in (
                ("15m", "tos_rvol_15m"), ("30m", "tos_rvol_30m"),
                ("1H", "tos_rvol_1h"), ("2H", "tos_rvol_2h"),
                ("4H", "tos_rvol_4h"), ("D", "tos_rvol_1d"),
            ):
                value = features.get(field)
                if value is not None and not pd.isna(value) and float(value) >= 0.4:
                    rvol_values.append(f"{label} {float(value):.2f}")
            if rvol_values:
                tags.append("RVOL " + ", ".join(rvol_values[:3]))
            momentum_score = int(latest.get("fast_momentum_score") or features.get("fast_momentum_score") or 0)
            if momentum_score:
                tags.append(f"Fast momentum {momentum_score}/3")
            if bool(features.get("volume_trend")):
                tags.append("Volume accelerating")
            flow = str(features.get("flow_type") or "").strip()
            liquidity = str(features.get("liquidity_winner") or "").strip()
            priority = str(features.get("priority_label") or features.get("oi_priority_label") or "").strip()
            for value in (priority, flow, liquidity):
                if value and value not in tags:
                    tags.append(value)
            if not tags:
                tags.append(str(latest.get("source") or "Scanner observation"))
            return tags[:7], " | ".join(tags[:7])

        ticker_analysis: list[dict] = []
        for observation in ticker_observations.to_dict("records") if not ticker_observations.empty else []:
            key = ticker_key(observation)
            latest = latest_map.get(key, {})
            trade = trade_map.get(key, {})
            horizons = horizon_map.get(key, {})
            latest_outcomes = latest_outcome_map.get(key, {})
            why_tags, why_summary = explain_ticker(latest)
            resolved_returns = [
                float(value.get("return_pct"))
                for value in latest_outcomes.values()
                if value.get("return_pct") is not None
            ]
            best_historical_returns = [
                float(value.get("best_return_pct"))
                for value in horizons.values()
                if value.get("best_return_pct") is not None
            ]
            move_value = max(resolved_returns, default=max(best_historical_returns, default=0.0))
            move_label = "Explosive" if move_value >= 3 else "Strong" if move_value >= 1 else "Developing" if move_value > 0 else "No follow-through yet"
            trade_count = int(trade.get("trades") or 0)
            trade_wins = int(trade.get("wins") or 0)
            ticker_analysis.append({
                "key": "|".join(key),
                "cohort": key[0],
                "cohort_label": "Mag7" if key[0] == "mag7" else "Watchlist 400",
                "product": key[1],
                "symbol": key[2],
                "contract": latest.get("contract"),
                "source": latest.get("source"),
                "setup_name": latest.get("setup_name"),
                "session_name": latest.get("session_name"),
                "reference_price": finite_number(latest.get("entry_price"), 4),
                "first_observed_at": observation.get("first_observed_at"),
                "last_observed_at": observation.get("last_observed_at"),
                "observations": int(observation.get("observations") or 0),
                "traded_observations": int(observation.get("traded_observations") or 0),
                "fast_momentum_score": int(latest.get("fast_momentum_score") or 0),
                "why_tags": why_tags,
                "why_summary": why_summary,
                "move_label": move_label,
                "move_score": round(move_value, 4),
                "horizons": {str(horizon): horizons.get(horizon, {}) for horizon in (5, 15, 30, 60)},
                "latest_outcomes": {str(horizon): latest_outcomes.get(horizon, {}) for horizon in (5, 15, 30, 60)},
                "trades": trade_count,
                "trade_wins": trade_wins,
                "trade_losses": int(trade.get("losses") or 0),
                "trade_win_rate": round((trade_wins / trade_count) * 100, 2) if trade_count else None,
                "trade_pnl": finite_number(trade.get("pnl"), 2) or 0.0,
                "avg_hold_minutes": finite_number(trade.get("avg_hold_minutes"), 2),
                "last_trade_at": trade.get("last_trade_at"),
            })
        ticker_analysis.sort(
            key=lambda row: (float(row.get("move_score") or 0), str(row.get("last_observed_at") or "")),
            reverse=True,
        )
        models = self._query_frame("SELECT * FROM learning_models ORDER BY trained_at DESC LIMIT 20")
        model_records = models.to_dict("records") if not models.empty else []
        for index, model in enumerate(model_records):
            previous = model_records[index + 1] if index + 1 < len(model_records) else None
            model["accuracy_change"] = (
                round((float(model.get("accuracy") or 0) - float(previous.get("accuracy") or 0)) * 100, 2)
                if previous and model.get("accuracy") is not None and previous.get("accuracy") is not None
                else None
            )
            model["brier_change"] = (
                round(float(model.get("brier_score") or 0) - float(previous.get("brier_score") or 0), 4)
                if previous and model.get("brier_score") is not None and previous.get("brier_score") is not None
                else None
            )
        cycles = self._query_frame("SELECT * FROM learning_cycles ORDER BY started_at DESC LIMIT 20")
        observations = int(summary.get("observations") or 0)
        days = int(summary.get("collection_days") or 0)
        resolved = int(outcome_summary.get("resolved_outcomes") or 0)
        resolved_60m = int(outcome_summary.get("resolved_60m") or 0)
        trades = int(trade_summary.get("trade_outcomes") or 0)
        initial_trade_target = 50
        scanner_outcome_target = 100
        validation_trade_target = 25
        sizing_review_trade_target = 100
        first_model_at = min(
            [str(model.get("trained_at")) for model in model_records if model.get("trained_at")],
            default=None,
        )
        unseen_validation_trades = 0
        if first_model_at:
            unseen_frame = self._query_frame(
                "SELECT COUNT(*) AS trades FROM learning_trade_outcomes WHERE julianday(closed_at) > julianday(?)",
                (first_model_at,),
            )
            unseen_validation_trades = int(unseen_frame.iloc[0].get("trades") or 0)
        initial_training_ready = trades >= initial_trade_target and resolved_60m >= scanner_outcome_target
        sizing_review_ready = (
            trades >= sizing_review_trade_target
            and resolved_60m >= scanner_outcome_target
            and unseen_validation_trades >= validation_trade_target
            and bool(model_records)
        )
        phase = "Outcome Collection"
        if initial_training_ready and not model_records:
            phase = "Shadow Training Ready"
        elif model_records and not sizing_review_ready:
            phase = "Shadow Validation"
        elif sizing_review_ready:
            phase = "Model-Guided Sizing Review Ready"
        daily_learning = self._learning_daily_reports(limit_days=90)
        return {
            "mode": "advisory_only",
            "canBlockTrades": False,
            "phase": phase,
            "collectionDays": days,
            "baselineTargetTrades": initial_trade_target,
            "validationTargetTrades": validation_trade_target,
            "sizingReviewTargetTrades": sizing_review_trade_target,
            "unseenValidationTrades": unseen_validation_trades,
            "initialTrainingReady": initial_training_ready,
            "sizingReviewReady": sizing_review_ready,
            "observations": observations,
            "tradedObservations": int(summary.get("traded_observations") or 0),
            "scheduledOutcomes": int(outcome_summary.get("scheduled_outcomes") or 0),
            "resolvedOutcomes": resolved,
            "resolved60mOutcomes": resolved_60m,
            "winningOutcomes": int(outcome_summary.get("winning_outcomes") or 0),
            "tradeOutcomes": trades,
            "winningTrades": int(trade_summary.get("winning_trades") or 0),
            "tradePnl": round(float(trade_summary.get("total_pnl") or 0.0), 2),
            "firstObservation": summary.get("first_observation"),
            "lastObservation": summary.get("last_observation"),
            "retention": {
                "policy": "indefinite",
                "note": "Learning observations, forward labels, model versions, and trade outcomes are retained until manually archived.",
                "snapshotCadence": "One complete row per symbol, contract, source, and five-minute bucket; every submitted trade is retained.",
            },
            "requirements": {
                "initialTradeTraining": {"label": "Closed trades for first model", "current": trades, "target": initial_trade_target},
                "scannerOutcomes": {"label": "Resolved scanner 60m outcomes", "current": resolved_60m, "target": scanner_outcome_target},
                "unseenValidationTrades": {"label": "Unseen trades after first model", "current": unseen_validation_trades, "target": validation_trade_target},
                "sizingReviewTrades": {"label": "Closed trades for sizing review", "current": trades, "target": sizing_review_trade_target},
                "deepLearningSequences": {"label": "Deep-learning sequences", "current": observations, "target": 10000},
            },
            "sources": source_rows.to_dict("records") if not source_rows.empty else [],
            "horizonPerformance": horizon_rows.to_dict("records") if not horizon_rows.empty else [],
            "outcomeTrend": trend_rows.to_dict("records") if not trend_rows.empty else [],
            "tradeBreakdown": trade_breakdown.to_dict("records") if not trade_breakdown.empty else [],
            "cohortComparison": cohort_records,
            "bookComparison": learning_books,
            "momentumDistribution": momentum_rows.to_dict("records") if not momentum_rows.empty else [],
            "recentObservations": recent.to_dict("records") if not recent.empty else [],
            "recentTradeOutcomes": recent_trades.to_dict("records") if not recent_trades.empty else [],
            "tickerAnalysis": ticker_analysis,
            "dailyReports": daily_learning["dailyReports"],
            "modelDiagnostics": daily_learning["modelDiagnostics"],
            "models": model_records,
            "cycles": cycles.to_dict("records") if not cycles.empty else [],
        }
    def log_bot_event(self, event_type: str, message: str, payload_json: str = "") -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO bot_events (created_at, event_type, message, payload_json)
                VALUES (?, ?, ?, ?)
                """,
                (datetime.utcnow().isoformat(), event_type, message, payload_json),
            )

    def get_recent_bot_events(self, limit: int = 20) -> pd.DataFrame:
        return self._query_frame(
            f"SELECT * FROM bot_events ORDER BY created_at DESC LIMIT {int(limit)}"
        )

    def update_symbol_memory_from_trades(self, trades: pd.DataFrame) -> None:
        if trades.empty or "symbol" not in trades.columns:
            return
        with self._connect() as connection:
            for symbol, group in trades.groupby("symbol"):
                observations = int(len(group))
                wins = int((group["pnl"] > 0).sum()) if "pnl" in group.columns else 0
                losses = observations - wins
                total_pnl = float(group["pnl"].sum()) if "pnl" in group.columns else 0.0
                total_r = float(group["r_multiple"].sum()) if "r_multiple" in group.columns else 0.0
                win_rate = wins / observations if observations else 0
                confidence = max(0, min(100, 45 + (win_rate * 45) + min(total_r, 10)))
                connection.execute(
                    """
                    INSERT INTO symbol_memory (
                        symbol, observations, wins, losses, total_pnl, total_r,
                        confidence, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(symbol) DO UPDATE SET
                        observations = excluded.observations,
                        wins = excluded.wins,
                        losses = excluded.losses,
                        total_pnl = excluded.total_pnl,
                        total_r = excluded.total_r,
                        confidence = excluded.confidence,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(symbol),
                        observations,
                        wins,
                        losses,
                        total_pnl,
                        total_r,
                        confidence,
                        datetime.utcnow().isoformat(),
                    ),
                )

    def get_symbol_memory(self, limit: int = 30) -> pd.DataFrame:
        return self._query_frame(
            f"SELECT * FROM symbol_memory ORDER BY confidence DESC, observations DESC LIMIT {int(limit)}"
        )

    def get_symbol_memory_snapshot(self, symbols: list[str]) -> dict[str, dict]:
        if not symbols:
            return {}
        placeholders = ",".join("?" for _ in symbols)
        frame = self._query_frame(
            f"SELECT * FROM symbol_memory WHERE symbol IN ({placeholders})",
            tuple(symbols),
        )
        if frame.empty:
            return {}
        return {
            str(row["symbol"]).upper(): row
            for row in frame.to_dict("records")
        }

    def get_catalyst_snapshot(
        self,
        symbols: list[str],
        lookback_hours: int = 72,
        as_of: datetime | None = None,
    ) -> dict[str, dict]:
        if not symbols:
            return {}
        upper_bound = (as_of.astimezone(timezone.utc) if as_of else datetime.now(timezone.utc))
        cutoff = (upper_bound - timedelta(hours=lookback_hours)).isoformat()
        upper_bound_iso = upper_bound.isoformat()
        placeholders = ",".join("?" for _ in symbols)
        frame = self._query_frame(
            f"""
            SELECT symbol, score, sentiment, tags, headline, published_at, created_at
            FROM catalyst_items
            WHERE symbol IN ({placeholders})
            AND datetime(COALESCE(published_at, created_at)) >= datetime(?)
            AND datetime(COALESCE(published_at, created_at)) <= datetime(?)
            AND datetime(created_at) <= datetime(?)
            ORDER BY datetime(COALESCE(published_at, created_at)) DESC, datetime(created_at) DESC
            """,
            tuple(symbols) + (cutoff, upper_bound_iso, upper_bound_iso),
        )
        if frame.empty:
            return {}

        snapshot: dict[str, dict] = {}
        now = upper_bound
        for row in frame.to_dict("records"):
            symbol = str(row["symbol"]).upper()
            if symbol in snapshot:
                continue
            published_raw = row.get("published_at")
            recency_bonus = 0.0
            try:
                published = datetime.fromisoformat(str(published_raw or row.get("created_at")).replace("Z", "+00:00"))
                age_hours = max((now - published.astimezone(timezone.utc)).total_seconds() / 3600.0, 0.0)
                recency_bonus = max(0.0, 18.0 - min(age_hours, 18.0))
            except Exception:
                age_hours = None
                recency_bonus = 0.0
            snapshot[symbol] = {
                **row,
                "first_seen_at": row.get("created_at"),
                "age_hours": age_hours,
                "recency_bonus": recency_bonus,
            }
        return snapshot

    def catalyst_shadow_study_status(self) -> dict:
        cache_at = getattr(self, "_catalyst_shadow_cache_at", None)
        cached = getattr(self, "_catalyst_shadow_cache", None)
        if cached is not None and cache_at is not None:
            if datetime.now(timezone.utc) - cache_at < timedelta(seconds=60):
                return dict(cached)
        catalyst = self._query_frame(
            """
            SELECT COUNT(*) AS items,
                   COUNT(DISTINCT symbol) AS symbols,
                   COUNT(DISTINCT CASE
                       WHEN lower(COALESCE(sentiment, '')) IN ('strong', 'positive') THEN symbol || '|' || headline || '|' || COALESCE(published_at, created_at)
                   END) AS positive_events,
                   COUNT(DISTINCT CASE
                       WHEN lower(COALESCE(sentiment, '')) = 'negative' THEN symbol || '|' || headline || '|' || COALESCE(published_at, created_at)
                   END) AS negative_events,
                   COUNT(DISTINCT CASE
                       WHEN lower(COALESCE(sentiment, '')) NOT IN ('strong', 'positive', 'negative') THEN symbol || '|' || headline || '|' || COALESCE(published_at, created_at)
                   END) AS neutral_events
            FROM catalyst_items
            """
        ).iloc[0].to_dict()
        observation_frame = self._query_frame(
            "SELECT symbol, observed_at, bucket_at FROM learning_observations"
        )
        event_frame = self._query_frame(
            "SELECT symbol, published_at, created_at FROM catalyst_items"
        )
        events_by_symbol: dict[str, list[tuple[pd.Timestamp, pd.Timestamp]]] = {}
        if not event_frame.empty:
            event_frame = event_frame.copy()
            event_frame["symbol"] = event_frame["symbol"].fillna("").astype(str).str.upper()
            event_frame["published_ts"] = pd.to_datetime(
                event_frame["published_at"].fillna(event_frame["created_at"]),
                errors="coerce",
                utc=True,
            )
            event_frame["first_seen_ts"] = pd.to_datetime(
                event_frame["created_at"],
                errors="coerce",
                utc=True,
            )
            event_frame = event_frame.dropna(subset=["published_ts", "first_seen_ts"])
            for row in event_frame.itertuples(index=False):
                if row.symbol:
                    events_by_symbol.setdefault(row.symbol, []).append((row.published_ts, row.first_seen_ts))

        published_matches = 0
        safe_matches = 0
        if observation_frame.empty:
            sessions = 0
            effective_samples = 0
            observations = 0
        else:
            observation_frame = observation_frame.copy()
            observation_frame["symbol"] = observation_frame["symbol"].fillna("").astype(str).str.upper()
            observation_frame["observed_ts"] = pd.to_datetime(
                observation_frame["observed_at"],
                errors="coerce",
                utc=True,
            )
            observations = len(observation_frame)
            sessions = int(observation_frame["observed_at"].astype(str).str.slice(0, 10).nunique())
            effective_samples = int(
                (observation_frame["symbol"].astype(str) + "|" + observation_frame["bucket_at"].astype(str)).nunique()
            )
            horizon = pd.Timedelta(hours=72)
            for row in observation_frame.itertuples(index=False):
                symbol = row.symbol
                observed_at = row.observed_ts
                if not symbol or pd.isna(observed_at):
                    continue
                eligible_by_publish = [
                    (published_at, first_seen_at)
                    for published_at, first_seen_at in events_by_symbol.get(symbol, [])
                    if observed_at - horizon <= published_at <= observed_at
                ]
                if eligible_by_publish:
                    published_matches += 1
                if any(first_seen_at <= observed_at for _, first_seen_at in eligible_by_publish):
                    safe_matches += 1
        learning = {
            "observations": observations,
            "sessions": sessions,
            "effective_samples": effective_samples,
            "published_time_matches": published_matches,
            "leakage_safe_matches": safe_matches,
            "no_news_observations": max(observations - safe_matches, 0),
        }
        latest_model_frame = self._query_frame(
            """
            SELECT version, trained_at, accuracy, brier_score, win_rate, validation_rows
            FROM learning_models
            ORDER BY trained_at DESC, id DESC
            LIMIT 1
            """
        )
        latest_model = latest_model_frame.iloc[0].to_dict() if not latest_model_frame.empty else {}

        positive_events = int(catalyst.get("positive_events") or 0)
        negative_events = int(catalyst.get("negative_events") or 0)
        sessions = int(learning.get("sessions") or 0)
        effective_samples = int(learning.get("effective_samples") or 0)
        no_news = int(learning.get("no_news_observations") or 0)
        thresholds = {
            "sessions": 20,
            "effectiveSamples": 1000,
            "positiveEvents": 50,
            "negativeEvents": 50,
            "noNewsObservations": 200,
        }
        exploratory_ready = bool(
            sessions >= thresholds["sessions"]
            and effective_samples >= thresholds["effectiveSamples"]
            and positive_events >= thresholds["positiveEvents"]
            and negative_events >= thresholds["negativeEvents"]
            and no_news >= thresholds["noNewsObservations"]
        )
        model_win_rate = float(latest_model.get("win_rate") or 0.0)
        model_brier = (
            float(latest_model.get("brier_score"))
            if latest_model.get("brier_score") is not None
            else None
        )
        baseline_brier = model_win_rate * (1.0 - model_win_rate) if latest_model else None
        brier_improvement = (
            baseline_brier - model_brier
            if baseline_brier is not None and model_brier is not None
            else None
        )
        published_matches = int(learning.get("published_time_matches") or 0)
        safe_matches = int(learning.get("leakage_safe_matches") or 0)
        result = {
            "status": "READY FOR PAIRED SHADOW FIT" if exploratory_ready else "COLLECTING",
            "verdict": (
                "MEASURABLE INFORMATIONAL VALUE"
                if exploratory_ready and brier_improvement is not None and brier_improvement >= 0.01
                else "NO PROVEN EDGE"
                if latest_model
                else "COLLECTING"
            ),
            "shadowOnly": True,
            "canBlockTrades": False,
            "canRankTrades": False,
            "canSizeTrades": False,
            "canDelayExecution": False,
            "classifierVersion": "keyword-v1",
            "featurePolicy": "published_at and first_seen_at must both be at or before observation time",
            "items": int(catalyst.get("items") or 0),
            "symbols": int(catalyst.get("symbols") or 0),
            "positiveEvents": positive_events,
            "negativeEvents": negative_events,
            "neutralEvents": int(catalyst.get("neutral_events") or 0),
            "sessions": sessions,
            "effectiveSamples": effective_samples,
            "noNewsObservations": no_news,
            "publishedTimeMatches": published_matches,
            "leakageSafeMatches": safe_matches,
            "leakageExcluded": max(published_matches - safe_matches, 0),
            "thresholds": thresholds,
            "exploratoryReady": exploratory_ready,
            "latestTechnicalShadowModel": latest_model or None,
            "constantBaselineBrier": round(baseline_brier, 4) if baseline_brier is not None else None,
            "latestModelBrier": round(model_brier, 4) if model_brier is not None else None,
            "brierImprovement": round(brier_improvement, 4) if brier_improvement is not None else None,
            "message": (
                "Enough point-in-time evidence exists for a paired technical baseline versus technical-plus-catalyst shadow fit."
                if exploratory_ready
                else "Keep collecting point-in-time events. News remains descriptive until both polarities and market sessions meet the minimums."
            ),
        }
        self._catalyst_shadow_cache = result
        self._catalyst_shadow_cache_at = datetime.now(timezone.utc)
        return dict(result)

    def _query_frame(self, sql: str, params: tuple = ()) -> pd.DataFrame:
        with self._connect() as connection:
            return pd.read_sql_query(sql, connection, params=params)

    def _ensure_column(self, connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        existing = {
            row["name"]
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _migrate_scanner_history_source(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE scanner_history SET source = 'Watchlist' WHERE source IS NULL OR TRIM(source) = ''"
        )
        old_unique = False
        for index_row in connection.execute("PRAGMA index_list(scanner_history)").fetchall():
            if not int(index_row["unique"] or 0):
                continue
            index_name = str(index_row["name"])
            columns = [
                str(column_row["name"])
                for column_row in connection.execute(f"PRAGMA index_info({index_name})").fetchall()
            ]
            if columns == ["scan_date", "symbol"]:
                old_unique = True
                break
        if not old_unique:
            return

        connection.execute("ALTER TABLE scanner_history RENAME TO scanner_history_old")
        connection.execute(
            """
            CREATE TABLE scanner_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_date TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'Watchlist',
                scanned_at TEXT NOT NULL,
                scan_bucket TEXT NOT NULL,
                symbol TEXT NOT NULL,
                last_price REAL,
                one_hour_close_change_pct REAL,
                four_hour_volume_change_pct REAL,
                four_hour_current_volume REAL,
                four_hour_volume_2_bars_ago REAL,
                trigger_source TEXT,
                setup_name TEXT,
                raw_json TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO scanner_history (
                id, scan_date, source, scanned_at, scan_bucket, symbol, last_price,
                one_hour_close_change_pct, four_hour_volume_change_pct,
                four_hour_current_volume, four_hour_volume_2_bars_ago,
                trigger_source, setup_name, raw_json
            )
            SELECT
                id, scan_date, COALESCE(NULLIF(TRIM(source), ''), 'Watchlist'), scanned_at,
                COALESCE(NULLIF(TRIM(scan_bucket), ''), scanned_at), symbol, last_price,
                one_hour_close_change_pct, four_hour_volume_change_pct,
                four_hour_current_volume, four_hour_volume_2_bars_ago,
                trigger_source, setup_name, raw_json
            FROM scanner_history_old
            """
        )
        connection.execute("DROP TABLE scanner_history_old")

    def _migrate_scanner_history_events(self, connection: sqlite3.Connection) -> None:
        old_daily_unique = False
        for index_row in connection.execute("PRAGMA index_list(scanner_history)").fetchall():
            if not int(index_row["unique"] or 0):
                continue
            index_name = str(index_row["name"])
            columns = [
                str(column_row["name"])
                for column_row in connection.execute(f"PRAGMA index_info({index_name})").fetchall()
            ]
            if columns == ["scan_date", "source", "symbol"]:
                old_daily_unique = True
                break
        if not old_daily_unique:
            return

        connection.execute("ALTER TABLE scanner_history RENAME TO scanner_history_event_old")
        connection.execute(
            """
            CREATE TABLE scanner_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_date TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'Watchlist',
                scanned_at TEXT NOT NULL,
                scan_bucket TEXT NOT NULL,
                symbol TEXT NOT NULL,
                last_price REAL,
                one_hour_close_change_pct REAL,
                four_hour_volume_change_pct REAL,
                four_hour_current_volume REAL,
                four_hour_volume_2_bars_ago REAL,
                trigger_source TEXT,
                setup_name TEXT,
                raw_json TEXT
            )
            """
        )
        connection.execute(
            """
            INSERT INTO scanner_history (
                id, scan_date, source, scanned_at, scan_bucket, symbol, last_price,
                one_hour_close_change_pct, four_hour_volume_change_pct,
                four_hour_current_volume, four_hour_volume_2_bars_ago,
                trigger_source, setup_name, raw_json
            )
            SELECT
                id, scan_date, COALESCE(NULLIF(TRIM(source), ''), 'Watchlist'), scanned_at,
                COALESCE(NULLIF(TRIM(scan_bucket), ''), scanned_at), symbol, last_price,
                one_hour_close_change_pct, four_hour_volume_change_pct,
                four_hour_current_volume, four_hour_volume_2_bars_ago,
                trigger_source, setup_name, raw_json
            FROM scanner_history_event_old
            """
        )
        connection.execute("DROP TABLE scanner_history_event_old")
