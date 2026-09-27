"""momx/market_turn.py - SPY back above VWAP after the morning dip -> top 5."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from momx import market_turn as mt

ET = ZoneInfo("America/New_York")
DAY = "2026-09-25"


def t(hhmm):
    h, m = hhmm.split(":")
    return datetime.fromisoformat(f"{DAY}T{int(h):02d}:{int(m):02d}:00").replace(tzinfo=ET)


def bar(hhmm, high, low, close, volume=1000):
    return {"time": int(t(hhmm).timestamp()), "high": high, "low": low, "close": close, "volume": volume}


def spy_day():
    # 09:30 open ~100, dips below VWAP from 09:50, back above VWAP on the 11:00 bar
    out = [bar("09:30", 100.5, 99.8, 100.4, 5000), bar("09:35", 100.6, 100.2, 100.5, 3000),
           bar("09:40", 100.6, 100.3, 100.4), bar("09:45", 100.4, 100.1, 100.2)]
    minute = 9 * 60 + 50
    while minute < 11 * 60:
        out.append(bar(f"{minute // 60}:{minute % 60:02d}", 100.0, 99.6, 99.8))
        minute += 5
    out.append(bar("11:00", 100.9, 99.9, 100.8, 4000))
    return out


def row(symbol, pct, last=10.0, vwap=9.8, plus=30, minus=10, adx=25, industry="Tech"):
    return {"symbol": symbol, "pctChange": pct, "last": last, "industry": industry,
            "m5": {"vwap": vwap}, "adx": {"30m": {"plus": plus, "minus": minus, "adx": adx}}}


def test_spy_turn_needs_a_dip_then_a_close_back_above_vwap():
    turn = mt.spy_turn(spy_day(), t("11:10"))
    assert turn["spyAt"][11:16] == "11:05"          # the 11:00 bar closes at 11:05
    assert mt.spy_turn(spy_day(), t("11:03")) is None  # 11:00 bar still forming
    no_dip = [bar("09:30", 100.5, 99.8, 100.4, 5000)] + [bar(f"10:{m:02d}", 101, 100.6, 100.9) for m in range(0, 30, 5)]
    assert mt.spy_turn(no_dip, t("11:00")) is None


def test_pick_keeps_strong_names_with_30m_buyers_and_no_etfs():
    rows = [row("AAA", 5.0), row("BBB", 3.0), row("SELL", 6.0, plus=10, minus=30), row("FLAT", 4.0, adx=12),
            row("UNDER", 7.0, last=9.0), row("SMALL", 0.5), row("SOXL", 9.0, industry="ETF-Lev")]
    assert [p["symbol"] for p in mt.pick(rows)] == ["AAA", "BBB"]


def test_book_latches_once_persists_and_pushes(tmp_path):
    pushed = []
    book = mt.MarketTurnBook(tmp_path, spy_bars=spy_day, notify=lambda b, turn: pushed.append((b, turn)))
    payload = {"rows": [row("AAA", 5.0), row("BBB", 3.0)], "rest": [row("CCC", 0.2)]}
    book.apply("Watchlist", payload, t("10:30"))              # no turn yet
    assert payload["marketTurn"] is None and pushed == []
    book.apply("Watchlist", payload, t("11:08"))
    assert payload["rows"][0]["marketTurn"]["rank"] == 1
    assert payload["rest"][0]["marketTurn"] is None
    assert len(pushed) == 1
    # later builds keep the SAME picks even when the board changes
    later = {"rows": [row("ZZZ", 9.0)], "rest": []}
    book.apply("Watchlist", later, t("12:00"))
    assert later["marketTurn"]["picks"][0]["symbol"] == "AAA" and len(pushed) == 1
    # a restarted worker reads the day back from disk
    again = mt.MarketTurnBook(tmp_path, spy_bars=spy_day)
    fresh = {"rows": [row("AAA", 5.0)], "rest": []}
    again.apply("Watchlist", fresh, t("13:00"))
    assert fresh["rows"][0]["marketTurn"]["rank"] == 1
    doc = json.loads((tmp_path / mt.DIRNAME / f"{DAY}.json").read_text(encoding="utf-8"))
    assert doc["boards"]["Watchlist"]["picks"][0]["symbol"] == "AAA"


def test_a_turn_first_seen_late_is_logged_without_picks_or_push(tmp_path):
    pushed = []
    book = mt.MarketTurnBook(tmp_path, spy_bars=spy_day, notify=lambda b, turn: pushed.append(b))
    payload = {"rows": [row("AAA", 5.0)], "rest": []}
    book.apply("Watchlist", payload, t("13:30"))
    assert payload["marketTurn"]["late"] is True and payload["marketTurn"]["picks"] == []
    assert payload["rows"][0]["marketTurn"] is None and pushed == []


def test_push_message_is_plain():
    title, body = mt.push_message("Watchlist", {"spyAt": f"{DAY}T11:05:00-04:00",
                                                 "picks": [{"symbol": "BE", "pct": 6.1, "price": 288.0}]})
    assert title == "Market turned up 11:05" and "BE +6.1%" in body and "not a recommendation" in body
