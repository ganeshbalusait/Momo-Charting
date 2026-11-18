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
def clean(monkeypatch):
    monkeypatch.setattr(scanner, "PACE_SECONDS", 0.0)
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
    many = [f"{chr(65 + i % 26)}{chr(65 + i // 26 % 26)}{chr(65 + i // 676)}" for i in range(500)]
    assert len(scanner.clean_symbols(many)) == scanner.MAX_SYMBOLS == 400


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


def test_whole_watchlist_by_ticker_list_and_its_own_allowance(tmp_path, fake, monkeypatch):
    """2026-11-17 "I see only mag7 tickets what about other tickets from watchlist": every ticker gets a By-ticker line
    even when Mag7's open interest fills the top of the list, and the watchlist read does not use the chat's hourly
    allowance."""
    monkeypatch.setattr(long_oi, "FETCHES_PER_HOUR", 1)
    tickers = ["NVDA", "PLTR", "SOFI", "AMD"]
    out = scanner.board(tickers, tmp_path, NOW, months_from=1, wait=True)
    assert sorted(fake) == sorted(tickers)                                    # 4 reads past a chat cap of 1
    assert [t["ticker"] for t in out["byTicker"]] == ["AMD", "NVDA", "PLTR", "SOFI"]
    line = out["byTicker"][0]
    assert line["call"]["strike"] == 200.0 and line["put"]["strike"] == 140.0
    assert line["entryCall"]["entry"] and line["entryPut"]["entry"]
    assert line["callOi"] > 0 and line["putOi"] > 0
    assert long_oi._MEMORY == {}                                              # full chains are not held in memory


def test_compact_copy_keeps_every_row_the_page_can_show(tmp_path, fake):
    full = scanner.board(["NVDA"], tmp_path, NOW, months_from=1, wait=True)
    scanner.reset()
    long_oi.reset()
    again = scanner.board(["NVDA"], tmp_path, NOW, months_from=1)                # read back from the small file
    assert again["rows"] == full["rows"] and again["entries"] == full["entries"]
    small = scanner.compact({"symbol": "NVDA", "fetchedAt": NOW.isoformat(), **dict(zip(("spot", "rows"), long_oi.rows_from_chain(chain(), TODAY)))})
    assert all(abs(r["delta"]) >= 0.10 for r in small["rows"])                    # lottery strikes dropped


def test_the_background_reader_waits_between_schwab_requests(tmp_path, fake, monkeypatch):
    naps: list = []
    monkeypatch.setattr(scanner, "PACE_SECONDS", 3.0)
    real_sleep = time.sleep
    # time.sleep is the whole program's: count only the reader's own pauses, let every other thread sleep as usual.
    monkeypatch.setattr(scanner.time, "sleep", lambda seconds: naps.append(seconds)
                        if threading.current_thread().name == "leap-oi-board" else real_sleep(seconds))
    scanner.board(["NVDA", "AAPL"], tmp_path, NOW)
    scanner._WORKER["thread"].join(5)
    assert naps == [3.0, 3.0] and sorted(fake) == ["AAPL", "NVDA"]


def test_a_ticker_with_no_chain_is_not_asked_again_every_five_minutes(tmp_path, monkeypatch):
    """2026-11-17 "Not loading properly": KITT, LIDR and PALU have no options at Schwab. long_oi forgets a failure after
    5 minutes, so the board re-queued them, flipped them back to "pending" and the page showed "Reading Schwab… 366 of
    369" again, all day. The board now remembers the failure an hour, with Schwab's own reason."""
    asked: list = []

    def no_options(ticker, today):
        asked.append(ticker)
        return {"symbol": ticker, "underlyingPrice": 2.0, "callExpDateMap": {}, "putExpDateMap": {}}
    monkeypatch.setattr(long_oi, "fetch", no_options)
    clock = [1000.0]
    monkeypatch.setattr(scanner.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(long_oi.time, "monotonic", lambda: clock[0])
    scanner.board(["KITT"], tmp_path, NOW)
    scanner._WORKER["thread"].join(5)
    assert asked == ["KITT"]
    clock[0] += 6 * 60                                                         # past long_oi's 5-minute memory
    later = scanner.board(["KITT"], tmp_path, NOW)
    assert later["pending"] == [] and later["tickers"][0]["state"] == "error"
    assert "no options" in later["tickers"][0]["error"]
    assert scanner._WORKER["thread"] is None and asked == ["KITT"]
    clock[0] += 61 * 60                                                        # an hour on, it is tried once more
    scanner.board(["KITT"], tmp_path, NOW)
    scanner._WORKER["thread"] and scanner._WORKER["thread"].join(5)
    assert asked == ["KITT", "KITT"]



def test_warm_queues_tickers_with_no_copy_without_a_page_open(tmp_path, fake):
    """2026-11-17 "Still I don't see full loading": the server reads the watchlist in the background, so the page is
    full when it opens. warm() only queues tickers with no copy younger than 12 hours."""
    scanner.board(["NVDA"], tmp_path, wait=True)                 # the reader stamps the real clock, so this test does too
    assert scanner.warm(["NVDA", "PLTR", "AMD"], tmp_path) == 2
    deadline = time.monotonic() + 5
    while scanner._WORKER["thread"] is not None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert sorted(fake) == ["AMD", "NVDA", "PLTR"]
    assert scanner.warm(["NVDA", "PLTR", "AMD"], tmp_path) == 0



def test_no_etfs_and_no_stocks_under_ten_dollars(tmp_path, monkeypatch):
    """2026-11-17 "No need spy, qqq and small stocks and etf": QQQ was the biggest put on the board; ETFs are never
    read, and a stock under $10 (NIO's 2 put was the top ENTRY) is left off every list."""
    asked: list = []

    def fetch(ticker, today):
        asked.append(ticker)
        doc = chain()
        doc["symbol"] = ticker
        if ticker == "NIO":
            doc["underlyingPrice"] = 4.2
        return doc
    monkeypatch.setattr(long_oi, "fetch", fetch)
    out = scanner.board(["QQQ", "NVDA", "NIO", "SPY"], tmp_path, NOW, months_from=1, wait=True)
    assert asked == ["NVDA", "NIO"]
    assert {r["ticker"] for r in out["rows"]} == {"NVDA"} and {r["ticker"] for r in out["entries"]} == {"NVDA"}
    assert [t["ticker"] for t in out["byTicker"]] == ["NVDA"]
    assert [t["ticker"] for t in out["tickers"]] == ["NVDA"]
    assert out["excluded"] == [{"ticker": "QQQ", "reason": "ETF"}, {"ticker": "SPY", "reason": "ETF"},
                               {"ticker": "NIO", "reason": "under $10"}]
    assert scanner.warm(["QQQ", "SPY"], tmp_path, NOW) == 0
