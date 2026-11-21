// Runs the real frontend chart modules on a chart payload saved by
// tests/test_chart_fib_swing_api.py: builds the D / W / M candles the chart
// shows (buildChartDisplayBars) and the Big swing Fib and Weekly / Monthly
// AutoFib inputs from them. Prints JSON for the Python test to check.
import { readFileSync } from "node:fs";
import { layoutChartAxisLabels } from "../frontend/src/chartAxisLabels.js";
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
  // The level formula over the long windows, every level (set explicitly:
  // the defaults are 50% / 61.8% with 20-candle D / W since 2026-11-21).
  const study = buildFibSwingStudy(bars, minutes, {
    ...FIB_SWING_DEFAULTS, fibSwingSource: "own", fibSwingShowAllLevels: true, fibSwingDayBars: 252, fibSwingWeekBars: 104,
  });
  const monthly = buildFibSwingStudy(bars, minutes, FIB_SWING_DEFAULTS, { dailyBars: payload.dailyBars });
  out[name] = {
    candles: bars.length,
    swing: study?.swing || null,
    levels: Object.fromEntries((study?.levels || []).map((level) => [String(level.ratio), level.price])),
    zoneStart: study?.zone?.fast?.[0]?.time ?? null,
    zoneEnd: study?.zone?.fast?.at(-1)?.time ?? null,
    lastCandle: bars.at(-1)?.time ?? null,
    mFib50: monthly?.levels?.find((level) => level.ratio === 0.5)?.label ?? null,
    // What the chart shows by default: D / W / M, 50% and 61.8%.
    defaultTags: fibSwingAxisLabels(buildFibSwingStudies(bars, minutes, FIB_SWING_DEFAULTS, { dailyBars: payload.dailyBars }))
      .map((label) => label.title),
    // 2026-11-21 "I need label W fib 50%": laid out in a crowded 120px pane
    // beside a LIVE tag, every Fib tag still shows (beside, not hidden).
    crowdedTags: (() => {
      const labels = fibSwingAxisLabels(buildFibSwingStudies(bars, minutes, FIB_SWING_DEFAULTS, { dailyBars: payload.dailyBars }));
      const prices = labels.map((label) => label.price);
      const lo = Math.min(...prices); const hi = Math.max(...prices);
      const span = hi - lo || 1;
      // A 400px pane with the LIVE tag on the top level: nearby levels crowd.
      const topOf = (price) => 10 + (hi - price) / span * 380;
      const laid = layoutChartAxisLabels([
        { key: "live-price", title: "LIVE", price: hi, top: topOf(hi), preservePricePosition: true },
        ...labels.map((label) => ({ ...label, top: topOf(label.price), mergeWhenBlocked: true })),
      ], { paneHeight: 400 });
      return laid.filter((label) => String(label.key).startsWith("fib-swing-")).map((label) => label.title);
    })(),
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
