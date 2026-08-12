import assert from "node:assert/strict";
import test from "node:test";
import {
  chartTapeContentUnchanged,
  resolveHistorySeriesUpdate,
  chartWallLevelSignature,
  createOiChartWarmingPayload,
  guardLightweightChartSeriesTree,
  isTransientOiChartTransportError,
  normalizeOiChartPayload,
  normalizeLightweightChartSeriesData,
  OI_CHART_LIVE_TAPE_MAX_BARS,
  OI_CHART_STUDY_TAPE_MAX_AGE_SECONDS,
} from "./chartSeriesData.js";

test("normalizes chart points into strictly ascending unique timestamps", () => {
  assert.deepEqual(
    normalizeLightweightChartSeriesData([
      { time: 300, value: 3 },
      { time: 100, value: 1 },
      { time: 300, value: 4 },
      { time: "bad", value: 9 },
      { time: 200.9, value: 2 },
    ]),
    [
      { time: 100, value: 1 },
      { time: 200, value: 2 },
      { time: 300, value: 4 },
    ],
  );
});

test("normalizes a shared chart payload once across every history series", () => {
  const payload = normalizeOiChartPayload({
    symbol: "AAPL",
    bars: [{ time: 2, close: 2 }, { time: 1, close: 1 }, { time: 2, close: 3 }],
    studyBars: [{ time: 4, close: 4 }, { time: 3, close: 3 }],
    dailyBars: [{ time: 6, close: 6 }, { time: 6, close: 7 }, { time: 5, close: 5 }],
  });

  assert.deepEqual(payload.bars, [{ time: 1, close: 1 }, { time: 2, close: 3 }]);
  assert.deepEqual(payload.studyBars, [{ time: 3, close: 3 }, { time: 4, close: 4 }]);
  assert.deepEqual(payload.dailyBars, [{ time: 5, close: 5 }, { time: 6, close: 7 }]);
});

test("caps a bloated live minute tape at the newest bars", () => {
  const minuteBar = (i) => ({ time: 60 * (i + 1), close: i });
  const oversized = Array.from({ length: OI_CHART_LIVE_TAPE_MAX_BARS + 500 }, (_, i) => minuteBar(i));
  const payload = normalizeOiChartPayload({ bars: oversized, studyBars: [], dailyBars: [] });

  assert.equal(payload.bars.length, OI_CHART_LIVE_TAPE_MAX_BARS);
  // The cap keeps the NEWEST bars — the live end of the tape.
  assert.equal(payload.bars.at(-1).time, oversized.at(-1).time);
  assert.equal(payload.bars[0].time, oversized.at(-OI_CHART_LIVE_TAPE_MAX_BARS).time);
});

test("trims deep history series to their documented lookback windows", () => {
  const day = 86_400;
  const newest = 3_000_000_000;
  // Simulates the 2026-08-10 pollution: years of daily rows concatenated
  // before the intraday study tape. Anchored to the study window itself so
  // the contract's VALUE can change (4H depth is this window) without
  // weakening what this test proves: the tape stays bounded.
  const studyWindowDays = OI_CHART_STUDY_TAPE_MAX_AGE_SECONDS / day;
  const staleDailyPrefix = Array.from(
    { length: 400 },
    (_, i) => ({ time: newest - (studyWindowDays + 500 - i) * day, close: i }),
  );
  const intradayTail = Array.from({ length: 200 }, (_, i) => ({ time: newest - (200 - i) * 1_800, close: i }));
  const payload = normalizeOiChartPayload({
    bars: [],
    studyBars: [...staleDailyPrefix, ...intradayTail],
    dailyBars: Array.from({ length: 5_000 }, (_, i) => ({ time: newest - (5_000 - i) * day, close: i })),
  });

  // Rows older than the study window (the ancient daily prefix) drop.
  assert.equal(payload.studyBars.at(-1).time, newest - 1_800);
  assert.ok(payload.studyBars.every(
    (bar) => bar.time >= newest - OI_CHART_STUDY_TAPE_MAX_AGE_SECONDS,
  ));
  assert.equal(payload.studyBars.length, 200);
  // The daily seed keeps only its ~10-year contract (window measured from
  // the newest daily bar, which sits one day behind `newest` here).
  assert.ok(payload.dailyBars.length <= 3_701);
  const newestDaily = payload.dailyBars.at(-1).time;
  assert.ok(payload.dailyBars.every((bar) => bar.time >= newestDaily - 3_700 * day));
  // In-contract series pass through without a copy (reference preserved).
  const inWindow = [{ time: newest - day, close: 1 }, { time: newest, close: 2 }];
  const clean = normalizeOiChartPayload({ bars: [], studyBars: inWindow, dailyBars: inWindow });
  assert.equal(clean.studyBars.length, 2);
  assert.equal(clean.dailyBars.length, 2);
});

