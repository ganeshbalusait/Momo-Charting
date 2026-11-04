"""Integration: one board build, end to end, served over real HTTP.

service._build_once runs the REAL board build (bars -> buckets -> RVOL / SQZ /
Skittles cells -> scan -> row contract) over a FAKE bar feed, publishes the
board, writes the disk cache and the History snapshot, and the real worker
route serves it. The RVOL cell on the wire is checked against his thinkScript
column evaluated by hand on the same bars.

Kept in-process and network-free: the process pools are off (the scanner
watchdog reaps pytest's children), industry / earnings / news lookups are
offline, and the post-build books that start their own threads or fetch bars
(chart arrows, AI news, market turn, SOLO, MomoX A+, strategy) are stubbed.
"""
from __future__ import annotations

import math
import statistics
from collections import namedtuple
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from momx import board, columns, flags, grade_log, history, industry_lookup, news, service
from worker_harness import running_worker

ANCHOR = int(datetime(2026, 11, 2, 14, 30, tzinfo=timezone.utc).timestamp())   # Mon 09:30 ET
Result = namedtuple("Result", "bars errors")
PALETTE = {"cyan", "magenta", "green", "red", "light_red", "plum", "lime", "dark_green", "dark_red",
           "violet", "downtick", "orange", "white", "black", "gray", None}


def _intraday(count: int, minutes: int, seed: int) -> list[dict]:
    step = minutes * 60
    bars = []
    for i in range(count):
        base = 100.0 + i * 0.05 + (seed % 3)
        volume = 10_000 + ((i * 37 + seed * 11) % 23) * 900 + (40_000 if i == count - 1 else 0)
        bars.append({"timestamp": ANCHOR - (count - 1 - i) * step, "open": base, "high": base + 0.6,
                     "low": base - 0.2, "close": base + 0.45, "volume": volume})
    return bars


def _daily(count: int) -> list[dict]:
    return [{"timestamp": ANCHOR - (count - 1 - i) * 86_400, "open": 100.0 + i * 0.3, "high": 101.0 + i * 0.3,
             "low": 99.0 + i * 0.3, "close": 100.5 + i * 0.3, "volume": 2_000_000 + i} for i in range(count)]


class FakeFeed:
    """Stands in for Schwab/Alpaca: canned tapes, a call log, no network."""

    def __init__(self, symbols, fail: bool = False):
        self.fail = fail
        self.calls: list[str] = []
        self.five = {s: _intraday(400, 5, i) for i, s in enumerate(symbols)}
        self.thirty = {s: _intraday(400, 30, i) for i, s in enumerate(symbols)}
        self.daily = {s: _daily(120) for s in symbols}

    def _answer(self, kind, held, symbols):
        self.calls.append(kind)
        if self.fail:
            raise ConnectionError("schwab and alpaca both down")
        return Result({s: held[s] for s in symbols if s in held}, {})

    def fetch_5m(self, symbols, **kwargs):
        return self._answer("5m", self.five, symbols)

    def fetch_30m(self, symbols, **kwargs):
        return self._answer("30m", self.thirty, symbols)

    def fetch_daily(self, symbols, **kwargs):
        return self._answer("daily", self.daily, symbols)


def tos_rvol(bars: list[dict]) -> tuple[float, bool]:
    """His RVOL column on the last bar, written out from the thinkScript:
    relVol = (volume - Average(volume, 50)) / StDev(volume, 50); round 1."""
    window = [float(b["volume"]) for b in bars[-columns.RVOL_LENGTH:]]
    mean = sum(window) / len(window)
    sd = statistics.pstdev(window)
    rel = (window[-1] - mean) / sd if sd else 0.0
    last = bars[-1]
    return math.floor(rel * 10 + 0.5) / 10, (last["close"] - last["low"]) >= (last["high"] - last["close"])


@pytest.fixture
def build(monkeypatch, tmp_path):
    monkeypatch.setattr(board, "_pool", lambda: None)
    monkeypatch.setattr(board, "_small_pool", lambda: None)
    monkeypatch.setenv(industry_lookup.CACHE_PATH_ENV, str(tmp_path / "industry.json"))
    monkeypatch.setattr(industry_lookup, "fetch_industry", lambda *a, **k: None)
    industry_lookup.clear_memory_cache()
    monkeypatch.setenv("MOMX_FLAGS_EARNINGS_NETWORK", "0")
    monkeypatch.setenv(news.NETWORK_ENV, "0")
    flags.reset_caches()
    news.reset_caches()
    board.clear_board_cache()
    quiet = SimpleNamespace(apply=lambda *a, **k: None)
    for name in ("_CHART_SIGNALS", "_BEAR_CHART_SIGNALS", "_NEWS_CATALYST", "_MARKET_TURN", "_BEAR_MARKET_TURN",
                 "_SOLO", "_MOMOX_APLUS", "_BEAR_MOMOX_APLUS", "_STRATEGY", "_BEAR_STRATEGY"):
        monkeypatch.setattr(service, name, quiet)
    monkeypatch.setattr(service, "_GRADE_LOG", grade_log.GradeLog(tmp_path / "grade"))
    monkeypatch.setattr(service, "_BEAR_GRADE_LOG", grade_log.GradeLog(tmp_path / "grade" / "bear", direction="bear"))
    monkeypatch.setattr(service, "_LIVE_SEEDS", {})
    feeds = {}

    def use(feed):
        feeds["now"] = feed
        monkeypatch.setattr(board, "cached_board", lambda symbols=None, ttl_seconds=None, **kw:
                            board.build_board(symbols, feed_module=feeds["now"], **kw))
        return feed

    yield use
    industry_lookup.clear_memory_cache()
    board.clear_board_cache()


