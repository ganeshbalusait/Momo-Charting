"""Integration: the scanner History tab's archive route over real HTTP.

The archive is written by the same recorder a build uses
(momx.history.record_board) into the worker's scratch history folder, with a
fixed ET clock, then read back through the worker's /history route - and once
through api_server's proxy, which must hand the gzip the worker produced to the
browser intact (the phone loads a day over the tunnel that way).
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from api_harness import running_api
from momx import history, service
from worker_harness import running_worker

ET = ZoneInfo("America/New_York")
D1, D2 = "2026-11-02", "2026-11-03"


def _row(symbol: str, rvol5: float, *, spark: int = 0) -> dict:
    return {
        "symbol": symbol, "scanPass": True, "pctChange": 2.5, "last": 20.0, "industry": "Semiconductors",
        "scanReasons": ["rvol:5m"],
        "rvol": {"5m": {"value": rvol5, "bg": "cyan" if rvol5 >= 2 else "black", "fg": "black" if rvol5 >= 2 else "green"}},
        "sqz": {"2h": {"value": 1, "bg": "red", "fg": "white"}},
        "skittles": {"2h": {"value": 55, "bg": "green", "fg": "black"}},
        "sparkline": [round(20 + i * 0.01, 2) for i in range(spark)],
    }


def _record(name: str, when: datetime, rows: list[dict], direction: str = "bull") -> None:
    assert history.record_board(name, {"rows": rows}, now_et=when, directory=service.history_dir(direction))


def _archive_two_days(name: str = "Watchlist") -> None:
    _record(name, datetime(2026, 11, 2, 10, 5, tzinfo=ET), [_row("AAA", 2.5)])
    _record(name, datetime(2026, 11, 3, 9, 31, tzinfo=ET), [_row("BBB", 1.5)])
    _record(name, datetime(2026, 11, 3, 9, 45, tzinfo=ET), [_row("BBB", 3.5), _row("AAA", 2.2)])   # BBB's RVOL cell changed
    _record(name, datetime(2026, 11, 3, 12, 10, tzinfo=ET), [_row("BBB", 1.0), _row("AAA", 2.2), _row("CCC", 4.0)])


def test_a_day_returns_its_newest_page_with_the_counters_the_bar_needs(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        _archive_two_days()
        status, body = worker.get(f"/api/momx-scanner/history?list=Watchlist&date={D2}")
    assert status == 200, body
    assert body["days"] == [D2, D1] and body["date"] == D2 and body["retentionDays"] == 30
    assert body["pageSize"] == history.HISTORY_MAX_DAY_ROWS
    # One row per snapshot, ascending by time: BBB arrives; at 09:45 BBB changes
    # and AAA arrives; at 12:10 BBB changes again and CCC arrives.
    assert [r["at"][11:16] for r in body["rows"]] == ["09:31", "09:45", "09:45", "12:10", "12:10"]
    assert sorted((r["at"][11:16], r["symbol"]) for r in body["rows"]) == [
        ("09:31", "BBB"), ("09:45", "AAA"), ("09:45", "BBB"), ("12:10", "BBB"), ("12:10", "CCC")]
    assert body["totalRows"] == 5 and body["offset"] == 0 and body["hasMore"] is False
    assert body["timeIndex"] == [["09:31", 0, 1], ["09:45", 1, 2], ["12:10", 3, 2]]
    assert dict(body["sessions"]) == {"Overnight": 0, "Pre": 0, "Open": 3, "Morning": 0, "Midday": 2, "Power": 0, "After": 0}
    changed = [r for r in body["rows"] if r["symbol"] == "BBB" and not r["isArrival"]]
    assert changed and all("rvol.5m" in r["changed"] for r in changed)


def test_no_date_opens_on_the_newest_day_and_a_junk_offset_is_page_one_not_a_500(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        _archive_two_days()
        status, newest = worker.get("/api/momx-scanner/history?list=Watchlist")
        junk_status, junk = worker.get(f"/api/momx-scanner/history?list=Watchlist&date={D2}&offset=abc")
        neg_status, negative = worker.get(f"/api/momx-scanner/history?list=Watchlist&date={D2}&offset=-5")
        page_status, older = worker.get(f"/api/momx-scanner/history?list=Watchlist&date={D2}&offset=3")
    assert status == 200 and newest["date"] == D2
    assert junk_status == 200 and junk["offset"] == 0 and junk["totalRows"] == 5 and len(junk["rows"]) == 5
    assert neg_status == 200 and negative["offset"] == 0
    # Offsets count back from the NEWEST row: 3 back leaves the two oldest.
    assert page_status == 200 and [r["at"][11:16] for r in older["rows"]] == ["09:31", "09:45"]


def test_a_ticker_search_with_no_date_spans_every_archived_day(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        _archive_two_days()
        status, body = worker.get("/api/momx-scanner/history?list=Watchlist&symbol=aaa")
        one_status, one_day = worker.get(f"/api/momx-scanner/history?list=Watchlist&symbol=AAA&date={D1}")
    assert status == 200, body
    assert body["symbol"] == "AAA" and body["date"] is None
    assert [(r["date"], r["at"][11:16]) for r in body["rows"]] == [(D2, "09:45"), (D1, "10:05")]   # newest day first
    assert one_status == 200 and [r["date"] for r in one_day["rows"]] == [D1] and one_day["date"] == D1


def test_an_absent_day_an_unknown_ticker_and_a_crafted_date_are_empty_200s(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        _archive_two_days()
        answers = [worker.get(path) for path in (
            "/api/momx-scanner/history?list=Watchlist&date=2026-10-01",
            "/api/momx-scanner/history?list=Watchlist&symbol=ZZZZ",
            "/api/momx-scanner/history?list=Watchlist&date=../../momx_board_cache/x",
            "/api/momx-scanner/history?list=Mag7",
        )]
    for status, body in answers:
        assert status == 200, body
        assert body["rows"] == []
    assert answers[3][1]["days"] == []                           # a list never archived: no days, not an error


def test_the_bear_tab_reads_the_bear_archive_not_the_bull_one(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        _archive_two_days()
        _record("Watchlist", datetime(2026, 11, 3, 10, 0, tzinfo=ET), [_row("DN", 3.0)], direction="bear")
        status, bear = worker.get(f"/api/momx-scanner/history?list=Watchlist&date={D2}&dir=bear")
        _, bull = worker.get(f"/api/momx-scanner/history?list=Watchlist&date={D2}")
    assert status == 200, bear
    assert [r["symbol"] for r in bear["rows"]] == ["DN"] and bear["days"] == [D2]
    assert "DN" not in {r["symbol"] for r in bull["rows"]}


def test_a_big_day_reaches_the_browser_gzipped_through_the_proxy(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        rows = [_row(f"S{i:02d}", 2.5 + i / 100, spark=40) for i in range(40)]
        _record("Watchlist", datetime(2026, 11, 3, 9, 40, tzinfo=ET), rows)
        status, body = api.get(f"/api/momx-scanner/history?list=Watchlist&date={D2}", headers={"Accept-Encoding": "gzip"})
        encoding, vary = api.last_headers.get("Content-Encoding"), api.last_headers.get("Vary")
        plain_status, plain = api.get(f"/api/momx-scanner/history?list=Watchlist&date={D2}")
        plain_encoding = api.last_headers.get("Content-Encoding")
    assert status == 200, body
    assert encoding == "gzip" and vary == "Accept-Encoding"
    assert len(body["rows"]) == 40                               # the client decoded real JSON
    assert len(json.dumps(body)) > 8192                          # big enough that gzip is worth it
    # A client that did not offer gzip is never handed it.
    assert plain_status == 200 and plain_encoding is None and plain["totalRows"] == 40


def test_a_small_answer_stays_plain_even_when_gzip_is_offered(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/history?list=Watchlist&date=2026-10-01", headers={"Accept-Encoding": "gzip"})
        assert worker.last_headers.get("Content-Encoding") is None
    assert status == 200 and body["rows"] == []
    assert gzip  # imported for the reader's benefit: the client decodes gzip itself
