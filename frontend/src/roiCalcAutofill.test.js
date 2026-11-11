import test from "node:test";
import assert from "node:assert/strict";
import {
  buildRoiAutofill,
  roiAutoTargets,
  roiEntryPrice,
  roiExpiryExpectedMove,
  roiStopLevel,
} from "./roiCalcAutofill.js";

const EXP = "2026-11-11";
const row = (side, strike, mark, extra = {}) => ({
  side, strike, mark, bid: mark - 0.02, ask: mark + 0.02, expiry: EXP,
  expected_move: 2.07, delta: side === "CALL" ? 0.4 : -0.4, gamma: 0.05, open_interest: 100, ...extra,
});
const rows = [
  row("CALL", 172.5, 3.2), row("CALL", 175, 1.6), row("CALL", 177.5, 0.6),
  row("CALL", 180, 0.2, { open_interest: 9000 }), row("CALL", 182.5, 0.05),
  row("PUT", 170, 0.1, { open_interest: 7000 }), row("PUT", 172.5, 0.5), row("PUT", 175, 1.2), row("PUT", 177.5, 2.6),
  row("CALL", 175, 9.9, { expiry: "2026-11-13", expected_move: 5.75 }),
];

test("expected move comes from the expiry map, else from the chain rows", () => {
  assert.equal(roiExpiryExpectedMove({ rows, expiry: EXP, expiryExpectedMoves: { [EXP]: 2.5 } }), 2.5);
  assert.equal(roiExpiryExpectedMove({ rows, expiry: EXP, expiryExpectedMoves: {} }), 2.07);
  assert.equal(roiExpiryExpectedMove({ rows, expiry: "2026-11-13" }), 5.75);
  assert.equal(roiExpiryExpectedMove({ rows, expiry: "2027-01-01" }), 0);
});

test("call auto-fill: ITM premium, ATM, ladder inside ±EM and first-OTM pick", () => {
  const fill = buildRoiAutofill({ rows, spot: 175.54, side: "CALL", expiry: EXP });
  assert.equal(fill.expectedMove, 2.07);
  assert.equal(fill.atmStrike, 175);
  assert.equal(fill.itmContract.strike, 172.5);
  assert.equal(fill.itmPremium, 3.2);
  assert.deepEqual(fill.ladder.map((item) => item.strike), [175, 177.5, 180, 182.5]);
  assert.equal(fill.ladder[0].isAtm, true);
  assert.equal(fill.defaultStrike, 177.5);
  assert.equal(fill.ladder[1].distance, 1.96);
  // The other expiry's 175 call never leaks into this expiry's ladder.
  assert.equal(fill.ladder[0].price, 1.6);
});

test("put auto-fill mirrors the call side", () => {
  const fill = buildRoiAutofill({ rows, spot: 175.54, side: "put", expiry: EXP });
  assert.equal(fill.itmContract.strike, 177.5);
  assert.deepEqual(fill.ladder.map((item) => item.strike), [175, 172.5, 170]);
  assert.equal(fill.defaultStrike, 172.5);
});

test("no spot or no chain gives an empty fill instead of throwing", () => {
  assert.deepEqual(buildRoiAutofill({ rows, spot: 0, side: "CALL", expiry: EXP }).ladder, []);
  assert.equal(buildRoiAutofill({ rows: null, spot: 100, expiry: EXP }).defaultStrike, 0);
});

test("entry price is the ask, else the mark", () => {
  assert.equal(roiEntryPrice({ ask: 1.25, mark: 1.2 }), 1.25);
  assert.equal(roiEntryPrice({ ask: 0, mark: 1.2 }), 1.2);
  assert.equal(roiEntryPrice(null), 0);
});

test("targets: half move, full move, biggest OI level in the trade's direction", () => {
  assert.deepEqual(roiAutoTargets({ rows, spot: 175.54, side: "CALL", expiry: EXP, expectedMove: 2.5 }), ["176.79", "178.04", "180.00"]);
  assert.deepEqual(roiAutoTargets({ rows, spot: 175.54, side: "PUT", expiry: EXP, expectedMove: 2.5 }), ["174.29", "173.04", "172.50"]);
  // The 180 wall is past two moves when the move is 2.07: fall back to 1.5x.
  assert.deepEqual(roiAutoTargets({ rows, spot: 175.54, side: "CALL", expiry: EXP, expectedMove: 2.07 }), ["176.58", "177.61", "178.65"]);
  // No OI beyond the full move: 1.5x the move.
  assert.deepEqual(roiAutoTargets({ rows: [], spot: 100, side: "CALL", expiry: EXP, expectedMove: 2 }), ["101.00", "102.00", "103.00"]);
  assert.deepEqual(roiAutoTargets({ rows, spot: 0, expectedMove: 2 }), ["", "", ""]);
});

test("stop level: stock price where the contract is down 50%", () => {
  const call = roiStopLevel({ side: "CALL", spot: 100, entry: 1, delta: 0.5, gamma: 0 });
  assert.equal(call.stopPremium, 0.5);
  assert.equal(call.stopStock, 99);
  const put = roiStopLevel({ side: "PUT", spot: 100, entry: 1, delta: -0.5, gamma: 0 });
  assert.equal(put.stopStock, 101);
  // Gamma shrinks delta on the way down, so the stop sits further away.
  assert.ok(roiStopLevel({ side: "CALL", spot: 100, entry: 1, delta: 0.5, gamma: 0.1 }).stopStock < 99);
  assert.equal(roiStopLevel({ side: "CALL", spot: 100, entry: 1, delta: 0 }), null);
});
