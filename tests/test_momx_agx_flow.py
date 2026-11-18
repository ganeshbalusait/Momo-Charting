"""Unit: AGX Flow (momx/agx_flow.py, 2026-11-17). Schwab is always a fake; the disk copy goes to tmp_path."""
from __future__ import annotations

import gzip
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from momx import agx_flow

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 17, 10, 0, tzinfo=ET)
TODAY = NOW.date()


def _key(days: int) -> str:
    return f"{(TODAY + timedelta(days=days)).isoformat()}:{days}"


def _contract(strike, volume, oi, *, last=2.0, bid=1.9, ask=2.1, mark=2.0, delta=0.4, vol=35.0):
    return {"strikePrice": strike, "totalVolume": volume, "openInterest": oi, "last": last, "bid": bid, "ask": ask,
            "mark": mark, "delta": delta, "volatility": vol}


def chain(volume_185=6000, spot=180.0):
    """NVDA-like: a 185 call bought at the ask with volume over OI, a 170 put sold at the bid, a quiet 190 call,
    a 160 put below MIN_VOLUME, and an expiry 60 days out that is outside the window."""
    return {
        "underlyingPrice": spot,
        "callExpDateMap": {
            _key(3): {"185.0": [_contract(185, volume_185, 1200, last=2.15, mark=2.0, delta=0.35)],
                      "190.0": [_contract(190, 900, 5000, delta=0.2)]},
            _key(60): {"185.0": [_contract(185, 99999, 1, mark=5.0)]},
        },
        "putExpDateMap": {
            _key(10): {"170.0": [_contract(170, 3000, 400, last=1.0, bid=1.0, ask=1.2, mark=None, delta=-0.3, vol=-999)],
                       "160.0": [_contract(160, 450, 10, delta=-0.1)]},
        },
    }


@pytest.fixture
def schwab(monkeypatch):
    asked: list[str] = []
    replies: dict = {}

    def fake(ticker, today):
        asked.append(ticker)
        reply = replies.get(ticker, chain)
        if isinstance(reply, Exception):
            raise reply
        return reply() if callable(reply) else reply

    monkeypatch.setattr(agx_flow, "fetch", fake)
    agx_flow.reset()
    yield asked, replies
    agx_flow.wait_idle(5)
    agx_flow.reset()


def test_parse_symbols_rejects_garbage_and_caps_at_four_hundred():
    assert agx_flow.parse_symbols("aapl, NVDA,nvda,BRK.B,  ,12AB,TOOLONGX,<x>,SPY") == ["AAPL", "NVDA", "BRK.B", "SPY"]
    assert len(agx_flow.parse_symbols([f"A{chr(65 + i % 26)}{chr(65 + i // 26)}" for i in range(40)])) == 40
    assert len(agx_flow.parse_symbols([f"{chr(65 + i % 26)}{chr(65 + i // 26 % 26)}{chr(65 + i // 676)}" for i in range(500)])) == agx_flow.MAX_SYMBOLS == 400
    assert agx_flow.parse_symbols(None) == []


def test_fill_guess_and_mark():
    assert agx_flow.fill_guess(2.1, 1.9, 2.1) == "ask"
    assert agx_flow.fill_guess(1.9, 1.9, 2.1) == "bid"
    assert agx_flow.fill_guess(2.0, 1.9, 2.1) == "mid"
    assert agx_flow.fill_guess(None, 1.9, 2.1) == "mid"
    assert agx_flow.mark_price(None, 1.0, 1.2) == pytest.approx(1.1)
    assert agx_flow.mark_price(0, None, None) is None


def test_unusual_needs_500_and_volume_over_oi():
    assert agx_flow.is_unusual(500, 499)
    assert not agx_flow.is_unusual(499, 10)
    assert not agx_flow.is_unusual(900, 900)


def test_board_rows_premium_fill_and_window(schwab, tmp_path):
    asked, _ = schwab
    out = agx_flow.board("NVDA", tmp_path, NOW, wait=True)
    assert asked == ["NVDA"]
    rows = out["rows"]
    assert [(r["side"], r["strike"]) for r in rows] == [("CALL", 185.0), ("PUT", 170.0)]   # 60-day and quiet ones left out
    call, put = rows
    assert call["premium"] == 6000 * 2.0 * 100 and call["fill"] == "ask" and call["volOi"] == 5.0 and call["days"] == 3
    assert put["premium"] == round(3000 * 1.1 * 100) and put["fill"] == "bid" and put["iv"] is None and put["delta"] == -0.3
    assert call["newVolume"] == 0 and call["ticker"] == "NVDA" and call["spot"] == 180.0
    t = out["tickers"][0]
    assert t["unusualCount"] == 2 and t["callVolume"] == 6900 and t["putVolume"] == 3450 and t["error"] is None
    assert t["cached"] is True and t["pending"] is False
    assert "not a direction signal" in out["note"]
    assert (tmp_path / "agx_flow_cache" / "NVDA.json.gz").exists()


