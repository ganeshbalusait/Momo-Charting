// Fibonacci on Daily / Weekly / Monthly charts (Ganesh, 2026-11-20: "In chart
// can we add fib like this for Monthly/weekly/Daily?", with a TradingView NVDA
// monthly chart showing 50% (127.53) and 61.8% (100.79) and the zone between
// them shaded; he answered "Do both").
//
// 1. AutoFib (his TOS shared_AutoFib_SingleTF study) gains Weekly and Monthly:
//    the same 20-bar highest-high / lowest-low rule, on weekly or monthly
//    candles built from the daily seed with the chart's own calendar buckets.
// 2. "Big swing Fib": like a TradingView retracement drawn from the lowest low
//    to the highest high of the last N candles (Daily 252 = about a year,
//    Weekly 104 = two years, Monthly 60 = five years). Whichever came first
//    is the start of the swing. The 50% - 61.8% zone is shaded.
import { aggregateChartBars, chartAggregationBucketTime } from "./chartAggregation.js";

export const AUTO_FIB_HIGHER_TIMEFRAMES = Object.freeze([
  { key: "week", label: "Weekly", minutes: 10080, optionKey: "autoFibWeek" },
  { key: "month", label: "Monthly", minutes: 43200, optionKey: "autoFibMonth" },
]);

// Weekly / monthly AutoFib candles, grouped exactly like the chart's own
// W and M candles (Eastern-midnight calendar buckets).
export function autoFibCalendarBars(dailyBars, minutes) {
  return aggregateChartBars(Array.isArray(dailyBars) ? dailyBars : [], minutes);
}

// The weekly / monthly bucket a displayed candle belongs to.
export function autoFibCalendarKey(time, minutes) {
  return chartAggregationBucketTime(time, minutes);
}

// TOS skips a checked aggregation below the chart's own timeframe. Weekly and
// Monthly also stay off intraday charts unless the trader allows them, so his
// 5m / 15m working charts do not gain two more wide clouds.
export function autoFibTimeframeAllowed(definitionMinutes, chartMinutes, options = {}) {
  const definition = Number(definitionMinutes) || 0;
  const chart = Math.max(1, Number(chartMinutes) || 1);
  if (definition < chart) return false;
  if (definition > 1440 && chart < 1440 && options.autoFibHigherOnDailyUp !== false) return false;
  return true;
}

export const FIB_SWING_RATIOS = Object.freeze([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]);
const FIB_SWING_KEY_RATIOS = Object.freeze([0, 0.5, 0.618, 1]);
const FIB_SWING_ZONE = Object.freeze([0.5, 0.618]);

export const FIB_SWING_DEFAULTS = Object.freeze({
  // Intraday charts (5m, 15m, 1h...): the swing of the last N trading days of
  // the chart's own candles (2026-11-20, "Fib I want to see in 5 mins chart").
  fibSwingIntraday: true,
  // Which swing a minute/hour chart draws: "M" / "W" / "D" = that chart's
  // swing built from the daily candles (his TradingView 5m with the monthly
  // zone: "I want same fib this M"), "days" = its own last N trading days.
  fibSwingSource: "M",
  // "I want to see in chart fib M/W/D" (2026-11-20): which swings to draw,
  // all on one chart. The chart's own swing is off by default.
  fibSwingShowM: true,
  fibSwingShowW: true,
  fibSwingShowD: true,
  fibSwingShowOwn: false,
  fibSwingIntradayDays: 5,
  fibSwingDayBars: 252,
  fibSwingWeekBars: 104,
  fibSwingMonthBars: 60,
  fibSwingShowAllLevels: true,
  fibSwingUpColor: "#26a69a",
  fibSwingDownColor: "#ef5350",
  fibSwingLineColor: "#9aa4b2",
  fibSwingZoneOpacity: 28,
});

// TradingView's own retracement colours (2026-11-20, "I don't see the fib
// line": thin grey dashes vanished into the EMA clouds on his phone).
export const FIB_SWING_LEVEL_COLORS = Object.freeze({
  0: "#9598a1",
  0.236: "#f23645",
  0.382: "#ff9800",
  0.5: "#4caf50",
  0.618: "#089981",
  0.786: "#00bcd4",
  1: "#9598a1",
});

