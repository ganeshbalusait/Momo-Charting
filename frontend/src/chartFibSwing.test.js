import assert from "node:assert/strict";
import test from "node:test";
import {
  AUTO_FIB_HIGHER_TIMEFRAMES,
  FIB_SWING_DEFAULTS,
  autoFibCalendarBars,
  autoFibCalendarKey,
  autoFibAxisLabels,
  autoFibTimeframeAllowed,
  buildFibSwingStudies,
  buildFibSwingStudy,
  FIB_SWING_OWN_OFF_VERSION,
  fibSwingOwnName,
  migrateFibSwingOwnOff,
  FIB_DWM_KEY_LEVELS_VERSION,
  migrateFibDwmKeyLevelOptions,
  migrateFibDwmKeyLevelSettings,
  fibSwingAxisLabels,
  fibSwingSourcesOn,
  findFibSwing,
  fibSwingLookback,
  fibSwingPrice,
  formatFibRatio,
} from "./chartFibSwing.js";
import { aggregateChartBars } from "./chartAggregation.js";

// The level formula with every level and the older long windows, set
// explicitly (the defaults are now 50% / 61.8% and 20-candle D / W, 2026-11-21).
const ALL_LEVELS = Object.freeze({ ...FIB_SWING_DEFAULTS, fibSwingShowAllLevels: true });
const LONG_WINDOWS = Object.freeze({ ...FIB_SWING_DEFAULTS, fibSwingDayBars: 252, fibSwingWeekBars: 104 });

// Eastern-midnight epoch for a calendar date (EST, UTC-5, good for Jan-Feb).
const etMidnight = (y, m, d) => Date.UTC(y, m - 1, d, 5) / 1000;
const bar = (time, low, high, close = (low + high) / 2) => ({ time, open: close, high, low, close, volume: 100 });

test("AutoFib gains Weekly and Monthly after Daily", () => {
  assert.deepEqual(AUTO_FIB_HIGHER_TIMEFRAMES.map(({ key, minutes, optionKey }) => [key, minutes, optionKey]), [
    ["week", 10080, "autoFibWeek"],
    ["month", 43200, "autoFibMonth"],
  ]);
});

// 2026-11-20 "keep Daily and weekly 'Autofib' on 5 min chart".
test("AutoFib: Weekly shows on a 5m chart; Monthly stays off intraday unless allowed", () => {
  assert.equal(autoFibTimeframeAllowed(10080, 43200, {}), false, "weekly on a monthly chart");
  assert.equal(autoFibTimeframeAllowed(43200, 43200, {}), true, "monthly on a monthly chart");
  assert.equal(autoFibTimeframeAllowed(10080, 1440, {}), true, "weekly on a daily chart");
  assert.equal(autoFibTimeframeAllowed(10080, 5, {}), true, "weekly on a 5m chart");
  assert.equal(autoFibTimeframeAllowed(43200, 5, {}), false, "monthly kept off a 5m chart by default");
  assert.equal(autoFibTimeframeAllowed(43200, 5, { autoFibHigherOnDailyUp: false }), true, "trader can allow it");
  assert.equal(autoFibTimeframeAllowed(1440, 5, {}), true, "Daily AutoFib on 5m is unchanged");
  assert.equal(autoFibTimeframeAllowed(60, 1440, {}), false, "1h on a daily chart stays skipped");
});

// 2026-11-21 "I want to see fib line 50% and 61.8% for D/W/M in 5 mins chart
// to all the timeframe charts": D, W and M on every timeframe.
test("every timeframe draws the D, W and M swings; each can be switched off", () => {
  for (const minutes of [5, 15, 60, 240, 1440, 10080, 43200]) {
    assert.deepEqual(fibSwingSourcesOn(FIB_SWING_DEFAULTS, minutes), ["M", "W", "D"], "chart " + minutes);
  }
  assert.deepEqual(fibSwingSourcesOn({ ...FIB_SWING_DEFAULTS, fibSwingShowW: false }, 5), ["M", "D"]);
  assert.deepEqual(fibSwingSourcesOn({ ...FIB_SWING_DEFAULTS, fibSwingShowM: false, fibSwingShowW: false, fibSwingShowD: false }, 5), []);
  assert.deepEqual(fibSwingSourcesOn({ ...FIB_SWING_DEFAULTS, fibSwingShowOwn: true }, 5), ["M", "W", "D", "own"]);
});

