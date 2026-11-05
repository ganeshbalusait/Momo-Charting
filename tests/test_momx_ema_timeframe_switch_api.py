"""Integration: switching an EMA list's timeframe retires the old list's warm loop.

2026-11-04 22:55 ET: both EMA lists were on Daily, yet the worker still built
"21 EMA Touch Monthly" and "21 EMA Touch Weekly" every 15 s and failed each
time with UnknownListError -- and /status showed that as the scanner's error.

Here the switch goes through the real worker route; the old list's real warm
loop then runs and must stop on its own. Only the board build is faked.
"""
from __future__ import annotations

import threading

import pytest

from momx import ema_touch, service
from worker_harness import running_worker

SWITCH = "/api/momx-scanner/ema-timeframe"
STATUS = "/api/momx-scanner/status"


@pytest.fixture
def ema_scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_EMA_LISTS", "1")
    monkeypatch.setenv("AGX_MOMX_EMA_TOUCH_PATH", str(tmp_path / "ema_touch.json"))
    monkeypatch.setenv("AGX_MOMX_EMA_TF_PATH", str(tmp_path / "ema_tf.json"))
    monkeypatch.setattr(ema_touch, "start_refresher", lambda universe: None)
    yield


def test_switching_timeframe_stops_the_old_lists_builds(tmp_path, monkeypatch, ema_scratch):
    built = []
    monkeypatch.setattr(service, "_build_once", lambda name: built.append(name) or 0.01)
    monkeypatch.setattr(service, "_grade_nightly", lambda: None)
    monkeypatch.setattr(service, "_next_delay", lambda elapsed, failed: 0.02)
    with running_worker(tmp_path, monkeypatch) as worker:
        old = "21 EMA Touch Monthly"
        status, body = worker.post(SWITCH, {"length": 21, "timeframe": "Daily"})
        assert status == 200 and body["ok"] and body["previous"] == old, body
        service._state(old).due_at = 0.0          # the old loop was due to build again
        loop = threading.Thread(target=service._warm_one, args=(old,), daemon=True)
        loop.start()
        loop.join(timeout=3.0)
        assert not loop.is_alive(), "the old list's warm loop never stopped"
        assert old not in built
        status, body = worker.get(STATUS)
        assert status == 200
        names = [item["name"] for item in body["lists"]]
        assert old not in names and "21 EMA Touch Daily" in names
        assert "Unknown list" not in body["error"]
