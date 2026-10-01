import assert from "node:assert/strict";
import test from "node:test";

import { aggregateChartBars } from "./chartAggregation.js";
import { mergeStudyHistoryBars } from "./mtfStudySource.js";

const bar = (time, close = 100) => ({ time, open: close, high: close, low: close, close, volume: 1 });
const ET_0930 = Date.UTC(2026, 9, 1, 13, 30) / 1000; // 2026-10-01 09:30 EDT

test("deep history fills only the time before the first live bar", () => {
  const history = Array.from({ length: 200 }, (_, index) => bar(ET_0930 - (200 - index) * 1800));
  const live = Array.from({ length: 30 }, (_, index) => bar(ET_0930 + index * 60, 101));

  const merged = mergeStudyHistoryBars(history, live);

  assert.equal(merged.length, 230);
  assert.deepEqual(merged.slice(-30), live);
  assert.ok(merged.every((item, index) => !index || item.time > merged[index - 1].time));
});

test("a history bar straddling the first live bar is not counted twice", () => {
  const history = [bar(ET_0930 - 1800), bar(ET_0930)];
  // Second history bar opens at 09:30, the same 30-minute slot as the live tape.
  const live = [bar(ET_0930 + 600, 105), bar(ET_0930 + 660, 106)];

  const merged = mergeStudyHistoryBars(history, live);

  assert.deepEqual(merged.map((item) => item.time), [ET_0930 - 1800, ET_0930 + 600, ET_0930 + 660]);
});

test("an overnight gap is not mistaken for the history cadence", () => {
  const history = [
    bar(ET_0930 - 86_400 - 3600),
    bar(ET_0930 - 86_400 - 1800),
    bar(ET_0930 - 1800), // a full day later
  ];
  const live = [bar(ET_0930, 101)];

  assert.equal(mergeStudyHistoryBars(history, live).length, 4);
});

test("4H squeeze length is reachable only with the merged tape", () => {
  const history = Array.from({ length: 20 * 48 }, (_, index) => bar(ET_0930 - (20 * 48 - index) * 1800));
  const live = Array.from({ length: 2 * 24 * 60 }, (_, index) => bar(ET_0930 - 2 * 86_400 + index * 60));

  assert.ok(aggregateChartBars(live, 240).length < 20);
  assert.ok(aggregateChartBars(mergeStudyHistoryBars(history, live), 240).length >= 20);
});

test("missing tapes fall back to whichever exists", () => {
  const live = [bar(ET_0930)];
  assert.deepEqual(mergeStudyHistoryBars([], live), live);
  assert.deepEqual(mergeStudyHistoryBars(live, []), live);
  assert.deepEqual(mergeStudyHistoryBars(null, null), []);
});

test("history older than the warm-up window is dropped", () => {
  const history = Array.from({ length: 200 * 48 }, (_, index) => bar(ET_0930 - (200 * 48 - index) * 1800));
  const live = [bar(ET_0930, 101)];

  const merged = mergeStudyHistoryBars(history, live);

  assert.ok(merged[0].time >= ET_0930 - 60 * 86_400);
  assert.ok(merged[0].time < ET_0930 - 59 * 86_400);
  assert.equal(mergeStudyHistoryBars(history, live, 0).length, history.length + 1);
});
