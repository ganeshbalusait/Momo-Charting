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
  fibSwingDayBars: 252,
  fibSwingWeekBars: 104,
  fibSwingMonthBars: 60,
  fibSwingShowAllLevels: true,
  fibSwingUpColor: "#26a69a",
  fibSwingDownColor: "#ef5350",
  fibSwingLineColor: "#9aa4b2",
  fibSwingZoneOpacity: 22,
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

export function buildFibSwingStudy(chartBars, chartMinutes, options = {}, { historyLoading = false } = {}) {
  const lookback = fibSwingLookback(chartMinutes, options);
  if (!lookback) return null;
  // A short first-paint tape would draw a different swing that jumps once the
  // full history lands. Wait for it unless the chart already has enough candles.
  if (historyLoading && (Array.isArray(chartBars) ? chartBars.length : 0) < lookback) return null;
  const swing = findFibSwing(chartBars, lookback);
  if (!swing) return null;
  const zoneColor = swing.direction === "up"
    ? options.fibSwingUpColor || FIB_SWING_DEFAULTS.fibSwingUpColor
    : options.fibSwingDownColor || FIB_SWING_DEFAULTS.fibSwingDownColor;
  const lineColor = options.fibSwingLineColor || FIB_SWING_DEFAULTS.fibSwingLineColor;
  const ratios = options.fibSwingShowAllLevels === false ? FIB_SWING_KEY_RATIOS : FIB_SWING_RATIOS;
  const levels = ratios.map((ratio) => {
    const inZone = FIB_SWING_ZONE.includes(ratio);
    const price = round4(fibSwingPrice(swing, ratio));
    return {
      ratio,
      price,
      // Reads like TradingView's "50.00% (127.53)".
      label: `Fib ${formatFibRatio(ratio)} (${price.toFixed(Math.abs(price) < 1 ? 4 : 2)})`,
      inZone,
      color: inZone ? zoneColor : lineColor,
    };
  });
  const top = round4(fibSwingPrice(swing, 0.5));
  const bottom = round4(fibSwingPrice(swing, 0.618));
  const zoneBars = chartBars.filter((bar) => Number(bar?.time) >= swing.startTime);
  return {
    swing,
    lookback,
    levels,
    zone: {
      color: zoneColor,
      opacity: Math.max(0.05, Math.min(0.6, Number(options.fibSwingZoneOpacity ?? 22) / 100)),
      fast: zoneBars.map((bar) => ({ time: Number(bar.time), value: top })),
      slow: zoneBars.map((bar) => ({ time: Number(bar.time), value: bottom })),
    },
  };
}
