import test from "node:test";
import assert from "node:assert/strict";

import {
  aggregateChartBars,
  buildChartDisplayBars,
  chartDeepHistoryPending,
  COARSE_TIMEFRAME_MIN_CANDLES,
  chartAggregationBucketTime,
  chartSourceBarSpacingMinutes,
  normalizeChartCandleBars,
  tosFourHourBucketTime,
} from "./chartAggregation.js";

const unix = (iso) => Math.floor(new Date(iso).getTime() / 1000);

test("aligns primary TOS 4H candles to midnight-Central boundaries", () => {
  const fiveEastern = unix("2026-07-31T09:00:00Z");
  const nineEastern = unix("2026-07-31T13:00:00Z");
  const thirteenEastern = unix("2026-07-31T17:00:00Z");

  assert.equal(tosFourHourBucketTime(unix("2026-07-31T12:59:00Z")), fiveEastern);
  assert.equal(tosFourHourBucketTime(unix("2026-07-31T13:00:00Z")), nineEastern);
  assert.equal(tosFourHourBucketTime(unix("2026-07-31T16:59:00Z")), nineEastern);
  assert.equal(tosFourHourBucketTime(unix("2026-07-31T17:00:00Z")), thirteenEastern);
  // Central and Eastern observe DST together, so the 01:00 ET anchor is also
  // stable during standard time.
  assert.equal(
    tosFourHourBucketTime(unix("2026-01-09T18:00:00Z")),
    unix("2026-01-09T18:00:00Z"), // 13:00 Eastern (EST)
  );
});

test("builds only true four-hour primary candles around the 13:00 boundary", () => {
  const bars = [
    { time: unix("2026-07-31T12:55:00Z"), open: 100, high: 101, low: 99, close: 100.5, volume: 10 }, // 08:55 ET
    { time: unix("2026-07-31T13:00:00Z"), open: 100.5, high: 103, low: 100, close: 102, volume: 20 }, // 09:00 ET
    { time: unix("2026-07-31T16:55:00Z"), open: 102, high: 104, low: 101, close: 103, volume: 30 }, // 12:55 ET
    { time: unix("2026-07-31T17:00:00Z"), open: 103, high: 105, low: 102, close: 104, volume: 40 }, // 13:00 ET
    { time: unix("2026-07-31T19:55:00Z"), open: 104, high: 105.5, low: 103.5, close: 105, volume: 45 },
    { time: unix("2026-07-31T20:00:00Z"), open: 105, high: 106, low: 104, close: 105.5, volume: 50 },
  ];

  const aggregated = aggregateChartBars(bars, 240);
  assert.deepEqual(
    aggregated.map(({ time, open, high, low, close, volume }) => ({ time, open, high, low, close, volume })),
    [
      { time: unix("2026-07-31T09:00:00Z"), open: 100, high: 101, low: 99, close: 100.5, volume: 10 }, // 05:00 ET
      { time: unix("2026-07-31T13:00:00Z"), open: 100.5, high: 104, low: 100, close: 103, volume: 50 }, // 09:00 ET
      { time: unix("2026-07-31T17:00:00Z"), open: 103, high: 106, low: 102, close: 105.5, volume: 135 }, // 13:00 ET
    ],
  );
});

test("extends only the 4H display with study history and cuts over cleanly to live bars", () => {
  const studyBars = [
    { time: unix("2026-07-29T13:00:00Z"), open: 90, high: 94, low: 89, close: 93, volume: 50 },
    // This cached row overlaps the live window: its prices must not be
    // counted, but its volume is a complete reading of the same bucket and
    // wins over the live tape's partial sum (see overlayLiveOnHistorical).
    { time: unix("2026-07-31T13:00:00Z"), open: 100, high: 999, low: 1, close: 500, volume: 5000 },
  ];
  const liveBars = [
    // Begin inside the cached 13:00 five-minute bucket. The fresh partial
    // bucket owns the prices; the same sub-bucket's volumes are never added.
    { time: unix("2026-07-31T13:03:00Z"), open: 100, high: 103, low: 99, close: 102, volume: 10 },
    { time: unix("2026-07-31T14:00:00Z"), open: 102, high: 105, low: 101, close: 104, volume: 20 },
  ];

  assert.deepEqual(
    buildChartDisplayBars({ studyBars, liveBars, aggregationMinutes: 240, sourcesNormalized: true })
      .map(({ time, open, high, low, close, volume }) => ({ time, open, high, low, close, volume })),
    [
      { time: unix("2026-07-29T13:00:00Z"), open: 90, high: 94, low: 89, close: 93, volume: 50 },
      // 5000 for the shared 13:00 sub-bucket (the cached reading wins over the
      // live partial 10) plus the 14:00 sub-bucket the cache never had (20).
      { time: unix("2026-07-31T13:00:00Z"), open: 100, high: 105, low: 99, close: 104, volume: 5020 },
    ],
  );

  assert.deepEqual(
    buildChartDisplayBars({ studyBars, liveBars, aggregationMinutes: 5 }),
    aggregateChartBars(liveBars, 5),
    "5m keeps the existing live-window-only display",
  );
});