const LOOKBACK_OPTION_BY_MINUTES = Object.freeze({
  1440: ["fibSwingDayBars", 252],
  10080: ["fibSwingWeekBars", 104],
  43200: ["fibSwingMonthBars", 60],
});

export function fibSwingLookback(chartMinutes, options = {}) {
  const entry = LOOKBACK_OPTION_BY_MINUTES[Number(chartMinutes)];
  if (!entry) return 0;
  const [optionKey, fallback] = entry;
  const value = Math.round(Number(options?.[optionKey]) || fallback);
  return Math.max(10, Math.min(1000, value));
}

export function findFibSwing(bars, lookback) {
  const valid = (Array.isArray(bars) ? bars : []).filter((bar) => (
    Number.isFinite(Number(bar?.time))
    && Number.isFinite(Number(bar?.high))
    && Number.isFinite(Number(bar?.low))
  ));
  const window = valid.slice(-Math.max(1, Number(lookback) || 1));
  if (!window.length) return null;
  let highIndex = 0;
  let lowIndex = 0;
  window.forEach((bar, index) => {
    if (Number(bar.high) > Number(window[highIndex].high)) highIndex = index;
    if (Number(bar.low) < Number(window[lowIndex].low)) lowIndex = index;
  });
  const high = Number(window[highIndex].high);
  const low = Number(window[lowIndex].low);
  if (!(high > low)) return null;
  const highTime = Number(window[highIndex].time);
  const lowTime = Number(window[lowIndex].time);
  // Same candle made both: call it by that candle's colour.
  const direction = lowIndex < highIndex
    ? "up"
    : lowIndex > highIndex
      ? "down"
      : Number(window[highIndex].close) >= Number(window[highIndex].open) ? "up" : "down";
  return { high, low, highTime, lowTime, direction, startTime: Math.min(highTime, lowTime) };
}

// 0% sits at the END of the swing and 100% at its start, as TradingView draws
// a retracement: after a rise the levels count down from the high.
export function fibSwingPrice(swing, ratio) {
  const range = Number(swing.high) - Number(swing.low);
  return swing.direction === "up"
    ? Number(swing.high) - range * ratio
    : Number(swing.low) + range * ratio;
}

export function formatFibRatio(ratio) {
  return `${Number((Number(ratio) * 100).toFixed(1))}%`;
}

const round4 = (value) => Number(Number(value).toFixed(4));

// The ET trading date of an epoch-seconds candle.
const ET_DATE = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" });
function etDateKey(seconds) {
  return ET_DATE.format(new Date(Number(seconds) * 1000));
}

// How many of the newest candles make up the last `days` ET trading dates.
export function intradaySwingBarCount(bars, days) {
  const list = Array.isArray(bars) ? bars : [];
  const want = Math.max(1, Math.min(60, Math.round(Number(days) || 5)));
  const seen = new Set();
  let count = 0;
  for (let index = list.length - 1; index >= 0; index -= 1) {
    const time = Number(list[index]?.time);
    if (!Number.isFinite(time)) continue;
    const key = etDateKey(time);
    if (!seen.has(key)) {
      if (seen.size === want) break;
      seen.add(key);
    }
    count += 1;
  }
  return { count, days: seen.size, want };
}

const HIGHER_SOURCE_MINUTES = Object.freeze({ D: 1440, W: 10080, M: 43200 });

// Which swing a chart draws (2026-11-20, "In fib mention 'M fib' in 5 mins
// charts and all other timeframes"): "M" / "W" / "D" = that timeframe's swing,
// built from the daily candles, on EVERY chart (5m through M), tagged "M Fib";
// "own" = the chart's own candles (D/W/M: last 252 / 104 / 60; minute and hour
// charts: last N trading days), tagged plain "Fib". Default "M".
export function fibSwingSource(options = {}) {
  const value = options.fibSwingSource ?? options.fibSwingIntradaySource ?? "M";
  if (value === "days") return "own";
  return HIGHER_SOURCE_MINUTES[value] || value === "own" ? value : "M";
}

