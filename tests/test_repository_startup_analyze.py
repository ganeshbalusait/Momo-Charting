"""Backend startup must not read the whole option-chain history (2026-11-04).

py-spy on the trader's PC caught a restart sitting for minutes on
`ANALYZE option_chain_daily_snapshots` inside TradingRepository.initialize():
a full ANALYZE reads every row of that table and its indexes - most of a
2.7 GB database - on every boot. It is now sampled (PRAGMA analysis_limit),
and any startup step slower than a threshold names itself in the log.
"""
from __future__ import annotations

import sqlite3

import pytest

from database import repository as repository_module
from database.repository import TradingRepository


@pytest.fixture
def traced_sql(monkeypatch):
    statements: list[str] = []
    real_connect = sqlite3.connect

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_trace_callback(lambda sql: statements.append(" ".join(str(sql).split())))
        return connection

    monkeypatch.setattr(repository_module.sqlite3, "connect", connect)
    return statements


def test_analyze_is_sampled_not_a_full_table_read(tmp_path, traced_sql):
    TradingRepository(tmp_path / "trades.db")
    analyze = [i for i, sql in enumerate(traced_sql) if sql.startswith("ANALYZE option_chain_daily_snapshots")]
    limit = [i for i, sql in enumerate(traced_sql) if sql.startswith("PRAGMA analysis_limit")]
    assert analyze, "startup no longer analyzes the option chain table"
    assert limit and limit[-1] < analyze[0], "ANALYZE runs before the sampling limit is set"
    assert "PRAGMA analysis_limit = 1000" in traced_sql


def test_planner_still_uses_the_prune_index_with_sampled_stats(tmp_path):
    path = tmp_path / "trades.db"
    TradingRepository(path)
    with sqlite3.connect(path) as connection:
        rows = [
            ("2099-01-%02d" % (day + 1), "t", f"S{symbol}", "2099-02-01", side, float(strike))
            for day in range(20) for symbol in range(20) for side in ("call", "put") for strike in range(30)
        ]
        connection.executemany(
            "INSERT INTO option_chain_daily_snapshots(snapshot_date, captured_at, symbol, expiry, side, strike) VALUES (?,?,?,?,?,?)",
            rows,
        )
    TradingRepository(path)  # second boot analyzes the populated table (sampled)
    with sqlite3.connect(path) as connection:
        plan = " ".join(row[-1] for row in connection.execute(
            "EXPLAIN QUERY PLAN DELETE FROM option_chain_daily_snapshots "
            "WHERE snapshot_date = ? AND symbol = ? AND side = ? AND strike = ? AND substr(expiry, 1, 10) = ?",
            ("2099-01-01", "S1", "call", 1.0, "2099-02-01"),
        ).fetchall())
    assert "idx_option_chain_daily_prune" in plan


def test_startup_names_its_slow_steps_and_reports_total(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(TradingRepository, "SLOW_INIT_STEP_SECONDS", 0.0)
    TradingRepository(tmp_path / "trades.db")
    out = capsys.readouterr().out
    assert "[startup] database: ANALYZE option_chain_daily_snapshots (sampled) took" in out
    assert "[startup] database: prune old trade journals took" in out
    assert "[startup] database ready in" in out


def test_fast_steps_stay_quiet(tmp_path, capsys):
    TradingRepository(tmp_path / "trades.db")
    out = capsys.readouterr().out
    assert "[startup] database:" not in out          # nothing slow on an empty database
    assert out.count("[startup] database ready in") == 1
