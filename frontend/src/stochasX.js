// shared_StochasX_v20263 (his TOS study, 2026-10-01; script verbatim in
// artifacts/tos-studies/shared_StochasX_v20263.ts).
//
//   FastK  = (close - Lowest(low, K)) / (Highest(high, K) - Lowest(low, K)) * 100
//            (0 when the range is 0)                     - TOS StochasticFull
//   FullK  = MovingAverage(WEIGHTED, FastK, slowing = 1) = FastK
//   FullD  = MovingAverage(WEIGHTED, FullK, D)
//   FullD crosses below over_bought (90) -> LIGHT_RED wedge ABOVE the bar
//   FullD crosses above over_sold  (10)  -> LIME wedge BELOW the bar
//   only on the last `daysBack` (2) calendar days.
//
// Evaluated on every chart bar including the forming one, like TOS: a wedge
// on the live candle can appear and vanish until that candle closes.

export const STOCHASX_DEFAULTS = Object.freeze({
  overBought: 90,
  overSold: 10,
  kPeriod: 8,
  dPeriod: 8,
  daysBack: 1,
  // His TOS inputs: "limit to last n days" = No on both copies.
  limitToLastNDays: false,
  // LIGHT_RED sampled from his TOS screenshot: (255, 63, 0).
  overboughtColor: "#ff3f00",
  // UpSignal colour from his TOS plot settings: (191, 255, 0).
  oversoldColor: "#bfff00",
});

// His TOS chart runs TWO copies of the study (settings screenshots,
// 2026-10-01). Copy 2 (HULL, D 2) is the fast one that prints most wedges.
export const STOCHASX_COPIES = Object.freeze([
  Object.freeze({ kPeriod: 8, dPeriod: 8, averageType: "WEIGHTED" }),
  Object.freeze({ kPeriod: 8, dPeriod: 2, averageType: "HULL" }),
]);

// thinkScript WMA: weights 1..n (newest heaviest); NaN until n values exist.
export function weightedMovingAverage(values, length) {
  const n = Math.max(1, Math.floor(Number(length) || 1));
  const denominator = (n * (n + 1)) / 2;
  return values.map((_, index) => {
    if (index < n - 1) return NaN;
    let sum = 0;
    for (let weight = 1; weight <= n; weight += 1) {
      const value = values[index - n + weight];
      if (!Number.isFinite(value)) return NaN;
      sum += value * weight;
    }
    return sum / denominator;
  });
}

// TOS HullMovingAvg: WMA(2 * WMA(x, ceil(n / 2)) - WMA(x, n), round(sqrt(n))).
export function hullMovingAverage(values, length) {
  const n = Math.max(1, Math.floor(Number(length) || 1));
  const half = weightedMovingAverage(values, Math.ceil(n / 2));
  const full = weightedMovingAverage(values, n);
  return weightedMovingAverage(half.map((value, index) => 2 * value - full[index]), Math.max(1, Math.round(Math.sqrt(n))));
}

function movingAverage(type, values, length) {
  return String(type).toUpperCase() === "HULL"
    ? hullMovingAverage(values, length)
    : weightedMovingAverage(values, length);
}

const easternDateFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
});

const option = (options, key, fallback) => {
  const value = options?.[key];
  return value === undefined || value === null || value === "" ? fallback : value;
};

export function calculateStochasX(bars, options = {}) {
  const source = (Array.isArray(bars) ? bars : []).filter((bar) => (
    [bar?.time, bar?.high, bar?.low, bar?.close].every((value) => Number.isFinite(Number(value)))
  ));
  if (!source.length) return { wedges: [] };
  const overBought = Number(option(options, "stochasXOverBought", STOCHASX_DEFAULTS.overBought));
  const overSold = Number(option(options, "stochasXOverSold", STOCHASX_DEFAULTS.overSold));
  const kPeriod = Math.max(1, Math.floor(Number(option(options, "stochasXKPeriod", STOCHASX_DEFAULTS.kPeriod))));
  const dPeriod = Math.max(1, Math.floor(Number(option(options, "stochasXDPeriod", STOCHASX_DEFAULTS.dPeriod))));
  const daysBack = Math.max(1, Math.floor(Number(option(options, "stochasXDaysBack", STOCHASX_DEFAULTS.daysBack))));
  const limitDays = option(options, "stochasXLimitDays", STOCHASX_DEFAULTS.limitToLastNDays) === true;
  const overboughtColor = option(options, "stochasXOverboughtColor", STOCHASX_DEFAULTS.overboughtColor);
  const oversoldColor = option(options, "stochasXOversoldUpColor", STOCHASX_DEFAULTS.oversoldColor);

  const fastKFor = (period) => source.map((bar, index) => {
    const window = source.slice(Math.max(0, index - period + 1), index + 1);
    const lowest = Math.min(...window.map((item) => Number(item.low)));
    const highest = Math.max(...window.map((item) => Number(item.high)));
    const range = highest - lowest;
    return range !== 0 ? ((Number(bar.close) - lowest) / range) * 100 : 0;
  });
  // A single explicit K/D (tests, custom use) or his two TOS copies.
  // Copies come ONLY from an explicit stochasXCopies list (tests / future
  // settings). Profiles saved by the first build carry stochasXKPeriod /
  // stochasXDPeriod / stochasXLimitToLastNDays=true; those keys are ignored
  // so a saved profile cannot pin the chart to one slow copy.
  const copies = Array.isArray(options.stochasXCopies) && options.stochasXCopies.length
    ? options.stochasXCopies
    : STOCHASX_COPIES;
  const fullDs = copies.map((copy) => movingAverage(copy.averageType, fastKFor(copy.kPeriod), copy.dPeriod));

  // GetYYYYMMDD() day index; keep the last `daysBack` distinct dates.
  const dates = source.map((bar) => easternDateFormatter.format(new Date(Number(bar.time) * 1000)));
  const keptDates = limitDays ? new Set([...new Set(dates)].slice(-daysBack)) : null;

  const wedges = [];
  for (let index = 1; index < source.length; index += 1) {
    if (keptDates && !keptDates.has(dates[index])) continue;
    const time = Number(source[index].time);
    // TOS "crosses below/above": now beyond the level, previous bar not.
    // Both copies paint the same wedge glyph, so a bar shows one per side.
    const crossedDown = fullDs.some((fullD) => fullD[index] < overBought && fullD[index - 1] >= overBought);
    const crossedUp = fullDs.some((fullD) => fullD[index] > overSold && fullD[index - 1] <= overSold);
    if (crossedDown) wedges.push({ key: `stochasx-${time}-ob`, time, position: "above", color: overboughtColor });
    if (crossedUp) wedges.push({ key: `stochasx-${time}-os`, time, position: "below", color: oversoldColor });
  }
  return { wedges };
}
