// The chart's REAL code over fixed tapes, so momx/chart_levels.py can be held
// to it value for value. Imports the chart's modules and lifts the rest out of
// App.jsx by source, so a change to a chart formula shows up as a failing test.
//
//   node tests/chart_levels_truth.mjs <out.json> <tape.json[.gz]> [more tapes]
//
// Read only: it touches nothing but its own output file.
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.join(here, "..", "frontend", "src");
const load = (file) => import(pathToFileURL(path.join(src, file)).href);

const aggregation = await load("chartAggregation.js");
const { buildChartDisplayBars, normalizeChartCandleBars, chartAggregationBucketTime } = aggregation;
const { mergeDailyAndChartOhlcDays, pivotPointsSourcePeriod } = await load("pivotPeriods.js");
const { dedupeDailyBars } = await load("dailyBars.js");
const { withoutDailySeedBadPrints } = await load("chartBadPrints.js");
const { buildWoodiesPivots, WOODIES_LEVELS } = await load("woodiesPivots.js");
const { layoutTosMtfChartSignals, snapTosMtfSignalsToBars } = await load("tosSignalLayout.js");
const { projectGaneshSignalsToChart } = await load("ganeshHigherTimeframeSignals.js");

const app = fs.readFileSync(path.join(src, "App.jsx"), "utf8").replace(/\r\n/g, "\n");
const lift = (re) => {
  const match = app.match(re);
  if (!match) throw new Error(`App.jsx no longer has ${re}`);
  return match[0];
};
const liftFunction = (name) => lift(new RegExp(`function ${name}\\([\\s\\S]*?\\n}`));
const liftConst = (name) => lift(new RegExp(`const ${name} = [\\s\\S]*?;\\n`));

const lifted = [
  liftConst("EASTERN_DATE_KEY_CACHE_LIMIT"),
  liftConst("easternDateKeyCaches"),
  liftFunction("easternDateKey"),
  liftConst("easternSessionPartCaches"),
  liftConst("EMPTY_SESSION_PARTS"),
  liftFunction("easternSessionParts"),
  liftFunction("calculateChartEma"),
  liftFunction("calculateChartSma"),
  liftFunction("calculateNumericEma"),
  liftFunction("calculateSessionVwap"),
  liftConst("previousOhlcPeriodKeyCache"),
  liftFunction("previousOhlcPeriodKey"),
  liftFunction("buildPreviousOhlcStudy"),
  liftFunction("pivotStudyPeriodKey"),
  liftFunction("buildPivotStudyContext"),
  liftFunction("standardPivotLevels"),
  lift(/const PIVOT_POINT_TIMEFRAMES = Object\.freeze\(\[[\s\S]*?\]\);\n/),
  liftConst("PIVOT_POINT_LEVELS"),
  lift(/const PIVOT_POINT_LEVEL_OPTIONS = Object\.freeze\(\{[\s\S]*?\}\);\n/),
  liftFunction("buildPivotPointsStudy"),
  lift(/const MTF_MA_LEVEL_DEFINITIONS = Object\.freeze\(\[[\s\S]*?\]\);\n/),
  lift(/const MTF_MA_LEVEL_TIMEFRAMES = Object\.freeze\(\[[\s\S]*?\]\);\n/),
  liftConst("MTF_MA_MIN_AVERAGE_SAMPLES"),
  liftFunction("buildMtfMaLevelsStudy"),
  lift(/const OI_CHART_TIMEFRAMES = Object\.freeze\(\[[\s\S]*?\]\);\n/),
].join("\n");

// The session levels and the signal filter live inside the chart component.
const sessionBody = lift(
  /if \(!chartBars\.length\) return holdStudyOutput\("sessionLevels", null\);[\s\S]*?allTimeHigh: Number\.isFinite\(allTimeHigh\) \? allTimeHigh : null,\n\s*}\);/,
);
const signalBody = lift(
  /const chartTimes = new Set\(chartBars\.map\(\(bar\) => Number\(bar\.time\)\)\);[\s\S]*?projectedSignalTimes\.add\(`\$\{signal\.time}-\$\{signal\.family}-\$\{signal\.label}`\)\)\);/,
);

const studies = new Function(
  "chartAggregationBucketTime", "mergeDailyAndChartOhlcDays", "pivotPointsSourcePeriod",
  "layoutTosMtfChartSignals", "snapTosMtfSignalsToBars",
  `${lifted}
  function sessionLevels(chartBars, dailyBars, easternDateFormatter, easternTimeFormatter) {
    const holdStudyOutput = (_, value) => value;
    ${sessionBody}
  }
  function chartSignalsFor(chartBars, selectedTimeframe, tosMtfSignals, tosMtfSignalsByTimeframe, indicatorOptions, indicatorSettings, easternDateFormatter, easternSessionFormatter) {
    ${signalBody}
    return chartSignals;
  }
  return { calculateChartEma, calculateChartSma, calculateSessionVwap, standardPivotLevels, buildPreviousOhlcStudy,
    buildPivotPointsStudy, buildMtfMaLevelsStudy, sessionLevels, chartSignalsFor, OI_CHART_TIMEFRAMES,
    PIVOT_POINT_LEVELS, PIVOT_POINT_LEVEL_OPTIONS };`,
)(chartAggregationBucketTime, mergeDailyAndChartOhlcDays, pivotPointsSourcePeriod,
  layoutTosMtfChartSignals, snapTosMtfSignalsToBars);

