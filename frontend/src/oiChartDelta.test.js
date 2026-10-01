import assert from "node:assert/strict";
import test from "node:test";

import {
  chartDeltaRequestTime,
  chartDeltaTapeChanged,
  mergeChartDeltaPayload,
} from "./oiChartDelta.js";

const bar = (time, close) => ({ time, open: close, high: close, low: close, close, volume: 10 });

const basePayload = {
  symbol: "AAPL",
  historyLoading: false,
  underlyingPrice: 312.5,
  signalTapeUpdatedAt: "2026-08-07T14:00:00Z",
  bars: [bar(100, 1), bar(160, 2), bar(220, 3)],
  studyBars: [bar(100, 1), bar(220, 3)],
  dailyBars: [bar(0, 1), bar(86400, 2)],
  mtfSignals: [{ id: "keep-me" }],
};

test("merges the delta tail over the held tape and keeps heavy tapes", () => {
  const merged = mergeChartDeltaPayload(basePayload, {
    delta: true,
    deltaSince: 200,
    underlyingPrice: 313.4,
    signalTapeUpdatedAt: "2026-08-07T14:00:00Z",
    bars: [bar(220, 3.5), bar(280, 4)],
    studyBars: [bar(220, 3.5)],
    dailyBars: [bar(86400, 2.1)],
  });

  assert.deepEqual(merged.bars.map((row) => [row.time, row.close]), [[100, 1], [160, 2], [220, 3.5], [280, 4]]);
  assert.deepEqual(merged.studyBars.map((row) => row.time), [100, 220]);
  assert.deepEqual(merged.dailyBars.map((row) => [row.time, row.close]), [[0, 1], [86400, 2.1]]);
  assert.equal(merged.underlyingPrice, 313.4);
  // Heavy tapes the delta omitted stay from the held payload.
  assert.deepEqual(merged.mtfSignals, [{ id: "keep-me" }]);
  assert.equal(merged.delta, undefined);
});

test("refuses to merge without a complete base or a delta marker", () => {
  assert.equal(mergeChartDeltaPayload(null, { delta: true }), null);
  assert.equal(mergeChartDeltaPayload({ bars: [] }, { delta: true }), null);
  assert.equal(mergeChartDeltaPayload(basePayload, { delta: false }), null);
});

test("empty delta tail keeps the held series untouched", () => {
  const merged = mergeChartDeltaPayload(basePayload, { delta: true, deltaSince: 200, bars: [], studyBars: [], dailyBars: [] });
  assert.deepEqual(merged.bars.map((row) => row.time), [100, 160, 220]);
  assert.deepEqual(merged.dailyBars.map((row) => row.time), [0, 86400]);
});

test("delta requests only fire from a complete, sizeable tape", () => {
  assert.equal(chartDeltaRequestTime(null), 0);
  assert.equal(chartDeltaRequestTime({ historyLoading: true, bars: basePayload.bars }), 0);
  assert.equal(chartDeltaRequestTime({ historyLoading: false, bars: [bar(1, 1)] }), 0);
  const bigBars = Array.from({ length: 600 }, (_, index) => bar(index * 60, index));
  assert.equal(chartDeltaRequestTime({ historyLoading: false, bars: bigBars }), 599 * 60);
  assert.equal(
    chartDeltaRequestTime({ historyLoading: false, bars: bigBars }, { forceFullTape: true }),
    0,
  );
});

test("detects a rebuilt signal tape so the browser refetches in full", () => {
  assert.equal(chartDeltaTapeChanged(basePayload, { signalTapeUpdatedAt: basePayload.signalTapeUpdatedAt }), false);
  assert.equal(chartDeltaTapeChanged(basePayload, { signalTapeUpdatedAt: "2026-08-07T15:00:00Z" }), true);
  assert.equal(chartDeltaTapeChanged({}, { signalTapeUpdatedAt: "x" }), false);
});

test("tail-merges the 60-day five-minute tape instead of replacing it with the delta tail", () => {
  // Measured 2026-09-04 08:46 ET: a refreshing:false delta carried a 1-row
  // fineStudyBars tail and the browser kept that 1 row in place of 13,806,
  // so the 5m view fell back to the 1-minute tape after every reconcile.
  const base = {
    ...basePayload,
    fineStudyBars: Array.from({ length: 4680 }, (_, index) => bar(index * 300, index)),
  };
  const merged = mergeChartDeltaPayload(base, {
    delta: true,
    deltaSince: 4679 * 300,
    bars: [bar(220, 3.5)],
    studyBars: [],
    fineStudyBars: [bar(4679 * 300, 99), bar(4680 * 300, 100)],
    dailyBars: [],
  });
  assert.equal(merged.fineStudyBars.length, 4681);
  assert.equal(merged.fineStudyBars[4678].close, 4678);
  assert.equal(merged.fineStudyBars[4679].close, 99);
  assert.equal(merged.fineStudyBars[4680].close, 100);

  // An empty tail (nothing new since the cutoff) leaves the held tape intact.
  const untouched = mergeChartDeltaPayload(base, { delta: true, deltaSince: 4679 * 300, bars: [bar(220, 3.5)], fineStudyBars: [] });
  assert.equal(untouched.fineStudyBars.length, 4680);
});
