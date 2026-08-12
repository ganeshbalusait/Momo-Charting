import assert from "node:assert/strict";
import test from "node:test";

import { CLOUD_BAND_STUDIES, calculateTosCloudBandPoints } from "./cloudBandStudy.js";

test("includes the independent 5-minute TOS squeeze band", () => {
  assert.deepEqual(CLOUD_BAND_STUDIES[0], {
    key: "5m",
    label: "5m",
    minutes: 5,
    indicatorKey: "cloudBands5m",
  });
});

test("calculates both yellow channel sides on premarket 5-minute candles", () => {
  const definition = CLOUD_BAND_STUDIES[0];
  const premarket = [
    { time: 100, open: 100, high: 101, low: 99, close: 100 },
    { time: 400, open: 100, high: 103, low: 100, close: 102 },
    { time: 700, open: 102, high: 102, low: 100, close: 101 },
  ];
  const points = calculateTosCloudBandPoints(premarket, definition, 2, 0);

  assert.equal(points.length, 2);
  assert.deepEqual(points[0], {
    time: 400,
    endTime: 700,
    timeframeKey: "5m",
    timeframe: "5m",
    indicatorKey: "cloudBands5m",
    upperBand: 103,
    lowerBand: 99,
    upperHigh: 103.5,
    lowerHigh: 98.5,
    upperMid: 104.75,
    lowerMid: 97.25,
    upperLow: 106,
    lowerLow: 96,
  });
  assert.ok(points.every((point) => point.time < 34200), "premarket points must not be RTH-filtered");
});
