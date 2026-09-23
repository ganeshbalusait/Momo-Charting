import test from "node:test";
import assert from "node:assert/strict";
import { squeezeReleaseEvents, calculateRollingStdDev } from "./squeezeRelease.js";

// Population standard deviation with a partial leading window, matching the
// original App.jsx helper. A sample stdev would give 1.0 for the third entry.
test("rolling stdev is population, not sample, and fills partial windows", () => {
  const values = calculateRollingStdDev([1, 2, 3], 20);
  assert.equal(values[0], 0);
  assert.equal(values[1], 0.5);
  assert.ok(Math.abs(values[2] - 0.816496580927726) < 1e-12);
});

// 21 flat bars put the series in a squeeze (stdev 0, ATR 0 -> 0 <= 0), then a
// wide bar breaks it. The release must be reported on the breakout bucket.
test("reports a release when the squeeze breaks on a closed bucket", () => {
  const bars = [];
  for (let index = 0; index < 21; index += 1) {
    const time = 1_700_000_000 + index * 3600;
    bars.push({ time, open: 100, high: 100, low: 100, close: 100 });
  }
  bars.push({ time: 1_700_000_000 + 21 * 3600, open: 100, high: 140, low: 60, close: 130 });
  // One extra bar so the breakout bucket is CLOSED, not forming.
  bars.push({ time: 1_700_000_000 + 22 * 3600, open: 130, high: 131, low: 129, close: 130 });

  const events = squeezeReleaseEvents(bars, 60);
  assert.equal(events.length, 1);
  assert.equal(events[0].tone, "bull");
  assert.equal(events[0].minutes, 60);
  assert.equal(events[0].closeTime, events[0].bucketTime + 3600);
});

// Live commit 461d4af: a release on a still-forming bucket flickers, so it is
// not a release until the bucket closes.
test("a still-forming bucket produces no release", () => {
  const bars = [];
  for (let index = 0; index < 21; index += 1) {
    const time = 1_700_000_000 + index * 3600;
    bars.push({ time, open: 100, high: 100, low: 100, close: 100 });
  }
  bars.push({ time: 1_700_000_000 + 21 * 3600, open: 100, high: 140, low: 60, close: 130 });

  assert.deepEqual(squeezeReleaseEvents(bars, 60), []);
});
