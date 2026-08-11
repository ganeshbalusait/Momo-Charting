const EMPTY_NATIVE_MODEL = Object.freeze({
  bars: [],
  timeframeMinutes: 5,
  sessions: [],
  sessionLines: [],
  cloudPairs: [],
  oneSidedClouds: [],
  cloudBands: [],
  cloudBandOptions: {},
  signals: [],
  candleHighlights: [],
  levelSegments: [],
});

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function colorWithOpacity(color, opacity) {
  const source = String(color || "").trim();
  const alpha = clamp(Number(opacity) || 0, 0, 1);
  const rgbMatch = source.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)(?:\s*,\s*([\d.]+))?\s*\)$/i);
  if (rgbMatch) {
    const sourceAlpha = rgbMatch[4] == null ? 1 : clamp(Number(rgbMatch[4]), 0, 1);
    return `rgba(${rgbMatch[1]}, ${rgbMatch[2]}, ${rgbMatch[3]}, ${alpha * sourceAlpha})`;
  }
  if (/^#[0-9a-f]{6}$/i.test(source)) {
    const red = Number.parseInt(source.slice(1, 3), 16);
    const green = Number.parseInt(source.slice(3, 5), 16);
    const blue = Number.parseInt(source.slice(5, 7), 16);
    return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
  }
  return source || `rgba(0, 0, 0, ${alpha})`;
}

export function nativeHighlightPulse(flashUntilMs, nowMs = Date.now()) {
  const until = Number(flashUntilMs);
  const now = Number(nowMs);
  if (!Number.isFinite(until) || !Number.isFinite(now) || until <= now) return 0;
  const wave = (Math.sin(now / 105) + 1) / 2;
  const fade = clamp((until - now) / 420, 0, 1);
  return (0.28 + wave * 0.72) * fade;
}

export function nativeFireMotion(nowMs = Date.now(), seed = 0) {
  const now = Number(nowMs);
  const phaseSeed = Number(seed) || 0;
  if (!Number.isFinite(now)) return { bend: 0, lift: 0, flicker: 0 };
  const fast = Math.sin(now / 74 + phaseSeed * 0.017);
  const slow = Math.sin(now / 131 + phaseSeed * 0.011 + 1.4);
  return {
    bend: fast * 0.9 + slow * 0.45,
    lift: Math.sin(now / 92 + phaseSeed * 0.013) * 0.65,
    flicker: clamp((fast + slow + 2) / 4, 0, 1),
  };
}

export function nativeFireGlyphPoints(highlight) {
  const top = Number(highlight?.top);
  const bottom = Number(highlight?.bottom);
  const x = Number(highlight?.x);
  const width = Math.max(4, Number(highlight?.width) || 4);
  if (![top, bottom, x].every(Number.isFinite) || bottom <= top) return [];
  const height = Math.max(12, bottom - top);
  const centerY = (top + bottom) / 2;
  const glyphTop = centerY - height / 2;
  const side = highlight?.bullish === false ? 1 : -1;
  const anchorX = x + side * (width / 2 + 2.5);
  return [
    [anchorX, glyphTop],
    [anchorX + side * 2.1, glyphTop + height * 0.18],
    [anchorX - side * 0.7, glyphTop + height * 0.37],
    [anchorX + side * 2.5, glyphTop + height * 0.56],
    [anchorX + side * 0.2, glyphTop + height * 0.76],
    [anchorX + side * 1.7, glyphTop + height],
  ];
}

