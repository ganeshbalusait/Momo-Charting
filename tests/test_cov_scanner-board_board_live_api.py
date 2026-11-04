"""Integration: the MomX live board route on the real scanner worker
(momx_worker.Handler over HTTP, tests/worker_harness.py).

What the trader sees when he opens MomX Scanner: the last saved board paints
at once (from disk after a restart), a list nobody has built yet says
"building" instead of erroring, a typo'd list name is a 400 the panel can
recover from, and SCAN queues a rebuild of ONLY the list on screen.

No warmer thread runs, no bars are fetched: every board here is seeded.
"""
from __future__ import annotations

import json
import time
import urllib.request

import pytest

from momx import ai_filter, service
from worker_harness import running_worker

BOARD = "/api/momx-scanner"
OLD_STAMP = "2026-01-05T15:00:00+00:00"     # long before any run of this test


@pytest.fixture(autouse=True)
def _no_ai_scores(monkeypatch):
    # The board is served through ai_filter.mark_rows, which reads the live
    # artifacts/ score files. Nothing here is about AI scores.
    monkeypatch.setattr(ai_filter, "mark_rows", lambda payload, side, now=None: None)


def board(symbols, stamp=OLD_STAMP, *, passes=(), direction="bull", padding=0):
    rows = [
        {
            "symbol": symbol,
            "pctChange": 1.5,
            "scanPass": symbol in passes,
            "industry": "Semis",
            "note": "x" * padding,
        }
        for symbol in symbols
    ]
    return {
        "generatedAt": stamp,
        "direction": direction,
        "list": "Mag7",
        "universe": list(symbols),
        "universeCount": len(symbols),
        "rows": rows,
        "rest": [],
        "errors": {},
    }


def test_the_last_saved_board_paints_from_disk_with_its_source_and_age(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.seed_board("Mag7", board(["AAPL", "NVDA"], passes=("NVDA",)))
        status, body = worker.get(BOARD + "?list=mag7")      # the URL name is case-blind
    assert status == 200, body
    assert [row["symbol"] for row in body["rows"]] == ["AAPL", "NVDA"]
    assert [row["scanPass"] for row in body["rows"]] == [False, True]
    assert (body["list"], body["direction"]) == ("Mag7", "bull")
    # Served from the disk copy, flagged so the panel says "updating..."
    assert body["fromDisk"] is True and body["stale"] is True
    # A board from a previous session says so in words.
    assert body["message"].startswith("Showing the last saved Mag7 board from a previous session")
    # Stamped at SERVE time: the toolbar's data source, freshness and TOS load.
    assert body["dataSource"] == "tos"
    assert body["freshness"]["scanCompletedAt"] == OLD_STAMP
    assert body["tosStatus"]["total"] == 2


def test_a_board_built_this_session_wins_over_the_disk_copy(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.seed_board("Mag7", board(["AAPL"]))
        service._state("Mag7").payload = board(["MSFT", "META"], "2026-11-04T15:00:00+00:00")
        status, body = worker.get(BOARD + "?list=Mag7")
    assert status == 200, body
    assert [row["symbol"] for row in body["rows"]] == ["MSFT", "META"]
    assert "fromDisk" not in body and body["generatedAt"] == "2026-11-04T15:00:00+00:00"


def test_a_failed_rebuild_keeps_the_last_good_board_and_flags_it_stale(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        state = service._state("Mag7")
        state.payload = board(["AAPL"], "2026-11-04T15:00:00+00:00")
        state.error = "alpaca said no"
        status, body = worker.get(BOARD + "?list=Mag7")
    assert status == 200, body
    assert [row["symbol"] for row in body["rows"]] == ["AAPL"]       # never a blank board
    assert body["stale"] is True and body["errors"]["_refresh"] == "alpaca said no"


def test_a_list_never_built_answers_building_not_an_error(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get(BOARD + "?list=Watchlist")
    assert status == 200, body
    assert body["warming"] is True and body["rows"] == []
    assert body["universe"] == ["SPY", "QQQ"]                       # the harness's Watchlist seed
    assert body["message"].startswith("Building the first Watchlist board from live bars")


def test_an_unknown_list_is_a_400_naming_the_lists_that_exist(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get(BOARD + "?list=NoSuchList")
    assert status == 400
    assert body["error"].startswith("Unknown list 'NoSuchList'")
    assert "Mag7" in body["error"] and "Watchlist" in body["error"]


def test_scan_queues_a_rebuild_of_only_the_list_on_screen(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        later = time.monotonic() + 999
        watch, mag7 = service._state("Watchlist"), service._state("Mag7")
        watch.payload = board(["SPY"], "2026-11-04T14:59:00+00:00")
        watch.due_at = mag7.due_at = later
        status, body = worker.post(BOARD + "/rebuild", {"list": "watchlist"})
        assert status == 200, body
        # The answer carries the board it will replace: the panel polls until this changes.
        assert body == {"ok": True, "list": "Watchlist", "queued": True, "generatedAt": "2026-11-04T14:59:00+00:00"}
        assert watch.due_at == 0.0
        assert mag7.due_at == later, "SCAN on Watchlist must not restart the Mag7 build"


def test_scan_on_a_list_that_is_gone_is_a_400_not_a_silent_rebuild(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.post(BOARD + "/rebuild", {"list": "Deleted List"})
    assert status == 400
    assert "Unknown list 'Deleted List'" in body["error"]


def test_a_big_board_is_gzipped_only_when_the_browser_offers_it(tmp_path, monkeypatch):
    symbols = [f"T{index:03d}" for index in range(60)]
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.seed_board("Mag7", board(symbols, padding=200))      # well over the 8 KB floor
        request = urllib.request.Request(worker.base_url + BOARD + "?list=Mag7", headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(request, timeout=20) as response:
            zipped_headers, raw = response.headers, response.read()
        status, plain = worker.get(BOARD + "?list=Mag7")
        plain_headers = worker.last_headers
    assert zipped_headers["Content-Encoding"] == "gzip"
    assert zipped_headers["Vary"] == "Accept-Encoding"
    assert raw[:2] == b"\x1f\x8b"
    import gzip

    unzipped = json.loads(gzip.decompress(raw))
    assert status == 200 and plain_headers.get("Content-Encoding") is None
    assert unzipped["rows"] == plain["rows"] and len(unzipped["rows"]) == 60


def test_a_small_board_stays_plain_even_when_gzip_is_offered(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.seed_board("Mag7", board(["AAPL"]))
        status, body = worker.get(BOARD + "?list=Mag7", headers={"Accept-Encoding": "gzip"})
        headers = worker.last_headers
    assert status == 200 and body["rows"][0]["symbol"] == "AAPL"
    assert headers.get("Content-Encoding") is None


def test_the_lists_route_reports_which_boards_are_still_building(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        service._state("Mag7").payload = board(["AAPL"], "2026-11-04T15:00:00+00:00")
        status, body = worker.get(BOARD + "/lists")
    assert status == 200, body
    by_name = {item["name"]: item for item in body["lists"]}
    assert by_name["Mag7"]["warming"] is False and by_name["Mag7"]["builtAt"] == "2026-11-04T15:00:00+00:00"
    assert by_name["Watchlist"]["warming"] is True and by_name["Watchlist"]["count"] == 2
    assert body["active"] == "Mag7" and body["maxLists"] == 6
