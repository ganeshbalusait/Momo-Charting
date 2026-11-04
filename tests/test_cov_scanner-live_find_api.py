"""Integration: FIND - "which tickers ever hit this condition" - over the
History archive, through the worker's /history-query route.

The browser builds the condition (frontend/src/momxHistoryQuery.js, from the
same colour tables that paint the board) and the worker only applies it. The
contract test below runs the REAL browser builder in node and sends exactly the
query string it produces, so a drift between the two halves fails here.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from api_harness import running_api
from live_guard import REPO
from momx import history, query, service
from worker_harness import running_worker

ET = ZoneInfo("America/New_York")
D1, D2 = "2026-11-02", "2026-11-03"
NODE = shutil.which("node")


def _row(symbol: str, rvol5: float, bg: str, fg: str = "black", skit_bg: str = "black") -> dict:
    return {"symbol": symbol, "scanPass": True, "pctChange": 1.5, "industry": "Software",
            "rvol": {"5m": {"value": rvol5, "bg": bg, "fg": fg}, "1h": {"value": 1.0, "bg": "black", "fg": "green"}},
            "skittles": {"2h": {"value": 60, "bg": skit_bg, "fg": "black"}}}


def _archive() -> None:
    directory = service.history_dir()
    history.record_board("Watchlist", {"rows": [
        _row("AAA", 3.4, "cyan"),                  # bullish, over 3
        _row("BBB", 3.9, "magenta"),               # over 3 but sellers winning
        _row("CCC", 2.6, "green"),                 # bullish, under 3
    ]}, now_et=datetime(2026, 11, 2, 10, 0, tzinfo=ET), directory=directory)
    history.record_board("Watchlist", {"rows": [
        _row("DDD", 4.2, "green", skit_bg="cyan"),
        _row("AAA", 1.1, "black", fg="green"),
    ]}, now_et=datetime(2026, 11, 3, 9, 50, tzinfo=ET), directory=directory)
    history.record_board("Watchlist", {"rows": [
        _row("DDD", 5.0, "green", skit_bg="cyan"),
        _row("AAA", 3.1, "green"),
    ]}, now_et=datetime(2026, 11, 3, 10, 30, tzinfo=ET), directory=directory)


RVOL_5M_BULLISH = "section=rvol&timeframe=5m&min=3&bg=cyan,green&fg=cyan,green,dark_green"


def test_find_returns_the_tickers_that_matched_with_day_first_last_and_peak(tmp_path, monkeypatch):
    query._RESULT_CACHE.clear()
    with running_worker(tmp_path, monkeypatch) as worker:
        _archive()
        status, body = worker.get(f"/api/momx-scanner/history-query?list=Watchlist&{RVOL_5M_BULLISH}")
        pin_status, pinned = worker.get(f"/api/momx-scanner/history-query?list=Watchlist&{RVOL_5M_BULLISH}&date={D1}")
    assert status == 200, body
    assert body["days"] == [D2, D1]
    assert [(r["date"], r["symbol"]) for r in body["results"]] == [(D2, "DDD"), (D2, "AAA"), (D1, "AAA")]
    ddd = body["results"][0]
    assert (ddd["first"], ddd["last"], ddd["hits"], ddd["peak"]) == ("09:50", "10:30", 2, 5.0)
    aaa_today = body["results"][1]
    assert (aaa_today["first"], aaa_today["hits"]) == ("10:30", 1)        # its 09:50 reading was 1.1
    assert body["tickers"] == 2 and body["truncated"] is False
    assert pin_status == 200 and [r["symbol"] for r in pinned["results"]] == ["AAA"] and pinned["days"] == [D1]


def test_a_colour_section_matches_on_background_only(tmp_path, monkeypatch):
    query._RESULT_CACHE.clear()
    with running_worker(tmp_path, monkeypatch) as worker:
        _archive()
        status, body = worker.get("/api/momx-scanner/history-query?list=Watchlist&section=skittles&timeframe=2h&bg=cyan,green,lime,dark_green")
    assert status == 200, body
    assert {r["symbol"] for r in body["results"]} == {"DDD"}


@pytest.mark.parametrize("bad", [
    "section=volume&timeframe=5m&min=3",          # unknown column
    "section=rvol&min=3",                          # no timeframe
    "section=rvol&timeframe=5m",                   # no minimum and no colour: would match everything
])
def test_a_condition_the_worker_cannot_apply_is_a_400_never_an_empty_answer(tmp_path, monkeypatch, bad):
    with running_worker(tmp_path, monkeypatch) as worker:
        _archive()
        status, body = worker.get(f"/api/momx-scanner/history-query?list=Watchlist&{bad}")
    assert status == 400, body
    assert body["error"]


def test_through_the_proxy_a_bad_condition_is_still_a_400(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        status, body = api.get("/api/momx-scanner/history-query?list=Watchlist&section=volume&timeframe=5m&min=3")
    assert status == 400, body
    assert body.get("error")


@pytest.mark.xfail(strict=True, reason=(
    "BUG: api_server's GET proxy replaces the worker's error body with the bare HTTP reason "
    "('Bad Request'), so FIND's dialog reads 'The search did not run. Bad Request' instead of the "
    "worker's explanation. The POST proxy already forwards the worker's body."))
def test_through_the_proxy_the_trader_reads_why_the_search_did_not_run(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        status, body = api.get("/api/momx-scanner/history-query?list=Watchlist&section=volume&timeframe=5m&min=3")
    assert status == 400
    assert "section must be one of" in body["error"]


def test_a_junk_limit_falls_back_to_the_ceiling_and_a_small_limit_says_it_truncated(tmp_path, monkeypatch):
    query._RESULT_CACHE.clear()
    with running_worker(tmp_path, monkeypatch) as worker:
        _archive()
        status, junk = worker.get(f"/api/momx-scanner/history-query?list=Watchlist&{RVOL_5M_BULLISH}&limit=abc")
        small_status, small = worker.get(f"/api/momx-scanner/history-query?list=Watchlist&{RVOL_5M_BULLISH}&limit=1")
    assert status == 200 and junk["returnedResults"] == junk["totalResults"] == 3 and junk["truncated"] is False
    assert small_status == 200 and small["returnedResults"] == 1 and small["totalResults"] == 3
    assert small["truncated"] is True                            # never a silent cap


@pytest.mark.xfail(strict=True, reason=(
    "BUG: /history-query passes date= straight into a file path (momx/query.py _day_results), so "
    "date=../<file> reads a JSON file outside the board's archive folder. /history refuses any "
    "non-ISO date for exactly this reason (review 2026-08-31); FIND does not."))
def test_a_crafted_date_never_reads_outside_the_archive(tmp_path, monkeypatch):
    query._RESULT_CACHE.clear()
    with running_worker(tmp_path, monkeypatch) as worker:
        _archive()
        outside = Path(service.history_dir()) / "planted.json"
        outside.write_text(json.dumps({"rows": {"EVIL": {"snapshots": [
            {"at": "2026-11-03T10:00:00-05:00", "row": _row("EVIL", 9.9, "cyan")}]}}}), encoding="utf-8")
        status, body = worker.get(f"/api/momx-scanner/history-query?list=Watchlist&{RVOL_5M_BULLISH}&date=../planted")
    assert "EVIL" not in {r["symbol"] for r in (body or {}).get("results", [])}


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_the_browsers_condition_builder_and_the_workers_query_agree(tmp_path, monkeypatch):
    """The DEFAULT FIND (RVOL >= 3, buyers winning, this day) on 5m and the
    default Skittles search, built by the real momxHistoryQuery.js."""
    module = (REPO / "frontend" / "src" / "momxHistoryQuery.js").as_uri()
    script = (
        f"import {{ DEFAULT_HISTORY_QUERY as d, historyQueryParams }} from {json.dumps(module)};\n"
        "const rvol = historyQueryParams({ ...d, timeframe: '5m' }, { list: 'Watchlist', date: '2026-11-03' });\n"
        "const skit = historyQueryParams({ ...d, section: 'skittles', timeframe: '2h', scope: 'all' }, { list: 'Watchlist' });\n"
        "const bear = historyQueryParams({ ...d, timeframe: '5m', scope: 'all' }, { list: 'Watchlist', direction: 'bear' });\n"
        "console.log(JSON.stringify([rvol.toString(), skit.toString(), bear.toString()]));\n"
    )
    out = subprocess.run([NODE, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60,
                         cwd=str(REPO / "frontend"))
    assert out.returncode == 0, out.stderr
    rvol_qs, skit_qs, bear_qs = json.loads(out.stdout.strip().splitlines()[-1])
    query._RESULT_CACHE.clear()
    with running_worker(tmp_path, monkeypatch) as worker:
        _archive()
        history.record_board("Watchlist", {"rows": [_row("DN", 3.3, "red")]},
                             now_et=datetime(2026, 11, 3, 10, 0, tzinfo=ET), directory=service.history_dir("bear"))
        rvol_status, rvol = worker.get("/api/momx-scanner/history-query?" + rvol_qs)
        skit_status, skit = worker.get("/api/momx-scanner/history-query?" + skit_qs)
        bear_status, bear = worker.get("/api/momx-scanner/history-query?" + bear_qs)
    assert rvol_status == 200, rvol
    assert rvol["days"] == [D2]                                  # "this day" pinned the date
    assert {r["symbol"] for r in rvol["results"]} == {"AAA", "DDD"}   # BBB's magenta is sellers, CCC is under 3
    assert skit_status == 200 and {r["symbol"] for r in skit["results"]} == {"DDD"}
    assert bear_status == 200 and {r["symbol"] for r in bear["results"]} == {"DN"}   # the bear archive, sellers winning
