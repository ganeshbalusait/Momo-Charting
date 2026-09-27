"""momx/momox_aplus.py - the competitor's A+ latched per symbol per day."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from momx import momox_aplus as mx

ET = ZoneInfo("America/New_York")
BULL = {tf: {"bg": "black", "fg": "cyan"} for tf in mx.SKIT_TFS}


def row(**over):
    base = {
        "symbol": "META", "industry": "Interactive Media", "last": 700.0,
        "news": {"headline": "Meta beats", "at": "2026-09-21T08:00:00-04:00"},
        "rvol": {"30m": {"bg": "cyan", "value": 3.2}},
        "sqz": {"4h": {"bg": "cyan"}},
        "skittles": dict(BULL),
        "badge": {"reasons": ["weeklies"]},
    }
    base.update(over)
    return base


def chain(wall_oi=5000, strike=705.0, spot=700.0, em=5.0, stale=False):
    rows = [{"side": "CALL", "strike": strike, "open_interest": wall_oi, "expiry": "2026-09-26"},
            {"side": "PUT", "strike": 690.0, "open_interest": 3000, "expiry": "2026-09-26"}]
    return {"selectedExpiryChainRows": rows, "underlyingPrice": spot, "stale": stale,
            "currentAtm": {"expectedMove": em}}


def book_at(directory, payload=None, **kw):
    fetch = kw.pop("fetch", lambda sym: chain() if payload is None else payload)
    return mx.MomoxAPlusBook(directory, fetch=fetch, background=False, **kw)


def t(hhmm):
    h, m = hhmm.split(":")
    return datetime(2026, 9, 21, int(h), int(m), tzinfo=ET)


def test_all_pillars_latch_once_with_weeklies(tmp_path):
    book = book_at(tmp_path)
    payload = {"rows": [row()], "rest": []}
    book.apply("Watchlist", payload, t("09:35"))
    hit = payload["rows"][0]["momoxAPlus"]
    assert hit["at"][11:16] == "09:35" and hit["weeklies"] is True and hit["sqz"] == ["4h"]
    later = {"rows": [row(last=720.0)], "rest": []}
    book.apply("Watchlist", later, t("10:30"))
    assert later["rows"][0]["momoxAPlus"]["price"] == 700.0      # latched
    again = book_at(tmp_path)                         # restart
    back = {"rows": [row()], "rest": []}
    again.apply("Watchlist", back, t("11:00"))
    assert back["rows"][0]["momoxAPlus"]["at"][11:16] == "09:35"


def test_each_pillar_is_required(tmp_path):
    for bad in (dict(news=None), dict(rvol={"30m": {"bg": "green"}}), dict(sqz={"4h": {"bg": "orange"}}),
                dict(skittles={**BULL, "M": {"bg": "black", "fg": "magenta"}}), dict(industry="ETF-Lev")):
        book = book_at(tmp_path / str(len(str(bad))))
        payload = {"rows": [row(**bad)], "rest": []}
        book.apply("Watchlist", payload, t("09:40"))
        assert payload["rows"][0]["momoxAPlus"] is None, bad


def test_a_squeeze_fire_seen_premarket_counts_for_30_minutes(tmp_path):
    book = book_at(tmp_path)
    book.apply("Watchlist", {"rows": [row(rvol={})], "rest": []}, t("09:20"))   # fire seen, no RVOL yet
    payload = {"rows": [row(sqz={})], "rest": []}
    book.apply("Watchlist", payload, t("09:45"))
    assert payload["rows"][0]["momoxAPlus"] is not None
    stale = book_at(tmp_path / "b")
    stale.apply("Watchlist", {"rows": [row(rvol={})], "rest": []}, t("08:30"))
    p2 = {"rows": [row(sqz={})], "rest": []}
    stale.apply("Watchlist", p2, t("09:40"))
    assert p2["rows"][0]["momoxAPlus"] is None


def test_outside_the_window_nothing_latches(tmp_path):
    book = book_at(tmp_path)
    payload = {"rows": [row()], "rest": []}
    book.apply("Watchlist", payload, t("09:31"))
    assert payload["rows"][0]["momoxAPlus"] is None


def test_high_oi_wall_is_required(tmp_path):
    for bad in (chain(strike=730.0),           # beyond +2x the expected move
                chain(strike=690.0),           # only a wall BELOW the price (support, not a magnet)
                chain(stale=True),             # yesterday's chain: unknown
                {}):                           # no chain at all
        book = book_at(tmp_path / str(id(bad)), bad)
        payload = {"rows": [row()], "rest": []}
        book.apply("Watchlist", payload, t("09:40"))
        assert payload["rows"][0]["momoxAPlus"] is None, bad


def test_high_oi_stamps_the_wall_and_off_switch(tmp_path):
    book = book_at(tmp_path / "a")
    payload = {"rows": [row()], "rest": []}
    book.apply("Watchlist", payload, t("09:40"))
    hit = payload["rows"][0]["momoxAPlus"]
    assert hit["oiStrike"] == 705.0 and hit["oi"] == 5000
    off = book_at(tmp_path / "b", {}, oi_required=False)
    p2 = {"rows": [row()], "rest": []}
    off.apply("Watchlist", p2, t("09:40"))
    assert p2["rows"][0]["momoxAPlus"]["oiStrike"] is None


def test_a_missing_chain_is_retried_not_latched_as_no(tmp_path):
    answers = [None, chain()]
    calls = []

    def fetch(sym):
        calls.append(sym)
        return answers[min(len(calls) - 1, 1)]

    book = book_at(tmp_path, fetch=fetch)
    p1 = {"rows": [row()], "rest": []}
    book.apply("Watchlist", p1, t("09:40"))
    assert p1["rows"][0]["momoxAPlus"] is None
    book._oi.clear()                            # the next build asks again
    p2 = {"rows": [row()], "rest": []}
    book.apply("Watchlist", p2, t("09:45"))
    assert p2["rows"][0]["momoxAPlus"]["at"][11:16] == "09:45"


def test_a_small_stocks_own_biggest_wall_is_its_magnet(tmp_path):
    """QMCO 2026-09-24: 35C with 739 OI was its biggest wall above - it counts."""
    book = book_at(tmp_path, chain(wall_oi=739, strike=35.0, spot=31.48, em=9.85))
    payload = {"rows": [row(symbol="QMCO", last=31.48)], "rest": []}
    book.apply("Watchlist", payload, t("09:55"))
    hit = payload["rows"][0]["momoxAPlus"]
    assert hit["oiStrike"] == 35.0 and hit["oi"] == 739