test("AutoFib tags name their timeframe: 'D AutoFib 50% (171.89)'", () => {
  const studies = [
    { key: "day", lines: { fib50: [{ time: 1, value: 171.9 }, { time: 2, value: 171.89 }], fibGold: [{ time: 2, value: 160.7096 }] }, goldRatio: 0.382 },
    { key: "week", lines: { fib50: [{ time: 2, value: 183.94 }], fibGold: [] } },
    { key: "5m", lines: { fib50: [], fibGold: [] } },
  ];
  const labels = autoFibAxisLabels(studies, { day: "#00ffff", week: "#ff00ff" });
  assert.deepEqual(labels.map((label) => label.title), [
    "D AutoFib 50% (171.89)",
    "D AutoFib 38.2% (160.71)",
    "W AutoFib 50% (183.94)",
  ]);
  assert.ok(labels.every((label) => label.key.startsWith("fib-swing-auto-")), "ranked with the Fib tags");
});

test("Weekly AutoFib bars group daily candles exactly like the chart's weekly candles", () => {
  // Mon 2026-01-05 .. Fri 2026-01-16: two trading weeks.
  const days = [5, 6, 7, 8, 9, 12, 13, 14, 15, 16].map((d, i) => bar(etMidnight(2026, 1, d), 10 + i, 20 + i));
  const weekly = autoFibCalendarBars(days, 10080);
  assert.deepEqual(weekly.map((w) => w.time), aggregateChartBars(days, 10080).map((w) => w.time));
  assert.equal(weekly.length, 2);
  assert.equal(weekly[0].high, 24);
  assert.equal(weekly[1].low, 15);
  // A displayed weekly candle and every daily candle of that week map to the same key.
  assert.equal(autoFibCalendarKey(days[3].time, 10080), weekly[0].time);
  assert.equal(autoFibCalendarKey(weekly[1].time, 10080), weekly[1].time);
});

test("Monthly AutoFib key is the month's own candle, not the previous month", () => {
  const jan30 = etMidnight(2026, 1, 30);
  const feb2 = etMidnight(2026, 2, 2);
  const monthly = autoFibCalendarBars([bar(jan30, 1, 2), bar(feb2, 3, 4)], 43200);
  assert.equal(monthly.length, 2);
  assert.equal(autoFibCalendarKey(feb2, 43200), monthly[1].time);
  assert.equal(autoFibCalendarKey(monthly[1].time, 43200), monthly[1].time);
});

test("swing lookback: Daily 20 and Weekly 20 (his TOS AutoFib window), Monthly 60, none intraday", () => {
  assert.equal(fibSwingLookback(1440, FIB_SWING_DEFAULTS), 20);
  assert.equal(fibSwingLookback(10080, FIB_SWING_DEFAULTS), 20);
  assert.equal(fibSwingLookback(1440, LONG_WINDOWS), 252, "a saved count still wins");
  assert.equal(fibSwingLookback(43200, FIB_SWING_DEFAULTS), 60);
  assert.equal(fibSwingLookback(5, FIB_SWING_DEFAULTS), 0);
  assert.equal(fibSwingLookback(240, FIB_SWING_DEFAULTS), 0);
  assert.equal(fibSwingLookback(43200, { fibSwingMonthBars: 36 }), 36);
  assert.equal(fibSwingLookback(43200, { fibSwingMonthBars: 3 }), 10, "too few bars is raised to 10");
});

test("An up swing (low first, then high) measures the pullback down from the high", () => {
  const bars = [bar(1, 50, 60), bar(2, 14, 30), bar(3, 40, 120), bar(4, 150, 240.8), bar(5, 120, 200)];
  const swing = findFibSwing(bars, 60);
  assert.equal(swing.direction, "up");
  assert.equal(swing.low, 14);
  assert.equal(swing.high, 240.8);
  assert.equal(swing.startTime, 2);
  assert.equal(fibSwingPrice(swing, 0), 240.8);
  assert.equal(fibSwingPrice(swing, 1), 14);
  assert.equal(Number(fibSwingPrice(swing, 0.5).toFixed(2)), 127.4);
  assert.equal(Number(fibSwingPrice(swing, 0.618).toFixed(2)), 100.64);
});

