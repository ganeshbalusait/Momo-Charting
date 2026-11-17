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


def test_parse_symbols_rejects_garbage_and_caps_at_twenty():
    assert agx_flow.parse_symbols("aapl, NVDA,nvda,BRK.B,  ,12AB,TOOLONGX,<x>,SPY") == ["AAPL", "NVDA", "BRK.B", "SPY"]
    assert len(agx_flow.parse_symbols([f"A{chr(65 + i % 26)}{chr(65 + i // 26)}" for i in range(40)])) == 20
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
    replies["SPY"] = big
    rows = agx_flow.board(["SPY"], tmp_path, NOW, wait=True)["rows"]
    assert len(rows) == 60 and rows[0]["strike"] == 179.0
    assert [r["premium"] for r in rows] == sorted((r["premium"] for r in rows), reverse=True)
