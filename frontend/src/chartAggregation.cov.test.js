// Coverage (2026-11-04): every chart timeframe, and the TOS 2H/4H clocks
// across both daylight-saving switches. TOS draws 4H candles from midnight
// Central (01:00, 05:00, 09:00, 13:00, 17:00, 21:00 ET) and 2H on the odd
// hours; a bucket that drifts by an hour on one side of a switch puts the
// CALL2H/CALL4H labels on different candles than the trader's TOS chart.
import test from "node:test";
import assert from "node:assert/strict";

import {
  COARSE_TIMEFRAME_MIN_CANDLES,
  buildChartDisplayBars,
  chartAggregationBucketTime,
  tosFourHourBucketTime,
  tosTwoHourBucketTime,
} from "./chartAggregation.js";

// The eleven timeframes of the panel's button row (OI_CHART_TIMEFRAMES in
// App.jsx, copied because App.jsx cannot be imported by node:test).
const TIMEFRAMES = [
  ["3m", 3], ["5m", 5], ["10m", 10], ["15m", 15], ["30m", 30], ["1h", 60],
  ["2h", 120], ["4h", 240], ["D", 1440], ["W", 10080], ["M", 43200],
];

const ET = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

function etClock(epoch) {
  const part = Object.fromEntries(ET.formatToParts(new Date(epoch * 1000)).map((p) => [p.type, p.value]));
  return `${part.year}-${part.month}-${part.day} ${part.hour}:${part.minute}`;
}

// Epoch of an Eastern wall-clock time, whichever of EST/EDT is in force.
function et(day, hhmm) {
  for (const offset of ["-04:00", "-05:00"]) {
    const epoch = Math.floor(new Date(`${day}T${hhmm}:00${offset}`).getTime() / 1000);
    if (etClock(epoch) === `${day} ${hhmm}`) return epoch;
  }
  throw new Error(`${day} ${hhmm} does not exist in Eastern time`);
}

// Weekdays on each side of the 2026 switches (Sun 03-08 and Sun 11-01).
const AROUND_SWITCHES = ["2026-03-06", "2026-03-09", "2026-10-30", "2026-11-02"];

for (const day of AROUND_SWITCHES) {
  test(`4H candles keep the TOS midnight-Central clock on ${day}`, () => {
    const cases = [
      ["00:30", `${day} 21:00`, -1], ["04:59", "01:00"], ["05:00", "05:00"], ["09:30", "09:00"],
      ["12:59", "09:00"], ["13:00", "13:00"], ["16:00", "13:00"], ["17:00", "17:00"], ["20:15", "17:00"], ["21:00", "21:00"],
    ];
    for (const [time, expected, dayShift] of cases) {
      const bucket = tosFourHourBucketTime(et(day, time));
      if (dayShift === -1) {
        // 00:30 belongs to the previous evening's 21:00 candle.
        assert.equal(etClock(bucket).slice(11), "21:00", `${day} ${time}`);
        assert.ok(bucket < et(day, "00:30"));
        continue;
      }
      assert.equal(etClock(bucket), `${day} ${expected}`, `${day} ${time}`);
      assert.equal(chartAggregationBucketTime(et(day, time), 240), bucket);
    }
  });

  test(`2H candles sit on the odd ET hours on ${day}`, () => {
    for (const [time, expected] of [["04:15", "03:00"], ["07:00", "07:00"], ["08:59", "07:00"], ["09:30", "09:00"],
      ["11:00", "11:00"], ["15:59", "15:00"], ["19:30", "19:00"]]) {
      assert.equal(etClock(tosTwoHourBucketTime(et(day, time))), `${day} ${expected}`, `${day} ${time}`);
      assert.equal(chartAggregationBucketTime(et(day, time), 120), tosTwoHourBucketTime(et(day, time)));
    }
  });
}

// The switch day itself is a Sunday, but the overnight (BOATS) session opens
// at 20:00 ET that evening and its candles feed Monday's first 2H/4H labels.
test("the Sunday-evening overnight candles keep the 17:00/21:00 4H and odd-hour 2H clock on a DST switch day", {
  todo: "Bucketing steps whole hours from 01:00 ET in absolute time, so on 2026-03-08 the 20:15 ET bar lands in an 18:00 4H / 20:00 2H candle (11-01: 20:00 / 20:00) instead of 17:00 / 19:00, and on 11-01 a 22:00 and a 23:00 2H candle overlap - confirm against a TOS screenshot of a DST Sunday night before fixing",
}, () => {
  for (const day of ["2026-03-08", "2026-11-01"]) {
    assert.equal(etClock(tosFourHourBucketTime(et(day, "20:15"))), `${day} 17:00`);
    assert.equal(etClock(tosTwoHourBucketTime(et(day, "20:15"))), `${day} 19:00`);
  }
  // 23:30 Sunday and 00:30 Monday share TOS's 23:00 2H candle; today the
  // first lands in a 22:00 candle that overlaps the 23:00 one.
  assert.equal(tosTwoHourBucketTime(et("2026-11-01", "23:30")), tosTwoHourBucketTime(et("2026-11-02", "00:30")));
});

