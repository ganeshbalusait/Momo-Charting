"""Integration (2026-11-11, feature roi-calc-autofill): the ROI Calc page
fills itself from the same GET /api/oi-finder-chain payload the screen reads.

Real route, real chain builder; Schwab is a fake client returning a fixed
chain (reused from the High OI walls test). The payload the route returns is
then fed to the real frontend module (frontend/src/roiCalcAutofill.js) under
Node, so a renamed field on either side (selectedExpiryChainRows, expected_move,
open_interest, ask, currentAtm.expiry, expiryExpectedMoves) breaks this test
instead of leaving the page blank.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from api_harness import running_api

# Same fixed chain and fake Schwab as the walls test: AAPL at 200, strikes
# 180-220 every 5, expiries 2 / 9 / 45 DTE.
from importlib import import_module

walls = import_module("test_cov_charts-studies_chain_levels_api")
SYMBOL, SPOT = walls.SYMBOL, walls.SPOT
schwab = walls.schwab  # the pytest fixture

MODULE = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "roiCalcAutofill.js").as_uri()

NODE_SCRIPT = """
import { readFileSync } from "node:fs";
const { buildRoiAutofill, roiAutoTargets, roiEntryPrice } = await import(process.argv[1]);
const body = JSON.parse(readFileSync(0, "utf8"));
const rows = body.selectedExpiryChainRows;
const expiry = body.currentAtm.expiry;
const out = {};
for (const side of ["CALL", "PUT"]) {
  const fill = buildRoiAutofill({ rows, spot: body.underlyingPrice, side, expiry, expiryExpectedMoves: body.expiryExpectedMoves });
  const picked = rows.find((row) => row.expiry === expiry && row.side === side && Number(row.strike) === fill.defaultStrike);
  out[side] = {
    expectedMove: fill.expectedMove,
    atmStrike: fill.atmStrike,
    itmStrike: fill.itmContract ? fill.itmContract.strike : null,
    itmPremium: fill.itmPremium,
    ladder: fill.ladder.map((item) => item.strike),
    defaultStrike: fill.defaultStrike,
    entry: roiEntryPrice(picked),
    targets: roiAutoTargets({ rows, spot: body.underlyingPrice, side, expiry, expectedMove: fill.expectedMove }),
  };
}
out.expiry = expiry;
process.stdout.write(JSON.stringify(out));
"""


def _autofill(body: dict) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_SCRIPT, MODULE],
        input=json.dumps(body), capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_chain_route_payload_fills_every_roi_calc_box(schwab):
    with running_api() as api:
        status, body = api.get(f"/api/oi-finder-chain?symbol={SYMBOL}&force=true")
    assert status == 200, body
    assert body["selectedExpiryChainRows"], "the ROI page reads selectedExpiryChainRows"
    fill = _autofill(body)
    expiry = fill["expiry"]
    move = float(body["expiryExpectedMoves"][expiry])
    assert move > 0

    call, put = fill["CALL"], fill["PUT"]
    for side in (call, put):
        assert side["expectedMove"] == pytest.approx(move)
        assert side["atmStrike"] == 200.0
    # First ITM strike that is not the ATM: 195 call, 205 put.
    assert call["itmStrike"] == 195.0 and put["itmStrike"] == 205.0
    assert call["itmPremium"] > 0 and put["itmPremium"] > 0
    # Ladder starts at the ATM and walks out of the money; first OTM is picked.
    assert call["ladder"][0] == 200.0 and call["ladder"][1:] == sorted(call["ladder"][1:])
    assert put["ladder"][0] == 200.0 and put["ladder"][1:] == sorted(put["ladder"][1:], reverse=True)
    assert call["defaultStrike"] == 205.0 and put["defaultStrike"] == 195.0
    # Entry is the live ask of the picked contract from the same payload.
    picked_call = next(row for row in body["selectedExpiryChainRows"]
                       if row["expiry"] == expiry and row["side"] == "CALL" and row["strike"] == 205.0)
    assert call["entry"] == pytest.approx(picked_call["ask"])
    # Targets: half move and full move in the trade's direction.
    assert float(call["targets"][0]) == pytest.approx(SPOT + move / 2, abs=0.01)
    assert float(call["targets"][1]) == pytest.approx(SPOT + move, abs=0.01)
    assert float(put["targets"][1]) == pytest.approx(SPOT - move, abs=0.01)
    assert float(call["targets"][2]) > float(call["targets"][1])
    assert float(put["targets"][2]) < float(put["targets"][1])