test("A down swing (high first, then low) measures the bounce up from the low", () => {
  const bars = [bar(1, 90, 200), bar(2, 80, 150), bar(3, 100, 120)];
  const swing = findFibSwing(bars, 60);
  assert.equal(swing.direction, "down");
  assert.equal(swing.startTime, 1);
  assert.equal(fibSwingPrice(swing, 0), 80);
  assert.equal(fibSwingPrice(swing, 0.5), 140);
  assert.equal(Number(fibSwingPrice(swing, 0.618).toFixed(3)), 154.16);
});

test("Only the last N bars count toward the swing", () => {
  const bars = [bar(1, 1, 1000), bar(2, 50, 60), bar(3, 40, 70), bar(4, 45, 80)];
  const swing = findFibSwing(bars, 3);
  assert.equal(swing.high, 80);
  assert.equal(swing.low, 40);
});

test("A flat or empty window has no swing", () => {
  assert.equal(findFibSwing([], 60), null);
  assert.equal(findFibSwing([bar(1, 5, 5), bar(2, 5, 5)], 60), null);
});

test("Ratios print like TradingView", () => {
  assert.deepEqual([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1].map(formatFibRatio),
    ["0%", "23.6%", "38.2%", "50%", "61.8%", "78.6%", "100%"]);
});

test("Big swing study: seven levels, shaded 50%-61.8% zone from the swing start to the last candle", () => {
  const bars = [bar(1, 50, 60), bar(2, 14, 30), bar(3, 40, 120), bar(4, 150, 240.8), bar(5, 120, 200)];
  const study = buildFibSwingStudy(bars, 43200, ALL_LEVELS);
  assert.equal(study.levels.length, 7);
  const fifty = study.levels.find((level) => level.ratio === 0.5);
  assert.equal(fifty.label, "M Fib 50% (127.40)");
  assert.equal(study.levels.find((level) => level.ratio === 0.618).label, "M Fib 61.8% (100.64)");
  assert.equal(fifty.price, 127.4);
  assert.equal(fifty.inZone, true);
  assert.equal(study.zone.color, FIB_SWING_DEFAULTS.fibSwingUpColor);
  assert.deepEqual(study.zone.fast.map((p) => p.time), [2, 3, 4, 5]);
  assert.ok(study.zone.fast.every((p) => p.value === 127.4));
  assert.ok(study.zone.slow.every((p) => p.value === 100.6376));
});

test("Big swing study: down swing is shaded in the down colour; by default only the 50% and 61.8% lines", () => {
  const bars = [bar(1, 90, 200), bar(2, 80, 150), bar(3, 100, 120)];
  const study = buildFibSwingStudy(bars, 1440, { ...FIB_SWING_DEFAULTS, fibSwingSource: "own" });
  assert.equal(study.zone.color, FIB_SWING_DEFAULTS.fibSwingDownColor);
  assert.deepEqual(study.levels.map((level) => level.ratio), [0.5, 0.618]);
  assert.equal(buildFibSwingStudy(bars, 1440, { ...ALL_LEVELS, fibSwingSource: "own" }).levels.length, 7, "the rest on request");
});

test("each level has its own bright TradingView colour so it stands out on a busy chart", () => {
  const bars = [bar(1, 50, 60), bar(2, 14, 30), bar(3, 40, 120), bar(4, 150, 240.8), bar(5, 120, 200)];
  const study = buildFibSwingStudy(bars, 43200, ALL_LEVELS);
  const colors = study.levels.map((level) => level.color);
  assert.equal(new Set(colors.slice(1, 6)).size, 5, "23.6 .. 78.6 each differ: " + colors.join(","));
  assert.equal(study.levels.find((level) => level.ratio === 0.5).color, "#4caf50");
  assert.equal(study.levels.find((level) => level.ratio === 0.618).color, "#089981");
  assert.ok(study.zone.opacity >= 0.25, "zone shading is visible over the EMA clouds");
});

test("A penny stock's Fib tags keep four decimals", () => {
  const bars = [bar(1, 0.1, 0.2), bar(2, 0.3, 0.9)];
  const study = buildFibSwingStudy(bars, 1440, { ...FIB_SWING_DEFAULTS, fibSwingSource: "own" });
  assert.equal(study.levels.find((level) => level.ratio === 0.5).label, "Fib 50% (0.5000)");
});

