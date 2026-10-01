import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { aggregateChartBars } from "./chartAggregation.js";

// calculateCloudMaxMtfStudy lives in App.jsx; lift it (and its two local
// helpers) out of the source so the live-forming rule can be tested in Node.
const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
const lift = (name) => {
  const start = appSource.indexOf(`\nfunction ${name}(`);
  const end = start < 0 ? -1 : appSource.indexOf("\n}\n", start + 1);
  const match = start < 0 || end < 0 ? null : [appSource.slice(start + 1, end + 3)];
  assert.ok(match, `could not lift ${name} out of App.jsx`);
  return match[0];
};
// eslint-disable-next-line no-new-func
const calculateCloudMaxMtfStudy = new Function(
  "aggregateChartBars",
  `${lift("calculateNumericEma")}\n${lift("calculateChartSma")}\n${lift("calculateCloudMaxMtfStudy")}\nreturn calculateCloudMaxMtfStudy;`,
)(aggregateChartBars);

// 2026-09-30 (his choice): CloudMAX arrows/bubbles must show on the FORMING
// candle the moment the cross happens, like TOS, not wait for the close.
test("CloudMAX 4x8 arrow prints on the forming 5m candle", () => {
  const start = 1_790_800_200; // a 5-minute boundary
  const bars = [];
  let price = 100;
  for (let minute = 0; minute < 60; minute += 1) {
    price -= 0.1; // steady fall: EMA 4 below EMA 8
    bars.push({ time: start + minute * 60, open: price, high: price + 0.05, low: price - 0.05, close: price, volume: 1000 });
  }
  // The newest 5m bucket has only two 1m candles so far - it is still forming -
  // and it rips higher, crossing EMA 4 back above EMA 8.
  const formingStart = start + 60 * 60;
  bars.push({ time: formingStart, open: price, high: price + 6, low: price, close: price + 6, volume: 5000 });
  bars.push({ time: formingStart + 60, open: price + 6, high: price + 6.2, low: price + 5.9, close: price + 6.1, volume: 4000 });

  const { signals } = calculateCloudMaxMtfStudy(bars, 5, {});
  const formingArrow = signals.find((signal) => (
    signal.time === formingStart && signal.shape === "arrowUp" && String(signal.key).includes("-48-")
  ));
  assert.ok(formingArrow, `expected a live 4x8 up arrow at the forming candle; got ${signals.map((s) => s.key).join(", ")}`);
});
