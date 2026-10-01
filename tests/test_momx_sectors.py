"""Sector rotation stamps (momx/sectors.py)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from momx import sectors

ET = ZoneInfo("America/New_York")
AT_950 = datetime(2026, 9, 24, 9, 50, tzinfo=ET)
DAYS = ["2026-09-17", "2026-09-18", "2026-09-21", "2026-09-22", "2026-09-23"]


def bars(closes):
    return [{"time": datetime.fromisoformat(d + "T16:00:00").replace(tzinfo=ET).timestamp(), "close": c}
            for d, c in zip(DAYS, closes)]


def fetch_factory(semis_closes, other_closes):
    def fetch(symbols):
        out = {"SPY": bars([100, 100, 100, 100.5, 101])}
        for s in symbols:
            if s.startswith("SEMI"):
                out[s] = bars(semis_closes)
            elif s != "SPY":
                out[s] = bars(other_closes)
        return out
    return fetch


def row(symbol, industry, pct=1.0, last=10.5, vwap=10.0):
    return {"symbol": symbol, "industry": industry, "pctChange": pct, "last": last, "m5": {"vwap": vwap}}


def board(up_semis=5):
    rows = [row(f"SEMI{i}", "Semis", pct=1.2 if i < up_semis else -0.5) for i in range(5)]
    rows += [row(f"SOFT{i}", "Software") for i in range(5)]
    return {"rows": rows[:6], "rest": rows[6:]}


def book(semis=(100, 100, 101, 103, 106), other=(100, 100, 100, 100, 100)):
    return sectors.RotationBook(fetch=fetch_factory(semis, other), background=False, always_on=True)


def test_hot_sector_beats_spy_growing_and_broad():
    payload = board()
    book().apply("Watchlist", payload, AT_950)
    semis = payload["rows"][0]["sectorRotation"]
    assert semis["hot"] is True and semis["up"] == 5 and semis["total"] == 5
    assert semis["rs3"] == 5.0  # +6% vs SPY +1%
    soft = payload["rest"][-1]["sectorRotation"]
    assert soft["hot"] is False, "lagging SPY: not hot even with every member up"


def test_not_hot_before_935():
    payload = board()
    book().apply("Watchlist", payload, datetime(2026, 9, 24, 9, 30, tzinfo=ET))
    assert payload["rows"][0]["sectorRotation"]["hot"] is False


def test_not_hot_when_breadth_under_60_percent():
    payload = board(up_semis=2)
    book().apply("Watchlist", payload, AT_950)
    assert payload["rows"][0]["sectorRotation"]["hot"] is False


def test_not_hot_when_the_lead_is_shrinking():
    payload = board()
    # rs3 to 09-23 = +3% - 1% = +2%; to 09-22 it was +6% - 0.5% = +5.5%: shrinking.
    book(semis=(100, 100, 104, 106, 103)).apply("Watchlist", payload, AT_950)
    s = payload["rows"][0]["sectorRotation"]
    assert s["rs3"] < s["rs3Prev"] and s["hot"] is False


def test_below_vwap_is_not_counted_up():
    payload = {"rows": [row(f"SEMI{i}", "Semis", last=9.0, vwap=10.0) for i in range(5)]}
    book().apply("Watchlist", payload, AT_950)
    assert payload["rows"][0]["sectorRotation"]["up"] == 0


def test_etf_baskets_are_not_a_sector_and_a_failed_fetch_costs_nothing():
    payload = {"rows": [row("TQQQ", "ETF-Lev"), row("SEMI0", "Semis")]}

    def boom(symbols):
        raise RuntimeError("feed down")

    sectors.RotationBook(fetch=boom, background=False, always_on=True).apply("Watchlist", payload, AT_950)
    assert payload["rows"][0]["sectorRotation"] is None
    assert payload["rows"][1]["sectorRotation"]["rs3"] is None


def test_off_switch(monkeypatch):
    monkeypatch.setenv("AGX_SECTOR_ROTATION", "0")
    payload = board()
    sectors.RotationBook(fetch=fetch_factory((1, 2, 3, 4, 5), (1, 1, 1, 1, 1)), background=False).apply("Watchlist", payload, AT_950)
    assert "sectorRotation" not in payload["rows"][0]


def test_a_sector_can_only_turn_hot_before_11_and_stays_lit():
    b = book()
    late = board()
    b.apply("Watchlist", late, datetime(2026, 9, 24, 11, 5, tzinfo=ET))
    assert late["rows"][0]["sectorRotation"]["hot"] is False, "first hot after 11:00: never"
    assert late["rows"][0]["sectorRotation"]["late"] is True, "but it is SHOWN as late rotation"
    b2 = book()
    b2.apply("Watchlist", board(), datetime(2026, 9, 24, 10, 0, tzinfo=ET))
    cooled = board(up_semis=1)
    b2.apply("Watchlist", cooled, datetime(2026, 9, 24, 13, 0, tzinfo=ET))
    assert cooled["rows"][0]["sectorRotation"]["hot"] is True, "lit at 10:00, still lit at 13:00"


def test_unusual_volume_count_and_the_leaders_list():
    payload = board()
    payload["rows"][1]["rvol"] = {"30m": {"value": 2.4}}
    payload["rows"][2]["pctChange"] = 4.0
    book().apply("Watchlist", payload, AT_950)
    s = payload["rows"][0]["sectorRotation"]
    assert s["volUp"] == 1
    assert s["leaders"][0] == {"symbol": "SEMI2", "pct": 4.0, "vol": False}
    assert {"symbol": "SEMI1", "pct": 1.2, "vol": True} in s["leaders"]
    assert len(s["leaders"]) == 5


def test_the_hot_sector_s_two_strongest_are_tagged_1_and_2_and_stay():
    b = book()
    payload = board()
    payload["rows"][3]["pctChange"] = 5.0
    payload["rows"][1]["pctChange"] = 3.0
    b.apply("Watchlist", payload, AT_950)
    tags = {r["symbol"]: r["hotLeader"] for r in payload["rows"] + payload["rest"] if r.get("hotLeader")}
    assert {k: v["rank"] for k, v in tags.items()} == {"SEMI3": 1, "SEMI1": 2}
    assert tags["SEMI3"]["sector"] == "Semis"
    later = board()
    later["rows"][0]["pctChange"] = 9.0  # a new leader later does not re-rank the day's picks
    b.apply("Watchlist", later, datetime(2026, 9, 24, 12, 0, tzinfo=ET))
    assert later["rows"][3]["hotLeader"]["rank"] == 1 and later["rows"][0]["hotLeader"] is None


def test_a_second_later_sector_can_still_light():
    b = book()
    early = board()
    b.apply("Watchlist", early, datetime(2026, 9, 24, 9, 40, tzinfo=ET))
    assert early["rows"][0]["sectorRotation"]["hot"] is True  # Semis first
    later = board()
    for r in later["rest"]:  # Software broadens later and becomes the best NEW sector
        r["industry"] = "Software"
    later["rows"][5]["industry"] = "Software"
    b.apply("Watchlist", later, datetime(2026, 9, 24, 10, 25, tzinfo=ET))
    assert later["rest"][0]["sectorRotation"]["hot"] is True, "a second sector gets the flame"


def test_moving_now_shows_a_strong_non_hot_sector_at_any_time():
    b = book()
    b.apply("Watchlist", board(), datetime(2026, 9, 24, 9, 40, tzinfo=ET))  # Semis takes a slot
    b._state["Watchlist"]["hot"] |= {"Other1"}  # both slots full
    later = board()
    for r in later["rest"]:
        r["industry"], r["pctChange"] = "Quantum", 3.0
    later["rows"][5]["industry"], later["rows"][5]["pctChange"] = "Quantum", 3.0
    b.apply("Watchlist", later, datetime(2026, 9, 24, 10, 50, tzinfo=ET))
    q = later["rest"][0]["sectorRotation"]
    assert q["hot"] is False and q["late"] is True and q["today"] == 3.0


def test_weak_moves_are_not_moving_now():
    b = book()
    b._state.setdefault("Watchlist", {"day": "", "rs": {}, "loading": False})
    payload = board()
    for r in payload["rows"] + payload["rest"]:
        r["pctChange"] = 0.3  # up, but under the +0.5% bar
    b.apply("Watchlist", payload, datetime(2026, 9, 24, 12, 0, tzinfo=ET))
    assert all(not (r["sectorRotation"] or {}).get("late") for r in payload["rows"] + payload["rest"])


def test_sector_shows_its_own_3_day_move_without_spy():
    payload = board()
    book().apply("Watchlist", payload, AT_950)
    assert payload["rows"][0]["sectorRotation"]["ret3"] == 6.0  # 100 -> 106, no SPY


def test_a_one_stock_group_is_never_moving_now():
    payload = {"rows": [row("TSSI", "AI-Infra", pct=9.3)]}
    book().apply("Watchlist", payload, datetime(2026, 9, 24, 10, 45, tzinfo=ET))
    assert payload["rows"][0]["sectorRotation"]["late"] is False
