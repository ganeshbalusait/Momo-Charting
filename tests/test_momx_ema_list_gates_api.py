"""Integration: the EMA Touch lists apply his stricter Price_Change gates
(VOLUME 4h >= 1.0%, CLOSE 1h >= 0.5%) and the Watchlist keeps 0.5% / 0.3%.

service._build_once runs the REAL board build over a fake bar feed for the
Watchlist and for "21 EMA Touch Daily", and the real worker route serves
both. To pin the two readings, the real per-symbol scan runs and then its
match and its two Price_Change readings are set:
  SPY  volume +0.7%, close +0.4%  -> passes the Watchlist, blocked on the EMA list
  QQQ  volume +1.2%, close +0.6%  -> passes both
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from momx import board, ema_touch, flags, grade_log, industry_lookup, news, service
from worker_harness import running_worker

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "live_build", Path(__file__).with_name("test_cov_scanner-live_build_api.py"))
live_build = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live_build)

READINGS = {"SPY": (0.7, 0.4), "QQQ": (1.2, 0.6)}
EMA_LIST = "21 EMA Touch Daily"


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
    # The two EMA lists exist, 21 is on Daily, and SPY + QQQ touch it.
    monkeypatch.setenv("AGX_MOMX_EMA_LISTS", "1")
    monkeypatch.setenv("AGX_MOMX_EMA_TOUCH_PATH", str(tmp_path / "ema_touch.json"))
    monkeypatch.setenv("AGX_MOMX_EMA_TF_PATH", str(tmp_path / "ema_tf.json"))
    monkeypatch.setattr(ema_touch, "start_refresher", lambda universe: None)
    (tmp_path / "ema_tf.json").write_text(json.dumps({"21": "Daily"}))
    (tmp_path / "ema_touch.json").write_text(json.dumps({
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "symbols": {s: {"21:Daily": {"pass": True}} for s in READINGS},
    }))
    ema_touch._CACHE.clear()
    ema_touch._CACHE_MTIME = None
    # The real scan runs; its match and its two readings are then pinned.
    real = board._scan_one

    def pinned(args):
        out = list(real(args))
        out[3] = True                       # bull match on the main (0.5 / 0.3) scan
        out[4] = [r for r in out[4] if not str(r).startswith("blocked:")] or ["macd:D"]
        out[9] = READINGS[args[0]]
        return tuple(out)

    monkeypatch.setattr(board, "_scan_one", pinned)
    feed = live_build.FakeFeed(list(READINGS))
    monkeypatch.setattr(board, "cached_board", lambda symbols=None, ttl_seconds=None, **kw:
                        board.build_board(symbols, feed_module=feed, **kw))
    yield
    ema_touch._CACHE.clear()
    ema_touch._CACHE_MTIME = None
    industry_lookup.clear_memory_cache()
    board.clear_board_cache()


def _rows(body):
    return {r["symbol"]: r for r in (body.get("rows") or []) + (body.get("rest") or [])}


def test_ema_list_uses_1_0_and_0_5_while_the_watchlist_keeps_0_5_and_0_3(tmp_path, monkeypatch, build):
    with running_worker(tmp_path, monkeypatch) as worker:
        service._build_once("Watchlist")
        service._build_once(EMA_LIST)
        wl_status, watchlist = worker.get("/api/momx-scanner?list=Watchlist&matches=0")
        ema_status, ema = worker.get("/api/momx-scanner?" + "list=21%20EMA%20Touch%20Daily&matches=0")
    assert wl_status == 200 and ema_status == 200, (watchlist, ema)
    wl, el = _rows(watchlist), _rows(ema)
    assert sorted(el) == ["QQQ", "SPY"]
    assert wl["SPY"]["scanPass"] is True and wl["QQQ"]["scanPass"] is True
    assert el["QQQ"]["scanPass"] is True
    assert el["SPY"]["scanPass"] is False
    assert el["SPY"]["scanReasons"][:2] == ["blocked:pricechange:volume:4h", "blocked:pricechange:close:1h"]
    assert not any(r.startswith("blocked:") for r in wl["SPY"]["scanReasons"])