test("leaves ordinary minute aggregation boundaries unchanged", () => {
  const time = unix("2026-07-31T14:07:00Z");
  assert.equal(aggregateChartBars([{ time, open: 1, high: 1, low: 1, close: 1, volume: 1 }], 5)[0].time, unix("2026-07-31T14:05:00Z"));
});

test("drops malformed broker candles without blanking the valid candle series", () => {
  const validTime = unix("2026-07-31T14:07:00Z");
  const malformedTime = unix("2026-07-31T14:08:00Z");
  const bars = [
    { time: validTime, open: 100, high: 99, low: 101, close: 102, volume: "12" },
    { time: malformedTime, open: 102, high: null, low: 101, close: 102, volume: 9 },
  ];

  assert.deepEqual(normalizeChartCandleBars(bars), [
    { time: validTime, open: 100, high: 102, low: 100, close: 102, volume: 12 },
  ]);
  assert.deepEqual(
    aggregateChartBars(bars, 5).map(({ time, open, high, low, close, volume }) => ({ time, open, high, low, close, volume })),
    [{ time: unix("2026-07-31T14:05:00Z"), open: 100, high: 102, low: 100, close: 102, volume: 12 }],
  );
});

test("daily candles retain one Eastern trading date across UTC midnight", () => {
  const bars = [
    { time: unix("2026-01-09T23:55:00Z"), open: 100, high: 102, low: 99, close: 101, volume: 10 }, // Fri 18:55 EST
    { time: unix("2026-01-10T00:05:00Z"), open: 101, high: 104, low: 100, close: 103, volume: 20 }, // Fri 19:05 EST
  ];

  assert.equal(chartAggregationBucketTime(bars[1].time, 1440), unix("2026-01-09T05:00:00Z"));

  assert.deepEqual(aggregateChartBars(bars, 1440), [{
    time: unix("2026-01-09T05:00:00Z"), // Fri 00:00 EST
    open: 100,
    high: 104,
    low: 99,
    close: 103,
    volume: 30,
  }]);
});

test("weekly candles use Eastern calendar Mondays instead of epoch Thursdays", () => {
  const bars = [
    { time: unix("2026-07-27T13:30:00Z"), open: 100, high: 102, low: 99, close: 101, volume: 10 },
    { time: unix("2026-07-31T23:55:00Z"), open: 101, high: 105, low: 100, close: 104, volume: 20 },
    { time: unix("2026-08-03T13:30:00Z"), open: 104, high: 106, low: 103, close: 105, volume: 30 },
  ];

  assert.deepEqual(
    aggregateChartBars(bars, 10080).map(({ time, open, close, volume }) => ({ time, open, close, volume })),
    [
      { time: unix("2026-07-27T04:00:00Z"), open: 100, close: 104, volume: 30 },
      { time: unix("2026-08-03T04:00:00Z"), open: 104, close: 105, volume: 30 },
    ],
  );
});

test("monthly candles use the first Eastern calendar day without 30-day drift", () => {
  const bars = [
    { time: unix("2026-07-01T13:30:00Z"), open: 100, high: 102, low: 99, close: 101, volume: 10 },
    { time: unix("2026-07-31T23:55:00Z"), open: 101, high: 105, low: 100, close: 104, volume: 20 },
    { time: unix("2026-08-03T13:30:00Z"), open: 104, high: 106, low: 103, close: 105, volume: 30 },
  ];

  assert.deepEqual(
    aggregateChartBars(bars, 43200).map(({ time, open, close, volume }) => ({ time, open, close, volume })),
    [
      { time: unix("2026-07-01T04:00:00Z"), open: 100, close: 104, volume: 30 },
      { time: unix("2026-08-01T04:00:00Z"), open: 104, close: 105, volume: 30 },
    ],
  );
});

