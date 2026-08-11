import assert from "node:assert/strict";
import test from "node:test";

import { layoutTosMtfChartSignals } from "./tosSignalLayout.js";

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
