"""Integration (2026-11-11, feature chart-project-roi): the chart's Project ROI
card is filled from the same GET /api/oi-finder-chain payload the chart reads.

Real route, real chain builder; Schwab is a fake client returning a fixed chain
(reused from the High OI walls test: AAPL at 200, strikes 180-220 every 5,
expiries 2 / 9 / 45 DTE). The payload is fed to the real frontend module
(frontend/src/chartProjectRoi.js) under Node, so a renamed field on either side
(selectedExpiryChainRows, expected_move, ask, delta, gamma, currentAtm) breaks
this test instead of leaving the card empty.
"""
from __future__ import annotations

import json
import subprocess
from importlib import import_module
from pathlib import Path

import pytest

from api_harness import running_api

walls = import_module("test_cov_charts-studies_chain_levels_api")
SYMBOL, SPOT = walls.SYMBOL, walls.SPOT
schwab = walls.schwab  # the pytest fixture

MODULE = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "chartProjectRoi.js").as_uri()

NODE_SCRIPT = """
import { readFileSync } from "node:fs";
const { buildProjectRoi, projectRoiChartLines } = await import(process.argv[1]);
const body = JSON.parse(readFileSync(0, "utf8"));
const rows = body.selectedExpiryChainRows;
const spot = body.underlyingPrice;
const atm = body.currentAtm || {};
const moves = atm.expiry && atm.expectedMove ? { [atm.expiry]: atm.expectedMove } : body.expiryExpectedMoves;
// A wall at 210 (call side) and 190 (put side), as the chart passes its OI walls.
const walls = [{ price: 210, side: "C", title: "9K" }, { price: 190, side: "P", title: "8K" }];
const out = {};
for (const side of ["CALL", "PUT"]) {
  const result = buildProjectRoi({ rows, spot, walls, defaultExpiry: atm.expiry, expiryExpectedMoves: moves, plan: { side } });
  out[side] = {
    ready: result.ready, expiry: result.expiry, expectedMove: result.expectedMove, strike: result.strike,
    reason: result.strikeReason, entry: result.entry, target: result.target, roi: result.roiPercent,
    stop: result.stopStock, ladder: result.ladder.map((item) => item.strike),
    lines: projectRoiChartLines(result).map((line) => line.key),
  };
}
out.spot = spot;
process.stdout.write(JSON.stringify(out));
"""


def _project(body: dict) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_SCRIPT, MODULE],
        input=json.dumps(body), capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_chain_route_payload_fills_the_chart_roi_card(schwab):
    with running_api() as api:
        status, body = api.get(f"/api/oi-finder-chain?symbol={SYMBOL}&force=true")
    assert status == 200, body
    assert body["selectedExpiryChainRows"], "the chart card reads selectedExpiryChainRows"
    out = _project(body)
    call, put = out["CALL"], out["PUT"]
    for side in (call, put):
        assert side["ready"] is True
        assert side["expectedMove"] > 0
        assert side["entry"] > 0, "entry is the picked contract's live ask"
        assert side["roi"] is not None
        assert side["lines"] == ["entry", "t1", "target", "t3", "stop"]  # T1 / T2 (Target) / T3, as the chart draws them
        assert side["ladder"][0] == 200.0, "the ladder starts at the ATM strike"
    # Target is the full expected move in the trade's direction.
    assert call["target"] == pytest.approx(SPOT + call["expectedMove"], abs=0.01)
    assert put["target"] == pytest.approx(SPOT - put["expectedMove"], abs=0.01)
    # The chain has deltas, so the strike is the chain ENTRY contract (nearest 0.20 delta, same as the Options chain,
    # f83a50fc); the wall-nearest-the-exit pick is the fallback without deltas (chartProjectRoi.test.js).
    assert call["strike"] in call["ladder"] and call["strike"] > SPOT
    assert put["strike"] in put["ladder"] and put["strike"] < SPOT
    assert "chain ENTRY" in call["reason"] and "nearest 0.20 delta" in call["reason"], call["reason"]
    assert "chain ENTRY" in put["reason"] and "nearest 0.20 delta" in put["reason"], put["reason"]
    # Stop is on the losing side of spot.
    assert call["stop"] < SPOT < put["stop"]