// The defaults the chart ships with, read from App.jsx where they are simple.
const defaultFrom = (key) => {
  const match = app.match(new RegExp(`\\n  ${key}: ([^,\\n]+),`));
  if (!match) throw new Error(`App.jsx has no default for ${key}`);
  return JSON.parse(match[1]);
};
const options = {
  mtfMaMovAvg1: defaultFrom("mtfMaMovAvg1"), mtfMaMovAvg2: defaultFrom("mtfMaMovAvg2"),
  mtfMaMovAvg3: defaultFrom("mtfMaMovAvg3"), mtfMaMovAvg4: defaultFrom("mtfMaMovAvg4"),
  mtfMaProximity: defaultFrom("mtfMaProximity"), mtfMaShowEma: true, mtfMaShowSma: true,
  mtfMaShowDaily: true, mtfMaShowWeekly: true, mtfMaShowMonthly: true,
  previousDayShowForDays: defaultFrom("previousDayShowForDays"),
  previousWeekShowForDays: defaultFrom("previousWeekShowForDays"),
  previousMonthShowForDays: defaultFrom("previousMonthShowForDays"),
  pivotPointsDay: true, pivotPointsWeek: true, pivotPointsMonth: true, pivotPointsShowOnlyToday: false,
  signalLimitRecentSessions: true, signalSessionsBack: defaultFrom("signalSessionsBack"),
  signalRthStartTime: defaultFrom("signalRthStartTime"), signalRthEndTime: defaultFrom("signalRthEndTime"),
};
Object.values(studies.PIVOT_POINT_LEVEL_OPTIONS).forEach((key) => { options[key] = true; });
const settings = { signals48: true, signals920: true };

const dateFormatter = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });
const timeFormatter = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });
const sessionFormatter = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });

const atLast = (series, lastTime) => {
  const point = (series || []).find((item) => Number(item.time) === lastTime);
  return point === undefined || point.value === undefined ? null : point.value;
};

function windowedGanesh(payload) {
  const tape = payload.ganeshHigherTimeframeSignals;
  const signals = Array.isArray(tape?.signals) ? tape.signals : [];
  const first = Number(payload.bars?.[0]?.time || 0);
  return first > 0 ? signals.filter((row) => Number(row?.time || 0) >= first) : signals;
}

// The chat's 21/50 EMA answer (2026-11-11): the chart's own calculateChartEma series, read with the same
// rules as momx/chart_levels.py ema_detail (newest cross, price crosses, slope over 5 bars, flat below 0.05%).
const SLOPE_BARS = 5;
const FLAT_FRACTION = 0.0005;
function lastCross(first, second) {
  for (let index = first.length - 1; index > 0; index -= 1) {
    const now = first[index] - second[index];
    const before = first[index - 1] - second[index - 1];
    if (now > 0 && before <= 0) return { index, dir: "up" };
    if (now < 0 && before >= 0) return { index, dir: "down" };
  }
  return null;
}
function slopeOf(series) {
  if (series.length <= SLOPE_BARS) return null;
  const change = series.at(-1) - series.at(-1 - SLOPE_BARS);
  if (Math.abs(change) <= Math.abs(series.at(-1)) * FLAT_FRACTION) return "flat";
  return change > 0 ? "rising" : "falling";
}
function emaDetail(chartBars) {
  if (chartBars.length < 2) return null;
  const closes = chartBars.map((bar) => Number(bar?.close || 0));
  const e21 = studies.calculateChartEma(chartBars, 21).map((point) => point.value);
  const e50 = studies.calculateChartEma(chartBars, 50).map((point) => point.value);
  const close = closes.at(-1);
  const stamp = (found) => (found ? { time: chartBars[found.index].time, dir: found.dir } : null);
  const side = (value) => ({ value, above: close > value, pct: value ? Number((((close - value) / value) * 100).toFixed(4)) : null });
  return {
    time: chartBars.at(-1).time, close, ema21: side(e21.at(-1)), ema50: side(e50.at(-1)), ema21Above50: e21.at(-1) > e50.at(-1),
    cross2150: stamp(lastCross(e21, e50)), priceCross21: stamp(lastCross(closes, e21)), priceCross50: stamp(lastCross(closes, e50)),
    slope21: slopeOf(e21), slope50: slopeOf(e50), bars: chartBars.length,
  };
}

