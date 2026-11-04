"""Integration (2026-11-04 coverage pass, group charts-studies, feature
charts-saved-grids): "Save layout" / the Grids menu through the real
GET/POST /api/chart-grids routes over HTTP, auth gate included.

The trader's account is the loopback auto-login (the real session path). A
second account is a tunnel visitor whose identity the auth gate is told by a
test header - the device-approval login flow is not what is under test here.
The grid file is ALWAYS a scratch file: api_server._chart_grids_path() points
at the live artifacts/chart_grids.json and conftest does not redirect it.
"""
from __future__ import annotations

import json

import pytest

from api_harness import RELAYED, api_server, running_api

MEMBER_EMAIL = "member@example.com"
MORNING = {"savedAt": "2026-11-04T14:00:00.000Z", "workspace": {"layoutId": "quad", "panels": [{"symbol": "SPY", "timeframe": "5m"}]}, "indicators": {"ema9": True}}
META = {"defaultGrid": {"name": "Morning", "savedAt": "2026-11-04T14:00:00.000Z"}, "deleted": {"Old": "2026-11-03T10:00:00.000Z"}}


@pytest.fixture
def grid_file(tmp_path, monkeypatch):
    path = tmp_path / "chart_grids.json"
    monkeypatch.setattr(api_server, "_chart_grids_path", lambda: path)
    real_session_user = api_server.ApiHandler._session_user

    def session_user(handler):
        if handler.headers.get("X-Test-Account") == MEMBER_EMAIL:
            return {"email": MEMBER_EMAIL, "isAdmin": False, "role": "member"}
        return real_session_user(handler)

    monkeypatch.setattr(api_server.ApiHandler, "_session_user", session_user)
    return path


MEMBER = {**RELAYED, "X-Test-Account": MEMBER_EMAIL}


def test_save_then_reload_returns_the_same_grids_including_the_default_marker(grid_file):
    grids = {"Morning": MORNING, "$meta": META}
    with running_api() as api:
        status, reply = api.post("/api/chart-grids", {"grids": grids})
        get_status, body = api.get("/api/chart-grids")
    assert (status, reply) == (200, {"ok": True, "count": 2})
    assert get_status == 200
    assert body == {"grids": grids}, "the $meta default grid and tombstones must survive the round trip"
    assert json.loads(grid_file.read_text(encoding="utf-8"))["version"] == 2


@pytest.mark.parametrize("payload", [{}, {"grids": None}, {"grids": ["Morning"]}, {"grids": "Morning"}])
def test_a_save_without_a_grids_object_is_refused_and_writes_nothing(grid_file, payload):
    with running_api() as api:
        status, body = api.post("/api/chart-grids", payload)
    assert status == 400
    assert body == {"error": "grids object is required."}
    assert not grid_file.exists()


def test_another_accounts_save_never_removes_or_shows_the_traders_grids(grid_file):
    with running_api() as api:
        api.post("/api/chart-grids", {"grids": {"Morning": MORNING, "$meta": META}})
        status, reply = api.post("/api/chart-grids", {"grids": {"Theirs": MORNING}}, headers=MEMBER)
        _s, member_view = api.get("/api/chart-grids", headers=MEMBER)
        _s, owner_view = api.get("/api/chart-grids")
        api.post("/api/chart-grids", {"grids": {}}, headers=MEMBER)   # they clear theirs
        _s, owner_after = api.get("/api/chart-grids")
    assert (status, reply["count"]) == (200, 1)
    assert member_view == {"grids": {"Theirs": MORNING}}
    assert owner_view == {"grids": {"Morning": MORNING, "$meta": META}}
    assert owner_after == owner_view


def test_a_legacy_flat_file_is_shown_to_the_owner_and_not_to_another_account(grid_file):
    grid_file.write_text(json.dumps({"Mags": MORNING}), encoding="utf-8")
    with running_api() as api:
        _s, owner = api.get("/api/chart-grids")
        _s, member = api.get("/api/chart-grids", headers=MEMBER)
    assert owner == {"grids": {"Mags": MORNING}}
    assert member == {"grids": {}}


def test_a_corrupt_grid_file_reads_as_empty_not_a_server_error(grid_file):
    grid_file.write_text("{\"version\": 2, \"users\": ", encoding="utf-8")
    with running_api() as api:
        status, body = api.get("/api/chart-grids")
        save_status, _reply = api.post("/api/chart-grids", {"grids": {"Morning": MORNING}})
        _s, after = api.get("/api/chart-grids")
    assert (status, body) == (200, {"grids": {}})
    assert save_status == 200
    assert after == {"grids": {"Morning": MORNING}}, "a save heals the corrupt file"


def test_a_signed_out_tunnel_visitor_cannot_read_or_write_grids(grid_file):
    with running_api() as api:
        get_status, _ = api.get("/api/chart-grids", headers=RELAYED)
        post_status, _ = api.post("/api/chart-grids", {"grids": {"X": MORNING}}, headers=RELAYED)
    assert (get_status, post_status) == (401, 401)
    assert not grid_file.exists()
