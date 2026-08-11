const TOS_CANDLE_COLORS = Object.freeze({
  cyan: "#00ffff",
  magenta: "#ff00ff",
  blue: "#0000ff",
  white: "#ffffff",
  // thinkScript Color.ORANGE is RGB (255, 200, 0), not CSS orange.
  orange: "#ffc800",
  // shared_CoolCandles_Momo_New24 defines these two colours with
  // CreateColor(13, 99, 102) and CreateColor(102, 0, 102), respectively.
  lessBear: "#0d6366",
  lessBull: "#660066",
});

function toNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : Number.NaN;
}

function simpleMovingAverage(values, period) {
  const length = Math.max(1, Number(period) || 1);
  const output = new Array(values.length).fill(Number.NaN);
  let sum = 0;
  for (let index = 0; index < values.length; index += 1) {
    const value = Number(values[index]);
    if (!Number.isFinite(value)) continue;
    sum += value;
    if (index >= length) sum -= Number(values[index - length]);
    if (index >= length - 1) output[index] = sum / length;
  }
  return output;
}

function populationStandardDeviation(values, period) {
  const length = Math.max(1, Number(period) || 1);
  return values.map((_, index) => {
    if (index < length - 1) return Number.NaN;
    const window = values.slice(index - length + 1, index + 1);
    if (!window.every(Number.isFinite)) return Number.NaN;
    const average = window.reduce((total, value) => total + value, 0) / length;
    return Math.sqrt(window.reduce((total, value) => total + (value - average) ** 2, 0) / length);
  });
}

// TOS's WILDERS MovingAverage begins with the period SMA, then smooths each
// subsequent value by 1 / period. Starting the smoothing on the first candle
// shifts DI/ADX enough to give different CoolCandles colours.
function wildersAverage(values, period) {
  const length = Math.max(1, Number(period) || 1);
  const output = new Array(values.length).fill(Number.NaN);
  let seedTotal = 0;
  let value = Number.NaN;
  for (let index = 0; index < values.length; index += 1) {
    const next = Number(values[index]);
    if (!Number.isFinite(next)) continue;
    if (index < length) seedTotal += next;
    if (index === length - 1) {
      value = seedTotal / length;
    } else if (index >= length) {
      value += (next - value) / length;
    }
    output[index] = value;
  }
  return output;
}

function exponentialAverage(values, period) {
  if (!values.length) return [];
  const multiplier = 2 / (Math.max(1, Number(period) || 1) + 1);
  let value = Number(values[0]);
  return values.map((next) => (value = Number(next) * multiplier + value * (1 - multiplier)));
}

function calculateTrueRanges(highs, lows, closes) {
  return highs.map((high, index) => {
    if (index === 0) return high - lows[index];
    return Math.max(
      high - lows[index],
      Math.abs(high - closes[index - 1]),
      Math.abs(lows[index] - closes[index - 1]),
    );
  });
}

function calculateInertia(values, period) {
  const length = Math.max(1, Number(period) || 1);
  const output = new Array(values.length).fill(Number.NaN);
  const sumX = (length * (length - 1)) / 2;
  const sumX2 = (length * (length - 1) * (2 * length - 1)) / 6;
  const denominator = length * sumX2 - sumX ** 2;
  for (let index = length - 1; index < values.length; index += 1) {
    const window = values.slice(index - length + 1, index + 1);
    if (!window.every(Number.isFinite)) continue;
    const sumY = window.reduce((sum, value) => sum + value, 0);
    const sumXY = window.reduce((sum, value, x) => sum + x * value, 0);
    const slope = denominator ? (length * sumXY - sumX * sumY) / denominator : 0;
    const intercept = (sumY - slope * sumX) / length;
    output[index] = slope * (length - 1) + intercept;
  }
  return output;
}

