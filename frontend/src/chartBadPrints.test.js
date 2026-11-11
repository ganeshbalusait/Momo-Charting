// Extended-hours bad prints (2026-11-11): SPY's 16:55 ET five-minute candle
// was drawn from $711 to $760 off one bad trade print. Real SPY minutes from
// that evening are the fixture; synthetic tapes cover what must NOT change.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { removeExtendedHoursBadPrints, withoutExtendedHoursBadPrints, isExtendedHoursTime } from "./chartBadPrints.js";
import { buildChartDisplayBars } from "./chartAggregation.js";

const spy = JSON.parse(readFileSync(new URL("../../tests/fixtures/spy_2026_11_11_bad_prints.json", import.meta.url), "utf8")).bars;
// 2026-11-11 is EST (UTC-5): ET wall clock -> epoch.
const et = (hour, minute) => Date.UTC(2026, 10, 11, hour + 5, minute) / 1000;
const at = (bars, hour, minute) => bars.find((bar) => bar.time === et(hour, minute));

function flatTape(startTime, count, price = 100) {
  return Array.from({ length: count }, (_, index) => ({
    time: startTime + index * 60, open: price, high: price + 0.02, low: price - 0.02, close: price, volume: 1000,
  }));
}

test("the real SPY $711.46 and $749.86 prints are removed", () => {
  const cleaned = removeExtendedHoursBadPrints(spy);
  for (const [hour, minute] of [[16, 15], [16, 28], [16, 38], [16, 55]]) {
    const bar = at(cleaned, hour, minute);
    assert.ok(bar.low > 759, `${hour}:${minute} low is still ${bar.low}`);
    assert.ok(bar.open > 759, `${hour}:${minute} open is still ${bar.open}`);
    assert.equal(bar.badPrintRemoved, true);
  }
  // The bad open is replaced by the minute before's close, the close is kept.
  assert.equal(at(cleaned, 16, 55).open, at(spy, 16, 54).close);
  assert.equal(at(cleaned, 16, 55).close, at(spy, 16, 55).close);
});

test("only the bad minutes change on the real SPY evening", () => {
  const cleaned = removeExtendedHoursBadPrints(spy);
  const changed = cleaned.filter((bar, index) => bar !== spy[index]).map((bar) => bar.time);
  assert.deepEqual(changed, [et(16, 15), et(16, 28), et(16, 38), et(16, 55)]);
});

test("the 16:55 five-minute candle no longer reaches down to $711", () => {
  const five = buildChartDisplayBars({ liveBars: spy, aggregationMinutes: 5 });
  const candle = five.find((bar) => bar.time === et(16, 55));
  assert.ok(candle.low > 759, `5m low ${candle.low}`);
  assert.ok(five.filter((bar) => bar.time >= et(16, 0)).every((bar) => bar.low > 755));
});

test("a real after-hours drop that keeps trading lower is kept", () => {
  // Earnings at 16:05: price falls 6% and stays there.
  const before = flatTape(et(16, 0), 5, 100);
  const after = flatTape(et(16, 6), 15, 94);
  const drop = { time: et(16, 5), open: 100, high: 100, low: 93.5, close: 94, volume: 900000 };
  const tape = [...before, drop, ...after];
  assert.equal(removeExtendedHoursBadPrints(tape), tape);
});

test("a fresh move on the newest minute is never hidden while it happens", () => {
  const tape = [...flatTape(et(16, 0), 12, 100), { time: et(16, 12), open: 100, high: 100, low: 93.9, close: 94, volume: 50000 }];
  assert.equal(removeExtendedHoursBadPrints(tape), tape);
});

test("a spike that came back inside the newest minute is removed at once", () => {
  const tape = [...flatTape(et(16, 0), 12, 100), { time: et(16, 12), open: 93.5, high: 100.02, low: 93.5, close: 100, volume: 300 }];
  const cleaned = removeExtendedHoursBadPrints(tape);
  assert.equal(cleaned.at(-1).open, 100);
  assert.ok(cleaned.at(-1).low > 99.9);
});

test("a lone off-market minute in the middle of the evening is flattened", () => {
  // SLV 2026-11-11 16:45: one 150-share minute at $43.75 while SLV traded ~$46.6.
  const tape = [...flatTape(et(16, 30), 15, 46.6), { time: et(16, 45), open: 43.75, high: 43.75, low: 43.75, close: 43.75, volume: 150 }, ...flatTape(et(16, 46), 10, 46.6)];
  const bar = removeExtendedHoursBadPrints(tape).find((row) => row.time === et(16, 45));
  assert.equal(bar.low, 46.6);
  assert.equal(bar.close, 46.6);
});

test("regular-session wicks are never touched", () => {
  const tape = [...flatTape(et(10, 0), 10, 100), { time: et(10, 10), open: 100, high: 100, low: 90, close: 100, volume: 5000 }, ...flatTape(et(10, 11), 10, 100)];
  assert.equal(removeExtendedHoursBadPrints(tape), tape);
  assert.equal(isExtendedHoursTime(et(9, 29)), true);
  assert.equal(isExtendedHoursTime(et(9, 30)), false);
  assert.equal(isExtendedHoursTime(et(15, 59)), false);
  assert.equal(isExtendedHoursTime(et(16, 0)), true);
});

test("a one-cent tick on a sub-dollar stock is not a bad print", () => {
  const tape = [...flatTape(et(7, 30), 10, 0.77), { time: et(7, 40), open: 0.78, high: 0.78, low: 0.77, close: 0.77, volume: 50000 }, ...flatTape(et(7, 41), 10, 0.77)];
  assert.equal(removeExtendedHoursBadPrints(tape), tape);
});

test("a clean tape comes back as the same array, and repeat calls hit the cache", () => {
  const tape = flatTape(et(17, 0), 30, 100);
  assert.equal(removeExtendedHoursBadPrints(tape), tape);
  const first = withoutExtendedHoursBadPrints(spy);
  assert.notEqual(first, spy);
  assert.equal(withoutExtendedHoursBadPrints(spy), first);
});
