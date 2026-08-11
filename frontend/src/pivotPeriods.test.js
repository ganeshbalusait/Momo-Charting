import assert from "node:assert/strict";
import test from "node:test";

import {
  mergeDailyAndChartOhlcDays,
  previousCompletedPivotPeriod,
  rollingPivotSourcePeriod,
} from "./pivotPeriods.js";

const day = (date, open, high, low, close) => ({ date, open, high, low, close });

test("uses the previous completed calendar week when today's daily candle is missing", () => {
  const period = previousCompletedPivotPeriod([
    day("2026-07-20", 210, 216, 208, 214),
    day("2026-07-21", 214, 219, 212, 218),
    day("2026-07-22", 218, 221, 215, 217),
    day("2026-07-23", 217, 220, 211, 213),
    day("2026-07-24", 213, 218, 210, 216),
    day("2026-07-27", 217, 224, 216, 223),
    day("2026-07-28", 223, 228, 221, 226),
    day("2026-07-29", 226, 230, 224, 229),
    day("2026-07-30", 229, 232, 227, 231),
  ], "2026-07-31", "WEEK");

  assert.deepEqual(period, {
    key: "2026-07-20",
    open: 210,
    high: 221,
    low: 208,
    close: 216,
  });
});

test("shows Monday's weekly pivot from the immediately preceding week", () => {
  const period = previousCompletedPivotPeriod([
    day("2026-07-27", 100, 103, 99, 102),
    day("2026-07-28", 102, 105, 101, 104),
    day("2026-07-29", 104, 108, 103, 107),
    day("2026-07-30", 107, 109, 104, 105),
    day("2026-07-31", 105, 110, 104, 109),
  ], "2026-08-03", "WEEK");

  assert.equal(period?.key, "2026-07-27");
  assert.equal(period?.high, 110);
  assert.equal(period?.low, 99);
  assert.equal(period?.close, 109);
});

test("uses the latest completed day and month without requiring a current daily bar", () => {
  const days = [
    day("2026-06-30", 90, 95, 88, 94),
    day("2026-07-30", 100, 108, 98, 106),
  ];

  assert.equal(previousCompletedPivotPeriod(days, "2026-07-31", "DAY")?.close, 106);
  assert.equal(previousCompletedPivotPeriod(days, "2026-07-31", "MONTH")?.close, 94);
});

test("Schwab PivotPoints counts the weekly period backward from the chart day", () => {
  const days = [
    day("2026-07-23", 238, 238.35, 232.05, 233.66),
    day("2026-07-24", 233, 234.95, 231.34, 232.11),
    day("2026-07-27", 232, 235.89, 230.99, 231.39),
    day("2026-07-28", 231, 233.11, 228.10, 230.86),
    day("2026-07-29", 231, 232.82, 226.16, 226.65),
    day("2026-07-30", 232, 239.82, 231.06, 235.50),
  ];
  const friday = rollingPivotSourcePeriod(days, "2026-07-31", "WEEK");
  assert.equal(friday?.high, 239.82);
  assert.equal(friday?.low, 226.16);
  assert.equal(friday?.close, 235.50);

  const thursday = rollingPivotSourcePeriod(days, "2026-07-30", "WEEK");
  assert.equal(thursday?.high, 238.35);
  assert.equal(thursday?.low, 226.16);
  assert.equal(thursday?.close, 226.65);
});

test("rolling PivotPoints does not require the current date in daily history", () => {
  const source = rollingPivotSourcePeriod([
    day("2026-07-28", 100, 104, 99, 102),
    day("2026-07-29", 102, 106, 101, 105),
  ], "2026-07-30", "DAY");
  assert.deepEqual(source, { open: 102, high: 106, low: 101, close: 105 });
});

test("merges a missing live session so prior-day and prior-week levels remain available", () => {
  const merged = mergeDailyAndChartOhlcDays([
    day("2026-07-27", 260, 266, 258, 264),
    day("2026-07-28", 264, 268, 261, 267),
    day("2026-07-29", 267, 270, 263, 266),
    day("2026-07-30", 266, 271, 264, 269),
    day("2026-07-31", 269, 272.52, 259.10, 266.75),
  ], [
    day("2026-08-03", 264, 264.50, 261.70, 262.35),
    day("2026-08-03", 264, 265.10, 261.50, 263.20),
  ], (bar) => bar.date);

  assert.equal(merged.at(-1)?.date, "2026-08-03");
  assert.equal(merged.at(-1)?.high, 265.10);
  assert.equal(merged.at(-1)?.low, 261.50);
  assert.equal(merged.at(-2)?.high, 272.52);
});

test("keeps completed daily OHLC authoritative over extended chart bars", () => {
  const merged = mergeDailyAndChartOhlcDays([
    day("2026-07-31", 269, 272.52, 259.10, 266.75),
  ], [
    day("2026-07-31", 275, 280, 250, 255),
    day("2026-08-03", 264, 265, 261, 262),
  ], (bar) => bar.date);

  assert.deepEqual(merged[0], day("2026-07-31", 269, 272.52, 259.10, 266.75));
});
