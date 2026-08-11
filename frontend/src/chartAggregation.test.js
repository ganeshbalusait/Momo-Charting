import test from "node:test";
import assert from "node:assert/strict";

import {
  aggregateChartBars,
  buildChartDisplayBars,
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
    // This cached row overlaps the live window and must not be counted.
    { time: unix("2026-07-31T13:00:00Z"), open: 100, high: 999, low: 1, close: 500, volume: 5000 },
  ];
  const liveBars = [
    // Begin inside the cached 13:00 five-minute bucket. The fresh partial
    // bucket replaces it instead of adding its volume or extreme prices.
    { time: unix("2026-07-31T13:03:00Z"), open: 100, high: 103, low: 99, close: 102, volume: 10 },
    { time: unix("2026-07-31T14:00:00Z"), open: 102, high: 105, low: 101, close: 104, volume: 20 },
  ];

  assert.deepEqual(
    buildChartDisplayBars({ studyBars, liveBars, aggregationMinutes: 240, sourcesNormalized: true })
      .map(({ time, open, high, low, close, volume }) => ({ time, open, high, low, close, volume })),
    [
      { time: unix("2026-07-29T13:00:00Z"), open: 90, high: 94, low: 89, close: 93, volume: 50 },
      { time: unix("2026-07-31T13:00:00Z"), open: 100, high: 105, low: 99, close: 104, volume: 30 },
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