test("While the long history is still loading and the chart is short, no swing is drawn (no jump later)", () => {
  // e2e 2026-11-20: the slim first paint had 16 NVDA monthly candles, so the
  // swing ran 243 -> this month's low, then jumped once the 5-year tape landed.
  const bars = Array.from({ length: 16 }, (_, i) => bar(i + 1, 100 + i, 120 + i));
  assert.equal(buildFibSwingStudy(bars, 43200, FIB_SWING_DEFAULTS, { historyLoading: true }), null);
  assert.ok(buildFibSwingStudy(bars, 43200, FIB_SWING_DEFAULTS, { historyLoading: false }));
  const full = Array.from({ length: 70 }, (_, i) => bar(i + 1, 100 + i, 120 + i));
  assert.ok(buildFibSwingStudy(full, 43200, FIB_SWING_DEFAULTS, { historyLoading: true }), "enough candles already");
});

// 2026-11-20 "Fib I want to see in 5 mins chart also": intraday charts take
// the swing of their own candles over the last N trading days (default 5).
const etTime = (day, hhmm) => Date.parse(`2026-11-${String(day).padStart(2, "0")}T${hhmm}:00-05:00`) / 1000;

test("on a 5-minute chart the swing covers the last 5 trading days of its own candles", () => {
  const bars = [
    bar(etTime(10, "10:00"), 1, 500),          // 8 trading days back: outside
    bar(etTime(13, "10:00"), 100, 110),        // day 1 of the 5 (Fri 13th)
    bar(etTime(16, "10:00"), 90, 105),         // Mon
    bar(etTime(17, "10:00"), 95, 140),         // Tue: high
    bar(etTime(18, "09:35"), 80, 100),         // Wed: low (after the high -> a fall)
    bar(etTime(19, "10:00"), 95, 120),
    bar(etTime(20, "15:55"), 110, 125),        // Fri 20th
  ];
  // 13, 16, 17, 18, 19, 20 = six dates; five days back starts on the 16th.
  const days = { ...FIB_SWING_DEFAULTS, fibSwingSource: "own" };
  const study = buildFibSwingStudy(bars, 5, days);
  assert.ok(study, "intraday charts now draw it");
  assert.equal(study.swing.high, 140);
  assert.equal(study.swing.low, 80);
  assert.equal(study.swing.direction, "down");
  assert.equal(study.levels.find((level) => level.ratio === 0.5).price, 110);
  const two = buildFibSwingStudy(bars, 5, { ...days, fibSwingIntradayDays: 2 });
  assert.equal(two.swing.high, 125);
  assert.equal(two.swing.low, 95);
});

test("intraday Big swing can be switched off on its own; D/W/M keep it", () => {
  const bars = [bar(etTime(19, "10:00"), 95, 120), bar(etTime(20, "10:00"), 100, 130)];
  assert.equal(buildFibSwingStudy(bars, 5, { ...FIB_SWING_DEFAULTS, fibSwingIntraday: false }), null);
  assert.equal(fibSwingLookback(15, FIB_SWING_DEFAULTS), 0, "count lookback is for D/W/M only");
});

test("while an intraday tape is still loading, the chart's own swing uses the days already there", () => {
  const bars = [bar(etTime(19, "10:00"), 95, 120), bar(etTime(20, "10:00"), 100, 130)];
  const days = { ...FIB_SWING_DEFAULTS, fibSwingSource: "own" };
  const loading = buildFibSwingStudy(bars, 5, days, { historyLoading: true });
  assert.ok(loading, "a slow history load must not leave the minute chart with no Fib");
  assert.equal(loading.swing.high, 130);
  assert.equal(loading.swing.low, 95);
});

// 2026-11-20 "I want same fib this M" (his TradingView 5m chart with the
// monthly 50% / 61.8% zone): by default a minute/hour chart draws the MONTHLY
// swing, built from the daily candles.
function dailyTape() {
  // Jan 2021 .. Nov 2026 at Eastern midnight, one candle per day; low 14 in
  // Jan 2023, high 240.8 in Jun 2026 -> an up swing.
  const out = [];
  const start = Date.UTC(2021, 0, 4, 5) / 1000;
  for (let day = 0; day < 2150; day += 1) {
    const time = start + day * 86_400;
    const date = new Date(time * 1000);
    let low = 60;
    let high = 70;
    if (date.getUTCFullYear() === 2023 && date.getUTCMonth() === 0 && date.getUTCDate() === 10) low = 14;
    if (date.getUTCFullYear() === 2026 && date.getUTCMonth() === 5 && date.getUTCDate() === 10) high = 240.8;
    out.push({ time, open: 65, high, low, close: 65, volume: 1 });
  }
  return out;
}

