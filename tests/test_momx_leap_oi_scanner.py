"""Unit: the LEAP OI Scanner page's board (momx/leap_oi_scanner.py, 2026-11-17).

Ganesh: "Leap OI scanner is better option", "Don't give high oi delta 0.02 ... max up to 0.1", "See the entry like that
logic", then "Build all 3 now". Covers: ticker cleaning, the month windows, the 0.10 delta floor, ENTRY tagging and the
ENTRY-only switch, side filter, board ranking, saved copies up to 12 hours, background reading for missing tickers, and
failures shown per ticker. Schwab is never reached: long_oi.fetch is a fake.
"""
from __future__ import annotations

import threading
import time
from datetime import timedelta

import pytest

from momx import leap_oi_scanner as scanner
from momx import long_oi
from test_momx_long_oi import NOW, TODAY, chain


def _drain():
    """Let a background reader from this test finish, so it never runs into the next test's fake."""
    scanner.reset()
    thread = scanner._WORKER["thread"]
    if thread is not None:
        thread.join(5)


@pytest.fixture(autouse=True)
def clean():
    _drain()
    long_oi.reset()
    yield
    _drain()
    long_oi.reset()


@pytest.fixture
def fake(monkeypatch):
    asked: list = []

    def fetch(ticker, today):
        asked.append(ticker)
        doc = chain()
        doc["symbol"] = ticker
        return doc
    monkeypatch.setattr(long_oi, "fetch", fetch)
    return asked


def test_tickers_are_cleaned_and_capped():
    assert scanner.clean_symbols("nvda, AAPL,,nvda, bad ticker, BRK.B") == ["NVDA", "AAPL", "BRK.B"]
    assert scanner.clean_symbols(["x" * 9, "", None, "msft"]) == ["MSFT"]
    assert len(scanner.clean_symbols([f"A{chr(65 + i % 26)}{chr(65 + i // 26)}" for i in range(60)])) == scanner.MAX_SYMBOLS


@pytest.mark.parametrize("months,days", [((3, 12), (88, 366)), ((1, 12), (28, 366)), ((1, 3), (28, 92)), ((12, 1), (358, 366))])
def test_month_windows_match_the_chat(months, days):
    assert scanner.day_range(*months) == days


def test_board_ranks_by_open_interest_with_the_delta_floor_and_entry(tmp_path, fake):
    out = scanner.board(["NVDA"], tmp_path, NOW, months_from=1, months_to=12, wait=True)
    assert fake == ["NVDA"]
    strikes = [(r["side"], r["strike"]) for r in out["rows"]]
    # 0.08-delta 250 call and the no-delta 220 call never show (0.10 floor); 400 days is outside 12 months.
    assert ("CALL", 250.0) not in strikes and ("CALL", 220.0) not in strikes and ("CALL", 300.0) not in strikes
    assert out["rows"][0]["strike"] == 200.0 and out["rows"][0]["oi"] == 344959
    assert [r["oi"] for r in out["rows"]] == sorted((r["oi"] for r in out["rows"]), reverse=True)
    assert out["biggestCall"]["strike"] == 200.0 and out["biggestPut"]["strike"] == 140.0
    # ENTRY: delta under 0.20, out of the money, closest to 0.20 -> the 32-day 200 call (0.16) and the 95-day 120 put.
    entry = {(r["side"], r["strike"], r["days"]) for r in out["entries"]}
    assert ("CALL", 200.0, 32) in entry and ("PUT", 120.0, 95) in entry
    assert all(r["entry"] for r in out["entries"])
    row = out["rows"][0]
    assert row["ticker"] == "NVDA" and row["spot"] == 155.6 and row["away"] == round((200 / 155.6 - 1) * 100, 1)
    assert out["tickers"] == [{"ticker": "NVDA", "state": "ready", "spot": 155.6, "readAt": NOW.isoformat(),
                               "callOi": sum(r["oi"] for r in out["rows"] if r["side"] == "CALL"),
                               "putOi": sum(r["oi"] for r in out["rows"] if r["side"] == "PUT"), "rows": len(out["rows"])}]
    assert "coin flip" in out["note"] and "not a pick" in out["note"]


