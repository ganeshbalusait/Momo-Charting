"""Integration (2026-11-04 coverage pass, group charts-studies, feature
charts-grade-markers): the grade tape the chart reads for its OHLC-strip letter
(days=1) and its A+/A step markers (days=5, bull and ?dir=bear), served by the
real scanner worker handler and through api_server's /api/momx-scanner proxy.

The tapes are written by the real GradeLog recorder into the worker harness's
scratch grade folder, so nothing touches the live artifacts/momx_grade*.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from api_harness import running_api
from momx import grade_log, service
from momx.grade_log import GradeLog
from worker_harness import running_worker

ET = ZoneInfo("America/New_York")
DAY = "2026-09-21"
NOW = datetime(2026, 9, 21, 9, 32, tzinfo=ET)


def _row(symbol: str, letter: str | None, last: float = 100.0) -> dict:
    return {"symbol": symbol, "last": last,
            "skittles": {"4h": {"bg": "black", "fg": "cyan"}},
            "rvol": {}, "sqz": {}, "news": None,
            "m5": {"chart": "below", "state": "quiet", "trigger": 101.0},
            "grade": {"letter": letter, "checks": {"skit": 7}, "reasons": ["r"]}}


@pytest.fixture
def tapes(monkeypatch):
    """Bull tape: AAA B then A+. Bear tape: AAA A. Today is pinned to the tape day."""
    monkeypatch.setattr(grade_log, "_today_et", lambda: DAY)

    def write():
        bull = GradeLog(service.grade_dir("bull"))
        bull.apply("Watchlist", {"rows": [_row("AAA", "B")], "rest": []}, NOW)
        bull.apply("Watchlist", {"rows": [_row("AAA", "A+", 103.0)], "rest": []}, NOW + timedelta(minutes=5))
        bear = GradeLog(service.grade_dir("bear"), direction="bear")
        bear.apply("Watchlist", {"rows": [_row("AAA", "A", 97.0)], "rest": []}, NOW + timedelta(minutes=5))
        assert service.grade_dir("bear") != service.grade_dir("bull")
    return write


def _letters(body):
    return [entry["letter"] for entry in body["entries"]]


def test_the_worker_serves_the_bear_tape_for_dir_bear_and_the_bull_tape_otherwise(tmp_path, monkeypatch, tapes):
    with running_worker(tmp_path, monkeypatch) as worker:
        tapes()
        bull_status, bull = worker.get("/api/momx-scanner/grade-tape?symbol=aaa&days=5")
        bear_status, bear = worker.get("/api/momx-scanner/grade-tape?symbol=AAA&days=5&dir=bear")
    assert (bull_status, bear_status) == (200, 200)
    assert bull["symbol"] == bear["symbol"] == "AAA"
    assert _letters(bull) == ["B", "A+"], "bull markers step B -> A+"
    assert _letters(bear) == ["A"], "the put-side markers must come from the bear tape, not the bull one"
    assert [entry["t"] for entry in bull["entries"]] == sorted(entry["t"] for entry in bull["entries"])


def test_the_letter_poll_sees_the_newest_entry_last(tmp_path, monkeypatch, tapes):
    # useChartGradeLetter shows entries[entries.length - 1].letter.
    with running_worker(tmp_path, monkeypatch) as worker:
        tapes()
        status, body = worker.get("/api/momx-scanner/grade-tape?symbol=AAA&days=1")
    assert status == 200
    assert body["entries"][-1]["letter"] == "A+"
    assert body["entries"][-1]["board"] == "Watchlist"


def test_an_ungraded_symbol_and_a_missing_symbol(tmp_path, monkeypatch, tapes):
    with running_worker(tmp_path, monkeypatch) as worker:
        tapes()
        none_status, none = worker.get("/api/momx-scanner/grade-tape?symbol=ZZZ&days=1")
        bad_status, bad = worker.get("/api/momx-scanner/grade-tape?days=1")
    assert (none_status, none) == (200, {"symbol": "ZZZ", "entries": []}), "ungraded = empty tape (the chart shows a dash)"
    assert (bad_status, bad) == (400, {"error": "symbol is required"})


def test_through_the_api_proxy_the_tape_arrives_unchanged_for_bull_and_bear(tmp_path, monkeypatch, tapes):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        tapes()
        _s, direct_bull = worker.get("/api/momx-scanner/grade-tape?symbol=AAA&days=5")
        _s, direct_bear = worker.get("/api/momx-scanner/grade-tape?symbol=AAA&days=5&dir=bear")
        bull_status, bull = api.get("/api/momx-scanner/grade-tape?symbol=AAA&days=5")
        bear_status, bear = api.get("/api/momx-scanner/grade-tape?symbol=AAA&days=5&dir=bear")
        missing_status, missing = api.get("/api/momx-scanner/grade-tape?days=1")
    assert (bull_status, bear_status) == (200, 200)
    assert bull == direct_bull and bear == direct_bear
    # The worker's 400 keeps its status through the proxy (it used to 500).
    assert missing_status == 400 and "error" in missing


def test_worker_down_gives_the_chart_a_body_with_no_letter(monkeypatch):
    # No worker_port: the harness refuses :3010, exactly like a dead worker.
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/grade-tape?symbol=SPY&days=1")
    assert status == 200
    assert body["warming"] is True
    assert "entries" not in body, "the chart reads a missing entries list as 'no letter' and shows the dash"
    assert "MomX worker unavailable" in body["message"]
