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
  buildFibSwingStudies,
  buildFibSwingStudy,
  fibSwingAxisLabels,
  migrateFibSwingOwnOff,
} from "../frontend/src/chartFibSwing.js";

const payload = JSON.parse(readFileSync(process.argv[2], "utf8"));
const out = {};
for (const [name, minutes] of [["D", 1440], ["W", 10080], ["M", 43200]]) {
  const bars = buildChartDisplayBars({
    liveBars: payload.bars,
    dailyBars: payload.dailyBars,
    aggregationMinutes: minutes,
  });
  // Each timeframe's own swing, and the default Monthly swing ("M Fib")
  // drawn on that chart from the same daily candles.
  const study = buildFibSwingStudy(bars, minutes, { ...FIB_SWING_DEFAULTS, fibSwingSource: "own" });
  const monthly = buildFibSwingStudy(bars, minutes, FIB_SWING_DEFAULTS, { dailyBars: payload.dailyBars });
  out[name] = {
    candles: bars.length,
    swing: study?.swing || null,
    levels: Object.fromEntries((study?.levels || []).map((level) => [String(level.ratio), level.price])),
    zoneStart: study?.zone?.fast?.[0]?.time ?? null,
    zoneEnd: study?.zone?.fast?.at(-1)?.time ?? null,
    lastCandle: bars.at(-1)?.time ?? null,
    mFib50: monthly?.levels?.find((level) => level.ratio === 0.5)?.label ?? null,
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
// The 5-minute chart with old saved settings that still tick the chart's own
// swing (his phone, 2026-11-20): after the one-time fix only the Monthly is
// drawn; ticked again, the own swing is tagged "5-day Fib".
{
  const five = buildChartDisplayBars({ liveBars: payload.bars, dailyBars: payload.dailyBars, aggregationMinutes: 5 });
  const saved = { ...FIB_SWING_DEFAULTS, fibSwingShowOwn: true };
  const migrated = { ...saved, ...migrateFibSwingOwnOff(saved) };
  const tags = (options) => fibSwingAxisLabels(buildFibSwingStudies(five, 5, options, { dailyBars: payload.dailyBars }))
    .map((label) => label.title);
  out.fiveMinute = {
    candles: five.length,
    sourcesAfterFix: buildFibSwingStudies(five, 5, migrated, { dailyBars: payload.dailyBars }).map((study) => study.source),
    tagsAfterFix: tags(migrated),
    tagsOwnTicked: tags({ ...migrated, fibSwingShowOwn: true }),
  };
}
process.stdout.write(JSON.stringify(out));