test("a 5-minute chart draws the Monthly swing by default, the same levels as the Monthly chart", () => {
  const intraday = [bar(etTime(20, "10:00"), 128, 131), bar(etTime(20, "10:05"), 129, 130)];
  const study = buildFibSwingStudy(intraday, 5, FIB_SWING_DEFAULTS, { dailyBars: dailyTape() });
  assert.ok(study);
  assert.equal(study.source, "M");
  assert.equal(study.swing.low, 14);
  assert.equal(study.swing.high, 240.8);
  assert.equal(study.levels.find((level) => level.ratio === 0.5).price, 127.4);
  assert.equal(study.levels.find((level) => level.ratio === 0.618).price, 100.6376);
  // The shaded zone covers every 5-minute candle on the chart.
  assert.deepEqual(study.zone.fast.map((point) => point.time), intraday.map((b) => b.time));
});

test("the minute-chart swing can be Weekly or Daily instead", () => {
  const intraday = [bar(etTime(20, "10:00"), 128, 131)];
  const weekly = buildFibSwingStudy(intraday, 5, { ...LONG_WINDOWS, fibSwingSource: "W" }, { dailyBars: dailyTape() });
  assert.equal(weekly.source, "W");
  assert.equal(weekly.swing.high, 240.8, "June 2026 is inside 104 weeks");
  assert.equal(weekly.swing.low, 60, "January 2023 is not");
  const daily = buildFibSwingStudy(intraday, 5, { ...LONG_WINDOWS, fibSwingSource: "D" }, { dailyBars: dailyTape() });
  assert.equal(daily.source, "D");
  assert.equal(daily.swing.high, 240.8);
  // The default 20-candle window sees only the last 20 days.
  const recent = buildFibSwingStudy(intraday, 5, { ...FIB_SWING_DEFAULTS, fibSwingSource: "D" }, { dailyBars: dailyTape() });
  assert.equal(recent.lookback, 20);
  assert.equal(recent.swing.high, 70, "June 2026 is not in the last 20 days");
});