def test_cache_five_minutes_then_new_volume_since_last_read(schwab, tmp_path, monkeypatch):
    asked, replies = schwab
    agx_flow.board(["NVDA"], tmp_path, NOW, wait=True)
    agx_flow.board(["NVDA"], tmp_path, NOW + timedelta(minutes=4), wait=True)
    assert asked == ["NVDA"]                                       # the 5-minute copy answered
    replies["NVDA"] = lambda: chain(volume_185=6800)
    out = agx_flow.board(["NVDA"], tmp_path, NOW + timedelta(minutes=6), wait=True)
    assert asked == ["NVDA", "NVDA"]
    assert out["rows"][0]["volume"] == 6800 and out["rows"][0]["newVolume"] == 800


def test_new_volume_is_zero_on_the_first_read_of_a_new_day(schwab, tmp_path):
    asked, replies = schwab
    agx_flow.board(["NVDA"], tmp_path, NOW, wait=True)
    replies["NVDA"] = lambda: chain(volume_185=700)
    out = agx_flow.board(["NVDA"], tmp_path, NOW + timedelta(days=1), wait=True)
    assert out["rows"][0]["newVolume"] == 0


def test_disk_copy_answers_after_a_restart(schwab, tmp_path):
    asked, _ = schwab
    agx_flow.board(["NVDA"], tmp_path, NOW, wait=True)
    agx_flow.reset()                                               # memory gone, disk stays
    out = agx_flow.board(["NVDA"], tmp_path, NOW + timedelta(minutes=1), wait=True)
    assert asked == ["NVDA"] and out["tickers"][0]["cached"] is True and len(out["rows"]) == 2
    with gzip.open(tmp_path / "agx_flow_cache" / "NVDA.json.gz", "rt", encoding="utf-8") as handle:
        assert json.load(handle)["symbol"] == "NVDA"


def test_failure_is_remembered_five_minutes(schwab, tmp_path):
    asked, replies = schwab
    replies["TSLA"] = RuntimeError("boom")
    out = agx_flow.board(["TSLA", "NVDA"], tmp_path, NOW, wait=True)
    assert "Schwab did not answer for TSLA" in out["tickers"][0]["error"] and out["tickers"][1]["error"] is None
    agx_flow.board(["TSLA"], tmp_path, NOW, wait=True)
    assert asked == ["TSLA", "NVDA"]                               # not asked again
    assert all(r["ticker"] == "NVDA" for r in out["rows"])


def test_empty_chain_is_an_error_not_a_crash(schwab, tmp_path):
    _, replies = schwab
    replies["XYZ"] = {}
    out = agx_flow.board(["XYZ"], tmp_path, NOW, wait=True)
    assert "no options" in out["tickers"][0]["error"] and out["rows"] == []


def test_hourly_cap(schwab, tmp_path, monkeypatch):
    asked, _ = schwab
    monkeypatch.setattr(agx_flow, "FETCHES_PER_HOUR", 2)
    out = agx_flow.board(["AAPL", "MSFT", "NVDA"], tmp_path, NOW, wait=True)
    assert asked == ["AAPL", "MSFT"]
    assert "many chains this hour" in out["tickers"][2]["error"]


def test_no_valid_ticker_raises(schwab, tmp_path):
    with pytest.raises(ValueError):
        agx_flow.board("<script>,12", tmp_path, NOW)


def test_board_never_waits_and_refreshes_in_the_background(schwab, tmp_path):
    asked, _ = schwab
    first = agx_flow.board("AAPL,NVDA", tmp_path)                  # real clock, background thread
    assert first["rows"] == [] or all(t["pending"] or t["cached"] for t in first["tickers"])
    assert agx_flow.wait_idle(5)
    assert sorted(asked) == ["AAPL", "NVDA"]
    second = agx_flow.board("AAPL,NVDA", tmp_path)
    assert all(t["cached"] and not t["pending"] for t in second["tickers"])
    assert sorted(asked) == ["AAPL", "NVDA"]                       # the fresh copies answered
    assert len(second["rows"]) == 4