function truthFor(payload, timeframe) {
  const live = normalizeChartCandleBars(payload.bars);
  const study = normalizeChartCandleBars(payload.studyBars);
  const fine = normalizeChartCandleBars(payload.fineStudyBars);
  // The chart panel repairs the daily seed from the minute tape (App.jsx dailyBars memo).
  const daily = withoutDailySeedBadPrints(dedupeDailyBars(normalizeChartCandleBars(payload.dailyBars)), live);
  const minutes = timeframe.minutes;
  const chartBars = buildChartDisplayBars({ studyBars: study, fineStudyBars: fine, liveBars: live, dailyBars: daily, aggregationMinutes: minutes, sourcesNormalized: true });
  if (!chartBars.length) return null;
  const last = chartBars.at(-1);
  const lastTime = Number(last.time);
  const latestDate = dateFormatter.format(new Date(lastTime * 1000));
  const session = studies.sessionLevels(chartBars, daily, dateFormatter, timeFormatter);
  const bounds = (slot) => (slot ? { high: slot.high, low: slot.low } : null);

  const previousStudy = studies.buildPreviousOhlcStudy(chartBars, daily, dateFormatter, options);
  const previous = {};
  for (const period of ["DAY", "WEEK", "MONTH"]) {
    const series = previousStudy[period];
    const row = { open: atLast(series.open, lastTime), high: atLast(series.high, lastTime), low: atLast(series.low, lastTime), close: atLast(series.close, lastTime) };
    previous[period] = row.high === null ? null : row;
  }
  const pivotStudy = studies.buildPivotPointsStudy(chartBars, daily, dateFormatter, options);
  const pivots = {};
  for (const period of ["DAY", "WEEK", "MONTH"]) {
    const row = Object.fromEntries(studies.PIVOT_POINT_LEVELS.map((level) => [level, atLast(pivotStudy[period][level], lastTime)]));
    pivots[period] = row.PP === null ? null : row;
  }
  let woodies = null;
  if (minutes === 1440) {
    const study4 = buildWoodiesPivots(chartBars, daily, {});
    const row = Object.fromEntries(WOODIES_LEVELS.map((level) => [level, atLast(study4[level], lastTime)]));
    woodies = row.PP === null ? null : row;
  }
  const mtfRow = ({ time, family, timeframe: tf, label, direction, compact, liveForming, isLiveSignal }, source) => ({
    time: Number(time), sourceTime: Number(source), family, timeframe: tf, label,
    direction: direction === "PUT" ? "PUT" : "CALL", compact: compact === true, forming: Boolean(liveForming || isLiveSignal),
  });
  // The stamp before the chart moved it onto a candle rides along on each signal.
  const stamped = (rows) => rows.map((signal) => (signal ? { ...signal, originalTime: signal.time } : signal));
  const tosSource = stamped(Array.isArray(payload.mtfSignals) ? payload.mtfSignals : []);
  const byTimeframe = Object.fromEntries(Object.entries(
    payload.mtfSignalsByTimeframe && typeof payload.mtfSignalsByTimeframe === "object" ? payload.mtfSignalsByTimeframe : {},
  ).map(([key, rows]) => [key, stamped(rows)]));
  const chartSignals = studies.chartSignalsFor(chartBars, timeframe, tosSource, byTimeframe, options, settings, dateFormatter, sessionFormatter);
  const higher = projectGaneshSignalsToChart(windowedGanesh(payload), chartBars, minutes);
  const tape = payload.ganeshHigherTimeframeSignals;
  return {
    levels: {
      timeframe: timeframe.key, minutes, n: chartBars.length, first: chartBars[0].time, lastTime, lastClose: last.close, latestDate,
      ema9: studies.calculateChartEma(chartBars, 9).at(-1).value,
      ema21: studies.calculateChartEma(chartBars, 21).at(-1).value,
      ema50: studies.calculateChartEma(chartBars, 50).at(-1).value,
      sma200: studies.calculateChartSma(chartBars, 200).at(-1)?.value ?? null,
      vwap: studies.calculateSessionVwap(chartBars, dateFormatter).at(-1).value,
      session: { pm: bounds(session.pm), day: bounds(session.day), week: bounds(session.week), allTimeHigh: session.allTimeHigh },
      previous, pivots, woodies,
      mtfMa: studies.buildMtfMaLevelsStudy(chartBars, daily, dateFormatter, options).map(({ key, value, label }) => ({ key, value, label })),
      emaDetail: emaDetail(chartBars),
    },
    signals: {
      mtf: chartSignals.map((signal) => mtfRow(signal, signal.originalTime)),
      higher: higher.map((signal) => ({ ...mtfRow(signal, signal.sourceTime), key: signal.key })),
      pending: { mtfSignalsPending: Boolean(payload.mtfSignalsPending), studiesPending: Boolean(payload.studiesPending), historyReady: tape?.historyReady === true },
    },
  };
}

const [outPath, ...tapes] = process.argv.slice(2);
const result = { timeframes: studies.OI_CHART_TIMEFRAMES, tapes: {} };
for (const file of tapes) {
  const raw = fs.readFileSync(file);
  const payload = JSON.parse(file.endsWith(".gz") ? zlib.gunzipSync(raw) : raw);
  const rows = {};
  for (const timeframe of studies.OI_CHART_TIMEFRAMES) rows[timeframe.key] = truthFor(payload, timeframe);
  result.tapes[path.basename(file)] = rows;
}
fs.writeFileSync(outPath, JSON.stringify(result));