def test_a_build_is_served_with_tos_parity_cells_and_lands_in_history_and_the_disk_cache(tmp_path, monkeypatch, build):
    with running_worker(tmp_path, monkeypatch) as worker:
        feed = build(FakeFeed(["SPY", "QQQ"]))                  # the harness's Watchlist seed
        service._build_once("Watchlist")
        status, body = worker.get("/api/momx-scanner?list=Watchlist&matches=0")
        bear_status, bear = worker.get("/api/momx-scanner?list=Watchlist&dir=bear")
        disk = service._load_disk("Watchlist", "bull")
        days = history.list_days("Watchlist", service.history_dir())
    assert status == 200, body
    assert sorted(feed.calls) == ["30m", "5m", "daily"]
    shipped = {row["symbol"]: row for row in (body["rows"] or []) + (body.get("rest") or [])}
    assert sorted(shipped) == ["QQQ", "SPY"]
    for symbol, row in shipped.items():
        want_value, bullish = tos_rvol(feed.five[symbol])
        cell = row["rvol"]["5m"]
        assert cell["value"] == want_value, (symbol, cell)
        assert (cell["bg"], cell["fg"]) == columns.rvol_colors(want_value, bullish)
        for section in ("rvol", "sqz", "skittles"):
            for tf, painted in (row.get(section) or {}).items():
                assert painted.get("bg") in PALETTE and painted.get("fg") in PALETTE, (symbol, section, tf, painted)
                assert not (isinstance(painted.get("value"), float) and math.isnan(painted["value"]))
        assert "_lb" not in row and "liveSeed" not in (row.get("m5") or {})     # build-only fields never ship
    assert body["generatedAt"] and body["scanStartedAt"] and body["scanCompletedAt"]
    assert body["dataSource"] in ("tos", "alpaca") and "freshness" in body
    assert bear_status == 200 and bear["direction"] == "bear"
    assert disk is not None and disk["generatedAt"] == body["generatedAt"]      # a restart serves this board
    if any(r.get("scanPass") for r in shipped.values()):
        assert days, "a scan match must reach the History archive"
    else:
        assert days                                              # a quiet day is still archived, as an empty day


def test_a_failed_rebuild_keeps_serving_the_last_good_board_marked_stale(tmp_path, monkeypatch, build):
    with running_worker(tmp_path, monkeypatch) as worker:
        build(FakeFeed(["SPY", "QQQ"]))
        service._build_once("Watchlist")
        _, good = worker.get("/api/momx-scanner?list=Watchlist")

        def crashed(*args, **kwargs):
            raise RuntimeError("build crashed mid-scan")

        monkeypatch.setattr(board, "cached_board", crashed)
        service._build_once("Watchlist")
        status, body = worker.get("/api/momx-scanner?list=Watchlist")
    assert status == 200, body
    assert body["stale"] is True
    assert "build crashed mid-scan" in body["errors"]["_refresh"]
    assert body["generatedAt"] == good["generatedAt"]
    assert [r["symbol"] for r in body["rows"]] == [r["symbol"] for r in good["rows"]]
    assert body["rows"][0]["rvol"]["5m"] == good["rows"][0]["rvol"]["5m"]       # the figures, not blanks


@pytest.mark.xfail(strict=True, reason=(
    "Possible bug: when EVERY bar fetch fails (Schwab and Alpaca both down) board.build_board "
    "swallows the errors and returns rows with all-null cells, so _build_once publishes that and the "
    "last good board is replaced by blank RVOL/SQZ/Skittles cells and no price - the 'a stale board "
    "always beats a blank one' rule in service.snapshot only covers a build that raises."))
def test_a_total_feed_outage_keeps_the_last_good_figures_on_screen(tmp_path, monkeypatch, build):
    with running_worker(tmp_path, monkeypatch) as worker:
        build(FakeFeed(["SPY", "QQQ"]))
        service._build_once("Watchlist")
        _, good = worker.get("/api/momx-scanner?list=Watchlist")
        build(FakeFeed(["SPY", "QQQ"], fail=True))
        service._build_once("Watchlist")
        status, body = worker.get("/api/momx-scanner?list=Watchlist")
    assert status == 200
    shown = {r["symbol"]: r for r in body["rows"] + (body.get("rest") or [])}
    assert shown["SPY"]["rvol"]["5m"]["value"] is not None, shown["SPY"]["rvol"]["5m"]
    assert shown["SPY"]["last"] is not None


def test_the_first_build_failing_reads_as_building_with_the_reason_not_an_empty_board(tmp_path, monkeypatch, build):
    def broken(*args, **kwargs):
        raise RuntimeError("schwab token expired")

    with running_worker(tmp_path, monkeypatch) as worker:
        monkeypatch.setattr(board, "cached_board", broken)
        service._build_once("Mag7")
        status, body = worker.get("/api/momx-scanner?list=Mag7")
    assert status == 200, body
    assert body["warming"] is True and body["rows"] == []
    assert "schwab token expired" in body["message"]
