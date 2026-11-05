"""Integration: a SPAC's flat pre-listing months must not bend the monthly EMA.

2026-11-04: Alpaca served OUST 39 flat $253.90 months (the SPAC shell) before
it traded in 2020-09. The scanner seeded its monthly 50 EMA from them and the
line came out $37.53 instead of ~$29, so the uptrend check failed and a real
touch never showed in "50 EMA Touch Monthly" (BBAI, DJT and GRAB the same).

Here the real refresh (momx.ema_touch.refresh) runs on a faked Alpaca reply,
then the list is read through the real scanner worker over HTTP. Only the
Alpaca call is faked.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from momx import ema_touch, feed
from worker_harness import running_worker

LISTS = "/api/momx-scanner/lists"


def _months(count, start_year=2010):
    for i in range(count):
        yield f"{start_year + i // 12:04d}-{i % 12 + 1:02d}-01T05:00:00Z"


def _alpaca_reply(placeholder_months: int):
    stamps = list(_months(placeholder_months + 111))
    real = [20.0 + 0.05 * i for i in range(111)]
    level = ema_touch.ema(real[:-1], 50)[-1]
    bars = [{"t": t, "l": 253.9, "c": 253.9} for t in stamps[:placeholder_months]]
    bars += [{"t": t, "l": c, "c": c} for t, c in zip(stamps[placeholder_months:], real)]
    bars[-1]["l"] = round(level - 0.01, 4)          # this month's low tags the real line
    return {"OUST": bars}


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
    yield
    ema_touch._CACHE.clear()
    ema_touch._CACHE_MTIME = None


def _fake_alpaca(monkeypatch, placeholder_months):
    monthly = _alpaca_reply(placeholder_months)

    def fake_get(url, params=None, headers=None, timeout=None):
        assert url == ema_touch.BARS_URL
        return _Reply({"bars": monthly if params["timeframe"] == "1Month" else {}})

    monkeypatch.setattr("requests.get", fake_get)


def _count(worker, name):
    status, body = worker.get(LISTS)
    assert status == 200, body
    return {item["name"]: item["count"] for item in body["lists"]}[name]


@pytest.mark.parametrize("placeholder_months", [0, 39])
def test_spac_lead_in_does_not_hide_a_real_monthly_50_touch(tmp_path, monkeypatch, ema_scratch, placeholder_months):
    _fake_alpaca(monkeypatch, placeholder_months)
    with running_worker(tmp_path, monkeypatch) as worker:
        (tmp_path / "worker" / "universe.json").write_text(json.dumps({"lists": {"Watchlist": ["OUST"]}}))
        summary = ema_touch.refresh(["OUST"])
        assert summary["50:Monthly"] == 1
        assert _count(worker, "50 EMA Touch Monthly") == 1
    state = ema_touch.detail("OUST")["50:Monthly"]
    real = [20.0 + 0.05 * i for i in range(110)]
    assert state["level"] == pytest.approx(ema_touch.ema(real, 50)[-1], abs=1e-3)
    assert state["uptrend"] and state["touched"]