test("daily view draws from the long daily seed with the live day spliced in", () => {
  // Seed rows use Eastern-midnight epochs, exactly as the API serves them.
  const seedMonday = unix("2026-08-03T04:00:00Z");
  const seedTuesday = unix("2026-08-04T04:00:00Z");
  const dailyBars = [
    { time: seedMonday, open: 100, high: 105, low: 99, close: 104, volume: 1000 },
    // Stale partial row for Tuesday; the live tape must replace it.
    { time: seedTuesday, open: 104, high: 104.5, low: 103, close: 103.5, volume: 10 },
  ];
  const liveBars = [
    { time: unix("2026-08-04T13:30:00Z"), open: 104, high: 106, low: 104, close: 105, volume: 50 },
    { time: unix("2026-08-04T19:59:00Z"), open: 105, high: 107, low: 105, close: 106.5, volume: 60 },
  ];
  const daily = buildChartDisplayBars({ liveBars, dailyBars, aggregationMinutes: 1440 });
  assert.equal(daily.length, 2);
  assert.equal(daily[0].time, seedMonday);
  assert.equal(daily[0].close, 104);
  assert.equal(daily[1].time, seedTuesday);
  assert.equal(daily[1].high, 107); // live candle replaced the stale seed row
  assert.equal(daily[1].volume, 110);
});

test("weekly view groups the daily seed into Monday buckets", () => {
  const dailyBars = [
    { time: unix("2026-07-27T04:00:00Z"), open: 1, high: 3, low: 1, close: 2, volume: 5 }, // Mon wk1
    { time: unix("2026-07-29T04:00:00Z"), open: 2, high: 5, low: 2, close: 4, volume: 5 }, // Wed wk1
    { time: unix("2026-08-03T04:00:00Z"), open: 4, high: 6, low: 3, close: 5, volume: 7 }, // Mon wk2
  ];
  const weekly = buildChartDisplayBars({ liveBars: [], dailyBars, aggregationMinutes: 10080 });
  assert.equal(weekly.length, 2);
  assert.equal(weekly[0].time, unix("2026-07-27T04:00:00Z"));
  assert.equal(weekly[0].high, 5);
  assert.equal(weekly[0].close, 4);
  assert.equal(weekly[0].volume, 10);
  assert.equal(weekly[1].time, unix("2026-08-03T04:00:00Z"));
});

test("daily view without a seed still aggregates the live tape", () => {
  const liveBars = [
    { time: unix("2026-08-04T13:30:00Z"), open: 10, high: 12, low: 9, close: 11, volume: 5 },
  ];
  const daily = buildChartDisplayBars({ liveBars, dailyBars: [], aggregationMinutes: 1440 });
  assert.equal(daily.length, 1);
  assert.equal(daily[0].close, 11);
});

test("chartSourceBarSpacingMinutes detects native cadence from modal gap", () => {
  const fiveMinute = Array.from({ length: 20 }, (_, index) => ({ time: 1000 + index * 300, open: 1, high: 1, low: 1, close: 1, volume: 1 }));
  const thirtyMinute = Array.from({ length: 20 }, (_, index) => ({ time: 1000 + index * 1800, open: 1, high: 1, low: 1, close: 1, volume: 1 }));
  assert.equal(chartSourceBarSpacingMinutes(fiveMinute), 5);
  assert.equal(chartSourceBarSpacingMinutes(thirtyMinute), 30);
  assert.equal(chartSourceBarSpacingMinutes([]), 5);
  assert.equal(chartSourceBarSpacingMinutes([{ time: 100 }]), 5);
});

