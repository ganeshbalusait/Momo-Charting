// Runs the real frontend chart modules on a chart payload saved by
// tests/test_chart_fib_swing_api.py: builds the D / W / M candles the chart
// shows (buildChartDisplayBars) and the Big swing Fib and Weekly / Monthly
// AutoFib inputs from them. Prints JSON for the Python test to check.
import { readFileSync } from "node:fs";
import { buildChartDisplayBars } from "../frontend/src/chartAggregation.js";
import {
  FIB_SWING_DEFAULTS,
  autoFibCalendarBars,
  autoFibCalendarKey,
  buildFibSwingStudy,
} from "../frontend/src/chartFibSwing.js";

const payload = JSON.parse(readFileSync(process.argv[2], "utf8"));
const out = {};
for (const [name, minutes] of [["D", 1440], ["W", 10080], ["M", 43200]]) {
  const bars = buildChartDisplayBars({
    liveBars: payload.bars,
    dailyBars: payload.dailyBars,
    aggregationMinutes: minutes,
  });
  const study = buildFibSwingStudy(bars, minutes, FIB_SWING_DEFAULTS);
  out[name] = {
    candles: bars.length,
    swing: study?.swing || null,
    levels: Object.fromEntries((study?.levels || []).map((level) => [String(level.ratio), level.price])),
    zoneStart: study?.zone?.fast?.[0]?.time ?? null,
    zoneEnd: study?.zone?.fast?.at(-1)?.time ?? null,
    lastCandle: bars.at(-1)?.time ?? null,
  };
  if (minutes > 1440) {
    const calendar = autoFibCalendarBars(payload.dailyBars, minutes);
    out[name].autoFibBars = calendar.length;
    out[name].autoFibKeysMatchCandles = bars.every((bar) => autoFibCalendarKey(bar.time, minutes) === bar.time);
    const window = calendar.slice(-20);
    out[name].autoFib20High = Math.max(...window.map((bar) => bar.high));
    out[name].autoFib20Low = Math.min(...window.map((bar) => bar.low));
  }
}
process.stdout.write(JSON.stringify(out));
