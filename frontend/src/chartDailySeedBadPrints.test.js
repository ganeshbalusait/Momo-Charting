// Daily seed bad prints (2026-11-13): the minute candles dropped SPY's
// 2026-11-11 $711.46 after-hours prints, but the saved daily row for that day
// was built from the same unfiltered minutes and kept low 711.4601, so D/W/M
// (and the chat's week low) still read 711.46 while 3m and 4H read 734.10.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
  reconcileDailySeedWithCleanedTape,
  removeExtendedHoursBadPrints,
  withoutDailySeedBadPrints,
} from "./chartBadPrints.js";
import { buildChartDisplayBars } from "./chartAggregation.js";

const spy = JSON.parse(readFileSync(new URL("../../tests/fixtures/spy_2026_11_11_bad_prints.json", import.meta.url), "utf8")).bars;
// November 2026 is EST (UTC-5): ET wall clock -> epoch.
const et = (day, hour, minute) => Date.UTC(2026, 10, day, hour + 5, minute) / 1000;

// The minute tape as the chart holds it: it starts the evening before, has
// the 11-11 regular-session low (753.98), then the real SPY evening.
const tape = [
  { time: et(10, 19, 0), open: 754.4, high: 754.6, low: 754.3, close: 754.5, volume: 1000 },
  { time: et(11, 10, 0), open: 755, high: 755.2, low: 753.98, close: 755.1, volume: 5000 },
  ...spy,
];
// The saved daily rows (Eastern midnight stamps), 11-11 as it was saved.
const seed = [
  { time: et(9, 0, 0), open: 739.93, high: 754.11, low: 734.1, close: 753.79, volume: 101189 },
  { time: et(10, 0, 0), open: 753.71, high: 755.31, low: 751.42, close: 754.42, volume: 66143 },
  { time: et(11, 0, 0), open: 754.5, high: 762.51, low: 711.4601, close: 756.81, volume: 28934859 },
  { time: et(12, 0, 0), open: 756.79, high: 763.04, low: 752.82, close: 758.04, volume: 30064947 },
];

test("the 11-11 seed row's $711.46 low goes back to the cleaned minutes", () => {
  const fixed = reconcileDailySeedWithCleanedTape(seed, tape, removeExtendedHoursBadPrints(tape));
  assert.notEqual(fixed, seed);
  assert.deepEqual(fixed[2], { ...seed[2], low: 753.98, badPrintRemoved: true });
  // only the spoiled row is touched, and the input is not mutated
  assert.equal(fixed[0], seed[0]);
  assert.equal(fixed[1], seed[1]);
  assert.equal(fixed[3], seed[3]);
  assert.equal(seed[2].low, 711.4601);
});

test("D, W and M no longer reach down to $711", () => {
  const daily = withoutDailySeedBadPrints(seed, tape);
  for (const minutes of [1440, 10080, 43200]) {
    const before = buildChartDisplayBars({ liveBars: tape, dailyBars: seed, aggregationMinutes: minutes, sourcesNormalized: true });
    const after = buildChartDisplayBars({ liveBars: tape, dailyBars: daily, aggregationMinutes: minutes, sourcesNormalized: true });
    assert.equal(Math.min(...before.map((bar) => bar.low)), 711.4601, `${minutes}`);
    assert.equal(Math.min(...after.map((bar) => bar.low)), 734.1, `${minutes}`);
  }
});

test("a seed low that is not the removed print is left alone", () => {
  const deeper = seed.map((row, index) => (index === 2 ? { ...row, low: 700 } : row));
  assert.equal(reconcileDailySeedWithCleanedTape(deeper, tape, removeExtendedHoursBadPrints(tape)), deeper);
});

test("the tape's first day may be partial, so it never repairs a row", () => {
  // the SPY fixture alone starts at 15:30 on 11-11
  assert.equal(reconcileDailySeedWithCleanedTape(seed, spy, removeExtendedHoursBadPrints(spy)), seed);
});

test("a real after-hours move keeps its daily high and low", () => {
  const flat = (day, from, to, price) => Array.from({ length: to - from }, (_, index) => {
    const minute = from + index;
    return { time: et(day, Math.floor(minute / 60), minute % 60), open: price, high: price + 0.02, low: price - 0.02, close: price, volume: 1 };
  });
  const moved = [...flat(10, 19 * 60, 19 * 60 + 5, 100), ...flat(11, 15 * 60, 16 * 60 + 30, 100), ...flat(11, 16 * 60 + 30, 18 * 60, 110)];
  const daily = [{ time: et(11, 0, 0), open: 100, high: 110.02, low: 99.98, close: 110, volume: 9 }];
  assert.equal(removeExtendedHoursBadPrints(moved), moved);
  assert.equal(withoutDailySeedBadPrints(daily, moved), daily);
});

test("an unchanged repair returns the same array, so daily studies are not recomputed", () => {
  const first = withoutDailySeedBadPrints(seed, tape);
  assert.notEqual(first, seed);
  // the live buffer grows by a quiet minute: same repair, same array
  const grown = [...tape, { ...tape.at(-1), time: tape.at(-1).time + 60 }];
  assert.equal(withoutDailySeedBadPrints(seed, grown), first);
  // repairing an already repaired seed changes nothing
  assert.equal(withoutDailySeedBadPrints(first, tape), first);
});