test("buildChartDisplayBars 4H view handles thirty-minute studyBars without ghost slots", () => {
  const base = 1754902800; // aligned epoch
  const studyBars = Array.from({ length: 16 }, (_, index) => ({
    time: base + index * 1800,
    open: 100 + index, high: 101 + index, low: 99 + index, close: 100.5 + index, volume: 10,
  }));
  const liveBars = Array.from({ length: 30 }, (_, index) => ({
    time: base + 16 * 1800 + index * 60,
    open: 120, high: 121, low: 119, close: 120.5, volume: 2,
  }));
  const fourHour = buildChartDisplayBars({ studyBars, liveBars, aggregationMinutes: 240 });
  assert.ok(fourHour.length >= 2, `expected aggregated 4H candles, got ${fourHour.length}`);
  // Every candle must sit on a 240-minute boundary with no duplicates.
  const times = fourHour.map((bar) => bar.time);
  assert.equal(new Set(times).size, times.length);
  times.forEach((time) => assert.equal((time % (240 * 60)) < 240 * 60, true));
  // Volume must be conserved: 16 study bars x10 + 30 live x2.
  const totalVolume = fourHour.reduce((sum, bar) => sum + bar.volume, 0);
  assert.equal(totalVolume, 16 * 10 + 30 * 2);
});

test("buildChartDisplayBars 4H view keeps five-minute studyBars behavior", () => {
  const base = 1754902800;
  const studyBars = Array.from({ length: 96 }, (_, index) => ({
    time: base + index * 300,
    open: 50, high: 51, low: 49, close: 50.5, volume: 3,
  }));
  const fourHour = buildChartDisplayBars({ studyBars, liveBars: [], aggregationMinutes: 240 });
  const totalVolume = fourHour.reduce((sum, bar) => sum + bar.volume, 0);
  assert.equal(totalVolume, 96 * 3);
});

// A fast-start payload ships SHORT seeds, not empty ones - measured on MMM
// 2026-08-18: historyLoading=true, studyBars=796, dailyBars=3, rendering 10
// candles on 4H and 3 on D. An emptiness check never fired; the count does.
test("deep history is pending while a coarse timeframe renders only a stub", () => {
  assert.equal(chartDeepHistoryPending({ renderedCandleCount: 10, aggregationMinutes: 240, historyLoading: true }), true);
  assert.equal(chartDeepHistoryPending({ renderedCandleCount: 3, aggregationMinutes: 1440, historyLoading: true }), true);
  assert.equal(chartDeepHistoryPending({ renderedCandleCount: 35, aggregationMinutes: 60, historyLoading: true }), true);
  assert.equal(chartDeepHistoryPending({ renderedCandleCount: 18, aggregationMinutes: 120, historyLoading: true }), true);
  // Warm: thousands of candles, so no badge.
  assert.equal(chartDeepHistoryPending({ renderedCandleCount: 5958, aggregationMinutes: 240, historyLoading: true }), false);
  // 30m and below reconstruct within seconds; labelling them would flicker.
  assert.equal(chartDeepHistoryPending({ renderedCandleCount: 26, aggregationMinutes: 30, historyLoading: true }), false);
  // Not loading => show what exists rather than a badge that never clears.
  assert.equal(chartDeepHistoryPending({ renderedCandleCount: 10, aggregationMinutes: 240, historyLoading: false }), false);
  // Boundary is exclusive.
  assert.equal(chartDeepHistoryPending({ renderedCandleCount: COARSE_TIMEFRAME_MIN_CANDLES, aggregationMinutes: 240, historyLoading: true }), false);
});

test("aggregateChartBars cache sees an in-place tail mutation of the same array", async () => {
  const { aggregateChartBars } = await import("./chartAggregation.js");
  const tape = [
    { time: 1787000460, open: 1, high: 2, low: 1, close: 1.5, volume: 10 },
    { time: 1787000520, open: 1.5, high: 2, low: 1, close: 1.8, volume: 10 },
    { time: 1787000580, open: 1.8, high: 2.2, low: 1.7, close: 2, volume: 5 },
  ];
  const first = aggregateChartBars(tape, 5);
  assert.equal(first.at(-1).close, 2);
  // Same array reference (the live stream buffer), last bar restated in place.
  tape[2] = { ...tape[2], close: 2.4, high: 2.5, volume: 9 };
  const second = aggregateChartBars(tape, 5);
  assert.equal(second.at(-1).close, 2.4);
  // Unchanged content -> cached result reused.
  assert.equal(aggregateChartBars(tape, 5), second);
});