test("every timeframe draws the same Monthly levels, tagged 'M Fib'; 'own' tags plain 'Fib'", () => {
  const daily = dailyTape();
  const prices = [5, 60, 1440, 10080, 43200].map((minutes) => {
    const chart = minutes >= 1440 ? daily.slice(-30) : [bar(etTime(20, "10:00"), 128, 131)];
    const study = buildFibSwingStudy(chart, minutes, FIB_SWING_DEFAULTS, { dailyBars: daily });
    const fifty = study.levels.find((level) => level.ratio === 0.5);
    assert.equal(fifty.label, "M Fib 50% (127.40)", "chart " + minutes);
    return fifty.price;
  });
  assert.deepEqual(new Set(prices).size, 1);
  const own = buildFibSwingStudy(daily, 1440, { ...ALL_LEVELS, fibSwingSource: "own" }, { dailyBars: daily });
  assert.match(own.levels[0].label, /^Fib 0% \(/);
});

// 2026-11-20 "I want to see in chart fib M/W/D": all three at once.
test("M, W and D Fibs are all drawn on one chart by default, each tagged with its letter", () => {
  const daily = dailyTape();
  const intraday = [bar(etTime(20, "10:00"), 128, 131)];
  const studies = buildFibSwingStudies(daily.slice(-30), 1440, FIB_SWING_DEFAULTS, { dailyBars: daily });
  assert.deepEqual(studies.map((study) => study.source), ["M", "W", "D"]);
  // 2026-11-21: the 5-minute chart draws all three as well.
  assert.deepEqual(buildFibSwingStudies(intraday, 5, FIB_SWING_DEFAULTS, { dailyBars: daily }).map((study) => study.source), ["M", "W", "D"]);
  assert.deepEqual(fibSwingSourcesOn(FIB_SWING_DEFAULTS), ["M", "W", "D"]);
  for (const study of studies) {
    assert.ok(study.levels.every((level) => level.label.startsWith(study.source + " Fib ")), study.source);
  }
  const onlyM = buildFibSwingStudies(intraday, 5, { ...FIB_SWING_DEFAULTS, fibSwingShowW: false, fibSwingShowD: false }, { dailyBars: daily });
  assert.deepEqual(onlyM.map((study) => study.source), ["M"]);
  const own = buildFibSwingStudies(daily, 1440, { ...FIB_SWING_DEFAULTS, fibSwingShowM: false, fibSwingShowW: false, fibSwingShowD: false, fibSwingShowOwn: true }, { dailyBars: daily });
  assert.deepEqual(own.map((study) => study.source), ["own"]);
});

// 2026-11-20 (his NVDA W/D charts): "M Fib 0% (243.37)", "W Fib 0% (243.37)"
// and "D Fib 100% (243.37)" were three tags glued into one long line.
test("levels at the same price share one short tag", () => {
  const studies = [
    { source: "M", levels: [{ ratio: 0, price: 243.37, color: "#9598a1" }, { ratio: 0.5, price: 127.09, color: "#4caf50" }] },
    { source: "W", levels: [{ ratio: 0, price: 243.37, color: "#9598a1" }, { ratio: 0.382, price: 183.49, color: "#ff9800" }] },
    { source: "D", levels: [{ ratio: 1, price: 243.37, color: "#9598a1" }, { ratio: 0.5, price: 183.94, color: "#4caf50" }] },
  ];
  const labels = fibSwingAxisLabels(studies);
  const top = labels.find((label) => label.price === 243.37);
  assert.equal(top.title, "M/W Fib 0% · D Fib 100% (243.37)");
  assert.equal(labels.filter((label) => label.price === 243.37).length, 1, "one tag for the shared price");
  assert.equal(labels.find((label) => label.price === 127.09).title, "M Fib 50% (127.09)");
  // Close but not equal stay separate tags (the chart hides the one that does not fit).
  assert.ok(labels.find((label) => label.price === 183.49));
  assert.ok(labels.find((label) => label.price === 183.94));
  assert.equal(new Set(labels.map((label) => label.key)).size, labels.length, "keys are unique");
});

test("no daily candles yet: the Monthly swing waits instead of guessing", () => {
  const intraday = [bar(etTime(20, "10:00"), 128, 131)];
  assert.equal(buildFibSwingStudy(intraday, 5, FIB_SWING_DEFAULTS, { dailyBars: [] }), null);
});

// 2026-11-20 his NVDA 5m phone screenshot: "I see 2 fib but I don't which fib
// is that I see only M fib", then "Fix it". The unlabelled "Fib 50% (134.39)"
// was the chart's own 5-day swing, still ticked in his saved settings.
test("the chart's own swing on a minute chart is tagged '5-day Fib', never a bare 'Fib'", () => {
  const bars = [
    bar(etTime(16, "10:00"), 124.5, 144.28),
    bar(etTime(17, "10:00"), 130, 140),
    bar(etTime(20, "15:55"), 124.5, 131),
  ];
  const study = buildFibSwingStudy(bars, 5, { ...FIB_SWING_DEFAULTS, fibSwingSource: "own" });
  assert.equal(study.name, "5-day Fib");
  assert.ok(study.levels.every((level) => level.label.startsWith("5-day Fib ")), study.levels.map((l) => l.label).join(" | "));
  const tags = fibSwingAxisLabels([study]).map((label) => label.title);
  assert.ok(tags.every((tag) => /^5-day Fib \d/.test(tag)), tags.join(" | "));
  assert.equal(fibSwingOwnName(5, { fibSwingIntradayDays: 3 }), "3-day Fib");
  assert.equal(fibSwingOwnName(1440, FIB_SWING_DEFAULTS), "Fib", "D/W/M charts keep plain 'Fib'");
  // Sharing a price with the Monthly swing: both names stay readable.
  const shared = fibSwingAxisLabels([
    { source: "M", name: "M Fib", levels: [{ ratio: 0.5, price: 127.09, color: "#4caf50" }] },
    { source: "own", name: "5-day Fib", levels: [{ ratio: 0.618, price: 127.09, color: "#089981" }] },
  ]);
  assert.equal(shared[0].title, "M Fib 50% · 5-day Fib 61.8% (127.09)");
});

test("saved settings with the chart's own swing ticked are unticked once; his later choice wins", () => {
  const saved = { ...FIB_SWING_DEFAULTS, fibSwingShowOwn: true };
  const migrated = { ...saved, ...migrateFibSwingOwnOff(saved) };
  assert.equal(migrated.fibSwingShowOwn, false);
  assert.equal(migrated.fibSwingOwnOffVersion, FIB_SWING_OWN_OFF_VERSION);
  const reticked = { ...migrated, fibSwingShowOwn: true };
  assert.deepEqual(migrateFibSwingOwnOff(reticked), {}, "runs only once");
  assert.ok(!fibSwingSourcesOn(migrated, 5).includes("own"), "5m: no own swing");
});

// 2026-11-21 "I want to see fib line 50% and 61.8% for D/W/M in 5 mins chart
// to all the timeframe charts". D and W over the last 20 candles (his TOS
// AutoFib window), measured like TOS (up from the low); M over 60 months,
// measured like the TradingView chart he confirmed.
test("D, W and M each draw 50% and 61.8% on every chart; D matches TOS AutoFib, M matches TradingView", () => {
  const daily = dailyTape();
  // Last 20 days: high 100 fifteen days back, low 50 three days back (a fall).
  // TOS AutoFib: 50% = 75.00, 61.8% = 50 + 0.618 x 50 = 80.90.
  const recent = daily.map((row, index) => {
    const back = daily.length - index;
    if (back === 15) return { ...row, high: 100 };
    if (back === 3) return { ...row, low: 50 };
    return row;
  });
  for (const minutes of [5, 60, 1440, 10080, 43200]) {
    const chart = minutes >= 1440 ? recent.slice(-30) : [bar(etTime(20, "10:00"), 64, 66)];
    const tags = fibSwingAxisLabels(buildFibSwingStudies(chart, minutes, FIB_SWING_DEFAULTS, { dailyBars: recent }))
      .map((label) => label.title);
    const seen = (text) => tags.some((tag) => tag.includes(text));
    assert.ok(seen("D Fib 50%") && tags.some((tag) => /D Fib 50%.*\(75\.00\)|\(75\.00\)$/.test(tag)), minutes + ": " + tags.join(" | "));
    assert.ok(tags.includes("D Fib 61.8% (80.90)") || tags.some((tag) => /D Fib 61\.8%.*\(80\.90\)/.test(tag)), minutes + ": " + tags.join(" | "));
    assert.ok(tags.includes("M Fib 50% (127.40)"), minutes + ": " + tags.join(" | "));
    assert.ok(tags.includes("M Fib 61.8% (100.64)"), minutes + ": " + tags.join(" | "));
    assert.ok(tags.some((tag) => /(?:^|\/|· )W(?:\/[MD])* Fib 50%/.test(tag)), minutes + ": " + tags.join(" | "));
    assert.ok(tags.every((tag) => !/\b(?:0|23\.6|38\.2|78\.6|100)%/.test(tag)), "only 50% / 61.8%: " + tags.join(" | "));
  }
});

test("TOS measure: 61.8% counts up from the low whichever came first (AAPL daily 2026-11-20)", () => {
  assert.equal(fibSwingPrice({ high: 357.88, low: 326.96, direction: "up" }, 0.618, "tos").toFixed(2), "346.07");
  assert.equal(fibSwingPrice({ high: 357.88, low: 326.96, direction: "up" }, 0.618).toFixed(2), "338.77", "TradingView retracement");
});

test("saved settings move once to D/W/M 50% + 61.8% on every chart, AutoFib off; his later choices win", () => {
  const savedOptions = { ...FIB_SWING_DEFAULTS, fibSwingDayBars: 252, fibSwingWeekBars: 104, fibSwingShowAllLevels: true, fibSwingShowOwn: true };
  const options = { ...savedOptions, ...migrateFibDwmKeyLevelOptions(savedOptions) };
  assert.equal(options.fibSwingDayBars, 20);
  assert.equal(options.fibSwingWeekBars, 20);
  assert.equal(options.fibSwingMonthBars, 60);
  assert.equal(options.fibSwingShowAllLevels, false);
  assert.equal(options.fibSwingShowOwn, false);
  assert.equal(options.fibDwmKeyLevelsVersion, FIB_DWM_KEY_LEVELS_VERSION);
  assert.deepEqual(migrateFibDwmKeyLevelOptions({ ...options, fibSwingShowAllLevels: true }), {}, "runs once");
  const settings = { fibSwing: false, autoFibSingleTf: true };
  const moved = { ...settings, ...migrateFibDwmKeyLevelSettings(settings) };
  assert.equal(moved.fibSwing, true);
  assert.equal(moved.autoFibSingleTf, false, "AutoFib's D/W would repeat the same lines");
  assert.deepEqual(migrateFibDwmKeyLevelSettings({ ...moved, autoFibSingleTf: true }), {}, "runs once");
});
