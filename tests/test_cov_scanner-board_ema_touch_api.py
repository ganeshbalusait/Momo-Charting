"""Integration: the "EMA chart:" Monthly / Weekly / Daily dropdown on the
21 / 50 EMA touch lists, through the real scanner worker routes.

The timeframe is part of the list's NAME, so switching it renames the list;
a trader who was on the old one lands on the new one. A bad choice is
refused with the worker's {ok: false, error} reply (HTTP 200 - the panel's
onEmaTimeframe reads payload.ok) and changes nothing. No bars are fetched:
the touch cache and the timeframe setting are scratch files and the
background refresher is off.
"""
from __future__ import annotations

import pytest

from momx import ema_touch
from worker_harness import running_worker

LISTS = "/api/momx-scanner/lists"
SWITCH = "/api/momx-scanner/ema-timeframe"


@pytest.fixture
def ema_lists(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_EMA_LISTS", "1")
    monkeypatch.setenv("AGX_MOMX_EMA_TOUCH_PATH", str(tmp_path / "ema_touch.json"))
    monkeypatch.setenv("AGX_MOMX_EMA_TF_PATH", str(tmp_path / "ema_tf.json"))
    monkeypatch.setattr(ema_touch, "start_refresher", lambda universe: None)
    return tmp_path / "ema_tf.json"


def listed(worker):
    status, body = worker.get(LISTS)
    assert status == 200, body
    return {item["name"]: item for item in body["lists"]}, body


def test_the_two_touch_lists_offer_the_three_timeframes(tmp_path, monkeypatch, ema_lists):
    with running_worker(tmp_path, monkeypatch) as worker:
        by_name, _ = listed(worker)
    assert list(by_name) == ["Mag7", "Watchlist", "21 EMA Touch Monthly", "50 EMA Touch Monthly"]
    monthly = by_name["21 EMA Touch Monthly"]
    assert (monthly["emaLength"], monthly["timeframe"], monthly["timeframes"]) == (21, "Monthly", ["Monthly", "Weekly", "Daily"])
    assert monthly["builtIn"] is True
    assert "emaLength" not in by_name["Mag7"]          # no dropdown on an ordinary list


def test_picking_weekly_renames_the_list_and_leaves_the_other_length_alone(tmp_path, monkeypatch, ema_lists):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.post(SWITCH, {"length": 21, "timeframe": "weekly"})
        by_name, raw = listed(worker)
        board_status, board = worker.get("/api/momx-scanner?list=21%20EMA%20Touch%20Weekly")
    assert status == 200, body
    assert (body["ok"], body["list"], body["previous"]) == (True, "21 EMA Touch Weekly", "21 EMA Touch Monthly")
    assert "21 EMA Touch Monthly" not in by_name
    weekly = by_name["21 EMA Touch Weekly"]
    assert (weekly["emaLength"], weekly["timeframe"]) == (21, "Weekly")
    assert by_name["50 EMA Touch Monthly"]["timeframe"] == "Monthly"
    assert raw["active"] == "Mag7"                     # he was not on that list
    assert board_status == 200 and board["list"] == "21 EMA Touch Weekly"


def test_the_trader_on_the_old_timeframe_lands_on_the_new_one(tmp_path, monkeypatch, ema_lists):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.post(LISTS + "/active", {"list": "50 EMA Touch Monthly"})
        status, body = worker.post(SWITCH, {"length": "50", "timeframe": "Daily"})
        _, raw = listed(worker)
    assert status == 200 and body["active"] == "50 EMA Touch Daily"
    assert raw["active"] == "50 EMA Touch Daily"


@pytest.mark.parametrize("payload, error", [
    ({"length": 21, "timeframe": "Hourly"}, "Timeframe must be one of Monthly, Weekly, Daily."),
    ({"length": 30, "timeframe": "Weekly"}, "Unknown EMA length 30."),
    ({"timeframe": "Weekly"}, None),                  # no length at all: refused, wording aside
])
def test_a_bad_choice_is_refused_and_nothing_changes(tmp_path, monkeypatch, ema_lists, payload, error):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.post(SWITCH, payload)
        by_name, _ = listed(worker)
    assert status == 200 and body["ok"] is False and body["error"]
    if error is not None:
        assert body["error"] == error
    assert "21 EMA Touch Monthly" in by_name and "21 EMA Touch Weekly" not in by_name
    assert not ema_lists.exists()                     # the setting was never written


def test_with_the_touch_lists_switched_off_the_dropdown_has_nothing_to_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_EMA_LISTS", "0")
    with running_worker(tmp_path, monkeypatch) as worker:
        by_name, _ = listed(worker)
    assert list(by_name) == ["Mag7", "Watchlist"]
