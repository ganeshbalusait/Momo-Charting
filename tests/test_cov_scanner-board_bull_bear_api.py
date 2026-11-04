"""Integration: the BULL / BEAR switch on the real scanner worker routes.

The panel adds &dir=bear to every board read when the trader flips to BEAR.
The worker must then serve the BEAR board of the same list - never the bull
rows under a BEAR label - and keep a separate momentum strip for each side.
"""
from __future__ import annotations

import pytest

from momx import ai_filter, service
from worker_harness import running_worker

BOARD = "/api/momx-scanner"


@pytest.fixture(autouse=True)
def _no_ai_scores(monkeypatch):
    monkeypatch.setattr(ai_filter, "mark_rows", lambda payload, side, now=None: None)


def board(stamp, rows, direction="bull"):
    return {
        "generatedAt": stamp,
        "direction": direction,
        "list": "Mag7",
        "universe": ["AAA", "BBB"],
        "universeCount": 2,
        "rows": rows,
        "rest": [],
        "errors": {},
    }


def row(symbol, passed):
    return {"symbol": symbol, "pctChange": 2.0 if symbol == "AAA" else -2.0, "scanPass": passed, "scanReasons": ["rvol:5m"]}


def test_dir_bear_serves_the_bear_board_and_no_dir_the_bull_one(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.seed_board("Mag7", board("2026-11-03T20:00:00+00:00", [row("AAA", True)]))
        worker.seed_board("Mag7", board("2026-11-03T20:00:00+00:00", [row("BBB", True)], "bear"), direction="bear")
        bull = worker.get(BOARD + "?list=Mag7")
        bear = worker.get(BOARD + "?list=Mag7&dir=bear")
        shouted = worker.get(BOARD + "?list=Mag7&dir=BEAR")
        junk = worker.get(BOARD + "?list=Mag7&dir=sideways")
    assert bull[0] == bear[0] == 200
    assert [r["symbol"] for r in bull[1]["rows"]] == ["AAA"] and bull[1]["direction"] == "bull"
    assert [r["symbol"] for r in bear[1]["rows"]] == ["BBB"] and bear[1]["direction"] == "bear"
    assert [r["symbol"] for r in shouted[1]["rows"]] == ["BBB"]
    # Anything that is not "bear" reads as the bull board.
    assert [r["symbol"] for r in junk[1]["rows"]] == ["AAA"]


def test_a_bear_board_not_built_yet_is_a_bear_warming_stub_never_the_bull_rows(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.seed_board("Mag7", board("2026-11-03T20:00:00+00:00", [row("AAA", True)]))
        status, body = worker.get(BOARD + "?list=Mag7&dir=bear")
    assert status == 200, body
    assert body["warming"] is True and body["rows"] == []
    assert body["direction"] == "bear"


def test_the_in_memory_bear_board_is_served_from_the_same_build(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        state = service._state("Mag7")
        state.payload = board("g1", [row("AAA", True)])
        state.bear = board("g1", [row("BBB", True)], "bear")
        status, body = worker.get(BOARD + "?list=Mag7&dir=bear")
    assert status == 200 and [r["symbol"] for r in body["rows"]] == ["BBB"]
    assert body["generatedAt"] == "g1"


def test_each_side_keeps_its_own_momentum_strip(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        state = service._state("Mag7")
        # Build 1: nothing matches on either side (the baseline).
        state.payload = board("g1", [row("AAA", False), row("BBB", False)])
        state.bear = board("g1", [row("AAA", False), row("BBB", False)], "bear")
        assert worker.get(BOARD + "/momentum?list=Mag7")[1]["events"] == []
        assert worker.get(BOARD + "/momentum?list=Mag7&dir=bear")[1]["events"] == []
        # Build 2: AAA enters the bull scan, BBB the bear scan.
        state.payload = board("g2", [row("AAA", True), row("BBB", False)])
        state.bear = board("g2", [row("AAA", False), row("BBB", True)], "bear")
        bull = worker.get(BOARD + "/momentum?list=Mag7")[1]
        bear = worker.get(BOARD + "/momentum?list=Mag7&dir=bear")[1]
    assert (bull["direction"], bear["direction"]) == ("bull", "bear")
    assert [(e["type"], e["symbol"]) for e in bull["events"]] == [("new_match", "AAA")]
    assert [(e["type"], e["symbol"]) for e in bear["events"]] == [("new_match", "BBB")]
    assert bull["generatedAt"] == bear["generatedAt"] == "g2"