function calculateTtmSqueeze(bars) {
  const closes = bars.map((bar) => toNumber(bar?.close));
  const highs = bars.map((bar) => toNumber(bar?.high));
  const lows = bars.map((bar) => toNumber(bar?.low));
  const length = 20;
  const averages = simpleMovingAverage(closes, length);
  const deviations = populationStandardDeviation(closes, length);
  const ranges = simpleMovingAverage(calculateTrueRanges(highs, lows, closes), length);
  // The built-in TTM histogram uses ExpAverage(close, 20) in its K value;
  // using the Bollinger SMA here shifts both the momentum tone and release
  // direction away from TOS.
  const histogramAverage = exponentialAverage(closes, length);
  const rawHistogram = closes.map((close, index) => {
    if (index < length - 1) return Number.NaN;
    const window = bars.slice(index - length + 1, index + 1);
    const highest = Math.max(...window.map((bar) => toNumber(bar?.high)));
    const lowest = Math.min(...window.map((bar) => toNumber(bar?.low)));
    const midpoint = (highest + lowest) / 2;
    return close - ((midpoint + histogramAverage[index]) / 2);
  });
  const histogram = calculateInertia(rawHistogram, length);
  // Keep high/medium squeeze candles orange to preserve the source study's
  // compression signal. The supplied TOS visual reference does not use the
  // source's white high-squeeze override, so high also resolves to orange.
  const isSqueezed = (factor) => closes.map((_, index) => {
    if (!Number.isFinite(averages[index])
      || !Number.isFinite(deviations[index])
      || !Number.isFinite(ranges[index])) return false;
    const upperBollinger = averages[index] + 2 * deviations[index];
    const lowerBollinger = averages[index] - 2 * deviations[index];
    const upperKeltner = averages[index] + factor * ranges[index];
    const lowerKeltner = averages[index] - factor * ranges[index];
    return upperBollinger <= upperKeltner && lowerBollinger >= lowerKeltner;
  });
  return {
    high: isSqueezed(1),
    medium: isSqueezed(1.5),
    low: isSqueezed(2),
    histogram,
  };
}

function squeezeRelease(squeezeState) {
  return squeezeState.map((isSqueezed, index) => (
    index > 0 && !isSqueezed && squeezeState[index - 1]
  ));
}

function crossedAbove(values, thresholdOrValues) {
  const isSeries = Array.isArray(thresholdOrValues);
  return values.map((value, index) => {
    if (index === 0) return false;
    const threshold = isSeries ? thresholdOrValues[index] : thresholdOrValues;
    const previousThreshold = isSeries ? thresholdOrValues[index - 1] : thresholdOrValues;
    return Number.isFinite(value)
      && Number.isFinite(values[index - 1])
      && Number.isFinite(threshold)
      && Number.isFinite(previousThreshold)
      && value >= threshold
      && values[index - 1] < previousThreshold;
  });
}

function crossedBelow(values, thresholdOrValues) {
  const isSeries = Array.isArray(thresholdOrValues);
  return values.map((value, index) => {
    if (index === 0) return false;
    const threshold = isSeries ? thresholdOrValues[index] : thresholdOrValues;
    const previousThreshold = isSeries ? thresholdOrValues[index - 1] : thresholdOrValues;
    return Number.isFinite(value)
      && Number.isFinite(values[index - 1])
      && Number.isFinite(threshold)
      && Number.isFinite(previousThreshold)
      && value <= threshold
      && values[index - 1] > previousThreshold;
  });
}

/**
 * Literal translation of `shared_CoolCandles_Momo_New24`.
 *
 * Source priority is meaningful: a high/mid squeeze, release direction, DMI,
 * EMA 9/20 cross, and TTM histogram can all colour the same candle. Do not
 * substitute the candle's up/down direction for its final fallback colour.
 */
