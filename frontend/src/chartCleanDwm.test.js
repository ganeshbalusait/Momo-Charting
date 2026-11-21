import assert from "node:assert/strict";
import test from "node:test";

import { CLEAN_DWM_KEEP, CLEAN_DWM_VERSION, cleanDwmSettings, isDwmTimeframe, migrateCleanDwm } from "./chartCleanDwm.js";

const DEFAULTS = {
  indicatorSettingsVersion: "focused-chart-indicators-v18",
  oiLevels: true,
  ema9: true,
  ema21: true,
  ema50: true,
  sma200: true,
  vwap: true,
  clouds: true,
  autoFibSingleTf: true,
  fibSwing: true,
  chartPattern: true,
  ichimoku: true,
  signals: true,
  mtfAdxCloudsLower: true,
  pivotPoints: false,
  tosCandleColors: true,
  candleCyanMagentaOnly: true,
};

test("only Daily, Weekly and Monthly charts are cleaned", () => {
  assert.deepEqual(["D", "W", "M"].map(isDwmTimeframe), [true, true, true]);
  assert.deepEqual(["5m", "1h", "4h", "", null].map(isDwmTimeframe), [false, false, false, false, false]);
});

test("the clean set keeps candles, EMA 9/21/50, Big swing Fib and Chart pattern, and turns every other study off", () => {
  const clean = cleanDwmSettings(DEFAULTS);
  assert.equal(clean.cleanDwmVersion, CLEAN_DWM_VERSION);
  const on = Object.entries(clean).filter(([, value]) => value === true).map(([key]) => key).sort();
  assert.deepEqual(on, [...CLEAN_DWM_KEEP].sort());
  for (const key of ["oiLevels", "sma200", "vwap", "clouds", "autoFibSingleTf", "ichimoku", "signals", "mtfAdxCloudsLower", "pivotPoints"]) {
    assert.equal(clean[key], false, key);
  }
  assert.equal("indicatorSettingsVersion" in clean, false, "version strings are left alone");
});

test("a saved D/W/M profile is cleaned once; after he saves it, his choice wins", () => {
  const oldMonthly = { ...DEFAULTS, ichimoku: true };
  assert.equal(migrateCleanDwm(oldMonthly, "M", DEFAULTS).ichimoku, false);
  const savedSince = { ...oldMonthly, cleanDwmVersion: CLEAN_DWM_VERSION, ichimoku: true };
  assert.deepEqual(migrateCleanDwm(savedSince, "M", DEFAULTS), {});
  assert.deepEqual(migrateCleanDwm(oldMonthly, "5m", DEFAULTS), {}, "minute charts are never touched");
  assert.equal(migrateCleanDwm(null, "W", DEFAULTS).chartPattern, true, "no saved Weekly: clean");
});
