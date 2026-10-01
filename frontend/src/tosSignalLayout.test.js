import assert from "node:assert/strict";
import test from "node:test";

import { layoutTosMtfChartSignals, snapTosMtfSignalsToBars } from "./tosSignalLayout.js";

test("preserves the source aggregation label and event time from ThinkScript", () => {
  const signals = [
    { time: 100, family: "9x20", direction: "CALL", timeframe: "1H", label: "C1H" },
    { time: 100, family: "4x8", direction: "CALL", timeframe: "2H", label: "CALL2H" },
    { time: 100, family: "4x8", direction: "CALL", timeframe: "4H", label: "C4H" },
  ];

  assert.deepEqual(
    layoutTosMtfChartSignals(signals).map(({ family, label, timeframe, time }) => `${family}:${label}:${timeframe}:${time}`),
    ["4x8:CALL2H:2H:100", "4x8:C4H:4H:100", "9x20:C1H:1H:100"],
  );
});

test("does not promote a crossover label to its confirmation timeframe", () => {
  const signals = [
    { time: 100, family: "4x8", direction: "CALL", timeframe: "30", label: "C30" },
    { time: 200, family: "4x8", direction: "CALL", timeframe: "1H", label: "CALL1H" },
    { time: 300, family: "4x8", direction: "CALL", timeframe: "2H", label: "C2H" },
  ];

  assert.deepEqual(
    layoutTosMtfChartSignals(signals).map(({ label, timeframe, time }) => ({ label, timeframe, time })),
    [
      { label: "C30", timeframe: "30", time: 100 },
      { label: "CALL1H", timeframe: "1H", time: 200 },
      { label: "C2H", timeframe: "2H", time: 300 },
    ],
  );
});

test("keeps yellow before cyan when bubbles share a candle", () => {
  const signals = [
    { time: 100, family: "9x20", direction: "CALL", timeframe: "2H", label: "C2H" },
    { time: 100, family: "4x8", direction: "CALL", timeframe: "4H", label: "C4H" },
  ];

  assert.deepEqual(
    layoutTosMtfChartSignals(signals).map(({ family, label, time }) => `${family}:${label}:${time}`),
    ["4x8:C4H:100", "9x20:C2H:100"],
  );
});

test("snaps a 5-minute signal stamp onto the visible chart's containing candle", () => {
  // 15-minute candles: 09:00, 09:15, 09:30 (epoch seconds, ET-agnostic)
  const bars = [{ time: 900 }, { time: 1800 }, { time: 2700 }];
  const signals = [
    { time: 1800, label: "CALL1H" },   // exact candle: untouched
    { time: 2400, label: "CALL2H" },   // 09:10-style stamp inside the 09:15 candle
    { time: 600, label: "P15" },       // older than the chart: left alone
  ];

  const snapped = snapTosMtfSignalsToBars(signals, bars);

  assert.equal(snapped[0], signals[0]);
  assert.equal(snapped[1].time, 1800);
  assert.equal(snapped[1].label, "CALL2H");
  assert.equal(snapped[2], signals[2]);
});

test("snapping tolerates empty inputs", () => {
  assert.deepEqual(snapTosMtfSignalsToBars(null, []), []);
  const signals = [{ time: 5 }];
  assert.deepEqual(snapTosMtfSignalsToBars(signals, null), signals);
});
