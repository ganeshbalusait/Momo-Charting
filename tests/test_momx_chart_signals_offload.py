"""momx/chart_signals.py OFFLOAD - the study runs in a child process (2026-10-16).

The child must give exactly what the in-process path gives, ship a symbol's
20-day frame once a day, and fall back to in-process when it breaks.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from momx import chart_signals as cs

ET = ZoneInfo("America/New_York")
DAY = "2026-10-15"


def _bars(seed: float, days: int = 12) -> pd.DataFrame:
    rows = []
    start = datetime.fromisoformat(DAY).replace(tzinfo=ET) - timedelta(days=days)
    price = 50.0 + seed
    for d in range(days + 1):
        session = start + timedelta(days=d)
        if session.weekday() >= 5:
            continue
        t = session.replace(hour=9, minute=30)
        for k in range(78):
            price *= 1 + 0.004 * math.sin((d * 78 + k) / (7 + seed))
            rows.append({"timestamp": t + timedelta(minutes=5 * k), "open": price, "high": price * 1.002,
                         "low": price * 0.998, "close": price, "volume": 1000})
    return pd.DataFrame(rows).set_index("timestamp")


class Feed:
    def __init__(self):
        self.tapes = {"AAA": _bars(1.0), "BBB": _bars(3.0)}

    def fetch(self, symbols, deep):
        today = {s: f[f.index.tz_convert(ET).date.astype(str) == DAY] for s, f in self.tapes.items()}
        src = self.tapes if deep else today
        return {s: src[s] for s in symbols if s in src}, {}


def _child_ready():
    """A started, warmed child (see cs.CHILD_START_ATTEMPTS). Retried because
    the live scanner_watchdog.ps1 reaps any spawned python child that is not
    momx_worker's - including this test's - every pass."""
    for _ in range(5):
        if cs._executor() is not None:
            return
        cs.shutdown_executor()
    raise AssertionError("could not start a chart-signal child process")


def _run(book, symbols):
    book._candidates = {s: datetime.fromisoformat(f"{DAY}T15:00").replace(tzinfo=ET).timestamp() for s in symbols}
    book._day = DAY
    book.run_once(datetime.fromisoformat(f"{DAY}T15:00").replace(tzinfo=ET))
    return dict(book._signals), dict(book._squeezes)


def test_child_process_matches_in_process(tmp_path):
    feed = Feed()
    here = cs.ChartSignalBook(tmp_path / "here", fetch=feed.fetch, background=False)
    assert here._offload is False
    away = cs.ChartSignalBook(tmp_path / "away", fetch=feed.fetch, background=False)
    away._offload = True
    try:
        _child_ready()
        want = _run(here, ["AAA", "BBB"])
        got = _run(away, ["AAA", "BBB"])
        assert cs._EXEC, "the child process was not used"
        assert {(DAY, "AAA"), (DAY, "BBB")} <= cs._EXEC_HAS_DEEP     # 20-day frames now live in the child
        strip = lambda d: {s: [{k: v for k, v in c.items() if k != "seenAt"} for c in cs_] for s, cs_ in d.items()}
        assert strip(got[0]) == strip(want[0])
        assert strip(got[1]) == strip(want[1])
    finally:
        cs.shutdown_executor()


def test_child_resends_a_frame_it_lost(tmp_path):
    feed = Feed()
    book = cs.ChartSignalBook(tmp_path, fetch=feed.fetch, background=False)
    book._offload = True
    try:
        _child_ready()
        _run(book, ["AAA"])
        cs._EXEC_HAS_DEEP.add((DAY, "BBB"))          # parent wrongly believes the child holds BBB
        book._deep["BBB"] = feed.tapes["BBB"]
        out = cs._offloaded(book, ["BBB"], {"BBB": feed.tapes["BBB"]}, {}, DAY)
        assert out is not None and "BBB" not in out[0]
        assert (DAY, "BBB") not in cs._EXEC_HAS_DEEP  # so the next pass sends it
    finally:
        cs.shutdown_executor()


def test_broken_child_falls_back_in_process(tmp_path, monkeypatch):
    feed = Feed()
    book = cs.ChartSignalBook(tmp_path, fetch=feed.fetch, background=False)
    book._offload = True

    class Broken:
        def submit(self, *a, **k):
            raise RuntimeError("child died")

        def shutdown(self, *a, **k):
            pass

    monkeypatch.setattr(cs, "_EXEC", Broken())
    signals, _ = _run(book, ["AAA"])
    assert cs._EXEC is None                           # dropped; the next pass makes a new child
    here = cs.ChartSignalBook(tmp_path / "ref", fetch=feed.fetch, background=False)
    assert signals.keys() == _run(here, ["AAA"])[0].keys()
