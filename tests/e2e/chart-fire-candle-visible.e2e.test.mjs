// Chart: a candle that carries several signals and squeeze fires stays readable
// (2026-11-19, NVDA 09:30 on the phone: "I cannot see properly candles").
// CALL5, CALL15, 🔥5 and 🔥15 each painted their own glow ON the candle, and
// four stacked cyan glows hid it. The highlights are drawn on the canvas, so
// this proves what the running page can: the chart's own drawing module (the
// one Vite serves to the app) keeps one highlight per candle with its frame
// outside the candle, and the NVDA 5m chart draws at phone and desktop width
// with no page errors.

import assert from "node:assert/strict";

import { PHONE, e2eTest } from "./harness.mjs";
import { waitForTape, withCharts } from "./charts-core.helpers.mjs";

async function checkRunningChart(app) {
  const { page } = app;
  await waitForTape(page, { symbol: "NVDA" });
  const result = await page.evaluate(async () => {
    const chart = await import("/src/tosNativeChartPrimitive.js");
    const merged = chart.mergeCandleHighlights([
      { time: 300, color: "#18f0ff", intensity: 1.3 },
      { time: 300, color: "#18f0ff", intensity: 1.3 },
      { time: 300, color: "#00e5ff", intensity: 1.45, fire: true },
      { time: 300, color: "#00e5ff", intensity: 1.45, fire: true },
    ]);
    const frame = chart.nativeHighlightFrame({ x: 40, top: 20, bottom: 80, width: 8 });
    // Four clouds over the same candle columns are shaded as one run, not four layers.
    const runs = chart.nativeCloudUnionRuns([
      { index: 0, bull: true, anchorY: 50, color: "#00ffff", opacity: 0.2 },
      { index: 1, bull: true, anchorY: 50, color: "#00ffff", opacity: 0.2 },
    ]);
    return {
      cloudRuns: runs.length,
      count: merged.length,
      fire: merged[0]?.fire,
      outside: frame.left < 36 && frame.left + frame.width > 44 && frame.top < 20 && frame.top + frame.height > 80,
    };
  });
  assert.deepEqual(result, { cloudRuns: 1, count: 1, fire: true, outside: true });
  assert.deepEqual(app.pageErrors, []);
}

e2eTest("NVDA 5m: a candle with several signals and fires gets one outline outside it (desktop)", async () => {
  await withCharts({ workspace: { symbols: ["NVDA"], timeframes: ["5m"] } }, checkRunningChart);
}, { timeout: 180_000 });

e2eTest("NVDA 5m: a candle with several signals and fires gets one outline outside it (phone)", async () => {
  await withCharts({ viewport: PHONE, workspace: { symbols: ["NVDA"], timeframes: ["5m"] } }, checkRunningChart);
}, { timeout: 180_000 });