function bar(time, price) {
  return { time, open: price, high: price + 1, low: price - 1, close: price + 0.25, volume: 100 };
}

// One payload shaped like the server's. Its intraday tapes end on a Wednesday
// clear of a DST switch (the switch nights are pinned by the todo above); the
// daily seed spans six years of them.
const FIXTURE_DAY = "2026-10-28";

function fixturePayload() {
  const end = et(FIXTURE_DAY, "15:59");
  const live = Array.from({ length: 3 * 24 * 60 }, (_, i) => bar(end - (3 * 24 * 60 - 1 - i) * 60, 500 + (i % 40)));
  const fineEnd = end - (end % 300);
  const fine = Array.from({ length: 20 * 24 * 12 }, (_, i) => bar(fineEnd - (20 * 24 * 12 - 1 - i) * 300, 480 + (i % 30)));
  const studyEnd = end - (end % 1800);
  const study = Array.from({ length: 160 * 48 }, (_, i) => bar(studyEnd - (160 * 48 - 1 - i) * 1800, 400 + (i % 50)));
  const daily = [];
  for (let i = 2400; i >= 0; i -= 1) {
    const date = new Date(Date.UTC(2026, 9, 28) - i * 86_400_000).toISOString().slice(0, 10);
    daily.push(bar(et(date, "00:00"), 300 + (i % 70)));
  }
  return { bars: live, fineStudyBars: fine, studyBars: study, dailyBars: daily };
}

test("every one of the eleven timeframes draws ascending, unique, bucket-aligned candles", () => {
  const payload = fixturePayload();
  for (const [key, minutes] of TIMEFRAMES) {
    const candles = buildChartDisplayBars({
      studyBars: payload.studyBars, fineStudyBars: payload.fineStudyBars,
      liveBars: payload.bars, dailyBars: payload.dailyBars, aggregationMinutes: minutes,
    });
    assert.ok(candles.length > 0, `${key}: no candles`);
    const stamps = candles.map((candle) => candle.time);
    for (let i = 1; i < stamps.length; i += 1) {
      assert.ok(stamps[i] > stamps[i - 1], `${key}: ${etClock(stamps[i])} is not after ${etClock(stamps[i - 1])}`);
    }
    for (const candle of candles) {
      assert.ok(Number.isFinite(candle.open) && Number.isFinite(candle.close), `${key}: NaN price`);
      assert.ok(candle.high >= Math.max(candle.open, candle.close) && candle.low <= Math.min(candle.open, candle.close), `${key}: bad OHLC`);
    }
    if (minutes < 1440) {
      const misaligned = stamps.filter((time) => chartAggregationBucketTime(time, minutes) !== time);
      assert.deepEqual(misaligned.map(etClock), [], `${key}: candles off the timeframe's clock`);
    }
    if (minutes >= 240) {
      assert.ok(candles.length >= COARSE_TIMEFRAME_MIN_CANDLES, `${key}: only ${candles.length} candles - a stub, not a chart`);
    }
    // The forming candle is the live tape's, on every timeframe.
    assert.equal(candles.at(-1).close, payload.bars.at(-1).close, `${key}: forming candle is stale`);
  }
});

test("4H opens with months of history from the study tape, not the 3-day live tape", () => {
  const payload = fixturePayload();
  const fourHour = buildChartDisplayBars({
    studyBars: payload.studyBars, fineStudyBars: payload.fineStudyBars,
    liveBars: payload.bars, dailyBars: payload.dailyBars, aggregationMinutes: 240,
  });
  const firstStudy = payload.studyBars[0].time;
  assert.ok(fourHour[0].time <= firstStudy + 4 * 3600, "4H must start where the study tape starts");
  const liveOnly = buildChartDisplayBars({ liveBars: payload.bars, aggregationMinutes: 240 });
  assert.ok(fourHour.length > liveOnly.length * 10);
});

test("W and M come from the daily seed and the live day replaces the seed's same-day row", () => {
  const payload = fixturePayload();
  const daily = buildChartDisplayBars({ liveBars: payload.bars, dailyBars: payload.dailyBars, aggregationMinutes: 1440 });
  const weeks = buildChartDisplayBars({ liveBars: payload.bars, dailyBars: payload.dailyBars, aggregationMinutes: 10080 });
  const months = buildChartDisplayBars({ liveBars: payload.bars, dailyBars: payload.dailyBars, aggregationMinutes: 43200 });
  const dates = daily.map((candle) => etClock(candle.time).slice(0, 10));
  assert.equal(new Set(dates).size, dates.length, "one D candle per day");
  assert.equal(etClock(daily.at(-1).time).slice(0, 10), FIXTURE_DAY);
  assert.ok(weeks.every((candle) => new Date(candle.time * 1000).toLocaleString("en-US", { timeZone: "America/New_York", weekday: "short" }) === "Mon"));
  assert.ok(months.every((candle) => etClock(candle.time).slice(8) === "01 00:00"));
  assert.ok(months.length >= 60 && weeks.length >= 60);
});
