"""Integration: the 21 / 50 EMA touch lists use his TOS scan script verbatim.

2026-11-05, Ganesh: "for 21 and 50 EMA use same as TOS script for our app"
    def ema21 = ExpAverage(close, 21);
    plot scan = low <= ema21 and high >= ema21;

The real refresh (momx.ema_touch.refresh) runs on a faked Alpaca reply and
the lists are read through the real scanner worker over HTTP. Only the
Alpaca call is faked. Three tickers on the Daily timeframe:
  DOWN  - falling stock whose bar straddles its 21 EMA  -> IN (no uptrend
          needed; the pre-11-05 rule left it out)
  ABOVE - rising stock whose whole bar sits above the EMA -> OUT
  STRAD - rising stock whose bar straddles the EMA built WITH today's close
          but sits above the EMA of yesterday's close -> IN (the old rule
          used the completed bar's EMA and left it out)
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest

from momx import ema_touch, feed
from worker_harness import running_worker

LISTS = "/api/momx-scanner/lists"
SWITCH = "/api/momx-scanner/ema-timeframe"


def _days(count):
    start = date(2025, 1, 1)
    return [f"{(start + timedelta(days=i)).isoformat()}T05:00:00Z" for i in range(count)]


def _tape(closes, last_low, last_high):
    stamps = _days(len(closes))
    bars = [{"t": t, "l": c * 0.999, "h": c * 1.001, "c": c} for t, c in zip(stamps, closes)]
    bars[-1]["l"], bars[-1]["h"] = last_low, last_high
    return bars


def _reply():
    down = [200.0 - 0.5 * i for i in range(120)]
    down_level = ema_touch.ema(down, 21)[-1]
    up = [50.0 + 0.2 * i for i in range(120)]
    up_level = ema_touch.ema(up, 21)[-1]
    # STRAD: a big up day. Yesterday's EMA sits below today's low; today's
    # EMA (pulled up by today's close) is inside the bar.
    strad = [50.0 + 0.2 * i for i in range(119)] + [200.0]
    old_level = ema_touch.ema(strad[:-1], 21)[-1]
    new_level = ema_touch.ema(strad, 21)[-1]
    assert old_level < new_level - 1
    return {
        "DOWN": _tape(down, down[-1] - 1, down_level + 0.01),
        "ABOVE": _tape(up, up_level + 0.5, up[-1] + 1),
        "STRAD": _tape(strad, old_level + 0.5, 201.0),
    }, {"strad_old": old_level, "strad_new": new_level}


class _Reply:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


@pytest.fixture
def ema_scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_EMA_LISTS", "1")
    monkeypatch.setenv("AGX_MOMX_EMA_TOUCH_PATH", str(tmp_path / "ema_touch.json"))
    monkeypatch.setenv("AGX_MOMX_EMA_TF_PATH", str(tmp_path / "ema_tf.json"))
    monkeypatch.setattr(ema_touch, "start_refresher", lambda universe: None)
    monkeypatch.setattr(feed, "resolve_credentials", lambda: [SimpleNamespace(headers={})])
    ema_touch._CACHE.clear()
    ema_touch._CACHE.update({"symbols": {}})
    ema_touch._CACHE_MTIME = None
    ema_touch._WANTED.clear()
    daily, levels = _reply()

    def fake_get(url, params=None, headers=None, timeout=None):
        assert url == ema_touch.BARS_URL
        return _Reply({"bars": daily if params["timeframe"] == "1Day" else {}})

    monkeypatch.setattr("requests.get", fake_get)
    yield levels
    ema_touch._CACHE.clear()
    ema_touch._CACHE_MTIME = None


def test_daily_21_list_is_exactly_the_tos_scan(tmp_path, monkeypatch, ema_scratch):
    with running_worker(tmp_path, monkeypatch) as worker:
        (tmp_path / "worker" / "universe.json").write_text(
            json.dumps({"lists": {"Watchlist": ["DOWN", "ABOVE", "STRAD"]}}))
        status, body = worker.post(SWITCH, {"length": 21, "timeframe": "Daily"})
        assert status == 200 and body.get("ok"), body
        summary = ema_touch.refresh(["DOWN", "ABOVE", "STRAD"])
        assert summary["21:Daily"] == 2
        status, body = worker.get(LISTS)
        assert status == 200, body
        counts = {item["name"]: item["count"] for item in body["lists"]}
        assert counts["21 EMA Touch Daily"] == 2
    assert ema_touch.touchers(["DOWN", "ABOVE", "STRAD"], 21, "Daily") == ["DOWN", "STRAD"]
    strad = ema_touch.detail("STRAD")["21:Daily"]
    assert strad["level"] == pytest.approx(ema_scratch["strad_new"], abs=1e-3)
    assert strad["low"] > ema_scratch["strad_old"]      # the old rule would have missed it
