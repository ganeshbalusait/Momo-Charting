import assert from "node:assert/strict";
import test from "node:test";

import { captureEntryLegLines, captureReasonLines } from "./botCapture.js";

const NO_BUYERS = "No buyers when it was checked (bid $0): no trade taken";

test("skipped signals are listed with their plain reason, biggest group first", () => {
  const lines = captureReasonLines({ reasons: { "Daily signal cap for this rule": 2, [NO_BUYERS]: 7 } });
  assert.deepEqual(lines, [{ reason: NO_BUYERS, n: 7 }, { reason: "Daily signal cap for this rule", n: 2 }]);
});

test("a missing, empty or broken reasons block lists nothing", () => {
  for (const capture of [null, undefined, {}, { reasons: null }, { reasons: "x" }, { reasons: { a: 0, b: null, c: "n" } }]) {
    assert.deepEqual(captureReasonLines(capture), []);
  }
});

test("each cause of a missing 0.20-delta option has its own sentence and its own count", () => {
  const lines = captureEntryLegLines({ entryLeg: { none: 3, missing: 2, buyers: 12, sellers: 1, unusable: 5 } });
  assert.deepEqual(lines.map((l) => [l.kind, l.n]), [["buyers", 12], ["sellers", 1], ["unusable", 5], ["missing", 2], ["none", 3]]);
  assert.deepEqual(lines.map((l) => l.text), [
    "12 entered trades have no 0.20-delta option with buyers (bid $0), so the paper accounts skip them.",
    "1 entered trade has no 0.20-delta option with sellers (no ask), so the paper accounts skip it.",
    "5 entered trades have a 0.20-delta option whose quote is not usable (buyers far below sellers, or crossed), so the paper accounts skip them.",
    "2 entered trades had no quote for the 0.20-delta option, so the paper accounts skip them.",
    "3 entered trades have no strike under 0.20 delta on that expiry, so the paper accounts skip them.",
  ]);
});

test("the no-buyers line counts only the trades whose bid really was $0", () => {
  // Round 1 put every cause under "no 0.20-delta option with buyers (bid $0)".
  const lines = captureEntryLegLines({ entryLeg: { none: 4, missing: 6, buyers: 0, sellers: 0, unusable: 9 } });
  assert.equal(lines.some((l) => l.kind === "buyers"), false);
  assert.equal(lines.some((l) => /bid \$0/.test(l.text)), false);
  assert.deepEqual(captureEntryLegLines({ entryLeg: { buyers: 1 } }).map((l) => l.text),
    ["1 entered trade has no 0.20-delta option with buyers (bid $0), so the paper accounts skip it."]);
  assert.deepEqual(captureEntryLegLines({ entryLeg: { missing: 1 } }).map((l) => l.text),
    ["1 entered trade had no quote for the 0.20-delta option, so the paper accounts skip it."]);
});

test("no line when the counts are zero, missing (an older server) or not numbers", () => {
  for (const capture of [null, {}, { entryLeg: null }, { entryLeg: "x" }, { entryLeg: {} }, { noEntryLeg: 5 },
    { entryLeg: { buyers: 0, none: null, missing: "x", sellers: -3, other: 7 } }]) {
    assert.deepEqual(captureEntryLegLines(capture), []);
  }
});
