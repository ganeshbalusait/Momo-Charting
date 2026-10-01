function rollingAverage(values, period) {
  const length = Math.max(1, Number(period) || 1);
  let total = 0;
  const window = [];
  return values.map((value) => {
    const numeric = Number(value || 0);
    window.push(numeric);
    total += numeric;
    if (window.length > length) total -= window.shift();
    return total / window.length;
  });
}

// ThinkScript StDev uses the population deviation of the rolling window.
function rollingPopulationStdDev(values, period) {
  const length = Math.max(1, Number(period) || 1);
  return values.map((_, index) => {
    const window = values.slice(Math.max(0, index - length + 1), index + 1);
    const average = window.reduce((sum, value) => sum + value, 0) / Math.max(window.length, 1);
    const variance = window.reduce((sum, value) => sum + (value - average) ** 2, 0) / Math.max(window.length, 1);
    return Math.sqrt(variance);
  });
}

// Direct translation of shared_RelVol_Candles_v324.
// - rawRelVol is the 50-bar volume z-score by default.
// - cyan is bullish (buying >= selling); magenta is bearish.
// - values appear only when rawRelVol reaches numDev.
// The script's default-on AddChart overlays border-highlight qualifying
// candles: RelVol >= numDev bars (borderhighlightRelVol) and every bar whose
// volume reaches its 50-bar average (borderhighlightVolAvg), cyan when buying
// dominates and magenta when selling dominates. The overlay recolours the
// candle outline/wick, so the CoolCandles body colour stays visible.
export function calculateRelativeVolumeCandleStudy(bars, options = {}) {
  const source = Array.isArray(bars) ? bars : [];
  const configuredLength = Number(options.relVolLength);
  const configuredThreshold = Number(options.relVolNumDev);
  const length = Math.max(2, Math.min(500, Number.isFinite(configuredLength) ? configuredLength : 50));
  const threshold = Math.max(0, Number.isFinite(configuredThreshold) ? configuredThreshold : 1.5);
  const highlightRelative = options.relVolHighlightRelative !== false;
  const highlightAverage = options.relVolHighlightAverage !== false;
  const volumes = source.map((bar) => Math.max(0, Number(bar?.volume || 0)));
  const averages = rollingAverage(volumes, length);
  const deviations = rollingPopulationStdDev(volumes, length);
  const candleStyles = [];
  const markers = [];

  source.forEach((bar, index) => {
    if (index < length - 1) {
      candleStyles.push({});
      return;
    }

    const volume = volumes[index];
    const deviation = deviations[index];
    const rawRelativeVolume = deviation > 0 ? (volume - averages[index]) / deviation : 0;
    const high = Number(bar?.high || 0);
    const low = Number(bar?.low || 0);
    const close = Number(bar?.close || 0);
    const range = high - low;
    const buying = range > 0 ? volume * (close - low) / range : volume / 2;
    const selling = range > 0 ? volume * (high - close) / range : volume / 2;
    const bullish = buying >= selling;
    const isHighRelativeVolume = rawRelativeVolume >= threshold;
    const isAboveAverageVolume = volume >= averages[index];
    const color = bullish
      ? (options.relVolBullColor || "#00ffff")
      : (options.relVolBearColor || "#ff00ff");

    const highlighted = (highlightRelative && isHighRelativeVolume)
      || (highlightAverage && isAboveAverageVolume);
    candleStyles.push(highlighted ? { borderColor: color, wickColor: color } : {});

    // RelVolAbove/RelVolBelow are not controlled by either border toggle.
    if (isHighRelativeVolume) {
      markers.push({
        key: `rel-vol-${bar.time}`,
        time: Number(bar.time),
        position: bullish ? "belowBar" : "aboveBar",
        color,
        text: rawRelativeVolume.toFixed(1),
        size: 0.85,
      });
    }
  });

  return { candleStyles, markers };
}
