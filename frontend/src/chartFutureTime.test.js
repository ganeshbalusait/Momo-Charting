import assert from "node:assert/strict";
import test from "node:test";
import { buildFutureChartTimes, projectFutureChartTime } from "./chartFutureTime.js";
import { aggregateChartBars } from "./chartAggregation.js";

const utc = (year, month, day, hour, minute = 0) => (
  Math.floor(Date.UTC(year, month - 1, day, hour, minute) / 1000)
);

test("future 5m slots continue in-session then skip the weekend", () => {
  const bars = [
    { time: utc(2026, 7, 30, 8, 0) }, // Thu 04:00 ET
    { time: utc(2026, 7, 30, 23, 55) }, // Thu 19:55 ET
    { time: utc(2026, 7, 31, 23, 50) }, // Fri 19:50 ET
    { time: utc(2026, 7, 31, 23, 55) }, // Fri 19:55 ET
  ];
  assert.deepEqual(
    buildFutureChartTimes(bars, 5, 2),
    [utc(2026, 8, 3, 8, 0), utc(2026, 8, 3, 8, 5)],
  );
});

test("future 5m slots retain the remaining current-session times", () => {
  const bars = [
    { time: utc(2026, 7, 30, 8, 0) },
    { time: utc(2026, 7, 30, 23, 55) },
    { time: utc(2026, 7, 31, 23, 45) },
  ];
  assert.deepEqual(
    buildFutureChartTimes(bars, 5, 2),
    [utc(2026, 7, 31, 23, 50), utc(2026, 7, 31, 23, 55)],
  );
});

test("4h projection follows the primary TOS midnight-Central clock", () => {
  const bars = [1, 5, 9, 13, 17].flatMap((easternHour) => [
    { time: utc(2026, 7, 30, easternHour + 4) },
    { time: utc(2026, 7, 31, easternHour + 4) },
  ]).sort((left, right) => left.time - right.time);
  // With the extended-hours session on, TOS opens the week at Sunday 21:00 ET
  // and then runs 01/05/09/13/17/21 through Monday.
  assert.deepEqual(
    buildFutureChartTimes(bars, 240, 6),
    [
      utc(2026, 8, 3, 1), // Sun 21:00 ET
      utc(2026, 8, 3, 5), // Mon 01:00 ET
      utc(2026, 8, 3, 9),
      utc(2026, 8, 3, 13),
      utc(2026, 8, 3, 17),
      utc(2026, 8, 3, 21),
    ],
  );
});

test("4h projection keeps six bars per day, matching aggregated history", () => {
  // The candle grid (tosFourHourBucketTime) produces six EXTO buckets a day.
  // The projection has to use the same density or bar spacing visibly changes
  // where the history ends and the reserved future area begins.
  const bars = [{ time: utc(2026, 8, 3, 21) }]; // Mon 17:00 ET
  const projected = buildFutureChartTimes(bars, 240, 12);
  assert.deepEqual(projected, [
    utc(2026, 8, 4, 1), // Mon 21:00 ET opens Tuesday's session
    utc(2026, 8, 4, 5), // Tue 01:00 ET
    utc(2026, 8, 4, 9),
    utc(2026, 8, 4, 13),
    utc(2026, 8, 4, 17),
    utc(2026, 8, 4, 21),
    utc(2026, 8, 5, 1), // Tue 21:00 ET opens Wednesday's session
    utc(2026, 8, 5, 5),
    utc(2026, 8, 5, 9),
    utc(2026, 8, 5, 13),
    utc(2026, 8, 5, 17),
    utc(2026, 8, 5, 21),
  ]);
  // Mon->Wed spans no weekend, so every step is exactly one 4H bucket.
  projected.slice(1).forEach((time, index) => {
    assert.equal(time - projected[index], 4 * 60 * 60);
  });
});

test("4h projection never repeats or skips backwards across a weekend", () => {
  const friday = [{ time: utc(2026, 7, 31, 21) }]; // Fri 17:00 ET
  const projected = buildFutureChartTimes(friday, 240, 3);
  assert.deepEqual(projected, [
    utc(2026, 8, 3, 1), // Sun 21:00 ET opens the new week
    utc(2026, 8, 3, 5),
    utc(2026, 8, 3, 9),
  ]);
  // Starting from that Sunday opener must advance, not stall on itself.
  assert.equal(
    buildFutureChartTimes([{ time: utc(2026, 8, 3, 1) }], 240, 1)[0],
    utc(2026, 8, 3, 5),
  );
});

test("daily projection follows Eastern calendar buckets and skips closed dates", () => {
  const source = [
    { time: utc(2026, 7, 31, 14), open: 1, high: 1, low: 1, close: 1, volume: 1 },
  ];
  const fridayBucket = aggregateChartBars(source, 1440);
  assert.equal(fridayBucket[0].time, utc(2026, 7, 31, 4));
  assert.deepEqual(
    buildFutureChartTimes(fridayBucket, 1440, 2),
    [utc(2026, 8, 3, 4), utc(2026, 8, 4, 4)],
  );
});

test("weekly and monthly projections follow calendar boundaries", () => {
  const source = [
    { time: utc(2026, 7, 31, 14), open: 1, high: 1, low: 1, close: 1, volume: 1 },
  ];
  const weeklyBucket = aggregateChartBars(source, 10080);
  const monthlyBucket = aggregateChartBars(source, 43200);
  assert.equal(weeklyBucket[0].time, utc(2026, 7, 27, 4));
  assert.deepEqual(
    buildFutureChartTimes(weeklyBucket, 10080, 2),
    [utc(2026, 8, 3, 4), utc(2026, 8, 10, 4)],
  );
  assert.equal(monthlyBucket[0].time, utc(2026, 7, 1, 4));
  assert.deepEqual(
    buildFutureChartTimes(monthlyBucket, 43200, 2),
    [utc(2026, 8, 1, 4), utc(2026, 9, 1, 4)],
  );
});

