import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

const lift = (pattern, what) => {
  const match = pattern.exec(appSource);
  assert.ok(match, `could not lift ${what} out of App.jsx`);
  return match[0];
};

// Lifted and run for real: this decides whether the decision board claims a
// directional read or admits the evidence is still coming in.
const oiFinderPickComparisons = new Function([
  lift(/function oiFinderPickComparisons\([\s\S]*?\n}/, "oiFinderPickComparisons"),
  "return oiFinderPickComparisons;",
].join("\n\n"))();

const sig = (comparisonsAvailable) => ({ strike: 350, score: 4, comparisonsAvailable });

test("a fully backed pair reports all four comparisons", () => {
  assert.equal(oiFinderPickComparisons(sig(4), sig(4)), 4);
});

test("the better-evidenced side wins, so one thin pick cannot mute the board", () => {
  assert.equal(oiFinderPickComparisons(sig(4), sig(1)), 4);
  assert.equal(oiFinderPickComparisons(sig(1), sig(4)), 4);
});

test("a history-free quick pass reports one comparison", () => {
  // Only otm_volume_gt_atm is live-only; the other three need saved history.
  assert.equal(oiFinderPickComparisons(sig(1), sig(1)), 1);
});

test("a missing side is ignored rather than counted as zero", () => {
  assert.equal(oiFinderPickComparisons(sig(4), null), 4);
  assert.equal(oiFinderPickComparisons(null, sig(2)), 2);
});

test("no picks at all falls through to complete", () => {
  // The board renders WAIT FOR DATA on its own !call && !put branch before this
  // value is consulted, so it must not manufacture a history warning.
  assert.equal(oiFinderPickComparisons(null, null), 4);
});

test("a payload without the field keeps the previous behaviour", () => {
  // THE REGRESSION GUARD. Reading a missing count as 0 would label every older
  // payload "LIVE VOLUME ONLY" and suppress a real directional read.
  assert.equal(oiFinderPickComparisons({ strike: 350, score: 4 }, { strike: 342.5, score: 3 }), 4);
  assert.equal(oiFinderPickComparisons({ comparisonsAvailable: null }, undefined), 4);
});

test("does not key off the board-wide historyReady flag", () => {
  // historyReady is all(history_days >= 5) over EVERY scanned OTM contract.
  // Measured TSLA 2026-08-26: 80 signals, 6 thin ones held it false while both
  // displayed picks had 4/4 comparisons and 10-11 days of history. Keying the
  // label off it called a fully backed 4/4 read "live volume only".
  assert.equal(oiFinderPickComparisons(sig(4), sig(4)), 4);
  assert.ok(
    !/const historyPending = activity\?\.historyReady/.test(appSource),
    "the board must not gate its read on the board-wide historyReady flag",
  );
});