test("a thin live bucket never replaces a completed TOS five-minute candle", () => {
  // Measured 2026-09-04 01:46 ET: the legend read O=H=L=C, Vol 0 because a
  // single sparse live minute landed on top of a full 5m candle. The TOS tape
  // is the source of truth for every bucket it has closed; the live tape may
  // only own the forming candle and anything newer.
  const fineStudyBars = [
    { time: unix("2026-09-03T13:30:00Z"), open: 100, high: 102, low: 99, close: 101, volume: 5000 },
    { time: unix("2026-09-03T13:35:00Z"), open: 101, high: 104, low: 100, close: 103, volume: 6000 },
    { time: unix("2026-09-03T13:40:00Z"), open: 103, high: 103.5, low: 102, close: 102.5, volume: 4000 },
  ];
  const liveBars = [
    // One trade-only minute inside the CLOSED 13:35 candle: flat, no volume.
    { time: unix("2026-09-03T13:37:00Z"), open: 103, high: 103, low: 103, close: 103, volume: 0 },
    // The forming 13:40 candle keeps ticking from the live tape.
    { time: unix("2026-09-03T13:43:00Z"), open: 103, high: 105, low: 102, close: 104.5, volume: 4500 },
    // A bucket the TOS tape has not shipped yet comes from live alone.
    { time: unix("2026-09-03T13:45:00Z"), open: 104.5, high: 106, low: 104, close: 105, volume: 700 },
  ];
  const out = buildChartDisplayBars({ fineStudyBars, liveBars, aggregationMinutes: 5, sourcesNormalized: true })
    .map(({ time, open, high, low, close, volume }) => ({ time, open, high, low, close, volume }));
  assert.deepEqual(out, [
    fineStudyBars[0],
    fineStudyBars[1],
    { time: unix("2026-09-03T13:40:00Z"), open: 103, high: 105, low: 102, close: 104.5, volume: 4500 },
    { time: unix("2026-09-03T13:45:00Z"), open: 104.5, high: 106, low: 104, close: 105, volume: 700 },
  ]);
});

test("study-history path keeps closed cached candles when the live tape is sparse", () => {
  const studyBars = [
    { time: unix("2026-07-29T13:00:00Z"), open: 90, high: 94, low: 89, close: 93, volume: 50 },
    { time: unix("2026-07-30T13:00:00Z"), open: 93, high: 97, low: 92, close: 96, volume: 60 },
  ];
  const liveBars = [
    // Sparse minute inside the CLOSED 07-29 candle must not flatten it.
    { time: unix("2026-07-29T13:03:00Z"), open: 91, high: 91, low: 91, close: 91, volume: 0 },
    // Partial minute inside the LAST cached candle replaces its prices
    // (forming); the cached candle's fuller volume is kept.
    { time: unix("2026-07-30T13:03:00Z"), open: 93, high: 95, low: 93, close: 94, volume: 10 },
  ];
  const out = buildChartDisplayBars({ studyBars, liveBars, aggregationMinutes: 240, sourcesNormalized: true })
    .map(({ time, open, high, low, close, volume }) => ({ time, open, high, low, close, volume }));
  assert.deepEqual(out, [
    studyBars[0],
    { time: unix("2026-07-30T13:00:00Z"), open: 93, high: 95, low: 93, close: 94, volume: 60 },
  ]);
});

test("the live tape fills holes the TOS tape is missing without touching its closed candles", () => {
  // Measured 2026-09-04 on AMZN: the night of Sep 2 had 32/96 five-minute
  // candles in the TOS tape but 96/96 derivable from the one-minute tape. A
  // hole must be filled from live; an existing candle must still be kept.
  const fineStudyBars = [
    { time: unix("2026-09-02T23:45:00Z"), open: 99, high: 100, low: 98, close: 99.5, volume: 300 },
    { time: unix("2026-09-02T23:50:00Z"), open: 99.5, high: 100.5, low: 99, close: 100, volume: 300 },
    { time: unix("2026-09-02T23:55:00Z"), open: 100, high: 101, low: 99.5, close: 100, volume: 300 },
    { time: unix("2026-09-03T00:00:00Z"), open: 100, high: 102, low: 99, close: 101, volume: 500 },
    // 00:05 and 00:10 missing from the TOS tape.
    { time: unix("2026-09-03T00:15:00Z"), open: 103, high: 104, low: 102, close: 103.5, volume: 400 },
  ];
  const liveBars = [
    { time: unix("2026-09-03T00:01:00Z"), open: 100, high: 100, low: 100, close: 100, volume: 0 }, // inside a closed candle: ignored
    { time: unix("2026-09-03T00:06:00Z"), open: 101, high: 102.5, low: 101, close: 102, volume: 60 }, // fills the 00:05 hole
    { time: unix("2026-09-03T00:11:00Z"), open: 102, high: 103, low: 102, close: 103, volume: 70 }, // fills the 00:10 hole
  ];
  const out = buildChartDisplayBars({ fineStudyBars, liveBars, aggregationMinutes: 5, sourcesNormalized: true })
    .map(({ time, open, high, low, close, volume }) => ({ time, open, high, low, close, volume }));
  assert.deepEqual(out, [
    ...fineStudyBars.slice(0, 4),
    { time: unix("2026-09-03T00:05:00Z"), open: 101, high: 102.5, low: 101, close: 102, volume: 60 },
    { time: unix("2026-09-03T00:10:00Z"), open: 102, high: 103, low: 102, close: 103, volume: 70 },
    fineStudyBars[4],
  ]);
});

