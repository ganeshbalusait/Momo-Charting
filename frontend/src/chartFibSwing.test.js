import assert from "node:assert/strict";
import test from "node:test";
import {
  AUTO_FIB_HIGHER_TIMEFRAMES,
  FIB_SWING_DEFAULTS,
  autoFibCalendarBars,
  autoFibCalendarKey,
  autoFibTimeframeAllowed,
  buildFibSwingStudy,
  findFibSwing,
  fibSwingLookback,
  fibSwingPrice,
  formatFibRatio,
} from "./chartFibSwing.js";
import { aggregateChartBars } from "./chartAggregation.js";

// Eastern-midnight epoch for a calendar date (EST, UTC-5, good for Jan-Feb).
const etMidnight = (y, m, d) => Date.UTC(y, m - 1, d, 5) / 1000;
const bar = (time, low, high, close = (low + high) / 2) => ({ time, open: close, high, low, close, volume: 100 });

test("AutoFib gains Weekly and Monthly after Daily", () => {
  assert.deepEqual(AUTO_FIB_HIGHER_TIMEFRAMES.map(({ key, minutes, optionKey }) => [key, minutes, optionKey]), [
    ["week", 10080, "autoFibWeek"],
    ["month", 43200, "autoFibMonth"],
  ]);
});

test("Weekly/Monthly AutoFib is skipped below the chart timeframe and, by default, on intraday charts", () => {
  assert.equal(autoFibTimeframeAllowed(10080, 43200, {}), false, "weekly on a monthly chart");
  assert.equal(autoFibTimeframeAllowed(43200, 43200, {}), true, "monthly on a monthly chart");
  assert.equal(autoFibTimeframeAllowed(10080, 1440, {}), true, "weekly on a daily chart");
  assert.equal(autoFibTimeframeAllowed(10080, 5, {}), false, "weekly kept off a 5m chart by default");
  assert.equal(autoFibTimeframeAllowed(10080, 5, { autoFibHigherOnDailyUp: false }), true, "trader can allow it");
  assert.equal(autoFibTimeframeAllowed(1440, 5, {}), true, "Daily AutoFib on 5m is unchanged");
  assert.equal(autoFibTimeframeAllowed(60, 1440, {}), false, "1h on a daily chart stays skipped");
});

test("Weekly AutoFib bars group daily candles exactly like the chart's weekly candles", () => {
  // Mon 2026-01-05 .. Fri 2026-01-16: two trading weeks.
  const days = [5, 6, 7, 8, 9, 12, 13, 14, 15, 16].map((d, i) => bar(etMidnight(2026, 1, d), 10 + i, 20 + i));
  const weekly = autoFibCalendarBars(days, 10080);
  assert.deepEqual(weekly.map((w) => w.time), aggregateChartBars(days, 10080).map((w) => w.time));
  assert.equal(weekly.length, 2);
  assert.equal(weekly[0].high, 24);
  assert.equal(weekly[1].low, 15);
  // A displayed weekly candle and every daily candle of that week map to the same key.
  assert.equal(autoFibCalendarKey(days[3].time, 10080), weekly[0].time);
  assert.equal(autoFibCalendarKey(weekly[1].time, 10080), weekly[1].time);
});

test("Monthly AutoFib key is the month's own candle, not the previous month", () => {
  const jan30 = etMidnight(2026, 1, 30);
  const feb2 = etMidnight(2026, 2, 2);
  const monthly = autoFibCalendarBars([bar(jan30, 1, 2), bar(feb2, 3, 4)], 43200);
  assert.equal(monthly.length, 2);
  assert.equal(autoFibCalendarKey(feb2, 43200), monthly[1].time);
  assert.equal(autoFibCalendarKey(monthly[1].time, 43200), monthly[1].time);
});

test("Big swing lookback is per chart timeframe: Daily 252, Weekly 104, Monthly 60, none intraday", () => {
  assert.equal(fibSwingLookback(1440, FIB_SWING_DEFAULTS), 252);
  assert.equal(fibSwingLookback(10080, FIB_SWING_DEFAULTS), 104);
  assert.equal(fibSwingLookback(43200, FIB_SWING_DEFAULTS), 60);
  assert.equal(fibSwingLookback(5, FIB_SWING_DEFAULTS), 0);
  assert.equal(fibSwingLookback(240, FIB_SWING_DEFAULTS), 0);
  assert.equal(fibSwingLookback(43200, { fibSwingMonthBars: 36 }), 36);
  assert.equal(fibSwingLookback(43200, { fibSwingMonthBars: 3 }), 10, "too few bars is raised to 10");
});