export function fibSwingSourcesOn(options = {}) {
  const list = ["M", "W", "D"].filter((source) => options["fibSwingShow" + source] !== false);
  if (options.fibSwingShowOwn === true) list.push("own");
  return list;
}

// One study per ticked swing, drawn together on the chart.
export function buildFibSwingStudies(chartBars, chartMinutes, options = {}, context = {}) {
  return fibSwingSourcesOn(options)
    .map((source) => buildFibSwingStudy(chartBars, chartMinutes, { ...options, fibSwingSource: source }, context))
    .filter(Boolean);
}

export function buildFibSwingStudy(chartBars, chartMinutes, options = {}, { historyLoading = false, dailyBars = null } = {}) {
  const chart = Array.isArray(chartBars) ? chartBars : [];
  const intraday = Number(chartMinutes) < 1440;
  if (intraday && options.fibSwingIntraday === false) return null;
  const source = fibSwingSource(options);
  let lookback;
  let swingBars = chart;
  const sourceMinutes = HIGHER_SOURCE_MINUTES[source];
  if (sourceMinutes) {
    const daily = Array.isArray(dailyBars) ? dailyBars : [];
    lookback = fibSwingLookback(sourceMinutes, options);
    if (daily.length) {
      swingBars = sourceMinutes === 1440 ? daily : aggregateChartBars(daily, sourceMinutes);
    } else if (Number(chartMinutes) === sourceMinutes) {
      swingBars = chart; // the chart IS that timeframe
    } else {
      return null; // no daily candles yet: wait rather than guess
    }
    if (historyLoading && swingBars.length < lookback) return null;
  } else if (intraday) {
    // The days already on the chart, even while older ones are still loading:
    // waiting left a minute chart with no Fib at all when history was slow
    // (seen in the e2e run, 2026-11-20). It widens when the rest arrives.
    lookback = intradaySwingBarCount(chart, options.fibSwingIntradayDays ?? 5).count;
  } else {
    lookback = fibSwingLookback(chartMinutes, options);
    // A short first-paint tape would draw a different swing that jumps once
    // the full history lands. Wait unless the chart already has enough candles.
    if (historyLoading && chart.length < lookback) return null;
  }
  if (!lookback) return null;
  const swing = findFibSwing(swingBars, lookback);
  if (!swing) return null;
  const zoneColor = swing.direction === "up"
    ? options.fibSwingUpColor || FIB_SWING_DEFAULTS.fibSwingUpColor
    : options.fibSwingDownColor || FIB_SWING_DEFAULTS.fibSwingDownColor;
  const lineColor = options.fibSwingLineColor || FIB_SWING_DEFAULTS.fibSwingLineColor;
  const ratios = options.fibSwingShowAllLevels === false ? FIB_SWING_KEY_RATIOS : FIB_SWING_RATIOS;
  const prefix = sourceMinutes ? source + " Fib" : "Fib";
  const levels = ratios.map((ratio) => {
    const inZone = FIB_SWING_ZONE.includes(ratio);
    const price = round4(fibSwingPrice(swing, ratio));
    return {
      ratio,
      price,
      // Reads like TradingView's "50.00% (127.53)".
      label: `${prefix} ${formatFibRatio(ratio)} (${price.toFixed(Math.abs(price) < 1 ? 4 : 2)})`,
      inZone,
      color: FIB_SWING_LEVEL_COLORS[ratio] || lineColor,
    };
  });
  const top = round4(fibSwingPrice(swing, 0.5));
  const bottom = round4(fibSwingPrice(swing, 0.618));
  const zoneBars = chart.filter((bar) => Number(bar?.time) >= swing.startTime);
  return {
    source: sourceMinutes ? source : "own",
    swing,
    lookback,
    levels,
    zone: {
      color: zoneColor,
      // Saved profiles from the first build carry 22; lift them to the new floor.
      opacity: Math.max(0.28, Math.min(0.6, Number(options.fibSwingZoneOpacity ?? 28) / 100)),
      fast: zoneBars.map((bar) => ({ time: Number(bar.time), value: top })),
      slow: zoneBars.map((bar) => ({ time: Number(bar.time), value: bottom })),
    },
  };
}