function labelTextColor(background) {
  const source = String(background || "").trim();
  if (!/^#[0-9a-f]{6}$/i.test(source)) return "#061018";
  const red = Number.parseInt(source.slice(1, 3), 16);
  const green = Number.parseInt(source.slice(3, 5), 16);
  const blue = Number.parseInt(source.slice(5, 7), 16);
  const luminance = (red * 299 + green * 587 + blue * 114) / 1000;
  return luminance >= 142 ? "#061018" : "#f7fbff";
}

const NATIVE_SIGNAL_PANE_PADDING = 2;
const NATIVE_SIGNAL_POINTER_HEIGHT = 5;
const NATIVE_SIGNAL_FIRST_ROW_OFFSET = 8;
const NATIVE_SIGNAL_ROW_GAP = 4;

export function nativeSignalStackScope(signal) {
  const family = String(signal?.family || "tos-mtf");
  // The supplied Ganesh 4×8, 9×20, and MACD scripts can all add a bubble to
  // the exact same source candle and price. TOS keeps each AddChartBubble
  // visible as a vertical stack; grouping by family caused the app to paint
  // the later bubble over the earlier one (for example CALLW under MACD-D).
  return family.startsWith("ganesh") ? "ganesh" : "shared";
}

function nativeSignalBoxHeight(label) {
  return label?.compact === true ? 17 : 19;
}

function nativeSignalRequestedTop(label, stackOffset = null) {
  const boxHeight = nativeSignalBoxHeight(label);
  const verticalOffset = NATIVE_SIGNAL_FIRST_ROW_OFFSET
    + (stackOffset == null
      ? (Number(label?.stackIndex) || 0) * (boxHeight + NATIVE_SIGNAL_ROW_GAP)
      : Number(stackOffset) || 0);
  return label?.placement === "below"
    ? Number(label?.y) + verticalOffset + NATIVE_SIGNAL_POINTER_HEIGHT
    : Number(label?.y) - verticalOffset - NATIVE_SIGNAL_POINTER_HEIGHT - boxHeight;
}

/**
 * Lay out a complete bubble stack as one block.
 *
 * The old renderer clamped every row separately. When an anchor was close to
 * (or beyond) the pane floor, every historical MACD row therefore landed on
 * the same final pixel and only the last painted bubble could be seen. Moving
 * the complete stack preserves both its source-candle x coordinate and every
 * TOS row even when a saved/manual price range leaves little vertical room.
 */
export function nativeSignalLabelTops(labels, paneHeight) {
  const source = Array.isArray(labels) ? labels : [];
  const height = Math.max(1, Number(paneHeight) || 1);
  const tops = source.map(() => null);
  const groups = new Map();

  source.forEach((label, index) => {
    if (label?.family === "rvol") return;
    const boxHeight = nativeSignalBoxHeight(label);
    const stackKey = String(label?.stackKey || `${label?.x}-${label?.placement}-${label?.family || "shared"}`);
    if (!groups.has(stackKey)) groups.set(stackKey, []);
    groups.get(stackKey).push({ index, label, boxHeight });
  });

  groups.forEach((rows) => {
    rows.sort((left, right) => (
      (Number(left.label?.stackIndex) || 0) - (Number(right.label?.stackIndex) || 0)
    ));
    let stackOffset = 0;
    rows.forEach((row) => {
      row.requestedTop = nativeSignalRequestedTop(row.label, stackOffset);
      stackOffset += row.boxHeight + NATIVE_SIGNAL_ROW_GAP;
    });
    if (rows.some((row) => !Number.isFinite(row.requestedTop))) return;
    const groupTop = Math.min(...rows.map((row) => row.requestedTop));
    const groupBottom = Math.max(...rows.map((row) => row.requestedTop + row.boxHeight));
    const paneTop = NATIVE_SIGNAL_PANE_PADDING;
    const paneBottom = Math.max(paneTop, height - NATIVE_SIGNAL_PANE_PADDING);
    const groupFits = groupBottom - groupTop <= paneBottom - paneTop;
    let shift = 0;
    if (groupFits) {
      if (groupBottom > paneBottom) shift = paneBottom - groupBottom;
      if (groupTop + shift < paneTop) shift += paneTop - (groupTop + shift);
    } else {
      // Never collapse an impossible stack. Keep the row nearest its source
      // candle in view and let the canvas clip only the farthest rows.
      shift = rows[0]?.label?.placement === "below"
        ? paneTop - groupTop
        : paneBottom - groupBottom;
    }

    rows.forEach((row) => {
      tops[row.index] = row.requestedTop + shift;
    });
  });

  return tops;
}

/**
 * Pixel room requested from Lightweight Charts while automatic scaling is
 * active. Manual/saved ranges still use nativeSignalLabelTops as a fallback,
 * so changing symbols, timeframes, or layouts cannot hide historical rows.
 */
export function nativeSignalAutoscaleMargins(signals, visibleRange = {}) {
  const from = finiteNumber(visibleRange?.from);
  const to = finiteNumber(visibleRange?.to);
  const minimumTime = from == null || to == null ? -Infinity : Math.min(from, to);
  const maximumTime = from == null || to == null ? Infinity : Math.max(from, to);
  const groups = new Map();

  (Array.isArray(signals) ? signals : []).forEach((signal) => {
    const time = finiteNumber(signal?.time);
    if (time == null || time < minimumTime || time > maximumTime || signal?.family === "rvol") return;
    const placement = signal?.position === "belowBar" ? "below" : "above";
    const stackScope = nativeSignalStackScope(signal);
    const key = `${time}-${placement}-${stackScope}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(signal);
  });

  let above = 0;
  let below = 0;
  groups.forEach((rows, key) => {
    let extent = 0;
    let stackOffset = 0;
    rows.forEach((row, index) => {
      const boxHeight = nativeSignalBoxHeight(row);
      const verticalOffset = NATIVE_SIGNAL_FIRST_ROW_OFFSET + stackOffset;
      extent = Math.max(
        extent,
        verticalOffset + NATIVE_SIGNAL_POINTER_HEIGHT + boxHeight + NATIVE_SIGNAL_PANE_PADDING,
      );
      stackOffset += boxHeight + NATIVE_SIGNAL_ROW_GAP;
    });
    if (key.includes("-below-")) below = Math.max(below, extent);
    else above = Math.max(above, extent);
  });

  return { above, below };
}

export function expandNativeSignalPriceRange({
  low,
  high,
  signals,
  visibleRange = {},
  paneHeight,
}) {
  const initialLow = finiteNumber(low);
  const initialHigh = finiteNumber(high);
  if (initialLow == null || initialHigh == null || initialHigh < initialLow) {
    return { low, high };
  }
  const from = finiteNumber(visibleRange?.from);
  const to = finiteNumber(visibleRange?.to);
  const minimumTime = from == null || to == null ? -Infinity : Math.min(from, to);
  const maximumTime = from == null || to == null ? Infinity : Math.max(from, to);
  const visibleSignals = (Array.isArray(signals) ? signals : []).filter((signal) => {
    const time = finiteNumber(signal?.time);
    return time != null
      && time >= minimumTime
      && time <= maximumTime
      && signal?.family !== "rvol";
  });
  if (!visibleSignals.length) return { low: initialLow, high: initialHigh };

  let anchorLow = initialLow;
  let anchorHigh = initialHigh;
  visibleSignals.forEach((signal) => {
    const placement = signal?.position === "belowBar" ? "below" : "above";
    const fallback = placement === "below" ? initialLow : initialHigh;
    const price = finiteNumber(signal?.price) ?? fallback;
    anchorLow = Math.min(anchorLow, price);
    anchorHigh = Math.max(anchorHigh, price);
  });

  const margins = nativeSignalAutoscaleMargins(visibleSignals, visibleRange);
  const height = Math.max(80, Number(paneHeight) || 0);
  // The price scale already keeps 12% at each edge. Convert the remaining
  // bubble footprint into price units for the app's explicit visible range.
  const drawableHeight = Math.max(80, height * 0.76);
  const span = Math.max(anchorHigh - anchorLow, Math.abs(anchorHigh || 1) * 0.001, 0.02);
  const pricePerPixel = span / drawableHeight;
  const belowAnchor = visibleSignals.reduce((minimum, signal) => {
    if (signal?.position !== "belowBar") return minimum;
    return Math.min(minimum, finiteNumber(signal?.price) ?? initialLow);
  }, Infinity);
  const aboveAnchor = visibleSignals.reduce((maximum, signal) => {
    if (signal?.position === "belowBar") return maximum;
    return Math.max(maximum, finiteNumber(signal?.price) ?? initialHigh);
  }, -Infinity);

  return {
    low: Number.isFinite(belowAnchor)
      ? Math.min(anchorLow, belowAnchor - margins.below * pricePerPixel)
      : anchorLow,
    high: Number.isFinite(aboveAnchor)
      ? Math.max(anchorHigh, aboveAnchor + margins.above * pricePerPixel)
      : anchorHigh,
  };
}

function normalizedPoints(points) {
  const pointsByTime = new Map();
  (Array.isArray(points) ? points : []).forEach((point) => {
    const time = finiteNumber(point?.time);
    const value = finiteNumber(point?.value);
    if (time != null && value != null) pointsByTime.set(time, { time, value });
  });
  return [...pointsByTime.values()].sort((left, right) => left.time - right.time);
}

export function alignNativeCloudPair(fastPoints, slowPoints) {
  const slowByTime = new Map(normalizedPoints(slowPoints).map((point) => [point.time, point.value]));
  return normalizedPoints(fastPoints).flatMap((point) => (
    slowByTime.has(point.time)
      ? [{ time: point.time, fast: point.value, slow: slowByTime.get(point.time) }]
      : []
  ));
}

function cloudBandOption(options, indicatorKey, suffix, fallback) {
  const instanceValue = options?.[`${indicatorKey}${suffix}`];
  if (instanceValue !== undefined) return instanceValue;
  const legacyValue = options?.[`cloudBands${suffix}`];
  return legacyValue !== undefined ? legacyValue : fallback;
}

export function nativeCloudBandShapes(points, options = {}) {
  const shapes = [];
  const addFill = (point, key, firstValue, secondValue, color, opacity) => {
    const first = finiteNumber(firstValue);
    const second = finiteNumber(secondValue);
    if (first == null || second == null) return;
    shapes.push({
      key: `${point.timeframeKey}-${key}-${point.time}`,
      kind: "fill",
      startTime: Number(point.time),
      endTime: Number(point.endTime),
      first,
      second,
      color,
      opacity,
    });
  };
  const addLine = (point, key, value, color) => {
    const price = finiteNumber(value);
    if (price == null) return;
    shapes.push({
      key: `${point.timeframeKey}-${key}-${point.time}`,
      kind: "line",
      startTime: Number(point.time),
      endTime: Number(point.endTime),
      price,
      color,
      opacity: 0.9,
      lineWidth: 1.25,
    });
  };
  (Array.isArray(points) ? points : []).forEach((point) => {
    if (!Number.isFinite(Number(point?.time)) || !Number.isFinite(Number(point?.endTime))) return;
    const option = (suffix, fallback) => cloudBandOption(options, point.indicatorKey, suffix, fallback);
    const highColor = option("HighColor", "#ffffff");
    const midColor = option("MidColor", "#fff200");
    const lowColor = option("LowColor", "#fff200");
    const opacity = clamp(Number(option("Opacity", 22)) / 100, 0.05, 0.8);
    if (option("ShowLow", true) !== false && point.upperLow > point.upperBand) {
      addFill(point, "low-upper", point.upperLow, point.upperBand, lowColor, opacity);
    }
    if (option("ShowLow", true) !== false && point.lowerBand > point.lowerLow) {
      addFill(point, "low-lower", point.lowerBand, point.lowerLow, lowColor, opacity);
    }
    if (option("ShowMid", true) !== false && point.upperMid > point.upperBand) {
      addFill(point, "mid-upper", point.upperMid, point.upperBand, midColor, opacity);
    }
    if (option("ShowMid", true) !== false && point.lowerBand > point.lowerMid) {
      addFill(point, "mid-lower", point.lowerBand, point.lowerMid, midColor, opacity);
    }
    // TOS default-draws the white high-squeeze cloud (showCloudSqueezeHigh =
    // Yes); an earlier white-suppression guard here hid it entirely.
    if (option("ShowHigh", true) !== false && point.upperHigh > point.upperBand) {
      addFill(point, "high-upper", point.upperHigh, point.upperBand, highColor, opacity);
    }
    if (option("ShowHigh", true) !== false && point.lowerBand > point.lowerHigh) {
      addFill(point, "high-lower", point.lowerBand, point.lowerHigh, highColor, opacity);
    }
    // Line colours from the script: Bollinger Color.BLUE, Keltner mid
    // Color.ORANGE (255,200,0), Keltner low Color.VIOLET.
    if (option("ShowBollingerLines", false) === true) {
      addLine(point, "bb-upper", point.upperBand, "#0000ff");
      addLine(point, "bb-lower", point.lowerBand, "#0000ff");
    }
    if (option("ShowKeltnerChannels", false) === true) {
      [
        ["kh-upper", point.upperHigh, highColor],
        ["kh-lower", point.lowerHigh, highColor],
        ["km-upper", point.upperMid, "#ffc800"],
        ["km-lower", point.lowerMid, "#ffc800"],
        ["kl-upper", point.upperLow, "#ee82ee"],
        ["kl-lower", point.lowerLow, "#ee82ee"],
      ].forEach(([key, value, color]) => addLine(point, key, value, color));
    }
  });
  return shapes;
}

function normalizeModel(model = {}) {
  const bars = (Array.isArray(model.bars) ? model.bars : []).flatMap((bar) => {
    const time = finiteNumber(bar?.time);
    const high = finiteNumber(bar?.high);
    const low = finiteNumber(bar?.low);
    return time == null || high == null || low == null ? [] : [{ ...bar, time, high, low }];
  });
  return {
    ...EMPTY_NATIVE_MODEL,
    ...model,
    bars,
    timeframeMinutes: Math.max(1, Number(model.timeframeMinutes) || 5),
    sessions: Array.isArray(model.sessions) ? model.sessions : [],
    sessionLines: Array.isArray(model.sessionLines) ? model.sessionLines : [],
    cloudPairs: (Array.isArray(model.cloudPairs) ? model.cloudPairs : []).map((pair) => ({
      ...pair,
      aligned: alignNativeCloudPair(pair.fast, pair.slow),
    })),
    oneSidedClouds: Array.isArray(model.oneSidedClouds) ? model.oneSidedClouds : [],
    cloudBands: nativeCloudBandShapes(model.cloudBands, model.cloudBandOptions),
    signals: Array.isArray(model.signals) ? model.signals : [],
    candleHighlights: (Array.isArray(model.candleHighlights) ? model.candleHighlights : [])
      .flatMap((highlight) => {
        const time = finiteNumber(highlight?.time);
        return time == null ? [] : [{ ...highlight, time }];
      }),
    levelSegments: (Array.isArray(model.levelSegments) ? model.levelSegments : [])
      .flatMap((segment) => {
        const price = finiteNumber(segment?.price);
        return price == null ? [] : [{ ...segment, price }];
      }),
  };
}

function lowerBoundBars(bars, target) {
  let low = 0;
  let high = bars.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (Number(bars[middle]?.time || 0) < target) low = middle + 1;
    else high = middle;
  }
  return low;
}

function coordinateProjector(model, chart, series) {
  const timeScale = chart.timeScale();
  const bars = model.bars;
  const firstTime = Number(bars[0]?.time || 0);
  const latestTime = Number(bars.at(-1)?.time || 0);
  const firstX = firstTime ? timeScale.timeToCoordinate(firstTime) : null;
  const latestX = latestTime ? timeScale.timeToCoordinate(latestTime) : null;
  const secondsPerBar = model.timeframeMinutes * 60;
  const barSpacing = Math.max(1, Number(timeScale.options?.().barSpacing) || 6);
  const timeCache = new Map();
  const priceCache = new Map();
  const xForTime = (value) => {
    const time = Number(value);
    if (!Number.isFinite(time)) return null;
    if (timeCache.has(time)) return timeCache.get(time);
    let coordinate = timeScale.timeToCoordinate(time);
    if ((coordinate == null || !Number.isFinite(Number(coordinate)))
      && bars.length >= 2
      && time > firstTime
      && time < latestTime) {
      const afterIndex = lowerBoundBars(bars, time);
      const before = bars[Math.max(0, afterIndex - 1)];
      const after = bars[Math.min(bars.length - 1, afterIndex)];
      const beforeX = timeScale.timeToCoordinate(before.time);
      const afterX = timeScale.timeToCoordinate(after.time);
      const timeSpan = Number(after.time) - Number(before.time);
      if (Number.isFinite(Number(beforeX)) && Number.isFinite(Number(afterX)) && timeSpan > 0) {
        coordinate = Number(beforeX)
          + ((time - Number(before.time)) / timeSpan) * (Number(afterX) - Number(beforeX));
      }
    }
    if ((coordinate == null || !Number.isFinite(Number(coordinate)))
      && time >= latestTime
      && Number.isFinite(Number(latestX))) {
      coordinate = Number(latestX) + ((time - latestTime) / secondsPerBar) * barSpacing;
    }
    if ((coordinate == null || !Number.isFinite(Number(coordinate)))
      && time <= firstTime
      && Number.isFinite(Number(firstX))) {
      coordinate = Number(firstX) + ((time - firstTime) / secondsPerBar) * barSpacing;
    }
    const result = Number.isFinite(Number(coordinate)) ? Number(coordinate) : null;
    timeCache.set(time, result);
    return result;
  };
  const yForPrice = (value) => {
    const price = Number(value);
    if (!Number.isFinite(price)) return null;
    if (!priceCache.has(price)) {
      const coordinate = series.priceToCoordinate(price);
      priceCache.set(price, Number.isFinite(Number(coordinate)) ? Number(coordinate) : null);
    }
    return priceCache.get(price);
  };
  return { barSpacing, xForTime, yForPrice };
}

function nativeGeometry(model, chart, series) {
  const paneSize = chart.paneSize?.(0) || {};
  const width = Math.max(1, Number(paneSize.width) || 1);
  const height = Math.max(1, Number(paneSize.height) || 1);
  const { barSpacing, xForTime, yForPrice } = coordinateProjector(model, chart, series);
  const visibleX = (left, right = left) => Math.max(left, right) >= -barSpacing * 2
    && Math.min(left, right) <= width + barSpacing * 2;
  const backgroundRects = [];
  const backgroundLabels = [];
  const fills = [];
  const lines = [];
  const candleHighlights = [];
  const visibleTimeRange = chart.timeScale().getVisibleRange?.();
  const visibleTimeFrom = finiteNumber(visibleTimeRange?.from);
  const visibleTimeTo = finiteNumber(visibleTimeRange?.to);
  const visiblePaddingSeconds = model.timeframeMinutes * 60 * 2;
  const timeIsNearViewport = (time) => (
    visibleTimeFrom == null
    || visibleTimeTo == null
    || (Number(time) >= visibleTimeFrom - visiblePaddingSeconds
      && Number(time) <= visibleTimeTo + visiblePaddingSeconds)
  );

  model.sessions.forEach((session) => {
    const leftCoordinate = xForTime(session.start);
    const rightCoordinate = xForTime(session.end);
    if (leftCoordinate == null || rightCoordinate == null) return;
    const left = Math.min(leftCoordinate, rightCoordinate);
    const right = Math.max(leftCoordinate, rightCoordinate) + barSpacing;
    if (!visibleX(left, right)) return;
    const premarket = session.tone === "premarket";
    const subdued = session.subdued === true;
    backgroundRects.push({
      left,
      right,
      top: 0,
      bottom: height,
      color: subdued
        ? (premarket ? "rgba(112, 105, 92, .07)" : "rgba(82, 77, 68, .06)")
        : (premarket ? "rgba(112, 105, 92, .28)" : "rgba(82, 77, 68, .26)"),
      borderColor: subdued
        ? (premarket ? "rgba(177, 172, 160, .08)" : "rgba(145, 140, 130, .07)")
        : (premarket ? "rgba(177, 172, 160, .42)" : "rgba(145, 140, 130, .40)"),
    });
    if (session.label) {
      backgroundLabels.push({
        x: left + 5,
        y: 13,
        text: String(session.label),
        color: premarket ? "rgba(215, 210, 199, .74)" : "rgba(195, 190, 179, .70)",
      });
    }
  });

  model.cloudPairs.forEach((pair) => {
    const firstVisibleIndex = visibleTimeFrom == null
      ? 1
      : Math.max(1, lowerBoundBars(pair.aligned, visibleTimeFrom - visiblePaddingSeconds));
    const lastVisibleIndex = visibleTimeTo == null
      ? pair.aligned.length
      : Math.min(
        pair.aligned.length,
        lowerBoundBars(pair.aligned, visibleTimeTo + visiblePaddingSeconds) + 1,
      );
    for (let index = firstVisibleIndex; index < lastVisibleIndex; index += 1) {
      const previous = pair.aligned[index - 1];
      const current = pair.aligned[index];
      const x1 = xForTime(previous.time);
      const x2 = xForTime(current.time);
      if (x1 == null || x2 == null || !visibleX(x1, x2)) continue;
      const fastY1 = yForPrice(previous.fast);
      const fastY2 = yForPrice(current.fast);
      const slowY1 = yForPrice(previous.slow);
      const slowY2 = yForPrice(current.slow);
      if ([fastY1, fastY2, slowY1, slowY2].some((value) => value == null)) continue;
      const bullish = current.fast >= current.slow;
      fills.push({
        color: bullish ? pair.bullColor : pair.bearColor,
        opacity: pair.opacity,
        points: [
          [x1, fastY1],
          [x2, fastY2],
          [x2, slowY2],
          [x1, slowY1],
        ],
      });
    }
  });

  model.oneSidedClouds.forEach((cloud) => {
    const startIndex = lowerBoundBars(model.bars, Number(cloud.startTime) - model.timeframeMinutes * 60 + 1);
    const endIndex = lowerBoundBars(model.bars, Number(cloud.endTime));
    const firstBar = model.bars[startIndex];
    const lastBar = model.bars[Math.max(startIndex, endIndex - 1)];
    if (!firstBar || !lastBar) return;
    const firstX = xForTime(firstBar.time);
    const lastX = xForTime(lastBar.time);
    const anchorY = yForPrice(cloud.anchor);
    if (firstX == null || lastX == null || anchorY == null) return;
    const left = firstX - barSpacing / 2;
    const right = lastX + barSpacing / 2;
    if (!visibleX(left, right)) return;
    const edgeY = cloud.tone === "bull" ? 0 : height;
    fills.push({
      color: cloud.color,
      opacity: cloud.opacity,
      points: [
        [left, edgeY],
        [right, edgeY],
        [right, clamp(anchorY, 0, height)],
        [left, clamp(anchorY, 0, height)],
      ],
    });
  });

  model.cloudBands.forEach((shape) => {
    const startIndex = lowerBoundBars(model.bars, Number(shape.startTime) - model.timeframeMinutes * 60 + 1);
    const endIndex = lowerBoundBars(model.bars, Number(shape.endTime));
    const firstBar = model.bars[startIndex];
    const lastBar = model.bars[Math.max(startIndex, endIndex - 1)];
    if (!firstBar || !lastBar) return;
    const leftX = xForTime(firstBar.time);
    const rightX = xForTime(lastBar.time);
    if (leftX == null || rightX == null) return;
    const left = leftX - barSpacing / 2;
    const right = rightX + barSpacing / 2;
    if (!visibleX(left, right)) return;
    if (shape.kind === "line") {
      const y = yForPrice(shape.price);
      if (y == null) return;
      lines.push({
        x1: left,
        x2: right,
        y1: y,
        y2: y,
        color: shape.color,
        opacity: shape.opacity,
        lineWidth: shape.lineWidth,
      });
      return;
    }
    const firstY = yForPrice(shape.first);
    const secondY = yForPrice(shape.second);
    if (firstY == null || secondY == null) return;
    fills.push({
      color: shape.color,
      opacity: shape.opacity,
      points: [
        [left, firstY],
        [right, firstY],
        [right, secondY],
        [left, secondY],
      ],
    });
  });

  model.sessionLines.forEach((line) => {
    const x = xForTime(line.time);
    if (x == null || !visibleX(x)) return;
    lines.push({
      x1: x,
      x2: x,
      y1: 0,
      y2: height,
      color: line.color,
      opacity: line.subdued === true ? 0.16 : line.firm ? 0.92 : 0.62,
      lineWidth: 1,
      dash: line.firm ? [] : [4, 4],
      label: line.label,
    });
  });

  // TOS-style study levels (MTF MA lines, OI walls): a horizontal segment
  // that starts at its anchor bar and ends at the label gutter — MomoX-style —
  // so the level's name/amount sits in clean space instead of being struck
  // through by its own dashes.
  // Labels hang left from 56px inside the pane edge; the widest wall label
  // ("26.4K 8/21" ≈ 66px) needs the line to stop 56 + 66 + 6 ≈ 128px out so
  // the dash never touches the text.
  const LEVEL_LABEL_GUTTER = 138;
  model.levelSegments.forEach((segment) => {
    const y = yForPrice(segment.price);
    if (y == null) return;
    const startX = xForTime(segment.startTime);
    const x1 = Math.max(0, startX == null ? 0 : startX);
    if (x1 > width) return;
    lines.push({
      x1,
      x2: Math.max(x1, width - LEVEL_LABEL_GUTTER),
      y1: y,
      y2: y,
      color: segment.color,
      opacity: 0.95,
      lineWidth: segment.lineWidth || 1,
      dash: Array.isArray(segment.dash) ? segment.dash : [],
    });
  });

  const barsByTime = new Map(model.bars.map((bar) => [Number(bar.time), bar]));
  model.candleHighlights.forEach((highlight) => {
    if (!timeIsNearViewport(highlight.time)) return;
    const bar = barsByTime.get(Number(highlight.time));
    const x = xForTime(highlight.time);
    const top = yForPrice(bar?.high);
    const bottom = yForPrice(bar?.low);
    const open = yForPrice(bar?.open);
    const close = yForPrice(bar?.close);
    if (!bar || x == null || top == null || bottom == null || !visibleX(x)) return;
    const bullish = Number(bar.close) >= Number(bar.open);
    candleHighlights.push({
      x,
      top: Math.min(top, bottom),
      bottom: Math.max(top, bottom),
      bodyTop: open == null || close == null ? Math.min(top, bottom) : Math.min(open, close),
      bodyBottom: open == null || close == null ? Math.max(top, bottom) : Math.max(open, close),
      width: clamp(barSpacing * 0.72, 4, 18),
      color: highlight.color || (bullish ? "#18f0ff" : "#ff20b8"),
      intensity: clamp(Number(highlight.intensity) || 1, 0.4, 1.8),
      flashUntilMs: Math.max(0, Number(highlight.flashUntilMs) || 0),
      fire: highlight.fire === true,
      bullish,
    });
  });

  const stackCounts = new Map();
  const labels = model.signals.flatMap((signal) => {
    const time = Number(signal.time);
    if (!timeIsNearViewport(time)) return [];
    const bar = barsByTime.get(time);
    if (!bar) return [];
    const x = xForTime(time);
    const placement = signal.position === "belowBar" ? "below" : "above";
    // Most bubbles hug the candle high/low. ThinkScript studies that specify
    // an ATR offset can provide their exact plot price without adding a
    // hidden series or changing the candle-only autoscale range.
    const suppliedPrice = finiteNumber(signal.price);
    const price = suppliedPrice == null
      ? (placement === "below" ? Number(bar.low) : Number(bar.high))
      : suppliedPrice;
    const y = yForPrice(price);
    if (x == null || y == null || !visibleX(x)) return [];
    const family = String(signal.family || "tos-mtf");
    const stackScope = nativeSignalStackScope(signal);
    const stackKey = `${time}-${placement}-${stackScope}`;
    const stackIndex = stackCounts.get(stackKey) || 0;
    stackCounts.set(stackKey, stackIndex + 1);
    return [{
      x,
      y,
      placement,
      stackIndex,
      stackKey,
      text: String(signal.text || ""),
      color: signal.color,
      textColor: labelTextColor(signal.color),
      family,
      compact: signal.compact === true,
    }];
  });

  return {
    width,
    height,
    backgroundRects,
    backgroundLabels,
    fills,
    lines,
    candleHighlights,
    labels,
  };
}

class TosBackgroundRenderer {
  constructor() {
    this.geometry = null;
  }

  setGeometry(geometry) {
    this.geometry = geometry;
  }

  draw(target) {
    if (!this.geometry) return;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      const geometry = this.geometry;
      context.save();
      context.beginPath();
      context.rect(0, 0, mediaSize.width, mediaSize.height);
      context.clip();
      geometry.backgroundRects.forEach((rectangle) => {
        context.fillStyle = rectangle.color;
        context.fillRect(rectangle.left, rectangle.top, rectangle.right - rectangle.left, rectangle.bottom - rectangle.top);
        context.strokeStyle = rectangle.borderColor;
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(rectangle.left + 0.5, rectangle.top);
        context.lineTo(rectangle.left + 0.5, rectangle.bottom);
        context.stroke();
      });
      geometry.fills.forEach((fill) => {
        if (!fill.points.length) return;
        context.fillStyle = colorWithOpacity(fill.color, fill.opacity);
        context.beginPath();
        context.moveTo(fill.points[0][0], fill.points[0][1]);
        fill.points.slice(1).forEach(([x, y]) => context.lineTo(x, y));
        context.closePath();
        context.fill();
      });
      geometry.lines.forEach((line) => {
        context.strokeStyle = colorWithOpacity(line.color, line.opacity ?? 1);
        context.lineWidth = line.lineWidth || 1;
        context.setLineDash(line.dash || []);
        context.beginPath();
        context.moveTo(line.x1, line.y1);
        context.lineTo(line.x2, line.y2);
        context.stroke();
        if (line.label) {
          context.save();
          context.translate(line.x1 - 3, Math.max(30, mediaSize.height - 44));
          context.rotate(-Math.PI / 2);
          context.fillStyle = colorWithOpacity(line.color, Math.min(1, (line.opacity ?? 1) + 0.08));
          context.font = "700 8px system-ui, sans-serif";
          context.textBaseline = "bottom";
          context.fillText(String(line.label), 0, 0);
          context.restore();
        }
      });
      context.setLineDash([]);
      geometry.backgroundLabels.forEach((label) => {
        context.fillStyle = label.color;
        context.font = "800 8px system-ui, sans-serif";
        context.textBaseline = "middle";
        context.fillText(label.text, label.x, label.y);
      });
      context.restore();
    });
  }
}

function roundedRectangle(context, x, y, width, height, radius) {
  const safeRadius = Math.min(radius, width / 2, height / 2);
  context.beginPath();
  context.moveTo(x + safeRadius, y);
  context.lineTo(x + width - safeRadius, y);
  context.quadraticCurveTo(x + width, y, x + width, y + safeRadius);
  context.lineTo(x + width, y + height - safeRadius);
  context.quadraticCurveTo(x + width, y + height, x + width - safeRadius, y + height);
  context.lineTo(x + safeRadius, y + height);
  context.quadraticCurveTo(x, y + height, x, y + height - safeRadius);
  context.lineTo(x, y + safeRadius);
  context.quadraticCurveTo(x, y, x + safeRadius, y);
  context.closePath();
}

class TosSignalRenderer {
  constructor() {
    this.geometry = null;
  }

  setGeometry(geometry) {
    this.geometry = geometry;
  }

  draw(target) {
    if (!this.geometry) return;
    target.useMediaCoordinateSpace(({ context, mediaSize }) => {
      context.save();
      context.beginPath();
      context.rect(0, 0, mediaSize.width, mediaSize.height);
      context.clip();
      context.font = "400 11px system-ui, sans-serif";
      context.textAlign = "center";
      context.textBaseline = "middle";
      const labelTops = nativeSignalLabelTops(this.geometry.labels, mediaSize.height);
      this.geometry.candleHighlights.forEach((highlight) => {
        const height = Math.max(4, highlight.bottom - highlight.top);
        const left = highlight.x - highlight.width / 2;
        const pulse = nativeHighlightPulse(highlight.flashUntilMs);
        const fireMotion = highlight.fire ? nativeFireMotion(Date.now(), highlight.x) : null;
        const glowStrength = highlight.intensity * (1 + pulse * 0.72 + (fireMotion?.flicker || 0) * 0.2);
        context.save();
        if (highlight.fire) {
          const bodyHeight = Math.max(2, highlight.bodyBottom - highlight.bodyTop);
          const bodyWidth = Math.max(3, highlight.width * 0.78);
          const bodyLeft = highlight.x - bodyWidth / 2;
          const glyphPoints = nativeFireGlyphPoints(highlight);

          context.lineCap = "round";
          context.lineJoin = "round";
          context.shadowColor = colorWithOpacity(highlight.color, 0.9);
          context.shadowBlur = 7 * glowStrength;
          context.strokeStyle = colorWithOpacity(highlight.color, 0.94 + pulse * 0.06);
          context.lineWidth = 1.2 + pulse * 0.65;
          context.beginPath();
          context.moveTo(highlight.x, highlight.top);
          context.lineTo(highlight.x, highlight.bodyTop);
          context.moveTo(highlight.x, highlight.bodyBottom);
          context.lineTo(highlight.x, highlight.bottom);
          context.stroke();
          context.strokeRect(bodyLeft, highlight.bodyTop, bodyWidth, bodyHeight);

          if (glyphPoints.length) {
            context.shadowBlur = 6 * glowStrength;
            context.strokeStyle = colorWithOpacity("#efffff", 0.9 + pulse * 0.1);
            context.lineWidth = 1.25 + pulse * 0.45;
            context.beginPath();
            context.moveTo(
              glyphPoints[0][0] + fireMotion.bend * 0.25,
              glyphPoints[0][1] - fireMotion.lift,
            );
            glyphPoints.slice(1).forEach(([pointX, pointY], pointIndex) => {
              const alternatingBend = pointIndex % 2 === 0 ? fireMotion.bend : -fireMotion.bend * 0.7;
              const verticalWave = Math.sin(Date.now() / 68 + pointIndex * 1.7) * 0.55;
              context.lineTo(pointX + alternatingBend, pointY + verticalWave - fireMotion.lift);
            });
            context.stroke();

            const ignitionX = glyphPoints[0][0] + fireMotion.bend * 0.55;
            const ignitionY = Math.max(3, highlight.top - 1.5 - fireMotion.lift);
            context.shadowColor = "rgba(255, 210, 30, .9)";
            context.shadowBlur = 4 + pulse * 3;
            context.fillStyle = "#ffd21e";
            context.strokeStyle = colorWithOpacity(highlight.color, 0.95);
            context.lineWidth = 1;
            context.beginPath();
            context.arc(
              ignitionX,
              ignitionY,
              1.55 + fireMotion.flicker * 0.75 + pulse * 0.35,
              0,
              Math.PI * 2,
            );
            context.fill();
            context.stroke();
          }
          context.restore();
          return;
        }
        context.strokeStyle = colorWithOpacity(highlight.color, 0.88 + pulse * 0.12);
        context.shadowColor = colorWithOpacity(highlight.color, 0.82 + pulse * 0.18);
        context.shadowBlur = 8 * glowStrength;
        context.lineWidth = 1.35 + pulse * 0.75;
        context.strokeRect(left, highlight.top, highlight.width, height);
        context.shadowBlur = 3 * glowStrength;
        context.strokeStyle = colorWithOpacity(highlight.color, 0.58 + pulse * 0.28);
        context.lineWidth = 2.35 + pulse * 1.2;
        context.strokeRect(left - 1, highlight.top - 1, highlight.width + 2, height + 2);
        context.restore();
      });
      this.geometry.labels.forEach((label, labelIndex) => {
        if (label.family === "rvol") {
          context.save();
          context.font = "700 10px system-ui, sans-serif";
          const rowOffset = 8 + label.stackIndex * 11;
          const textY = label.placement === "below"
            ? label.y + rowOffset
            : label.y - rowOffset;
          context.textAlign = "center";
          context.fillStyle = label.color;
          context.fillText(label.text, label.x, textY);
          context.restore();
          return;
        }
        const textWidth = Math.ceil(context.measureText(label.text).width);
        const boxWidth = Math.max(label.compact ? 27 : 32, textWidth + (label.compact ? 9 : 12));
        const boxHeight = nativeSignalBoxHeight(label);
        const pointerHeight = NATIVE_SIGNAL_POINTER_HEIGHT;
        const left = clamp(label.x - boxWidth / 2, 2, Math.max(2, mediaSize.width - boxWidth - 2));
        const centerX = clamp(label.x, left + 5, left + boxWidth - 5);
        const top = labelTops[labelIndex] ?? clamp(
          nativeSignalRequestedTop(label),
          NATIVE_SIGNAL_PANE_PADDING,
          Math.max(NATIVE_SIGNAL_PANE_PADDING, mediaSize.height - boxHeight - NATIVE_SIGNAL_PANE_PADDING),
        );
        const isSqueeze = label.family === "squeeze";
        const fillColor = isSqueeze ? colorWithOpacity(label.color, 0.72) : label.color;
        context.save();
        context.strokeStyle = colorWithOpacity(label.color, 0.74);
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(label.x, label.y + (label.placement === "below" ? 2 : -2));
        context.lineTo(label.x, label.placement === "below" ? top - pointerHeight : top + boxHeight + pointerHeight);
        context.stroke();
        context.fillStyle = fillColor;
        context.strokeStyle = isSqueeze ? label.color : "rgba(2, 7, 12, .76)";
        context.lineWidth = 1;
        context.shadowColor = colorWithOpacity(label.color, isSqueeze ? 0.68 : 0.38);
        context.shadowBlur = isSqueeze ? 6 : 3;
        roundedRectangle(context, left, top, boxWidth, boxHeight, 4);
        context.fill();
        context.stroke();
        context.shadowBlur = 0;
        context.beginPath();
        if (label.placement === "below") {
          context.moveTo(centerX - 4, top);
          context.lineTo(centerX, top - pointerHeight);
          context.lineTo(centerX + 4, top);
        } else {
          context.moveTo(centerX - 4, top + boxHeight);
          context.lineTo(centerX, top + boxHeight + pointerHeight);
          context.lineTo(centerX + 4, top + boxHeight);
        }
        context.closePath();
        context.fill();
        context.stroke();
        context.fillStyle = isSqueeze ? "#061018" : label.textColor;
        context.fillText(label.text, left + boxWidth / 2, top + boxHeight / 2 + 0.5);
        context.restore();
      });
      context.restore();
    });
  }
}

class TosPrimitivePaneView {
  constructor(renderer, zOrder) {
    this.targetRenderer = renderer;
    this.targetZOrder = zOrder;
  }

  renderer() {
    return this.targetRenderer;
  }

  zOrder() {
    return this.targetZOrder;
  }
}

export class TosNativeChartPrimitive {
  constructor() {
    this.model = EMPTY_NATIVE_MODEL;
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
    this.animationFrameId = 0;
    this.animationTimerId = 0;
    this.lastAnimationPaintAt = 0;
    this.interactionActive = false;
    this.backgroundRenderer = new TosBackgroundRenderer();
    this.signalRenderer = new TosSignalRenderer();
    this.geometryModel = null;
    this.geometryViewKey = "";
    this.views = [
      new TosPrimitivePaneView(this.backgroundRenderer, "bottom"),
      new TosPrimitivePaneView(this.signalRenderer, "top"),
    ];
  }

  attached({ chart, series, requestUpdate }) {
    this.chart = chart;
    this.series = series;
    this.requestUpdate = requestUpdate;
    this.updateAllViews();
    requestUpdate();
    this.ensureAnimationLoop();
  }

  detached() {
    if (this.animationFrameId && typeof globalThis.cancelAnimationFrame === "function") {
      globalThis.cancelAnimationFrame(this.animationFrameId);
    }
    if (this.animationTimerId && typeof globalThis.clearTimeout === "function") {
      globalThis.clearTimeout(this.animationTimerId);
    }
    this.animationFrameId = 0;
    this.animationTimerId = 0;
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
    this.interactionActive = false;
  }

  paneViews() {
    return this.views;
  }

  autoscaleInfo() {
    // App.jsx owns automatic price fitting so saved/manual ranges remain
    // untouched and marker primitives cannot overwrite each other's margins.
    return null;
  }

  setData(model) {
    this.model = normalizeModel(model);
    this.updateAllViews();
    this.requestUpdate?.();
    this.ensureAnimationLoop();
  }

  refreshGeometry() {
    this.updateAllViews();
    this.requestUpdate?.();
    this.ensureAnimationLoop();
  }

  updateLastBar(bar, liveOverlay = {}) {
    const time = finiteNumber(bar?.time);
    const high = finiteNumber(bar?.high);
    const low = finiteNumber(bar?.low);
    if (time == null || high == null || low == null) return;
    const bars = this.model.bars;
    const latest = bars.at(-1);
    if (!latest || Number(latest.time) !== time) return;
    let signals = this.model.signals;
    let signalsChanged = false;
    if (Object.prototype.hasOwnProperty.call(liveOverlay, "rvolMarker")) {
      const kept = signals.filter((signal) => !(
        signal?.family === "rvol" && Number(signal?.time) === time
      ));
      const nextSignals = liveOverlay.rvolMarker ? [...kept, liveOverlay.rvolMarker] : kept;
      signalsChanged = nextSignals.length !== signals.length
        || (liveOverlay.rvolMarker != null
          && signals[signals.length - 1] !== liveOverlay.rvolMarker);
      if (signalsChanged) signals = nextSignals;
    }
    const merged = { ...latest, ...bar, time, high, low };
    const barChanged = ["open", "high", "low", "close", "volume"]
      .some((field) => Number(merged[field]) !== Number(latest[field]));
    // A quote inside the current second often restates identical values;
    // replacing the model then forces a full geometry recompute for nothing.
    if (!barChanged && !signalsChanged) return;
    this.model = {
      ...this.model,
      bars: barChanged ? [...bars.slice(0, -1), merged] : bars,
      signals,
    };
    this.updateAllViews();
    this.requestUpdate?.();
    this.ensureAnimationLoop();
  }

  hasActiveFlash(now = Date.now()) {
    return this.model.candleHighlights.some((highlight) => Number(highlight.flashUntilMs) > now);
  }

  hasVisibleFire() {
    return this.signalRenderer.geometry?.candleHighlights?.some((highlight) => highlight.fire) === true;
  }

  hasActiveAnimation(now = Date.now()) {
    return !this.interactionActive && (this.hasActiveFlash(now) || this.hasVisibleFire());
  }

  setInteractionActive(active) {
    const nextActive = Boolean(active);
    if (this.interactionActive === nextActive) return;
    this.interactionActive = nextActive;
    if (nextActive) {
      if (this.animationFrameId && typeof globalThis.cancelAnimationFrame === "function") {
        globalThis.cancelAnimationFrame(this.animationFrameId);
      }
      if (this.animationTimerId && typeof globalThis.clearTimeout === "function") {
        globalThis.clearTimeout(this.animationTimerId);
      }
      this.animationFrameId = 0;
      this.animationTimerId = 0;
    }
    if (!nextActive) {
      this.requestUpdate?.();
      this.ensureAnimationLoop();
    }
  }

  ensureAnimationLoop() {
    if (this.animationFrameId
      || this.animationTimerId
      || !this.requestUpdate
      || typeof globalThis.requestAnimationFrame !== "function"
      || typeof globalThis.setTimeout !== "function") return;
    if (!this.hasActiveAnimation()) return;
    // Sleep between decorative paints instead of waking on every display
    // frame just to discover that the 50/200ms interval has not elapsed.
    // Four visible charts previously created hundreds of idle callbacks per
    // second before any candle or overlay work even started.
    const frameInterval = this.hasVisibleFire() ? 200 : 50;
    this.animationTimerId = globalThis.setTimeout(() => {
      this.animationTimerId = 0;
      if (!this.requestUpdate || !this.hasActiveAnimation()) {
        this.requestUpdate?.();
        return;
      }
      this.animationFrameId = globalThis.requestAnimationFrame((timestamp) => {
        this.animationFrameId = 0;
        if (!this.requestUpdate || !this.hasActiveAnimation()) {
          this.requestUpdate?.();
          return;
        }
        this.lastAnimationPaintAt = timestamp;
        this.requestUpdate();
        this.ensureAnimationLoop();
      });
    }, frameInterval);
  }

  updateAllViews() {
    if (!this.chart || !this.series) return;
    // Lightweight Charts calls this hook on EVERY invalidation, and the
    // flash/fire animation loop requests one every 50-200ms — previously each
    // repaint recomputed the complete projection (sessions, clouds, bands,
    // signals) even though nothing had moved, which saturated the main
    // thread whenever a fire marker was on screen or live quotes ticked
    // (measured 2026-08-10: ~1-2s tasks near-continuously). Geometry only
    // depends on the model and the view mapping, so recompute solely when
    // one of those actually changes; decorative repaints redraw the cached
    // shapes (the pulse/fire flicker is time-based inside the renderers).
    const timeScale = this.chart.timeScale();
    const visibleRange = timeScale.getVisibleLogicalRange?.() || {};
    const paneSize = this.chart.paneSize?.(0) || {};
    const viewKey = `${Number(visibleRange.from) || 0}|${Number(visibleRange.to) || 0}`
      + `|${Number(paneSize.width) || 0}|${Number(paneSize.height) || 0}`
      + `|${Number(timeScale.options?.().barSpacing) || 0}`;
    if (this.model === this.geometryModel && viewKey === this.geometryViewKey) return;
    const geometry = nativeGeometry(this.model, this.chart, this.series);
    this.geometryModel = this.model;
    this.geometryViewKey = viewKey;
    this.backgroundRenderer.setGeometry(geometry);
    this.signalRenderer.setGeometry(geometry);
  }
}
