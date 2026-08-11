import test from "node:test";
import assert from "node:assert/strict";

import {
  holdStudyPointsOnChartGrid,
  snapStudyPointsToChartGrid,
  studyMatchesChartGrid,
} from "./chartGridProjection.js";
import { chartAggregationBucketTime, tosFourHourBucketTime } from "./chartAggregation.js";

// 2026-08-03 01:00 ET (05:00 UTC) is a TOS 4H bucket boundary.
const AUG_3_01_ET = Date.UTC(2026, 7, 3, 5, 0, 0) / 1000;
const FOUR_HOURS = 4 * 60 * 60;

function fourHourBars(count, start = AUG_3_01_ET) {
  return Array.from({ length: count }, (_, index) => ({ time: start + index * FOUR_HOURS }));
}

test("the daily bucket never lands on the TOS 4H grid", () => {
  // This is the whole reason the projection exists: Daily buckets are anchored
  // to Eastern midnight, the 4H grid to midnight Central.
  const easternMidnight = chartAggregationBucketTime(AUG_3_01_ET + 3600, 1440);
  const grid = new Set(fourHourBars(12).map(({ time }) => time));
  // 00:00 ET sits three hours inside the 21:00 ET bucket and is never a bar
  // time, so a Daily study plotted at its own timestamp adds a phantom column.
  assert.equal(grid.has(easternMidnight), false);
  assert.equal(easternMidnight, AUG_3_01_ET - 3600);
  assert.equal(tosFourHourBucketTime(easternMidnight), AUG_3_01_ET - FOUR_HOURS);
});

test("holds a daily value across the 4H bars that follow it", () => {
  const bars = fourHourBars(6);
  const daily = [
    { time: bars[1].time - 60, value: 10 },
    { time: bars[3].time - 60, value: 20 },
  ];
  const held = holdStudyPointsOnChartGrid(daily, bars);
  assert.deepEqual(held, [
    { time: bars[1].time, value: 10 },
    { time: bars[2].time, value: 10 },
    { time: bars[3].time, value: 20 },
    { time: bars[4].time, value: 20 },
    { time: bars[5].time, value: 20 },
  ]);
});

test("projected study times are always a subset of the chart bar times", () => {
  const bars = fourHourBars(10);
  const barTimes = new Set(bars.map(({ time }) => time));
  const offGrid = [
    { time: bars[2].time + 3600, value: 1 },
    { time: bars[5].time + 60, value: 2 },
  ];
  assert.equal(studyMatchesChartGrid(offGrid, bars), false);
  const held = holdStudyPointsOnChartGrid(offGrid, bars);
  const snapped = snapStudyPointsToChartGrid(offGrid, bars);
  assert.ok(held.length > 0);
  assert.ok(snapped.length > 0);
  held.forEach(({ time }) => assert.ok(barTimes.has(time), `held ${time} is off grid`));
  snapped.forEach(({ time }) => assert.ok(barTimes.has(time), `snapped ${time} is off grid`));
  assert.equal(studyMatchesChartGrid(held, bars), true);
  assert.equal(studyMatchesChartGrid(snapped, bars), true);
});

test("leaves bars before the first study point empty", () => {
  const bars = fourHourBars(4);
  const held = holdStudyPointsOnChartGrid([{ time: bars[2].time, value: 5 }], bars);
  assert.deepEqual(held.map(({ time }) => time), [bars[2].time, bars[3].time]);
});

test("extendPastLastPoint:false stops the plot where its source data stops", () => {
  const bars = fourHourBars(6);
  const chikou = [{ time: bars[1].time, value: 1 }, { time: bars[3].time, value: 2 }];
  const held = holdStudyPointsOnChartGrid(chikou, bars, { extendPastLastPoint: false });
  assert.deepEqual(held.map(({ time }) => time), [bars[1].time, bars[2].time, bars[3].time]);
});

test("snap keeps one point per chart bar and never invents a later signal", () => {
  const bars = fourHourBars(5);
  const crossings = [
    { time: bars[1].time + 60, value: 20 },
    { time: bars[1].time + 120, value: 25 },
    { time: bars[3].time + 60, value: 20 },
  ];
  const snapped = snapStudyPointsToChartGrid(crossings, bars);
  assert.deepEqual(snapped, [
    { time: bars[1].time, value: 25 },
    { time: bars[3].time, value: 20 },
  ]);
});

test("carries extra point payload such as per-point color", () => {
  const bars = fourHourBars(3);
  const held = holdStudyPointsOnChartGrid(
    [{ time: bars[0].time, value: 1, color: "#ff0000" }],
    bars,
  );
  assert.equal(held.length, 3);
  held.forEach((point) => assert.equal(point.color, "#ff0000"));
});

test("returns nothing when either side is empty", () => {
  assert.deepEqual(holdStudyPointsOnChartGrid([], fourHourBars(3)), []);
  assert.deepEqual(holdStudyPointsOnChartGrid([{ time: AUG_3_01_ET, value: 1 }], []), []);
  assert.deepEqual(snapStudyPointsToChartGrid(null, fourHourBars(3)), []);
});