test("creates a non-error warming payload when a cache-first request is delayed", () => {
  const payload = createOiChartWarmingPayload(" amzn ");

  assert.equal(payload.symbol, "AMZN");
  assert.equal(payload.warming, true);
  assert.equal(payload.refreshing, true);
  assert.equal(payload.historyLoading, true);
  assert.deepEqual(payload.bars, []);
  assert.equal(payload.error, "");
});

test("keeps chart recovery active for a local transport interruption only", () => {
  assert.equal(isTransientOiChartTransportError({ name: "AbortError" }), true);
  assert.equal(isTransientOiChartTransportError(new TypeError("Failed to fetch")), true);
  assert.equal(isTransientOiChartTransportError(new TypeError("NetworkError when attempting to fetch resource.")), true);
  assert.equal(isTransientOiChartTransportError({ httpStatus: 503 }), true);
  assert.equal(isTransientOiChartTransportError(new Error("Chart request failed for NVDA.")), false);
});

test("OI wall signatures ignore quote-only volume changes", () => {
  const base = [{
    price: 205,
    color: "#00aaff",
    lineWidth: 2,
    lineStyle: 3,
    title: "C Strong 12K",
    tier: 5,
    side: "C",
    strength: "strong",
    openInterest: 12_000,
    volume: 200,
  }];

  assert.equal(
    chartWallLevelSignature(base),
    chartWallLevelSignature([{ ...base[0], volume: 900 }]),
  );
  assert.notEqual(
    chartWallLevelSignature(base),
    chartWallLevelSignature([{ ...base[0], price: 210 }]),
  );
});

test("guards every nested series before it reaches Lightweight Charts", () => {
  const received = [];
  const tree = {
    candle: { setData: (data) => received.push(data) },
    studies: {
      momentum: { setData: (data) => received.push(data) },
    },
  };

  guardLightweightChartSeriesTree(tree);
  tree.candle.setData([{ time: 2, value: 2 }, { time: 1, value: 1 }, { time: 2, value: 3 }]);
  tree.studies.momentum.setData([{ time: 5, value: 5 }, { time: 5, value: 6 }]);

  assert.deepEqual(received, [
    [{ time: 1, value: 1 }, { time: 2, value: 3 }],
    [{ time: 5, value: 6 }],
  ]);
});

test("clears only the rejected series instead of crashing the workspace", () => {
  const received = [];
  const tree = {
    optionalStudy: {
      setData: (data) => {
        received.push(data);
        if (data.length) throw new Error("provider rejected series");
      },
    },
  };
  const previousConsoleError = console.error;
  console.error = () => {};
  try {
    guardLightweightChartSeriesTree(tree);
    assert.doesNotThrow(() => tree.optionalStudy.setData([{ time: 1, value: 2 }]));
  } finally {
    console.error = previousConsoleError;
  }
  assert.deepEqual(received, [[{ time: 1, value: 2 }], []]);
});

