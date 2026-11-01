"""momx/jev_scanner.py - Jev scores every matched scanner row; the bot's "Jev pick" rule (no network)."""
from __future__ import annotations

import pytest

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from momx import jev_scanner as js
from momx import option_track as ot

ET = ZoneInfo("America/New_York")
MON = datetime(2026, 10, 5, 10, 0, tzinfo=ET)


@pytest.fixture(autouse=True)
def _rows_are_live(monkeypatch):
    """Fixture rows carry no candle times; option_track.live_today (3f3344b) has its own tests."""
    from momx import option_track as _ot
    monkeypatch.setattr(_ot, "live_today", lambda row, now: True)


class FakeJudge:
    def __init__(self, p=0.4):
        self.asked, self.p = [], p

    def submit(self, state, side, done):
        self.asked.append((state, side))
        done({"jevP": self.p, "regime": 0.8, "direction": 0.7, "pressure": 0.6, "quality": 1.4, "risk": 0.5, "news": 0.5})
        return True


def row(sym="X", grade="A", pct=2.0):
    return {"symbol": sym, "last": 100.0, "pctChange": pct, "grade": {"letter": grade},
            "rvol": {"15m": {"value": 4.0, "bg": "cyan"}}, "m5": {"vwap": 99.0, "ema21": 99.5}}