test("future sessions skip US equity holidays", () => {
  const fridayBeforeLaborDay = utc(2026, 9, 4, 23, 55); // 19:55 Eastern
  assert.equal(
    buildFutureChartTimes([{ time: fridayBeforeLaborDay }], 5, 1)[0],
    utc(2026, 9, 8, 8), // Tuesday 04:00 Eastern
  );
});

test("Saturday New Year's Day does not close the preceding Friday", () => {
  const source = [
    { time: utc(2021, 12, 30, 15), open: 1, high: 1, low: 1, close: 1, volume: 1 },
  ];
  const thursdayBucket = aggregateChartBars(source, 1440);
  assert.deepEqual(
    buildFutureChartTimes(thursdayBucket, 1440, 2),
    [utc(2021, 12, 31, 5), utc(2022, 1, 3, 5)],
  );
});

test("logical fallback returns the projected slot", () => {
  const bars = [
    { time: utc(2026, 7, 30, 8, 0) },
    { time: utc(2026, 7, 30, 23, 55) },
    { time: utc(2026, 7, 31, 23, 55) },
  ];
  assert.equal(projectFutureChartTime(bars, 5, 1), utc(2026, 8, 3, 8));
});

test("future sessions retain 04:00 Eastern across daylight-saving weekends", () => {
  const springFriday = utc(2026, 3, 7, 0, 55); // Fri Mar 6 19:55 EST
  const fallFriday = utc(2026, 10, 30, 23, 55); // Fri Oct 30 19:55 EDT
  assert.equal(buildFutureChartTimes([{ time: springFriday }], 5, 1)[0], utc(2026, 3, 9, 8));
  assert.equal(buildFutureChartTimes([{ time: fallFriday }], 5, 1)[0], utc(2026, 11, 2, 9));
});

test("weekly calendar projection retains Eastern midnight across DST", () => {
  const source = [
    { time: utc(2026, 10, 30, 15), open: 1, high: 1, low: 1, close: 1, volume: 1 },
  ];
  const weeklyBucket = aggregateChartBars(source, 10080);
  assert.equal(weeklyBucket[0].time, utc(2026, 10, 26, 4));
  assert.equal(buildFutureChartTimes(weeklyBucket, 10080, 1)[0], utc(2026, 11, 2, 5));
});

test("2h candles retain the 04:00 Eastern session anchor through EST and EDT", () => {
  const winterSource = [
    { time: utc(2026, 1, 5, 9), open: 1, high: 1, low: 1, close: 1, volume: 1 }, // 04:00 ET
    { time: utc(2026, 1, 6, 0, 55), open: 1, high: 1, low: 1, close: 1, volume: 1 }, // 19:55 ET
  ];
  const winterBuckets = aggregateChartBars(winterSource, 120);
  assert.deepEqual(winterBuckets.map(({ time }) => time), [utc(2026, 1, 5, 9), utc(2026, 1, 5, 23)]);
  assert.equal(buildFutureChartTimes(winterBuckets, 120, 1)[0], utc(2026, 1, 6, 9));

  const summerSource = [
    { time: utc(2026, 7, 6, 8), open: 1, high: 1, low: 1, close: 1, volume: 1 }, // 04:00 ET
    { time: utc(2026, 7, 6, 23, 55), open: 1, high: 1, low: 1, close: 1, volume: 1 }, // 19:55 ET
  ];
  const summerBuckets = aggregateChartBars(summerSource, 120);
  assert.deepEqual(summerBuckets.map(({ time }) => time), [utc(2026, 7, 6, 8), utc(2026, 7, 6, 22)]);
  assert.equal(buildFutureChartTimes(summerBuckets, 120, 1)[0], utc(2026, 7, 7, 8));
});

test("every selectable chart timeframe produces ordered future whitespace", () => {
  const intradaySource = [{ time: utc(2026, 7, 31, 17) }]; // Fri 13:00 Eastern
  for (const minutes of [3, 5, 10, 15, 30, 60, 120, 240]) {
    const future = buildFutureChartTimes(intradaySource, minutes, 12);
    assert.equal(future.length, 12, `${minutes}m should reserve all requested future slots`);
    assert.equal(new Set(future).size, future.length, `${minutes}m should not duplicate timestamps`);
    assert.ok(
      future.every((time, index) => index === 0 || time > future[index - 1]),
      `${minutes}m future timestamps should be strictly increasing`,
    );
    assert.equal(
      projectFutureChartTime(intradaySource, minutes, 12),
      future.at(-1),
      `${minutes}m crosshair fallback should use the same future session clock`,
    );
  }

  const calendarSource = [{
    time: utc(2026, 7, 31, 14),
    open: 1,
    high: 1,
    low: 1,
    close: 1,
    volume: 1,
  }];
  for (const minutes of [1440, 10080, 43200]) {
    const bars = aggregateChartBars(calendarSource, minutes);
    const future = buildFutureChartTimes(bars, minutes, 12);
    assert.equal(future.length, 12, `${minutes}m should reserve all requested future slots`);
    assert.equal(new Set(future).size, future.length, `${minutes}m should not duplicate timestamps`);
    assert.ok(
      future.every((time, index) => index === 0 || time > future[index - 1]),
      `${minutes}m future timestamps should be strictly increasing`,
    );
    assert.equal(
      projectFutureChartTime(bars, minutes, 12),
      future.at(-1),
      `${minutes}m crosshair fallback should use the same future calendar bucket`,
    );
  }
});