test("An up swing (low first, then high) measures the pullback down from the high", () => {
  const bars = [bar(1, 50, 60), bar(2, 14, 30), bar(3, 40, 120), bar(4, 150, 240.8), bar(5, 120, 200)];
  const swing = findFibSwing(bars, 60);
  assert.equal(swing.direction, "up");
  assert.equal(swing.low, 14);
  assert.equal(swing.high, 240.8);
  assert.equal(swing.startTime, 2);
  assert.equal(fibSwingPrice(swing, 0), 240.8);
  assert.equal(fibSwingPrice(swing, 1), 14);
  assert.equal(Number(fibSwingPrice(swing, 0.5).toFixed(2)), 127.4);
  assert.equal(Number(fibSwingPrice(swing, 0.618).toFixed(2)), 100.64);
});

test("A down swing (high first, then low) measures the bounce up from the low", () => {
  const bars = [bar(1, 90, 200), bar(2, 80, 150), bar(3, 100, 120)];
  const swing = findFibSwing(bars, 60);
  assert.equal(swing.direction, "down");
  assert.equal(swing.startTime, 1);
  assert.equal(fibSwingPrice(swing, 0), 80);
  assert.equal(fibSwingPrice(swing, 0.5), 140);
  assert.equal(Number(fibSwingPrice(swing, 0.618).toFixed(3)), 154.16);
});

test("Only the last N bars count toward the swing", () => {
  const bars = [bar(1, 1, 1000), bar(2, 50, 60), bar(3, 40, 70), bar(4, 45, 80)];
  const swing = findFibSwing(bars, 3);
  assert.equal(swing.high, 80);
  assert.equal(swing.low, 40);
});

test("A flat or empty window has no swing", () => {
  assert.equal(findFibSwing([], 60), null);
  assert.equal(findFibSwing([bar(1, 5, 5), bar(2, 5, 5)], 60), null);
});

test("Ratios print like TradingView", () => {
  assert.deepEqual([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].map(formatFibRatio),
    ["0%", "23.6%", "38.2%", "50%", "61.8%", "78.6%", "100%"]);
});

test("Big swing study: seven levels, shaded 50%-61.8% zone from the swing start to the last candle", () => {
  const bars = [bar(1, 50, 60), bar(2, 14, 30), bar(3, 40, 120), bar(4, 150, 240.8), bar(5, 120, 200)];
  const study = buildFibSwingStudy(bars, 43200, FIB_SWING_DEFAULTS);
  assert.equal(study.levels.length, 7);
  const fifty = study.levels.find((level) => level.ratio === 0.5);
  assert.equal(fifty.label, "Fib 50% (127.40)");
  assert.equal(study.levels.find((level) => level.ratio === 0.618).label, "Fib 61.8% (100.64)");
  assert.equal(fifty.price, 127.4);
  assert.equal(fifty.inZone, true);
  assert.equal(study.zone.color, FIB_SWING_DEFAULTS.fibSwingUpColor);
  assert.deepEqual(study.zone.fast.map((p) => p.time), [2, 3, 4, 5]);
  assert.ok(study.zone.fast.every((p) => p.value === 127.4));
  assert.ok(study.zone.slow.every((p) => p.value === 100.6376));
});

test("Big swing study: down swing is shaded in the down colour; key levels only when asked", () => {
  const bars = [bar(1, 90, 200), bar(2, 80, 150), bar(3, 100, 120)];
  const study = buildFibSwingStudy(bars, 1440, { ...FIB_SWING_DEFAULTS, fibSwingShowAllLevels: false });
  assert.equal(study.zone.color, FIB_SWING_DEFAULTS.fibSwingDownColor);
  assert.deepEqual(study.levels.map((level) => level.ratio), [0, 0.5, 0.618, 1]);
});

test("A penny stock's Fib tags keep four decimals", () => {
  const bars = [bar(1, 0.1, 0.2), bar(2, 0.3, 0.9)];
  const study = buildFibSwingStudy(bars, 1440, FIB_SWING_DEFAULTS);
  assert.equal(study.levels.find((level) => level.ratio === 0.5).label, "Fib 50% (0.5000)");
});

test("While the long history is still loading and the chart is short, no swing is drawn (no jump later)", () => {
  // e2e 2026-11-20: the slim first paint had 16 NVDA monthly candles, so the
  // swing ran 243 -> this month's low, then jumped once the 5-year tape landed.
  const bars = Array.from({ length: 16 }, (_, i) => bar(i + 1, 100 + i, 120 + i));
  assert.equal(buildFibSwingStudy(bars, 43200, FIB_SWING_DEFAULTS, { historyLoading: true }), null);
  assert.ok(buildFibSwingStudy(bars, 43200, FIB_SWING_DEFAULTS, { historyLoading: false }));
  const full = Array.from({ length: 70 }, (_, i) => bar(i + 1, 100 + i, 120 + i));
  assert.ok(buildFibSwingStudy(full, 43200, FIB_SWING_DEFAULTS, { historyLoading: true }), "enough candles already");
});

test("Big swing study is off on intraday charts", () => {
  const bars = [bar(1, 90, 200), bar(2, 80, 150)];
  assert.equal(buildFibSwingStudy(bars, 5, FIB_SWING_DEFAULTS), null);
});
