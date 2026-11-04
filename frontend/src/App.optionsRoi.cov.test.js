// Unit: the option ROI math behind the chain's ROI / Γ ROI columns and the
// ROI Calc page (2026-11-04 coverage). These helpers still live inside
// App.jsx, so they are lifted out of the source and executed - the same
// technique as chainFeedFresh.test.js. Tolerates CRLF (a Python edit can
// flip App.jsx's line endings).
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

const lift = (name) => {
  const match = new RegExp(`function ${name}\\([\\s\\S]*?\\r?\\n}\\r?\\n`).exec(appSource);
  assert.ok(match, `could not lift ${name} out of App.jsx`);
  return match[0];
};

const roi = new Function([
  "optionSnapshotPrice", "optionChainMark", "calculateGammaRoiEstimate",
  "buildOptionRoiEstimates", "buildOptionGammaRoiEstimates",
].map(lift).join("\n") + "\nreturn { optionSnapshotPrice, optionChainMark, calculateGammaRoiEstimate, buildOptionRoiEstimates, buildOptionGammaRoiEstimates };")();

const close = (actual, expected, label) => assert.ok(Math.abs(actual - expected) < 1e-9, `${label}: ${actual} != ${expected}`);

// spot 100. 105 is the entry: the OTM call under 0.20 delta closest to 0.20.
const calls = [
  { strike: 99, delta: 0.55, bid: 2.9, ask: 3.1, gamma: 0.05 },   // ITM: never an entry or a target
  { strike: 101, delta: 0.45, bid: 2.0, ask: 2.2, gamma: 0.05 },
  { strike: 103, delta: 0.30, bid: 1.0, ask: 1.2, gamma: 0.045 },
  { strike: 105, delta: 0.18, bid: 0.5, ask: 0.6, gamma: 0.04 },
  { strike: 107, delta: 0.10, bid: 0.2, ask: 0.3, gamma: 0.02 },  // past the entry: no ROI
];
const puts = calls.map((row) => ({ ...row, strike: 200 - row.strike, delta: -row.delta }));

test("a quote's price never invents a value: mid first for snapshots, mark first for the chain cell", () => {
  assert.equal(roi.optionSnapshotPrice({ bid: 1.0, ask: 1.2, mark: 5, last: 9 }), 1.1);
  assert.equal(roi.optionSnapshotPrice({ bid: 0, ask: 1.2, mark: 0.9, last: 9 }), 0.9);
  assert.equal(roi.optionSnapshotPrice({ last: 0.35 }), 0.35);
  assert.equal(roi.optionSnapshotPrice({}), 0);
  assert.equal(roi.optionSnapshotPrice(null), 0);
  assert.equal(roi.optionChainMark({ mark: 1.15, bid: 1.0, ask: 1.2 }), 1.15);
  assert.equal(roi.optionChainMark({ mark: 0, bid: 1.0, ask: 1.2 }), 1.1);
  assert.equal(roi.optionChainMark({}), 0, "an empty quote prints '--', not a made-up price");
});

test("call ROI: the entry is the nearest call under 0.20 delta, targets sit between spot and the entry", () => {
  const estimates = roi.buildOptionRoiEstimates(calls, 100, "CALL");
  assert.deepEqual([...estimates.keys()].sort((a, b) => a - b), [101, 103, 105]);
  assert.equal(estimates.get(105).kind, "entry");
  close(estimates.get(105).entryPrice, 0.55, "entry price");
  // ROI = (target Mark / entry Mark - 1) x 100
  assert.equal(estimates.get(103).percent, 100);
  assert.equal(estimates.get(101).percent, 282);
  assert.equal(estimates.has(107), false);
  assert.equal(estimates.has(99), false);
});