def test_scores_each_matched_row_once_until_its_picture_changes(tmp_path):
    judge = FakeJudge(0.4)
    book = js.JevScannerBook(tmp_path, judge=judge)
    payload = {"rows": [row("X")], "rest": [row("Y")]}
    book.apply("Watchlist", payload, MON)
    assert [s for s, _ in judge.asked][0].startswith("Ticker: X") and len(judge.asked) == 1     # only matches
    assert "not chosen yet" in judge.asked[0][0] and "scanner match" in judge.asked[0][0]
    assert payload["rows"][0]["jevRow"]["p"] == 0.4 and "jevRow" not in payload["rest"][0]
    book.apply("Watchlist", {"rows": [row("X")]}, MON + timedelta(minutes=20))                     # same picture
    assert len(judge.asked) == 1
    book.apply("Watchlist", {"rows": [row("X", grade="A+")]}, MON + timedelta(minutes=5))          # changed, too soon
    assert len(judge.asked) == 1
    book.apply("Watchlist", {"rows": [row("X", grade="A+")]}, MON + timedelta(minutes=11))         # changed + 10 min
    assert len(judge.asked) == 2
    book.apply("Watchlist", {"rows": [row("X", grade="A+")]}, MON + timedelta(minutes=45))         # stale after 30 min
    assert len(judge.asked) == 3
    lines = (tmp_path / js.DIRNAME / "2026-10-05.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3 and json.loads(lines[0])["symbol"] == "X"


def test_after_hours_it_scores_once_and_re_reads_only_on_change(tmp_path):
    judge = FakeJudge()
    book = js.JevScannerBook(tmp_path, judge=judge)
    evening = MON.replace(hour=20, minute=48)
    payload = {"rows": [row()]}
    book.apply("Watchlist", payload, evening)
    assert len(judge.asked) == 1 and payload["rows"][0]["jevRow"]["p"] == 0.4
    later = {"rows": [row()]}
    book.apply("Watchlist", later, evening + timedelta(hours=2))          # no change: no new ask, still shown
    assert len(judge.asked) == 1 and later["rows"][0]["jevRow"]["p"] == 0.4


def test_retired_jev_scanner_take_does_not_create_a_bot_trade(tmp_path):
    chain = {"underlyingPrice": 100.2, "selectedExpiryChainRows": [
        {"symbol": "X_C101", "side": "CALL", "strike": 101.0, "expiry": "2026-10-09", "days_to_expiration": 4,
         "bid": 1.0, "ask": 1.1, "delta": 0.4}]}
    asked = []

    class Jev:
        def submit(self, state, side, done):
            asked.append(state)
            return True

    b = ot.OptionTrackBook(tmp_path, fetch=lambda s: chain, healthy=lambda: True, background=False,
                           clock=lambda: MON.timestamp(), sleep=lambda s: None, jev=Jev())
    r = dict(row(), jevRow={"p": 0.45, "at": MON.isoformat()})
    b.apply("Watchlist", {"rows": [r]}, MON)
    assert not any(x["rule"] == "jev" for x in b.trades())
    assert not any(x.get("jevSource") == "scanner" for x in b.trades())
    old = dict(row("Z"), jevRow={"p": 0.9, "at": (MON - timedelta(minutes=40)).isoformat()})
    b.apply("Watchlist", {"rows": [old]}, MON)
    assert not any(x["rule"] == "jev" and x["symbol"] == "Z" for x in b.trades())   # too old


def test_setup_tags_today_only_and_in_the_description():
    from momx.jev_trade import describe, setup_tags
    r = dict(row(), momoxAPlus={"at": "2026-10-05T09:50:00-04:00"}, marketTurn={"rank": 2, "at": "2026-10-04T10:00:00-04:00"},
             dayLines={"zs": "2026-10-05T10:01:00-04:00", "jev": "2026-10-05T10:02:00-04:00"},
             chartSignals=[{"label": "CALL2H", "seenAt": "2026-10-05T09:40:00-04:00"},
                           {"label": "C4H", "seenAt": "2026-10-05T09:41:00-04:00", "goneAt": "2026-10-05T10:00:00-04:00"}])
    r["m5"] = dict(r["m5"], sqzMom={"color": "cyan"})
    tags = setup_tags(r, "bull", "2026-10-05")
    assert tags == ["grade A", "MX A+", "ZS↑", "CALL2H chart arrow", "MOM↑ (momentum getting stronger)"]
    text = describe({"rule": "scanner", "symbol": "X", "at": "2026-10-05T10:05:00-04:00", "stockPrice": 100.0}, r, None, "bull", "2026-10-05")
    assert "Setup tags the scanner shows for this stock today (the trader's own signals): grade A, MX A+" in text


def test_a_row_with_stale_volume_bars_waits_and_the_bot_text_warns():
    """APLD 10-02: at 09:44 the row still had 09:05-09:10 RVOL bars and an old price."""
    import tempfile
    from pathlib import Path
    from momx.jev_trade import describe
    stale_bar = int(datetime(2026, 10, 5, 9, 5, tzinfo=ET).timestamp())
    fresh_bar = int(datetime(2026, 10, 5, 9, 50, tzinfo=ET).timestamp())
    stale = dict(row(), rvol={"5m": {"value": 0.2, "bg": "black", "barAt": stale_bar}})
    judge = FakeJudge()
    book = js.JevScannerBook(Path(tempfile.mkdtemp()), judge=judge)
    book.apply("Watchlist", {"rows": [stale]}, MON)                                   # 10:00, bar 50 min old
    assert judge.asked == []
    fresh = dict(row(), rvol={"5m": {"value": 1.5, "bg": "cyan", "barAt": fresh_bar}})
    book.apply("Watchlist", {"rows": [fresh]}, MON)
    assert len(judge.asked) == 1
    text = describe({"rule": "zs2", "symbol": "X", "at": "2026-10-05T10:00:00-04:00", "stockPrice": 100.0,
                     "contract": "X  261009C00101000", "entry": 1.0}, stale, None, "bull", "2026-10-05")
    assert "WARNING: the scanner had not refreshed this stock's volume yet" in text and "50 minutes old" in text
    assert "WARNING" not in describe({"rule": "zs2", "symbol": "X", "at": "2026-10-05T10:00:00-04:00", "stockPrice": 100.0},
                                     fresh, None, "bull", "2026-10-05")


def test_his_four_check_list_uses_his_thresholds():
    from momx.jev_trade import checklist, checklist_line
    r = {"news": {"headline": "x", "at": "2026-10-05T13:00:00+00:00"},
         "rvol": {"5m": {"value": 4.9}, "15m": {"value": 5.0}, "30m": {"value": 2.9}, "D": {"value": 3.0}},
         "sqz": {"2h": {"bg": "cyan"}, "4h": {"bg": "black"}},
         "skittles": {tf: {"bg": "cyan"} for tf in ("2h", "4h", "D", "2D", "3D", "4D")}}
    c = checklist(r, "bull", "2026-10-05T10:00:00-04:00")
    assert c["news"] and c["rvol"] == ["15m 5.0", "D 3.0"] and c["sqz"] == ["2h"] and len(c["skit"]) == 6
    assert "(4 of 4 met)" in checklist_line(r, "bull", "2026-10-05T10:00:00-04:00")
    assert "not met" in checklist_line({"rvol": {"5m": {"value": 4.9}}}, "bull")


def test_chart_levels_line_has_pivots_walls_and_the_other_sides_arrows():
    from momx import jev_trade
    jev_trade.register_arrows("bear", lambda s: [{"label": "PUT2H", "seenAt": "2026-10-05T11:05:00-04:00"}])
    try:
        r = dict(row(), prevClose=100.0)
        r["m5"] = dict(r["m5"], pillars={"prevHigh": 102.0, "premarketHigh": 101.5, "ribbon5m": {"trend": "bull"}},
                       dayLine={"prevLow": 98.0})
        t = {"rule": "go", "symbol": "X", "at": "2026-10-05T10:00:00-04:00", "stockPrice": 100.5,
             "callWalls": [{"strike": 105, "oi": 9000}], "putWalls": [{"strike": 95, "oi": 4000}], "expectedMove": 2.1}
        text = jev_trade.describe(t, r, None, "bull", "2026-10-05")
        line = next(x for x in text.split("\n") if x.startswith("Chart levels and signals:"))
        assert "P 100.00, R1 102.00, R2 104.00, S1 98.00, S2 96.00" in line
        assert "calls 105 (9000 OI)" in line and "puts 95 (4000 OI)" in line and "+/-2.10" in line
        assert "premarket high 101.50" in line and "ribbon 5m: bull" in line and "(PUT side): PUT2H" in line
    finally:
        jev_trade._ARROWS.clear()


def test_retired_jev_rule_ignores_old_10_percent_entry_threshold(tmp_path):
    chain = {"underlyingPrice": 100.2, "selectedExpiryChainRows": [
        {"symbol": "X_C101", "side": "CALL", "strike": 101.0, "expiry": "2026-10-09", "days_to_expiration": 4,
         "bid": 1.0, "ask": 1.1, "delta": 0.4}]}

    class Jev:
        def submit(self, state, side, done):
            return True

    b = ot.OptionTrackBook(tmp_path, fetch=lambda s: chain, healthy=lambda: True, background=False,
                           clock=lambda: MON.timestamp(), sleep=lambda s: None, jev=Jev())
    r = dict(row(), jevRow={"p": 0.12, "at": MON.isoformat()})
    b.apply("Watchlist", {"rows": [r]}, MON)
    b.apply("Watchlist", {"rows": [r]}, MON)
    assert not any(x["rule"] == "jev" for x in b.trades())
    assert "jevPick" not in r                       # the scanner's ✓ JEV tag needs 30%


def test_parse_records_the_model_that_answered():
    """Cut-offs are calibrated to one model; jev-latest moves, so each score keeps its version."""
    from momx.jev_trade import parse
    out = parse({"model": "jev-1.13.0", "answers": {"action": {"probabilities": {"take": 0.12}}}})
    assert out["jevP"] == 0.12 and out["jevModel"] == "jev-1.13.0"