def test_top_sixty_sorted_by_premium(schwab, tmp_path):
    _, replies = schwab

    def big():
        strikes = {f"{100 + i}.0": [_contract(100 + i, 1000 + i, 1, mark=1.0)] for i in range(80)}
        return {"underlyingPrice": 150, "callExpDateMap": {_key(5): strikes}, "putExpDateMap": {}}
    replies["AMD"] = big
    rows = agx_flow.board(["AMD"], tmp_path, NOW, wait=True)["rows"]
    assert len(rows) == 60 and rows[0]["strike"] == 179.0
    assert [r["premium"] for r in rows] == sorted((r["premium"] for r in rows), reverse=True)


def test_whole_watchlist_is_read_in_the_background_paced_and_untraded_contracts_are_dropped(schwab, tmp_path, monkeypatch):
    """2026-11-17 "what about other tickers from watchlist": the Watchlist board sends ~360 tickers. They are read one
    at a time, PACE_SECONDS apart, and only contracts that traded are kept."""
    asked, replies = schwab
    naps: list = []
    monkeypatch.setattr(agx_flow, "PACE_SECONDS", 3.5)
    import threading
    import time as _time
    real_sleep = _time.sleep
    # time.sleep is the whole program's: count only the readers' own pauses, let every other thread sleep as usual.
    monkeypatch.setattr(agx_flow.time, "sleep", lambda seconds: naps.append(seconds)
                        if threading.current_thread().name.startswith("agx-flow-reader") else real_sleep(seconds))
    quiet = chain()
    quiet["callExpDateMap"][_key(3)]["190.0"][0]["totalVolume"] = 0
    replies["SOFI"] = quiet
    tickers = ["NVDA", "PLTR", "SOFI"]
    agx_flow.board(tickers, tmp_path)
    assert agx_flow.wait_idle(5)
    assert sorted(asked) == sorted(tickers) and naps == [3.5, 3.5, 3.5]   # two readers: order may interleave
    doc = agx_flow._held("SOFI", tmp_path)
    assert doc["contracts"] and all(row["volume"] > 0 for row in doc["contracts"])
    assert not any(row["strike"] == 190 for row in doc["contracts"])
    out = agx_flow.board(tickers, tmp_path)
    assert {t["ticker"] for t in out["tickers"]} == set(tickers) and all(t["cached"] for t in out["tickers"])


def test_two_readers_read_at_the_same_time_and_never_the_same_ticker(schwab, tmp_path):
    """2026-11-17 "Still I don't see full loading": one reader took ~36 minutes for the watchlist. Two readers work at
    once (never more than READERS Schwab requests in flight) and no ticker is read twice."""
    import threading
    import time as _time
    asked, replies = schwab
    inside = {"now": 0, "most": 0}
    guard = threading.Lock()

    def slow():
        with guard:
            inside["now"] += 1
            inside["most"] = max(inside["most"], inside["now"])
        _time.sleep(0.05)
        with guard:
            inside["now"] -= 1
        return chain()
    tickers = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
    for t in tickers:
        replies[t] = slow
    agx_flow.board(tickers, tmp_path)
    assert agx_flow.wait_idle(5)
    assert sorted(asked) == tickers                                # each once
    assert inside["most"] == agx_flow.READERS == 2


def test_after_the_close_a_read_stays_fresh_until_the_next_open(schwab, tmp_path):
    """Option volume does not move after 16:00 ET, so the whole watchlist is read once after the close and then left
    alone overnight and over the weekend; at 9:30 it is read again."""
    asked, _ = schwab
    friday_close = datetime(2026, 11, 20, 16, 5, tzinfo=ET)       # Friday
    agx_flow.board(["NVDA"], tmp_path, friday_close, wait=True)
    for later in (datetime(2026, 11, 20, 23, 0, tzinfo=ET), datetime(2026, 11, 22, 12, 0, tzinfo=ET),
                  datetime(2026, 11, 23, 9, 29, tzinfo=ET)):
        agx_flow.board(["NVDA"], tmp_path, later, wait=True)
    assert asked == ["NVDA"]
    agx_flow.board(["NVDA"], tmp_path, datetime(2026, 11, 23, 9, 31, tzinfo=ET), wait=True)
    assert asked == ["NVDA", "NVDA"]                               # Monday's open reads again
    # A read from before the close is re-read once after it (to get the day's final volume).
    agx_flow.board(["TSLA"], tmp_path, datetime(2026, 11, 23, 15, 58, tzinfo=ET), wait=True)
    agx_flow.board(["TSLA"], tmp_path, datetime(2026, 11, 23, 16, 1, tzinfo=ET), wait=True)
    assert asked.count("TSLA") == 2
    assert agx_flow.last_close(datetime(2026, 11, 23, 9, 0, tzinfo=ET)) == datetime(2026, 11, 20, 16, 0, tzinfo=ET)


