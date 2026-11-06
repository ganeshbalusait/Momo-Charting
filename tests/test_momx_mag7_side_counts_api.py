"""Integration (2026-11-05): the Mag7 BULL n / BEAR n pills read both sides of
the same list from the real worker route. Each read must be that side's board
of Mag7, and the pill counts are the scan matches in rows AND rest."""
from __future__ import annotations

import pytest

from momx import ai_filter
from worker_harness import running_worker

BOARD = "/api/momx-scanner"
STAMP = "2026-11-05T15:00:00+00:00"


@pytest.fixture(autouse=True)
def _no_ai_scores(monkeypatch):
    monkeypatch.setattr(ai_filter, "mark_rows", lambda payload, side, now=None: None)


def board(rows, rest=(), direction="bull"):
    return {"generatedAt": STAMP, "direction": direction, "list": "Mag7", "universe": ["A", "B", "C"],
            "universeCount": 3, "rows": list(rows), "rest": list(rest), "errors": {}}


def r(symbol, passed):
    return {"symbol": symbol, "pctChange": 1.0, "scanPass": passed, "scanReasons": []}


def matches(payload):
    return sum(1 for row in (payload.get("rows") or []) + (payload.get("rest") or []) if row.get("scanPass"))


def test_both_sides_of_mag7_read_back_with_their_own_match_counts(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.seed_board("Mag7", board([r("A", True)], rest=[r("C", False)]))
        worker.seed_board("Mag7", board([r("B", True), r("C", True)], rest=[r("A", True)], direction="bear"), direction="bear")
        bull_status, bull = worker.get(BOARD + "?list=Mag7")
        bear_status, bear = worker.get(BOARD + "?list=Mag7&dir=bear")
    assert bull_status == bear_status == 200
    assert bull["list"] == bear["list"] == "Mag7"
    assert (matches(bull), matches(bear)) == (1, 3), "BULL 1 / BEAR 3 - rest rows count too"
