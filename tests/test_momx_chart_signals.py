"""momx/chart_signals.py - the chart's CALL2H / CALL4H arrows on the scanner row."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from momx import chart_signals as cs

ET = ZoneInfo("America/New_York")


def at(hhmm, day="2026-09-23"):
    h, m = hhmm.split(":")
    return datetime.fromisoformat(f"{day}T{int(h):02d}:{int(m):02d}:00").replace(tzinfo=ET)


def sig(label, tf, hhmm, family="4x8", direction="CALL", day="2026-09-23"):
    return {"label": label, "timeframe": tf, "time": int(at(hhmm, day).timestamp()), "family": family,
            "direction": direction}


def test_today_calls_keeps_only_todays_call_2h_4h():
    payload = {"signals": [
        sig("CALL2H", "2H", "13:00"), sig("CALL4H", "4H", "13:00"), sig("CALL2H", "2H", "13:00", family="9x20"),
        sig("CALL1H", "1H", "12:00"),                                  # 1H not shown
        sig("C2H", "2H", "07:00"),                                     # compact (against trend) not shown
        sig("PUT2H", "2H", "10:00", direction="PUT"),                  # puts not shown
        sig("CALL2H", "2H", "15:00", day="2026-09-22"),                # yesterday
    ]}
    got = cs.today_calls(payload, "2026-09-23")
    assert [(s["label"], s["family"], s["at"][11:16]) for s in got] == [
        ("CALL2H", "4x8", "13:00"), ("CALL4H", "4x8", "13:00"), ("CALL2H", "9x20", "13:00")]


def board(*rows):
    return {"rows": list(rows), "rest": []}


def row(symbol, letter="A+", scan=False):
    return {"symbol": symbol, "grade": {"letter": letter}, "scanPass": scan}


class Fake:
    def __init__(self, calls):
        self.calls, self.fetched, self.computed = calls, [], []

    def fetch(self, symbols, deep):
        self.fetched.append((tuple(symbols), deep))
        bars = {s: [{"time": int(at("13:00").timestamp()), "close": 25.0}] for s in symbols}
        return bars, {}

    def compute(self, frame, daily):
        self.computed.append(len(frame))
        return {"signals": self.calls}


def test_book_stamps_rows_and_remembers_first_seen(tmp_path):
    fake = Fake([sig("CALL2H", "2H", "13:00"), sig("CALL4H", "4H", "13:00")])
    book = cs.ChartSignalBook(tmp_path, fetch=fake.fetch, compute=fake.compute, background=False)
    p = board(row("FSLY"), row("QUIET", letter=None))
    book.apply("Watchlist", p, at("13:05"))
    fsly, quiet = p["rows"]
    assert [(s["label"], s["at"][11:16], s["seenAt"][11:16]) for s in fsly["chartSignals"]] == [
        ("CALL2H", "13:00", "13:05"), ("CALL4H", "13:00", "13:05")]
    # 2026-09-29 (CHPT): an ungraded row is computed too, on the slow lane
    assert [s["label"] for s in quiet["chartSignals"]] == ["CALL2H", "CALL4H"]
    assert any("FSLY" in f[0] and "QUIET" in f[0] for f in fake.fetched)
    saved = json.loads((tmp_path / "momx_chart_signals" / "2026-09-23.json").read_text(encoding="utf-8"))
    assert saved["signals"]["FSLY"][0]["label"] == "CALL2H"

    # 2 minutes later: not recomputed yet (60 s rule) ... after 60 s, seenAt is kept
    later = board(row("FSLY"))
    book.apply("Watchlist", later, at("13:07"))
    assert later["rows"][0]["chartSignals"][0]["seenAt"][11:16] == "13:05"


def test_a_repainted_arrow_is_kept_and_marked_gone(tmp_path):
    # 2026-09-28 ABVX: a C2H showed 09:45-11:05 then vanished from the chart.
    # The scanner keeps it with goneAt instead of forgetting it had been there.
    fake = Fake([sig("CALL2H", "2H", "13:00")])
    book = cs.ChartSignalBook(tmp_path, fetch=fake.fetch, compute=fake.compute, background=False)
    book.apply("Watchlist", board(row("FSLY")), at("13:05"))
    fake.calls = []
    p = board(row("FSLY"))
    book.apply("Watchlist", p, at("13:10"))
    [arrow] = p["rows"][0]["chartSignals"]
    assert arrow["label"] == "CALL2H" and arrow["seenAt"][11:16] == "13:05" and arrow["goneAt"][11:16] == "13:10"
    # it comes back -> live again
    fake.calls = [sig("CALL2H", "2H", "13:00")]
    p = board(row("FSLY"))
    book.apply("Watchlist", p, at("13:15"))
    assert "goneAt" not in p["rows"][0]["chartSignals"][0]


def test_deep_tape_is_fetched_once_per_symbol_per_day(tmp_path):
    fake = Fake([])
    book = cs.ChartSignalBook(tmp_path, fetch=fake.fetch, compute=fake.compute, background=False)
    book.apply("Watchlist", board(row("FSLY")), at("10:00"))
    book.apply("Watchlist", board(row("FSLY")), at("10:02"))
    assert [d for _, d in fake.fetched].count(True) == 1


def test_never_raises(tmp_path):
    def boom(*a, **k):
        raise RuntimeError("network")
    book = cs.ChartSignalBook(tmp_path, fetch=boom, compute=boom, background=False)
    p = board(row("FSLY"))
    book.apply("Watchlist", p, at("10:00"))
    assert p["rows"][0]["chartSignals"] == []
    book.apply("Watchlist", None, at("10:00"))


# ------------------------------------------------------------- BEAR (spec 2026-09-24)

def test_today_signals_bear_keeps_only_todays_put_2h_4h():
    payload = {"signals": [
        sig("PUT2H", "2H", "10:00", direction="PUT"),
        sig("PUT4H", "4H", "13:00", family="9x20", direction="PUT"),
        sig("P2H", "2H", "07:00", direction="PUT"),                    # compact, against the higher trend
        sig("CALL2H", "2H", "13:00"),
        sig("PUT2H", "2H", "15:00", direction="PUT", day="2026-09-22"),
    ]}
    got = cs.today_signals(payload, "2026-09-23", direction="bear")
    assert [(s["label"], s["family"], s["at"][11:16]) for s in got] == [("PUT2H", "4x8", "10:00"), ("PUT4H", "9x20", "13:00")]
    assert cs.today_signals(payload, "2026-09-23") == cs.today_calls(payload, "2026-09-23")
    assert [s["label"] for s in cs.today_calls(payload, "2026-09-23")] == ["CALL2H"]


def test_bear_book_stamps_put_arrows(tmp_path):
    fake = Fake([sig("PUT2H", "2H", "13:00", direction="PUT"), sig("CALL4H", "4H", "13:00")])
    book = cs.ChartSignalBook(tmp_path, fetch=fake.fetch, compute=fake.compute, background=False, direction="bear")
    p = board(row("FSLY"))
    book.apply("Watchlist", p, at("13:05"))
    assert [(s["label"], s["at"][11:16]) for s in p["rows"][0]["chartSignals"]] == [("PUT2H", "13:00")]
    assert book.direction == "bear"
    bull = cs.ChartSignalBook(tmp_path / "b", fetch=fake.fetch, compute=fake.compute, background=False)
    q = board(row("FSLY"))
    bull.apply("Watchlist", q, at("13:05"))
    assert [s["label"] for s in q["rows"][0]["chartSignals"]] == ["CALL4H"]


def test_compact_c2h_c4h_are_kept_only_in_regular_hours():
    """2026-09-25 PYPL: C4H (9x20) at 10:10 led a +3.3% run - his ask to show
    C2H / C4H. Premarket ones (the 2026-09-23 HAL/OXY/CRM/USO noise) stay out."""
    payload = {"signals": [
        sig("C2H", "2H", "07:00", day="2026-09-25"),
        sig("C2H", "2H", "09:35", family="9x20", day="2026-09-25"),
        sig("C4H", "4H", "10:10", family="9x20", day="2026-09-25"),
        sig("CALL2H", "2H", "06:00", day="2026-09-25"),
    ]}
    got = cs.today_calls(payload, "2026-09-25")
    assert [(s["label"], s["at"][11:16], s["compact"]) for s in got] == [
        ("CALL2H", "06:00", False), ("C2H", "09:35", True), ("C4H", "10:10", True)]


def test_bear_compact_p2h_p4h_are_kept_only_in_regular_hours():
    """The bear side shares today_signals: P2H / P4H follow the same 09:30-16:00 rule."""
    payload = {"signals": [
        sig("P2H", "2H", "07:00", direction="PUT", day="2026-09-25"),
        sig("P4H", "4H", "10:10", family="9x20", direction="PUT", day="2026-09-25"),
        sig("PUT2H", "2H", "06:00", direction="PUT", day="2026-09-25"),
        sig("C4H", "4H", "10:10", day="2026-09-25"),                    # bull arrow: not on the bear row
    ]}
    got = cs.today_signals(payload, "2026-09-25", "bear")
    assert [(s["label"], s["at"][11:16], s["compact"]) for s in got] == [
        ("PUT2H", "06:00", False), ("P4H", "10:10", True)]


def test_slow_lane_every_five_minutes_and_no_etfs(tmp_path):
    fake = Fake([sig("CALL2H", "2H", "13:00")])
    book = cs.ChartSignalBook(tmp_path, fetch=fake.fetch, compute=fake.compute, background=False)
    etf = {**row("QQQ", letter=None), "etf": True}
    book.apply("Watchlist", board(row("CHPT", letter=None), etf), at("13:05"))
    first = len(fake.computed)
    assert first == 1                                     # CHPT only; the ETF is never computed
    book.apply("Watchlist", board(row("CHPT", letter=None)), at("13:08"))
    assert len(fake.computed) == first                    # slow lane: not again within 5 min
    book.apply("Watchlist", board(row("CHPT", letter=None)), at("13:11"))
    assert len(fake.computed) == first + 1                # 6 min later: recomputed


def test_bull_and_bear_books_share_one_compute_per_bar(tmp_path):
    fake = Fake([sig("CALL2H", "2H", "13:00")])
    bull = cs.ChartSignalBook(tmp_path / "bull", fetch=fake.fetch, compute=fake.compute, background=False)
    bear = cs.ChartSignalBook(tmp_path / "bear", fetch=fake.fetch, compute=fake.compute, background=False,
                              direction="bear")
    bull.apply("Watchlist", board(row("SHARE", letter=None)), at("13:05"))
    bear.apply("Watchlist", board(row("SHARE", letter=None)), at("13:05"))
    assert len(fake.computed) == 1                      # one study run for both books
    assert sum(1 for f in fake.fetched if f[1]) == 1    # one 20-day fetch for both