export function calculateTosCandlePaints(bars) {
  const source = Array.isArray(bars) ? bars : [];
  if (!source.length) return [];
  const highs = source.map((bar) => toNumber(bar?.high));
  const lows = source.map((bar) => toNumber(bar?.low));
  const closes = source.map((bar) => toNumber(bar?.close));
  const ranges = calculateTrueRanges(highs, lows, closes);
  const plusDm = source.map((_, index) => {
    if (index === 0) return 0;
    const highDifference = highs[index] - highs[index - 1];
    const lowDifference = lows[index - 1] - lows[index];
    return highDifference > lowDifference && highDifference > 0 ? highDifference : 0;
  });
  const minusDm = source.map((_, index) => {
    if (index === 0) return 0;
    const highDifference = highs[index] - highs[index - 1];
    const lowDifference = lows[index - 1] - lows[index];
    return lowDifference > highDifference && lowDifference > 0 ? lowDifference : 0;
  });
  const atr = wildersAverage(ranges, 10);
  const plusDi = wildersAverage(plusDm, 10).map((value, index) => (
    Number.isFinite(value) && atr[index] > 0 ? 100 * value / atr[index] : Number.NaN
  ));
  const minusDi = wildersAverage(minusDm, 10).map((value, index) => (
    Number.isFinite(value) && atr[index] > 0 ? 100 * value / atr[index] : Number.NaN
  ));
  const dx = plusDi.map((value, index) => {
    const denominator = value + minusDi[index];
    return Number.isFinite(denominator) && denominator > 0
      ? 100 * Math.abs(value - minusDi[index]) / denominator
      : 0;
  });
  const adx = wildersAverage(dx, 10);
  const ema9 = exponentialAverage(closes, 9);
  const ema20 = exponentialAverage(closes, 20);
  const squeeze = calculateTtmSqueeze(source);
  const highReleased = squeezeRelease(squeeze.high);
  const mediumReleased = squeezeRelease(squeeze.medium);
  const lowReleased = squeezeRelease(squeeze.low);
  const emaBull5m = crossedAbove(ema9, ema20);
  const emaBear5m = crossedBelow(ema9, ema20);
  const call = crossedAbove(plusDi, 25);
  const put = crossedAbove(minusDi, 25);

  return source.map((_, index) => {
    // The source spells this `ADX and DI >= 25`: its numeric ADX value is a
    // truthiness guard, not `ADX >= 25`. Preserve that literal behavior.
    const adxBull = Boolean(adx[index]) && plusDi[index] >= 25;
    const adxBear = Boolean(adx[index]) && minusDi[index] >= 25;
    const histogram = squeeze.histogram[index];
    const previousHistogram = squeeze.histogram[index - 1];
    const releaseIsRising = histogram > previousHistogram;
    let color;

    // Keep the actual thinkScript priority.  A high squeeze is a subset of
    // the medium squeeze, but the source intentionally paints it white before
    // considering the orange medium-squeeze branch.
    if (squeeze.high[index]) color = TOS_CANDLE_COLORS.white;
    else if (squeeze.medium[index]) color = TOS_CANDLE_COLORS.orange;
    else if (highReleased[index]) color = releaseIsRising ? TOS_CANDLE_COLORS.cyan : TOS_CANDLE_COLORS.magenta;
    else if (mediumReleased[index]) color = releaseIsRising ? TOS_CANDLE_COLORS.cyan : TOS_CANDLE_COLORS.magenta;
    else if (lowReleased[index]) color = releaseIsRising ? TOS_CANDLE_COLORS.cyan : TOS_CANDLE_COLORS.magenta;
    else if (squeeze.low[index] && adxBull) color = TOS_CANDLE_COLORS.cyan;
    else if (squeeze.low[index] && adxBear) color = TOS_CANDLE_COLORS.magenta;
    else if (emaBull5m[index]) color = TOS_CANDLE_COLORS.cyan;
    else if (emaBear5m[index]) color = TOS_CANDLE_COLORS.magenta;
    else if (squeeze.low[index] && call[index]) color = TOS_CANDLE_COLORS.cyan;
    else if (squeeze.low[index] && put[index]) color = TOS_CANDLE_COLORS.magenta;
    else if (adxBull) color = TOS_CANDLE_COLORS.cyan;
    else if (adxBear) color = TOS_CANDLE_COLORS.magenta;
    else if (call[index]) color = TOS_CANDLE_COLORS.cyan;
    else if (put[index]) color = TOS_CANDLE_COLORS.magenta;
    else if (histogram >= 0) color = histogram > previousHistogram
      ? TOS_CANDLE_COLORS.cyan
      : TOS_CANDLE_COLORS.lessBull;
    else color = histogram < previousHistogram
      ? TOS_CANDLE_COLORS.magenta
      : TOS_CANDLE_COLORS.lessBear;

    // The trader's TradingView conversion renders this study with a single
    // barcolor() — body, wick, and border all take the study colour, so a
    // white high-squeeze candle is solid white with no cyan/magenta outline.
    // Match that whole-candle look instead of the old separate border cascade.
    return { color, borderColor: color, wickColor: color };
  });
}
