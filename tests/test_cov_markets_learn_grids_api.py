"""Integration: the chart-grid read that the Learn auto-open has to beat
(2026-11-04 coverage).

On first mount the app reads GET /api/chart-grids, passes `grids` through
chartWorkspaceState.readChartGridMeta, and steers a browser that has not
applied the saved default grid to Charts & OI - unless Learn just auto-opened.
The end-to-end harness reads the same route the same way to stop that steer.
This drives the real route over HTTP, then hands its JSON to the real
frontend function in node, so a change on either side is caught.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from api_harness import api_server, running_api

REPO = Path(__file__).resolve().parents[1]
WORKSPACE_STATE = (REPO / "frontend" / "src" / "chartWorkspaceState.js").as_uri()

GRID = {"layout": "2x1", "charts": [{"symbol": "SPY", "timeframe": "5m"}, {"symbol": "NVDA", "timeframe": "5m"}]}
SAVED_AT = "2026-11-03T21:15:00.000Z"


def read_meta_in_node(grids: dict, tmp_path: Path) -> dict:
    source = tmp_path / "grids.json"
    source.write_text(json.dumps(grids), encoding="utf-8")
    script = (
        f"const m = await import({json.dumps(WORKSPACE_STATE)});"
        "const fs = await import('node:fs');"
        "const grids = JSON.parse(fs.readFileSync(process.argv[1], 'utf8'));"
        "const meta = m.readChartGridMeta(grids);"
        "const stamp = meta.defaultGrid ? m.chartGridStamp(meta.defaultGrid.name, meta.defaultGrid.savedAt) : null;"
        "console.log(JSON.stringify({ ...meta, stamp }));"
    )
    out = subprocess.run(["node", "--input-type=module", "-e", script, str(source)],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture
def grids_file(monkeypatch, tmp_path):
    path = tmp_path / "chart_grids.json"
    monkeypatch.setattr(api_server, "_chart_grids_path", lambda: path)
    return path


def _owner_keys() -> list[str]:
    email = str(os.getenv("LOCAL_AUTO_LOGIN_EMAIL", "") or "").strip().lower()
    return [key for key in (email, "_default") if key]


def test_a_saved_default_grid_reaches_the_boot_steer_as_name_and_saved_at(grids_file, tmp_path):
    bucket = {"Two up": GRID, "$meta": {"defaultGrid": {"name": "Two up", "savedAt": SAVED_AT}, "deleted": {}}}
    grids_file.write_text(json.dumps({"version": 2, "users": {key: bucket for key in _owner_keys()}}), encoding="utf-8")
    with running_api() as api:
        status, body = api.get("/api/chart-grids")
    assert status == 200, body
    assert body["grids"]["Two up"] == GRID
    meta = read_meta_in_node(body["grids"], tmp_path)
    assert meta["defaultGrid"] == {"name": "Two up", "savedAt": SAVED_AT}
    assert meta["stamp"] == f"Two up@{SAVED_AT}", "the applied-grid stamp the harness and the app both compare"


def test_no_saved_grid_means_no_steer(grids_file, tmp_path):
    with running_api() as api:                     # the file does not exist yet
        status, body = api.get("/api/chart-grids")
    assert status == 200 and body == {"grids": {}}
    assert read_meta_in_node(body["grids"], tmp_path)["defaultGrid"] is None


def test_another_accounts_default_grid_never_steers_this_browser(grids_file, tmp_path):
    other = {"Mags": GRID, "$meta": {"defaultGrid": {"name": "Mags", "savedAt": SAVED_AT}}}
    grids_file.write_text(json.dumps({"version": 2, "users": {"someone-else@example.invalid": other}}), encoding="utf-8")
    with running_api() as api:
        status, body = api.get("/api/chart-grids")
    assert status == 200
    assert "Mags" not in body["grids"]
    assert read_meta_in_node(body["grids"], tmp_path)["defaultGrid"] is None


def test_a_torn_grid_file_reads_as_no_grids_not_an_error(grids_file, tmp_path):
    grids_file.write_text("{ half a json", encoding="utf-8")
    with running_api() as api:
        status, body = api.get("/api/chart-grids")
    assert status == 200 and body == {"grids": {}}