def test_warm_queues_only_stale_tickers_without_a_page_open(schwab, tmp_path):
    asked, replies = schwab
    replies["BAD"] = RuntimeError("no chain")
    agx_flow.board(["NVDA"], tmp_path, wait=True)                  # NVDA fresh now
    queued = agx_flow.warm(["NVDA", "PLTR", "BAD", "bad ticker"], tmp_path)
    assert queued == 2 and agx_flow.wait_idle(5)
    assert sorted(asked) == ["BAD", "NVDA", "PLTR"]
    assert agx_flow.warm(["NVDA", "PLTR", "BAD"], tmp_path) == 0   # fresh, fresh, failed recently


def test_no_etfs_and_no_stocks_under_ten_dollars(schwab, tmp_path):
    """2026-11-17 "No need spy, qqq and small stocks and etf": ETFs are never read or shown, and a stock read under $10
    is left off the board and not read again that day."""
    asked, replies = schwab
    replies["NIO"] = lambda: chain(spot=4.2)
    out = agx_flow.board(["SPY", "NVDA", "QQQ", "NIO", "MSTU"], tmp_path, NOW, wait=True)
    assert asked == ["NVDA", "NIO"]                                     # ETFs never asked
    assert [t["ticker"] for t in out["tickers"]] == ["NVDA"]
    assert {r["ticker"] for r in out["rows"]} == {"NVDA"}
    assert out["excluded"] == [{"ticker": "SPY", "reason": "ETF"}, {"ticker": "QQQ", "reason": "ETF"},
                               {"ticker": "MSTU", "reason": "ETF"}, {"ticker": "NIO", "reason": "under $10"}]
    assert out["minPrice"] == 10.0
    assert agx_flow.warm(["NIO", "SPY"], tmp_path, NOW + timedelta(minutes=30)) == 0   # NIO stays out today
    assert agx_flow.warm(["NIO"], tmp_path, NOW + timedelta(days=1)) == 1              # a new day checks the price
    assert agx_flow.wait_idle(5)
    with pytest.raises(ValueError, match="stocks only"):
        agx_flow.board(["SPY", "QQQ"], tmp_path, NOW)


def test_a_ticker_with_no_options_is_not_asked_again_for_an_hour(schwab, tmp_path, monkeypatch):
    """2026-11-17 screenshot "Reading Schwab... (260 of 261)": KITT and LIDR have no options. Their failure was forgotten
    after 5 minutes, so they were queued again and the page went back to "reading". Now remembered an hour; a Schwab
    hiccup on a real ticker is still retried after 5 minutes."""
    asked, replies = schwab
    replies["KITT"] = {}
    replies["TSLA"] = RuntimeError("boom")
    clock = [5000.0]
    monkeypatch.setattr(agx_flow.time, "monotonic", lambda: clock[0])
    agx_flow.board(["KITT", "TSLA"], tmp_path, NOW, wait=True)
    assert asked == ["KITT", "TSLA"]
    clock[0] += 6 * 60
    out = agx_flow.board(["KITT"], tmp_path, NOW + timedelta(minutes=6))
    assert out["tickers"][0]["pending"] is False and "no options" in out["tickers"][0]["error"]
    assert agx_flow.warm(["KITT", "TSLA"], tmp_path, NOW + timedelta(minutes=6)) == 1   # only TSLA is retried
    assert agx_flow.wait_idle(5) and asked == ["KITT", "TSLA", "TSLA"]
    clock[0] += 61 * 60
    assert agx_flow.warm(["KITT"], tmp_path, NOW + timedelta(minutes=70)) == 1
    assert agx_flow.wait_idle(5) and asked[-1] == "KITT"


def test_a_searched_ticker_is_read_before_the_warm_up_queue(schwab, tmp_path, monkeypatch):
    """2026-11-17 "Add search ticker also": a ticker typed into the search box jumps ahead of the warm-up's long
    queue (about 260 names), so the trader is not left waiting minutes for it."""
    import threading as _threading
    asked, replies = schwab
    monkeypatch.setattr(agx_flow, "READERS", 1)
    gate = _threading.Event()
    started = _threading.Event()

    def slow():
        started.set()
        gate.wait(5)                                       # the reader is busy on the first warm-up ticker
        return chain()
    replies["AAPL"] = slow
    assert agx_flow.warm(["AAPL", "MSFT", "AMZN"], tmp_path) == 3
    assert started.wait(5)
    out = agx_flow.board(["PLTR"], tmp_path)              # the search: not read yet, so queued at the front
    assert out["tickers"][0]["pending"] is True
    with agx_flow._STATE:
        waiting = [t for t in agx_flow._PENDING if t not in agx_flow._TAKEN]
    assert waiting == ["PLTR", "MSFT", "AMZN"]
    gate.set()
    assert agx_flow.wait_idle(5)
    assert asked == ["AAPL", "PLTR", "MSFT", "AMZN"]