test("a mid-promotion empty history series never replaces the one on screen", () => {
  const bar = (time, close) => ({ time, open: close - 1, high: close + 1, low: close - 2, close, volume: 100 });
  // Same guard protects the D/W/M daily seed and the 4H five-minute tape.
  const seed = [bar(86400, 10), bar(172800, 11)];

  // The refresh that lands while the server is still promoting full history
  // carries no bars. Dropping the series collapses 4H/W/M to one candle.
  assert.equal(resolveHistorySeriesUpdate(seed, [], true), seed);

  // A real replacement (longer history finished) is taken.
  const promoted = [bar(0, 9), ...seed];
  assert.equal(resolveHistorySeriesUpdate(seed, promoted, true), promoted);

  // Once the server reports history complete, an empty series is authoritative
  // (a symbol with genuinely no history must not show stale candles).
  assert.deepEqual(resolveHistorySeriesUpdate(seed, [], false), []);

  // Unchanged content still keeps the current reference (no study recompute).
  assert.equal(resolveHistorySeriesUpdate(seed, [bar(86400, 10), bar(172800, 11)], false), seed);

  // Nothing held yet: an empty response stays empty rather than throwing.
  assert.deepEqual(resolveHistorySeriesUpdate([], [], true), []);
  assert.deepEqual(resolveHistorySeriesUpdate(null, null, true), []);

  // A SHORTER series mid-promotion degrades 4H the same way an empty one
  // does: the fast-start build answers with a few days of one-minute bars
  // where the 60-day five-minute tape used to be.
  const longTape = Array.from({ length: 6000 }, (_, i) => bar(i * 300, 10));
  const shortTape = Array.from({ length: 1752 }, (_, i) => bar(i * 60, 10));
  assert.equal(resolveHistorySeriesUpdate(longTape, shortTape, true), longTape);
  // The same shorter tape is accepted once the server reports history ready.
  assert.equal(resolveHistorySeriesUpdate(longTape, shortTape, false), shortTape);
  // Growth is always accepted while loading.
  assert.equal(resolveHistorySeriesUpdate(shortTape, longTape, true), longTape);
});

test("identical candle tapes are recognized so state keeps its reference", () => {
  const bar = (time, close) => ({ time, open: close - 1, high: close + 1, low: close - 2, close, volume: 100 });
  const current = [bar(60, 10), bar(120, 11)];
  const identicalCopy = [bar(60, 10), bar(120, 11)];
  assert.equal(chartTapeContentUnchanged(current, identicalCopy), true);
  // Last-bar restatement (live forming candle) must pass through.
  assert.equal(chartTapeContentUnchanged(current, [bar(60, 10), bar(120, 11.5)]), false);
  // Appended bar must pass through.
  assert.equal(chartTapeContentUnchanged(current, [...current, bar(180, 12)]), false);
  // Older history arriving (backfill shifts the first bar) must pass through.
  assert.equal(chartTapeContentUnchanged(current, [bar(0, 9), bar(120, 11)]), false);
  // Empties and non-arrays.
  assert.equal(chartTapeContentUnchanged([], []), true);
  assert.equal(chartTapeContentUnchanged(null, []), false);
});

test("study tape drops the daily archive but keeps weekend gaps in the fine tape", () => {
  const day = 86_400;
  const newest = 3_000_000_000;
  // A daily-cadence archive (consecutive day-plus gaps) in front of a
  // 30-minute tape that contains an isolated weekend gap. Aggregating the
  // archive into 4H buckets is what crushed every real candle against the
  // left edge of the 4H chart on 2026-08-10.
  const dailyArchive = Array.from({ length: 300 }, (_, i) => ({
    time: newest - (400 - i) * day,
    close: 100 + i,
  }));
  const beforeWeekend = Array.from({ length: 20 }, (_, i) => ({
    time: newest - 5 * day + i * 1_800,
    close: 200 + i,
  }));
  const afterWeekend = Array.from({ length: 40 }, (_, i) => ({
    time: newest - 2 * day + i * 1_800,
    close: 300 + i,
  }));

  const payload = normalizeOiChartPayload({
    bars: [],
    studyBars: [...dailyArchive, ...beforeWeekend, ...afterWeekend],
    dailyBars: [],
  });

  // The whole daily archive is gone...
  assert.equal(payload.studyBars.length, beforeWeekend.length + afterWeekend.length);
  assert.equal(payload.studyBars[0].time, beforeWeekend[0].time);
  // ...and the isolated weekend gap did NOT truncate the fine tape.
  assert.equal(payload.studyBars.at(-1).time, afterWeekend.at(-1).time);
  assert.ok(payload.studyBars.some((bar) => bar.close === 200));
});

test("a clean fine study tape is returned untouched", () => {
  const newest = 3_000_000_000;
  const fine = Array.from({ length: 50 }, (_, i) => ({ time: newest - (50 - i) * 1_800, close: i }));
  const payload = normalizeOiChartPayload({ bars: [], studyBars: fine, dailyBars: [] });
  assert.equal(payload.studyBars.length, fine.length);
});
