"""Integration (2026-11-05): the real MX A+ book stamps SPY on a Mag7 board and
the worker serves that stamp over HTTP; another ETF on the same board stays
unstamped. Nothing leaves the PC."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from momx import ai_filter, etf_list, strategy
from worker_harness import running_worker

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 5, 10, 30, tzinfo=ET)


@pytest.fixture(autouse=True)
def _no_ai_scores(monkeypatch):
    monkeypatch.setattr(ai_filter, "mark_rows", lambda payload, side, now=None: None)


def row(symbol):
    return {"symbol": symbol, "industry": "ETF", "pctChange": 1.5, "last": 500.0, "scanPass": True, "scanReasons": [],
            "grade": {"letter": "A+", "checks": {"pushRvol": ["15m", "1h"], "sqzFired": ["1h"]}},
            "gradeFresh": {"firstToday": {"A+": {"at": NOW.isoformat()}}},
            "m5": {"state": "extended"}}


def test_spy_gets_its_mx_a_plus_stamp_on_the_served_mag7_board_and_iwm_does_not(tmp_path, monkeypatch):
    monkeypatch.setattr(etf_list, "refresh_in_background_if_stale", lambda: None)
    monkeypatch.setattr(etf_list, "is_etf", lambda symbol: symbol in {"SPY", "QQQ", "IWM"})
    payload = {"generatedAt": NOW.isoformat(), "list": "Mag7", "direction": "bull", "universe": ["SPY", "QQQ", "IWM"],
               "universeCount": 3, "rows": [row("SPY"), row("QQQ"), row("IWM")], "rest": [], "errors": {}}
    etf_list.mark(payload)                       # the real ETF flag step of a build
    book = strategy.StrategyBook(tmp_path / "strategy", background=False)
    book.apply("Mag7", payload, NOW)             # the real MX A+ book
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.seed_board("Mag7", payload)
        status, body = worker.get("/api/momx-scanner?list=Mag7")
    assert status == 200, body
    served = {r["symbol"]: r for r in body["rows"] + body.get("rest", [])}
    assert all(served[s].get("etf") is True for s in ("SPY", "QQQ", "IWM"))
    assert served["SPY"]["strategy"]["v2"] is True and served["SPY"]["strategy"]["daily2"]
    assert served["QQQ"]["strategy"]["v2"] is True
    assert served["IWM"]["strategy"]["v2"] is False
