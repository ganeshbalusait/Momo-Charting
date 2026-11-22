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


def test_each_by_ticker_line_names_its_sector(tmp_path, fake):
    """2026-11-22 "can we add sector also which one have more leaps option": the scanner's own industry groups
    (Semis, Software, Fintech...), "Other" for a ticker the table does not list."""
    out = scanner.board(["NVDA", "PLTR", "SOFI", "AMD"], tmp_path, NOW, months_from=1, wait=True)
    assert {t["ticker"]: t["sector"] for t in out["byTicker"]} == {
        "AMD": "Semis", "NVDA": "Semis", "PLTR": "Software", "SOFI": "Fintech"}
    assert scanner._ticker_summary("ZZZQ", 50.0, [])["sector"] == "Other"


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


def test_a_searched_ticker_is_read_before_the_warm_up_queue(tmp_path, monkeypatch):
    """2026-11-17 "Add search ticker also": a ticker typed into the search box jumps ahead of the warm-up's long
    queue (about 260 names), so the trader is not left waiting minutes for it."""
    import threading as _threading
    gate = _threading.Event()
    started = _threading.Event()
    asked: list = []

    def fetch(ticker, today):
        asked.append(ticker)
        if ticker == "AAPL":
            started.set()
            gate.wait(5)                                   # the reader is busy on the first warm-up ticker
        doc = chain()
        doc["symbol"] = ticker
        return doc
    monkeypatch.setattr(long_oi, "fetch", fetch)
    assert scanner.warm(["AAPL", "MSFT", "AMZN"], tmp_path) == 3
    assert started.wait(5)
    out = scanner.board(["PLTR"], tmp_path)              # the search: not read yet, so queued at the front
    assert [t["state"] for t in out["tickers"]] == ["pending"]
    assert scanner.pending()[0] == "PLTR"
    gate.set()
    scanner._WORKER["thread"].join(5)
    assert asked == ["AAPL", "PLTR", "MSFT", "AMZN"]
    assert [t["state"] for t in scanner.board(["PLTR"], tmp_path)["tickers"]] == ["ready"]


def _doc(stamp, oi):
    return {"schema": scanner.COMPACT_SCHEMA, "symbol": "NVDA", "fetchedAt": stamp, "spot": 180.0,
            "rows": [{"side": "CALL", "expiry": "2027-03-19", "strike": 200.0, "oi": oi, "volume": 10, "delta": 0.2,
                      "days": 122, "bid": 5.0, "ask": 5.2}]}


def test_open_interest_change_is_against_the_previous_day_not_the_last_read(tmp_path):
    """2026-11-17 "All six": the change is vs the last read on an EARLIER day (the OCC posts open interest once a
    day), carried through later reads the same day; no earlier day -> no change shown, never a made-up 0."""
    monday = _doc("2026-11-16T20:00:00-05:00", 1000)
    tuesday = _doc("2026-11-17T08:00:00-05:00", 1500)
    tuesday_later = _doc("2026-11-17T13:00:00-05:00", 1500)
    assert "prevOi" not in scanner.with_previous(None, monday)
    assert "prevOi" not in scanner.with_previous(tuesday, tuesday_later)        # same day, nothing earlier
    second = scanner.with_previous(monday, tuesday)
    assert second["prevOi"] == {"CALL|2027-03-19|200": 1000} and second["prevAt"] == monday["fetchedAt"]
    carried = scanner.with_previous(second, tuesday_later)
    assert carried["prevOi"] == second["prevOi"] and carried["prevAt"] == monday["fetchedAt"]
    row, = scanner.ticker_rows(carried, min_days=1, max_days=400, side="calls")
    assert row["oiChange"] == 500 and row["oiChangeSince"] == monday["fetchedAt"]
    first, = scanner.ticker_rows(monday, min_days=1, max_days=400, side="calls")
    assert first["oiChange"] is None and first["oiChangeSince"] is None


def test_the_previous_day_survives_a_restart(tmp_path):
    scanner._save_compact(tmp_path, "NVDA", _doc("2026-11-16T20:00:00-05:00", 1000))
    scanner.reset()                                                              # memory gone, the file stays
    saved = scanner._save_compact(tmp_path, "NVDA", _doc("2026-11-17T08:00:00-05:00", 900))
    assert saved["prevOi"] == {"CALL|2027-03-19|200": 1000}
    row, = scanner.ticker_rows(saved, min_days=1, max_days=400, side="calls")
    assert row["oiChange"] == -100