def test_leap_window_side_entry_only_and_higher_delta(tmp_path, fake):
    leap = scanner.board(["NVDA"], tmp_path, NOW, wait=True)               # default LEAP 3..12 months
    assert all(88 <= r["days"] <= 366 for r in leap["rows"]) and {r["days"] for r in leap["rows"]} == {95, 305}
    calls = scanner.board(["NVDA"], tmp_path, NOW, months_from=1, side="calls", wait=True)
    assert {r["side"] for r in calls["rows"]} == {"CALL"} and calls["biggestPut"] is None
    only = scanner.board(["NVDA"], tmp_path, NOW, months_from=1, entry_only=True, wait=True)
    assert only["rows"] and all(r["entry"] for r in only["rows"])
    high = scanner.board(["NVDA"], tmp_path, NOW, months_from=1, min_delta=0.30, wait=True)
    assert all(abs(r["delta"]) >= 0.30 for r in high["rows"])
    low = scanner.board(["NVDA"], tmp_path, NOW, months_from=1, min_delta=0.01, wait=True)
    assert low["minDelta"] == 0.10                                           # never below the floor
    assert fake == ["NVDA"]                                                  # every later board read the copy


def test_a_copy_up_to_twelve_hours_old_is_used_without_asking_schwab(tmp_path, fake):
    scanner.board(["NVDA"], tmp_path, NOW, wait=True)
    long_oi.reset()                                                          # only the disk copy is left
    later = scanner.board(["NVDA"], tmp_path, NOW + timedelta(hours=11))
    assert later["tickers"][0]["state"] == "ready" and later["pending"] == [] and fake == ["NVDA"]
    stale = scanner.board(["NVDA"], tmp_path, NOW + timedelta(hours=13))
    assert stale["tickers"][0]["state"] == "pending" and stale["pending"] == ["NVDA"]


def test_missing_tickers_are_read_by_one_background_thread_and_the_request_never_waits(tmp_path, monkeypatch):
    gate = threading.Event()
    asked: list = []

    def slow(ticker, today):
        asked.append(ticker)
        gate.wait(5)
        doc = chain()
        doc["symbol"] = ticker
        return doc
    monkeypatch.setattr(long_oi, "fetch", slow)
    started = time.monotonic()
    first = scanner.board(["NVDA", "AAPL"], tmp_path)
    assert time.monotonic() - started < 1.0
    assert first["pending"] == ["NVDA", "AAPL"] and first["rows"] == []
    again = scanner.board(["NVDA", "AAPL"], tmp_path)                       # asking again queues nothing twice
    assert again["pending"] == ["NVDA", "AAPL"]
    gate.set()
    deadline = time.monotonic() + 5
    while scanner.pending() or (scanner._WORKER["thread"] is not None and time.monotonic() < deadline):
        time.sleep(0.05)
    assert asked == ["NVDA", "AAPL"]
    done = scanner.board(["NVDA", "AAPL"], tmp_path)
    assert {t["ticker"]: t["state"] for t in done["tickers"]} == {"NVDA": "ready", "AAPL": "ready"}
    assert {r["ticker"] for r in done["rows"]} == {"NVDA", "AAPL"}


def test_a_ticker_schwab_refused_shows_its_reason_and_is_not_asked_again(tmp_path, monkeypatch):
    asked: list = []

    def broken(ticker, today):
        asked.append(ticker)
        raise RuntimeError("boom")
    monkeypatch.setattr(long_oi, "fetch", broken)
    out = scanner.board(["ZZZ"], tmp_path, NOW, wait=True)
    assert out["tickers"][0]["state"] == "error" and "Schwab did not answer for ZZZ" in out["tickers"][0]["error"]
    later = scanner.board(["ZZZ"], tmp_path, NOW)
    assert later["tickers"][0]["state"] == "error" and later["pending"] == [] and asked == ["ZZZ"]
