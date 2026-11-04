"""Integration: the scanner grade's track record and 5-minute tape routes,
on the real scanner worker and through api_server's proxy.

The "why" panel under a Setup letter reads /grade-record (recorded record,
else the History back-test, else "track record unavailable"); the BEAR board
reads its own record under the bear folder. The chart circles read
/grade-tape, whose symbol is upper-cased and whose day count is held to 1-10.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from api_harness import api_server, running_api
from momx import grade_log, service
from momx.grade_log import GradeLog
from worker_harness import running_worker

ET = ZoneInfo("America/New_York")
RECORD = "/api/momx-scanner/grade-record"
TAPE = "/api/momx-scanner/grade-tape"


def tape_row(symbol="AAA", letter="A+"):
    return {"symbol": symbol, "last": 100.0, "skittles": {"4h": {"bg": "black", "fg": "cyan"}},
            "rvol": {}, "sqz": {}, "news": None,
            "m5": {"chart": "below", "state": "quiet", "trigger": 101.0},
            "grade": {"letter": letter, "checks": {"skit": 7}, "reasons": ["r"]}}


BACKTEST = {"days": ["2026-09-01", "2026-09-02"], "source": "History archive",
            "letters": {"A+": {"count": 3, "pctHigherClose": 44.0, "avgToClose": 0.1,
                               "medianToClose": -0.1, "up3": 1, "down3": 0}},
            "events": [{"day": "2026-09-01"}]}


def test_each_direction_reads_its_own_record(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        bear_dir = service.grade_dir("bear")
        bear_dir.mkdir(parents=True, exist_ok=True)
        (bear_dir / grade_log.BACKTEST_NAME).write_text(json.dumps(BACKTEST), encoding="utf-8")
        bull = worker.get(RECORD)
        bear = worker.get(RECORD + "?dir=bear")
    assert bull == (200, {"source": None, "patternSource": None, "direction": "bull"})
    assert bear[0] == 200
    assert bear[1]["direction"] == "bear"
    assert bear[1]["source"] == "back-test from History archive (2 days)"
    assert bear[1]["letters"]["A+"]["count"] == 3
    assert "events" not in bear[1]                    # the per-event list never ships per poll


def test_the_record_falls_back_recorded_then_back_test_then_unavailable(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        folder = service.grade_dir()
        folder.mkdir(parents=True, exist_ok=True)
        unavailable = worker.get(RECORD)[1]
        (folder / grade_log.BACKTEST_NAME).write_text(json.dumps(BACKTEST), encoding="utf-8")
        back_test = worker.get(RECORD)[1]
        # A recorded file with no scored day yet still defers to the back-test.
        (folder / grade_log.RECORD_NAME).write_text(json.dumps({"source": "recorded", "days": [], "letters": {}}), encoding="utf-8")
        still_back_test = worker.get(RECORD)[1]
        (folder / grade_log.RECORD_NAME).write_text(json.dumps({
            "source": "recorded", "days": ["2026-09-21"],
            "letters": {"A+": {"count": 2, "pctHigherClose": 50.0}}}), encoding="utf-8")
        recorded = worker.get(RECORD)[1]
    assert unavailable["source"] is None                       # "track record unavailable"
    assert back_test["source"] == still_back_test["source"] == "back-test from History archive (2 days)"
    assert recorded["source"] == "recorded" and recorded["days"] == ["2026-09-21"]
    assert recorded["letters"]["A+"]["count"] == 2


@pytest.fixture
def twelve_days_of_tape(monkeypatch):
    """AAA graded at 10:00 ET on every session 2026-09-01 (Tue) .. 09-18 (Fri):
    fourteen trading days; the chart's look-back from 09-18 reaches 09-04."""
    monkeypatch.setattr(grade_log, "_today_et", lambda: "2026-09-18")

    def seed():
        log = GradeLog(service.grade_dir())
        for day in range(1, 19):
            when = datetime(2026, 9, day, 10, 0, tzinfo=ET)
            if when.weekday() < 5:
                log.apply("Watchlist", {"rows": [tape_row(letter="B" if day % 2 else "A+")], "rest": []}, when)
    return seed


def test_the_tape_route_upper_cases_the_symbol_and_holds_days_to_ten(tmp_path, monkeypatch, twelve_days_of_tape):
    with running_worker(tmp_path, monkeypatch) as worker:
        twelve_days_of_tape()
        status, body = worker.get(TAPE + "?symbol=aaa&days=99")
        default = worker.get(TAPE + "?symbol=AAA&days=junk")[1]
        one = worker.get(TAPE + "?symbol=AAA&days=1")[1]
        missing = worker.get(TAPE)
    assert status == 200 and body["symbol"] == "AAA"
    days = [entry["t"][:10] for entry in body["entries"]]
    assert len(days) == 10 and days[0] == "2026-09-07" and days[-1] == "2026-09-18"
    assert [entry["letter"] for entry in body["entries"]][-2:] == ["B", "A+"]
    assert len(default["entries"]) == 5                       # junk days -> the default 5
    assert [entry["t"][:10] for entry in one["entries"]] == ["2026-09-18"]
    assert missing == (400, {"error": "symbol is required"})


def test_the_tape_comes_through_api_server_the_same_way(tmp_path, monkeypatch, twelve_days_of_tape):
    monkeypatch.setattr(api_server.ApiHandler, "_session_user",
                        lambda self: {"id": "u1", "email": "trader@example.com", "role": "member"})
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        twelve_days_of_tape()
        direct = worker.get(TAPE + "?symbol=nvda&days=3")[1]
        status, body = api.get(TAPE + "?symbol=aaa&days=0")
        record_status, record = api.get(RECORD + "?dir=bear")
    assert direct["symbol"] == "NVDA" and direct["entries"] == []      # never graded: empty, not an error
    assert status == 200 and body["symbol"] == "AAA"
    assert len(body["entries"]) == 5                          # days=0 -> the default 5
    assert record_status == 200 and record["direction"] == "bear"