def test_a_weekend_read_keeps_fridays_real_change(tmp_path):
    """Review 2026-11-17: Saturday and Sunday reads see Friday's numbers again; the page must keep showing Friday's
    real change, not "no change" on every row."""
    thursday = _doc("2026-11-12T20:00:00-05:00", 1000)
    friday = scanner.with_previous(thursday, _doc("2026-11-13T09:00:00-05:00", 1400))
    saturday = scanner.with_previous(friday, _doc("2026-11-14T09:00:00-05:00", 1400))
    sunday = scanner.with_previous(saturday, _doc("2026-11-15T09:00:00-05:00", 1400))
    for doc in (friday, saturday, sunday):
        row, = scanner.ticker_rows(doc, min_days=1, max_days=400, side="calls")
        assert row["oiChange"] == 400 and row["oiChangeSince"] == thursday["fetchedAt"]
    monday = scanner.with_previous(sunday, _doc("2026-11-16T09:00:00-05:00", 1300))   # the next real posting
    row, = scanner.ticker_rows(monday, min_days=1, max_days=400, side="calls")
    assert row["oiChange"] == -100 and row["oiChangeSince"] == sunday["fetchedAt"]


def test_a_brand_new_build_up_shows_its_whole_change(tmp_path):
    """Review 2026-11-17: a strike outside yesterday's kept rows (or with no open interest) still gets a change, from
    the whole chain's open interest kept on disk (allOi)."""
    yesterday = {**_doc("2026-11-16T20:00:00-05:00", 1000), "allOi": {"CALL|2027-03-19|200": 1000, "CALL|2027-03-19|210": 2000}}
    today = _doc("2026-11-17T09:00:00-05:00", 1500)
    today["rows"] += [{**today["rows"][0], "strike": 210.0, "oi": 40000}, {**today["rows"][0], "strike": 230.0, "oi": 9000}]
    scanner._save_compact(tmp_path, "NVDA", yesterday)
    saved = scanner._save_compact(tmp_path, "NVDA", today)
    assert "allOi" not in saved and "allOi" not in scanner._COMPACT["NVDA"]      # memory stays small
    changes = {r["strike"]: r["oiChange"] for r in scanner.ticker_rows(saved, min_days=1, max_days=400, side="calls")}
    assert changes == {200.0: 500, 210.0: 38000, 230.0: 9000}
    assert set(scanner.compact({"symbol": "NVDA", "fetchedAt": "x", "spot": 180.0, "rows": []})) >= {"allOi"}


# ---------------------------------------------------------------- closing prices, 2026-11-20
# The NVDA card at 5:39 PM ET showed Bid 1.90 / Mid 1.95 / delta 0.13 from a 12:53 PM read, before NVDA fell 7% into the
# close. Open interest is once a day, but option prices are not: a market-hours copy is read again once after 4:05 PM.
FRIDAY_NOON = NOW.replace(year=2026, month=11, day=20, hour=12, minute=53)


@pytest.mark.parametrize("read,now,again", [
    ((12, 53), (15, 59), False),        # still market hours: the noon copy stands
    ((12, 53), (16, 6), True),          # after the close: read the closing prices once
    ((12, 53), (17, 39), True),
    ((16, 10), (20, 0), False),         # read after the close already
    ((7, 0), (16, 6), True),            # a premarket read is also before the close
])
def test_a_market_hours_copy_is_read_again_after_the_close(read, now, again):
    doc = {"fetchedAt": FRIDAY_NOON.replace(hour=read[0], minute=read[1]).isoformat()}
    assert scanner._read_before_close(doc, FRIDAY_NOON.replace(hour=now[0], minute=now[1])) is again


def test_a_weekend_read_is_never_a_before_close_read():
    saturday = FRIDAY_NOON + timedelta(days=1)
    assert scanner._read_before_close({"fetchedAt": saturday.isoformat()}, saturday + timedelta(hours=6)) is False


def test_the_board_keeps_the_noon_copy_on_screen_and_reads_the_close_again(tmp_path, fake, monkeypatch):
    scanner.board(["NVDA"], tmp_path, FRIDAY_NOON, wait=True)
    long_oi.reset()
    queued: list = []
    monkeypatch.setattr(scanner, "_schedule", lambda tickers, root, front=False: queued.append((list(tickers), front)))
    before = scanner.board(["NVDA"], tmp_path, FRIDAY_NOON.replace(hour=15, minute=50))
    assert before["tickers"][0]["state"] == "ready" and queued == [] and fake == ["NVDA"]
    assert before["rows"] and all(row["readAt"] == FRIDAY_NOON.isoformat() for row in before["rows"])   # prices "as of"
    after = scanner.board(["NVDA"], tmp_path, FRIDAY_NOON.replace(hour=16, minute=6))
    assert after["tickers"][0]["state"] == "ready" and after["rows"], after["tickers"]    # never blank while it reads
    assert queued == [(["NVDA"], False)]
    # The warm-up also queues it, once, and a copy read after the close is left alone.
    queued.clear()
    assert scanner.warm(["NVDA"], tmp_path, FRIDAY_NOON.replace(hour=16, minute=6)) == 1
    scanner._save_compact(tmp_path, "NVDA", {**scanner.held("NVDA", tmp_path, FRIDAY_NOON), "fetchedAt": FRIDAY_NOON.replace(hour=16, minute=7).isoformat()})
    assert scanner.warm(["NVDA"], tmp_path, FRIDAY_NOON.replace(hour=16, minute=20)) == 0