test("put ROI mirrors the call side and reads put deltas as magnitudes", () => {
  const estimates = roi.buildOptionRoiEstimates(puts, 100, "PUT");
  assert.equal(estimates.get(95).kind, "entry");
  assert.equal(estimates.get(97).percent, 100);
  assert.equal(estimates.get(99).percent, 282);
  assert.equal(estimates.has(93), false);
});

test("no contract under 0.20 delta: no ROI column at all rather than a wrong entry", () => {
  assert.equal(roi.buildOptionRoiEstimates(calls.filter((row) => row.delta >= 0.2), 100, "CALL").size, 0);
});

test("a server chain-mark estimate wins over the local one", () => {
  const withServer = calls.map((row) => (row.strike === 103
    ? { ...row, roiEstimate: { kind: "entry", source: "chain-mark", entryStrike: 103, entryPrice: 1.1, entryDelta: 0.3 } }
    : row));
  const estimates = roi.buildOptionRoiEstimates(withServer, 100, "CALL");
  assert.deepEqual([...estimates.keys()], [103]);
});

test("gamma ROI: exit = entry + average(delta now, delta at target) x move", () => {
  const call = roi.calculateGammaRoiEstimate({ side: "CALL", spotPrice: 100, strike: 105, entryPrice: 1, targetPrice: 104, delta: 0.3, gamma: 0.05 });
  close(call.estimatedExitPrice, 2.6, "call exit");
  close(call.roiPercent, 160, "call ROI");
  close(call.profitPerContract, 160, "call profit / contract");
  close(call.projectedDelta, 0.5, "call delta at target");
  const put = roi.calculateGammaRoiEstimate({ side: "PUT", spotPrice: 100, strike: 95, entryPrice: 1, targetPrice: 96, delta: 0.3, gamma: 0.05 });
  close(put.estimatedExitPrice, 2.6, "the put mirrors the call");
  close(put.entryDelta, -0.3, "put delta is signed by side");
  const negativeInput = roi.calculateGammaRoiEstimate({ side: "PUT", spotPrice: 100, strike: 95, entryPrice: 1, targetPrice: 96, delta: -0.3, gamma: 0.05 });
  close(negativeInput.estimatedExitPrice, 2.6, "a feed that sends signed put deltas gives the same answer");
});

test("gamma ROI never prices below intrinsic value and keeps delta inside 0..1", () => {
  const deep = roi.calculateGammaRoiEstimate({ side: "CALL", spotPrice: 100, strike: 100, entryPrice: 1, targetPrice: 110, delta: 0.5, gamma: 0 });
  close(deep.estimatedExitPrice, 10, "intrinsic floor");
  const clamped = roi.calculateGammaRoiEstimate({ side: "CALL", spotPrice: 100, strike: 98, entryPrice: 3, targetPrice: 105, delta: 0.9, gamma: 0.1 });
  close(clamped.projectedDelta, 1, "a call delta cannot pass 1");
});

test("gamma ROI needs every input: a missing one gives no estimate", () => {
  const base = { side: "CALL", spotPrice: 100, strike: 105, entryPrice: 1, targetPrice: 104, delta: 0.3, gamma: 0.05 };
  for (const missing of ["spotPrice", "strike", "entryPrice", "targetPrice"]) {
    assert.equal(roi.calculateGammaRoiEstimate({ ...base, [missing]: 0 }), null, missing);
  }
});

test("the chain's Γ ROI column projects the SAME entry call to each target strike", () => {
  const estimates = roi.buildOptionGammaRoiEstimates(calls, 100, "CALL");
  assert.equal(estimates.get(105).kind, "entry");
  close(estimates.get(105).gamma, 0.04, "entry gamma");
  // 105C at 0.55, delta 0.18, gamma 0.04, stock to 103: exit 0.55 + (0.18 + 0.30) / 2 x 3 = 1.27
  close(estimates.get(103).estimatedExitPrice, 1.27, "exit at 103");
  assert.equal(estimates.get(103).percent, 131);
  assert.equal(estimates.has(107), false);
});
