"""Integration: the WATCHLISTS picker's routes on the real scanner worker.

Create / rename / delete a list, the six-list limit, and the built-in lists
(Mag7, Watchlist) that can be neither renamed nor deleted. Every write lands
in the harness's scratch universe file, never artifacts/momx_universe.json.

The worker answers a refused create/rename with HTTP 200 and {ok: false,
error} (the panel reads payload.ok - see listAction in MomxScannerPanel.jsx);
a refused delete of a built-in list is a 400. Both are asserted as the
contract the screen depends on.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from api_harness import api_server, running_api
from momx import board
from worker_harness import running_worker

LISTS = "/api/momx-scanner/lists"


def universe_file() -> Path:
    return board.universe_path()


def names(worker):
    status, body = worker.get(LISTS)
    assert status == 200, body
    return {item["name"]: item for item in body["lists"]}, body


def test_a_new_list_appears_empty_user_owned_and_active(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.post(LISTS + "/create", {"name": "  Swing  "})
        assert status == 200, body
        assert (body["ok"], body["list"], body["created"], body["universe"]) == (True, "Swing", True, [])
        by_name, raw = names(worker)
    assert list(by_name)[:3] == ["Mag7", "Watchlist", "Swing"]
    assert (by_name["Swing"]["builtIn"], by_name["Swing"]["count"]) == (False, 0)
    assert by_name["Mag7"]["builtIn"] is True and by_name["Watchlist"]["builtIn"] is True
    assert raw["active"] == "Swing"


def test_a_duplicate_name_is_refused_whatever_its_case(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.post(LISTS + "/create", {"name": "Swing"})
        status, body = worker.post(LISTS + "/create", {"name": "SWING"})
        blank = worker.post(LISTS + "/create", {"name": "   "})[1]
        long = worker.post(LISTS + "/create", {"name": "x" * 33})[1]
    assert status == 200 and body == {"ok": False, "error": "You already have a list called SWING."}
    assert blank == {"ok": False, "error": "Give the list a name."}
    assert long["ok"] is False and "too long" in long["error"]


def test_the_seventh_list_is_refused_and_the_file_is_left_alone(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        for name in ("One", "Two", "Three", "Four"):          # + Mag7 + Watchlist = 6
            assert worker.post(LISTS + "/create", {"name": name})[1]["ok"] is True
        before = universe_file().read_bytes()
        status, body = worker.post(LISTS + "/create", {"name": "Seven"})
        after = universe_file().read_bytes()
        listed, _ = names(worker)
    assert status == 200 and body["ok"] is False
    assert body["error"].startswith("You already have 6 lists, which is the limit.")
    assert after == before
    assert "Seven" not in listed and len(listed) == 6


def test_renaming_onto_an_existing_name_is_refused_and_both_lists_survive(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.post(LISTS + "/create", {"name": "Swing", "text": "AAPL NVDA"})
        worker.post(LISTS + "/create", {"name": "Earnings", "text": "SPY"})
        status, body = worker.post(LISTS + "/rename", {"list": "Swing", "name": "earnings"})
        listed, _ = names(worker)
    assert status == 400
    assert body["error"] == "You already have a list called earnings."
    assert listed["Swing"]["count"] == 2 and listed["Earnings"]["count"] == 1


def test_a_rename_keeps_the_symbols_the_position_and_the_active_tab(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.post(LISTS + "/create", {"name": "Swing", "text": "AAPL NVDA"})
        worker.post(LISTS + "/create", {"name": "Other"})
        worker.post(LISTS + "/active", {"list": "Swing"})
        status, body = worker.post(LISTS + "/rename", {"list": "swing", "name": "Swing Trades"})
        listed, raw = names(worker)
    assert status == 200 and body == {"ok": True, "list": "Swing Trades", "renamedFrom": "Swing", "universeCount": 2}
    assert list(listed) == ["Mag7", "Watchlist", "Swing Trades", "Other"]
    assert raw["active"] == "Swing Trades"
    assert board.load_universe("Swing Trades") == ["AAPL", "NVDA"]


def test_built_in_lists_cannot_be_renamed_or_deleted(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        rename = worker.post(LISTS + "/rename", {"list": "Mag7", "name": "Big Tech"})
        delete = worker.post(LISTS + "/delete", {"list": "Watchlist"})
        listed, _ = names(worker)
    assert rename == (400, {"error": "Mag7 is a built-in list, so its name cannot be changed."})
    assert delete[0] == 400 and delete[1]["error"].startswith("Watchlist is a built-in list and cannot be deleted.")
    assert {"Mag7", "Watchlist"} <= set(listed)


def test_deleting_the_active_user_list_sends_the_trader_back_to_mag7(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.post(LISTS + "/create", {"name": "Swing", "text": "AAPL"})
        worker.seed_board("Swing", {"generatedAt": "g1", "rows": [{"symbol": "AAPL"}], "universe": ["AAPL"]})
        status, body = worker.post(LISTS + "/delete", {"list": "Swing"})
        listed, raw = names(worker)
        gone = worker.get("/api/momx-scanner?list=Swing")
    assert status == 200, body
    assert (body["ok"], body["deleted"], body["active"]) == (True, "Swing", "Mag7")
    assert "Swing" not in listed and raw["active"] == "Mag7"
    assert gone[0] == 400       # its board is gone with it, not served from the old disk copy
    saved = json.loads(universe_file().read_text(encoding="utf-8"))
    assert "Swing" not in saved["lists"]


def test_a_refusal_through_api_server_keeps_the_workers_words(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server.ApiHandler, "_session_user",
                        lambda self: {"id": "u1", "email": "trader@example.com", "role": "member"})
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        status, body = api.post(LISTS + "/delete", {"list": "Mag7"})
        created = api.post(LISTS + "/create", {"name": "Via Proxy"})
        listed = api.get(LISTS)[1]
    assert status == 400
    assert body["error"].startswith("Mag7 is a built-in list and cannot be deleted.")
    assert created[1]["ok"] is True
    assert "Via Proxy" in [item["name"] for item in listed["lists"]]