test("a missing volume is a 0, and a bucket of non-finite volumes never becomes NaN", () => {
  const time = unix("2026-09-03T13:40:00Z");
  const [missing] = normalizeChartCandleBars([{ time, open: 1, high: 1, low: 1, close: 1 }]);
  assert.equal(missing.volume, 0);
  const [text] = normalizeChartCandleBars([{ time, open: 1, high: 1, low: 1, close: 1, volume: "12" }]);
  assert.equal(text.volume, 12);
  // Pre-normalised callers can still hand over null/NaN; they add nothing.
  const mixed = [
    { time, open: 1, high: 1, low: 1, close: 1, volume: 300 },
    { time: time + 60, open: 1, high: 1, low: 1, close: 1, volume: null },
    { time: time + 120, open: 1, high: 1, low: 1, close: 1, volume: NaN },
  ];
  assert.equal(aggregateChartBars(mixed, 5)[0].volume, 300);
});

test("the forming candle keeps the larger of the two volumes it is known by", () => {
  // Measured 2026-09-04 08:30:01 ET: Vol 60,858 -> Vol 0 at the 5m boundary.
  // The live tape's forming bucket is its closed minutes plus a forming minute
  // that has barely started, so it always understates; the server's
  // five-minute bar for the same bucket is complete but may be a reconcile
  // behind. Price comes from the live side; volume from whichever knows more.
  const fineStudyBars = [
    { time: unix("2026-09-03T13:35:00Z"), open: 101, high: 104, low: 100, close: 103, volume: 6000 },
    { time: unix("2026-09-03T13:40:00Z"), open: 103, high: 103.5, low: 102, close: 102.5, volume: 4000 },
  ];
  const liveBars = [
    { time: unix("2026-09-03T13:40:00Z"), open: 103, high: 103.5, low: 102, close: 102.5, volume: 1200 },
    { time: unix("2026-09-03T13:41:00Z"), open: 102.5, high: 105, low: 102.5, close: 104.5, volume: 0 },
  ];
  const out = buildChartDisplayBars({ fineStudyBars, liveBars, aggregationMinutes: 5, sourcesNormalized: true })
    .map(({ time, open, high, low, close, volume }) => ({ time, open, high, low, close, volume }));
  assert.deepEqual(out[1], { time: unix("2026-09-03T13:40:00Z"), open: 103, high: 105, low: 102, close: 104.5, volume: 4000 });
  // Once the live side has seen MORE than the server's stale reading, it wins.
  const later = buildChartDisplayBars({
    fineStudyBars,
    liveBars: [{ time: unix("2026-09-03T13:40:00Z"), open: 103, high: 105, low: 102, close: 104.5, volume: 4300 }],
    aggregationMinutes: 5,
    sourcesNormalized: true,
  });
  assert.equal(later[1].volume, 4300);
  // A live bucket the server has not shipped yet keeps its own reading.
  const ahead = buildChartDisplayBars({
    fineStudyBars,
    liveBars: [{ time: unix("2026-09-03T13:45:00Z"), open: 1, high: 1, low: 1, close: 1, volume: 7 }],
    aggregationMinutes: 5,
    sourcesNormalized: true,
  });
  assert.equal(ahead[ahead.length - 1].volume, 7);
});
