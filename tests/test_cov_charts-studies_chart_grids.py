"""Unit (2026-11-04 coverage pass, group charts-studies, feature
charts-saved-grids): the server's per-account saved-grid store in api_server.

The file used to be one flat {name: grid} map shared by everyone, and a save
rewrote the whole file - one user's save deleted every other user's grids
(2026-08-31). These pin the v2 store: per-account buckets, the legacy flat
file handed to the admin only, and a non-admin save that never drops the
admin's un-migrated grids. Every test points the store at a scratch file.
"""
from __future__ import annotations

import json

import pytest

import api_server

ADMIN = {"email": "Owner@Example.com", "isAdmin": True}
MEMBER = {"email": "member@example.com", "isAdmin": False}
GRID = {"savedAt": "2026-11-04T14:00:00.000Z", "workspace": {"layoutId": "quad", "panels": []}, "indicators": None}
META = {"defaultGrid": {"name": "Morning", "savedAt": "2026-11-04T14:00:00.000Z"}, "deleted": {}}


@pytest.fixture
def grid_file(tmp_path, monkeypatch):
    path = tmp_path / "chart_grids.json"
    monkeypatch.setattr(api_server, "_chart_grids_path", lambda: path)
    return path


def test_a_missing_file_is_an_empty_v2_store(grid_file):
    assert api_server._load_chart_grids_document() == {"version": 2, "users": {}}
    assert api_server._chart_grids_for_user(ADMIN) == {}


@pytest.mark.parametrize("raw", ["{not json", "[1, 2, 3]", "\"text\"", ""])
def test_a_corrupt_file_never_raises(grid_file, raw):
    grid_file.write_text(raw, encoding="utf-8")
    assert api_server._load_chart_grids_document() == {"version": 2, "users": {}}
    assert api_server._chart_grids_for_user(MEMBER) == {}


def test_a_legacy_flat_file_goes_to_the_admin_only(grid_file):
    grid_file.write_text(json.dumps({"Mags": GRID, "junk": 5}), encoding="utf-8")
    document = api_server._load_chart_grids_document()
    assert document["_legacy"] == {"Mags": GRID}, "non-grid values are not carried"
    assert api_server._chart_grids_for_user(ADMIN) == {"Mags": GRID}
    assert api_server._chart_grids_for_user(MEMBER) == {}
    assert api_server._chart_grids_for_user(None) == {}


def test_accounts_are_keyed_by_lowercased_email_and_anonymous_shares_one_bucket():
    assert api_server._chart_grids_user_key(ADMIN) == "owner@example.com"
    assert api_server._chart_grids_user_key({"email": "  "}) == "_default"
    assert api_server._chart_grids_user_key(None) == "_default"


def test_save_keeps_only_named_grid_objects_and_returns_the_count(grid_file):
    count = api_server._save_chart_grids_for_user(MEMBER, {
        "Morning": GRID, "$meta": META, "List grid": [1, 2], "bad": "text", "worse": 7,
    })
    assert count == 3
    saved = json.loads(grid_file.read_text(encoding="utf-8"))
    assert saved["version"] == 2
    assert set(saved["users"]["member@example.com"]) == {"Morning", "$meta", "List grid"}
    assert api_server._chart_grids_for_user(MEMBER)["$meta"] == META, "the $meta default/tombstones round-trip"


def test_one_accounts_save_never_touches_another_accounts_grids(grid_file):
    api_server._save_chart_grids_for_user(ADMIN, {"Owner grid": GRID})
    api_server._save_chart_grids_for_user(MEMBER, {"Member grid": GRID})
    api_server._save_chart_grids_for_user(MEMBER, {})  # member deletes all of THEIR grids
    assert api_server._chart_grids_for_user(ADMIN) == {"Owner grid": GRID}
    assert api_server._chart_grids_for_user(MEMBER) == {}


def test_a_member_save_keeps_the_legacy_grids_and_the_admin_save_migrates_them(grid_file):
    grid_file.write_text(json.dumps({"Mags": GRID}), encoding="utf-8")
    api_server._save_chart_grids_for_user(MEMBER, {"Mine": GRID})
    after_member = json.loads(grid_file.read_text(encoding="utf-8"))
    assert after_member["_legacy"] == {"Mags": GRID}, "a non-admin save deleted the owner's unattributed grids"
    assert api_server._chart_grids_for_user(ADMIN) == {"Mags": GRID}

    # The admin's next save carries the legacy grids he was shown; the block goes.
    api_server._save_chart_grids_for_user(ADMIN, api_server._chart_grids_for_user(ADMIN))
    after_admin = json.loads(grid_file.read_text(encoding="utf-8"))
    assert "_legacy" not in after_admin
    assert after_admin["users"]["owner@example.com"] == {"Mags": GRID}
    assert after_admin["users"]["member@example.com"] == {"Mine": GRID}


def test_the_admins_own_grid_wins_over_a_legacy_grid_of_the_same_name(grid_file):
    newer = {**GRID, "savedAt": "2026-11-05T09:00:00.000Z"}
    grid_file.write_text(json.dumps({
        "version": 2, "users": {"owner@example.com": {"Mags": newer}}, "_legacy": {"Mags": GRID, "Old": GRID},
    }), encoding="utf-8")
    assert api_server._chart_grids_for_user(ADMIN) == {"Mags": newer, "Old": GRID}


# 2026-11-25: the morning checklist flagged "GOOGLE (Mags panel 3)" again two days after the file was fixed by hand -
# another account's browser re-saved its old copy. The store now writes the real ticker whatever the browser sends.
def test_a_save_holding_the_retired_GOOGLE_ticker_is_stored_as_GOOGL(grid_file):
    mags = {"savedAt": "2026-08-31T14:37:32.665Z", "workspace": {"layoutId": "grid10", "panels": [
        {"symbol": "AAPL"}, {"symbol": "GOOGLE", "timeframe": "5m"}, {"symbol": "google"}]}}
    api_server._save_chart_grids_for_user(MEMBER, {"Mags": mags})
    panels = json.loads(grid_file.read_text(encoding="utf-8"))["users"]["member@example.com"]["Mags"]["workspace"]["panels"]
    assert [p["symbol"] for p in panels] == ["AAPL", "GOOGL", "GOOGL"]
    assert panels[1]["timeframe"] == "5m"
    assert mags["workspace"]["panels"][1]["symbol"] == "GOOGLE", "the caller's object is not changed in place"


def test_a_stored_GOOGLE_is_read_back_as_GOOGL_so_the_browser_corrects_its_copy(grid_file):
    grid_file.write_text(json.dumps({"version": 2, "users": {"member@example.com": {
        "Mags": {"workspace": {"panels": [{"symbol": "GOOGLE"}, {"symbol": "GOOG"}]}}}}}), encoding="utf-8")
    panels = api_server._chart_grids_for_user(MEMBER)["Mags"]["workspace"]["panels"]
    assert [p["symbol"] for p in panels] == ["GOOGL", "GOOG"], "GOOG is a real ticker and stays"