def test_expiry_windows_this_week_next_week_30_days():
    """2026-11-17 "All six": This week = through this Friday (the coming Friday on a weekend), Next week = the week
    after, 30 days = through 30 days out."""
    from datetime import date
    tue, fri, sat = date(2026, 11, 17), date(2026, 11, 20), date(2026, 11, 21)
    assert agx_flow.expiry_window("week", tue) == (None, fri)
    assert agx_flow.expiry_window("week", fri) == (None, fri)
    assert agx_flow.expiry_window("week", sat) == (None, date(2026, 11, 27))
    assert agx_flow.expiry_window("next", tue) == (fri, date(2026, 11, 27))
    assert agx_flow.expiry_window("month", tue) == (None, date(2026, 12, 17))
    assert agx_flow.expiry_window("all", tue) == (None, None)
    assert agx_flow.in_window("2026-11-20", (None, fri)) and not agx_flow.in_window("2026-11-20", (fri, date(2026, 11, 27)))
    assert agx_flow.in_window("2026-11-19", (None, fri)) and agx_flow.in_window("anything", (None, None))
    assert not agx_flow.in_window("not a date", (None, fri))


def test_expiry_filter_and_hot_right_now_order(schwab, tmp_path):
    asked, replies = schwab
    week = agx_flow.board(["NVDA"], tmp_path, NOW, wait=True, expiry="week")     # call Nov 20, put Nov 27
    assert [(r["side"], r["strike"]) for r in week["rows"]] == [("CALL", 185.0)] and week["expiry"] == "week"
    assert week["tickers"][0]["putPremium"] == 0 and week["tickers"][0]["callPremium"] > 0
    nxt = agx_flow.board(["NVDA"], tmp_path, NOW, expiry="next")
    assert [(r["side"], r["strike"]) for r in nxt["rows"]] == [("PUT", 170.0)] and nxt["tickers"][0]["callPremium"] == 0
    assert len(agx_flow.board(["NVDA"], tmp_path, NOW, expiry="month")["rows"]) == 2
    # Hot right now: only what traded since the last read, most new dollars first.
    assert agx_flow.board(["NVDA"], tmp_path, NOW, order="hot")["rows"] == []        # first read: nothing "new" yet
    replies["NVDA"] = lambda: chain(volume_185=7000)
    later = NOW + timedelta(minutes=6)
    hot = agx_flow.board(["NVDA"], tmp_path, later, wait=True, order="hot")
    assert [(r["strike"], r["newVolume"], r["newPremium"]) for r in hot["rows"]] == [(185.0, 1000, 200_000)]
    odd = agx_flow.board(["NVDA"], tmp_path, later, expiry="bogus", order="bogus")
    assert odd["expiry"] == "all" and odd["order"] == "premium" and len(odd["rows"]) == 2



def test_market_holidays_are_closed_and_hot_uses_only_this_sessions_reads(schwab, tmp_path):
    """Review 2026-11-17: Thanksgiving is not a session; and Hot right now never shows yesterday's after-close volume
    before the open (it is empty and says the market is closed)."""
    assert agx_flow.market_open(NOW) is True
    assert agx_flow.market_open(datetime(2026, 11, 26, 11, 0, tzinfo=ET)) is False       # Thanksgiving
    asked, replies = schwab
    close = NOW.replace(hour=15, minute=50)
    agx_flow.board(["NVDA"], tmp_path, close, wait=True)
    replies["NVDA"] = lambda: chain(volume_185=7000)
    after = NOW.replace(hour=16, minute=5)
    agx_flow.board(["NVDA"], tmp_path, after, wait=True)                                    # +1000 after the close
    premarket = (NOW + timedelta(days=1)).replace(hour=8, minute=30)
    out = agx_flow.board(["NVDA"], tmp_path, premarket, order="hot")
    assert out["rows"] == [] and out["marketOpen"] is False
    assert agx_flow.board(["NVDA"], tmp_path, premarket)["rows"]                          # Biggest $ still shows them
