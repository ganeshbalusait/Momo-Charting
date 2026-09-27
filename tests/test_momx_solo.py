"""SOLO big-money single stocks (momx/solo.py)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from momx import solo

ET = ZoneInfo("America/New_York")
AT = lambda h, m: datetime(2026, 9, 24, h, m, tzinfo=ET)  # noqa: E731


def row(symbol="QMCO", ratio=6.0, pct=3.0, last=30.0, vwap=29.0, open_=29.5, sector=None):
    return {"symbol": symbol, "pctChange": pct, "last": last,
            "m5": {"vwap": vwap, "todVol": {"ratio": ratio, "open": open_, "sessions": 4}},
            "sectorRotation": sector}


def test_big_volume_up_above_vwap_and_open_is_solo(tmp_path):
    payload = {"rows": [row()]}
    solo.SoloBook(tmp_path).apply("Watchlist", payload, AT(10, 5))
    assert payload["rows"][0]["solo"]["ratio"] == 6.0
    assert payload["rows"][0]["solo"]["at"].startswith("2026-09-24T10:05")


def test_each_rule(tmp_path):
    cases = {
        "volume only 3x": row(ratio=3.0), "pump spike 40x": row(ratio=40.0), "up only 0.5%": row(pct=0.5),
        "below VWAP": row(last=28.0), "below the open": row(open_=31.0), "under $5": row(last=4.0, vwap=3.9, open_=3.9),
        "sector moving": row(sector={"total": 6, "breadth": 0.67}),
    }
    for name, r in cases.items():
        assert solo.qualifies(r) is None, name
    assert solo.qualifies(row(sector={"total": 6, "breadth": 0.2})) == 6.0, "sector flat: solo"
    assert solo.qualifies(row(sector={"total": 3, "breadth": 1.0})) == 6.0, "3 stocks is not a sector"


def test_only_09_45_to_11_and_it_stays_for_the_day(tmp_path):
    book = solo.SoloBook(tmp_path)
    early = {"rows": [row("EARLY")]}
    book.apply("Watchlist", early, AT(9, 40))
    assert early["rows"][0]["solo"] is None
    late = {"rows": [row("LATE")]}
    book.apply("Watchlist", late, AT(11, 5))
    assert late["rows"][0]["solo"] is None
    book.apply("Watchlist", {"rows": [row("HIT")]}, AT(10, 0))
    faded = {"rows": [row("HIT", pct=-2.0, last=27.0)]}
    book.apply("Watchlist", faded, AT(14, 0))
    assert faded["rows"][0]["solo"]["price"] == 30.0, "keeps the first hit"
    again = {"rows": [row("HIT")]}
    solo.SoloBook(tmp_path).apply("Watchlist", again, AT(15, 0))
    assert again["rows"][0]["solo"] is not None, "survives a restart"
