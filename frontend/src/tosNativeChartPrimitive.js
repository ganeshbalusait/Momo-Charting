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
  trendSegments: [],
  patternFills: [],
  patternMarkers: [],
  wedges: [],
});

function finiteNumber(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

// Number(null) is 0: a missing time or price must not land on 1970 / $0.
function patternNumber(value) {
  return value == null || value === "" ? null : finiteNumber(value);
}

function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

export function nativeLevelLabelGutter(labelText, fallback = 92) {
  const text = String(labelText || "").trim();
  const fallbackGutter = Math.max(14, Number(fallback) || 92);
  if (!text) return fallbackGutter;
  // The 10px monospace chip measures 5.86px per glyph and has 8px of inline
  // padding. Its right edge overlaps the plot/price-axis boundary by 6px, so
  // the line only needs textWidth + 2px reserved to meet the chip's left edge.
  return Math.max(14, Math.min(fallbackGutter, text.length * 5.86 + 2));
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

// Candle highlights (signal glow and 🔥 fire) are drawn as a thin frame a few
// pixels OUTSIDE the candle, never on its body or wick. On 2026-11-19 NVDA's
// 09:30 candle carried CALL5, CALL15, 🔥5 and 🔥15: four cyan glows painted on
// top of a cyan candle turned it into one glowing block on the phone ("I cannot
// see properly candles"). The candle must stay readable under any signal count.
export const NATIVE_HIGHLIGHT_GAP = 2;

/**
 * One highlight per candle. Several studies can mark the same bar; drawing a
 * glow for each stacks their shadows into a solid block. Fire wins (it keeps
 * the flame), the strongest intensity and the latest flash are kept.
 */
export function mergeCandleHighlights(highlights) {
  const merged = new Map();
  (Array.isArray(highlights) ? highlights : []).forEach((highlight) => {
    const time = Number(highlight?.time);
    if (!Number.isFinite(time)) return;
    const previous = merged.get(time);
    if (!previous) {
      merged.set(time, { ...highlight });
      return;
    }
    const fire = previous.fire === true || highlight.fire === true;
    merged.set(time, {
      ...previous,
      color: previous.fire !== true && highlight.fire === true ? highlight.color : previous.color,
      fire,
      intensity: Math.max(Number(previous.intensity) || 0, Number(highlight.intensity) || 0) || previous.intensity,
      flashUntilMs: Math.max(Number(previous.flashUntilMs) || 0, Number(highlight.flashUntilMs) || 0),
    });
  });
  return [...merged.values()];
}

/**
 * One-sided cloud columns (one per candle and side, already unioned) joined into
 * runs of neighbouring candles with the same anchor, colour and opacity, so a
 * cloud spanning several candles is one shape with no seams between them.
 */
export function nativeCloudUnionRuns(columns) {
  const sorted = (Array.isArray(columns) ? columns : [])
    .slice()
    .sort((left, right) => (left.bull === right.bull ? left.index - right.index : left.bull ? -1 : 1));
  const runs = [];
  sorted.forEach((column) => {
    const last = runs.at(-1);
    if (
      last
      && last.bull === column.bull
      && last.to === column.index - 1
      && Math.abs(last.anchorY - column.anchorY) < 0.5
      && last.color === column.color
      && last.opacity === column.opacity
    ) {
      last.to = column.index;
      return;
    }
    runs.push({
      from: column.index, to: column.index, bull: column.bull,
      anchorY: column.anchorY, color: column.color, opacity: column.opacity,
    });
  });
  return runs;
}

/** A candle highlight is drawn only while its new-signal flash runs. */
export function nativeHighlightShows(highlight, nowMs = Date.now()) {
  return Number(highlight?.flashUntilMs) > Number(nowMs);
}

/** The frame drawn around a highlighted candle: wick to wick, outside the body. */
export function nativeHighlightFrame(highlight) {
  const x = Number(highlight?.x);
  const top = Number(highlight?.top);
  const bottom = Number(highlight?.bottom);
  const width = Math.max(4, Number(highlight?.width) || 4);
  if (![x, top, bottom].every(Number.isFinite)) return null;
  const height = Math.max(4, bottom - top);
  return {
    left: x - width / 2 - NATIVE_HIGHLIGHT_GAP,
    top: top - NATIVE_HIGHLIGHT_GAP,
    width: width + NATIVE_HIGHLIGHT_GAP * 2,
    height: height + NATIVE_HIGHLIGHT_GAP * 2,
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
  // Just outside the highlight frame, so the trace never sits on the candle.
  const anchorX = x + side * (width / 2 + NATIVE_HIGHLIGHT_GAP + 3);
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
// Scanner-grade circle drawn to the right of a bullish CALL bubble.
export const NATIVE_SIGNAL_GRADE_RADIUS = 7;
const NATIVE_SIGNAL_GRADE_GAP = 3;
const NATIVE_SIGNAL_GRADE_COLORS = { "A+": "#39ff88", A: "#6fdc8c", B: "#9aa0a6" };
//: The BEAR grade (spec 2026-09-24) on PUT labels: the same ladder in red.
const NATIVE_SIGNAL_GRADE_COLORS_BEAR = { "A+": "#ff4d6d", A: "#ff8fa3", B: "#9aa0a6" };
//: A signal whose price has since closed back below its trigger. Faded enough
//: to read as "past", dark enough to still be legible on the candle behind it.
const NATIVE_SIGNAL_GRADE_FAILED_ALPHA = 0.45;

// Left edge of a signal bubble, clamped so the bubble - and its grade circle
// when it has one - never runs past either pane edge.
export function nativeSignalBoxLeft(x, boxWidth, paneWidth, grade) {
  const extra = grade ? NATIVE_SIGNAL_GRADE_GAP + NATIVE_SIGNAL_GRADE_RADIUS * 2 : 0;
  return clamp(x - boxWidth / 2, 2, Math.max(2, paneWidth - boxWidth - extra - 2));
}

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
export function nativeSignalLabelTops(labels, paneHeight, clampToPane = true) {
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
    // While the trader is actively panning (clampToPane === false) every stack
    // must stay rigidly glued to its source candle, even if that pushes rows
    // past the pane edge. The clamp below keeps stacks readable AT REST, but
    // during a vertical pan it pinned edge stacks at a fixed screen Y while
    // the candles moved underneath - on a short phone pane nearly every stack
    // sits within one stack-height of an edge, so the fire chips and signal
    // bubbles visibly slid relative to their candles ("not glued", iPhone
    // 2026-08-20). Clipping during the gesture is the TOS/TradingView look;
    // the clamp re-engages on the gesture-end repaint.
    if (clampToPane) {
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
    // Chart patterns (chartPattern.js): lines between two (time, price)
    // points, so a trendline can slope; endTime null runs to the axis.
    trendSegments: (Array.isArray(model.trendSegments) ? model.trendSegments : [])
      .flatMap((segment) => {
        const startTime = finiteNumber(segment?.startTime);
        const startPrice = finiteNumber(segment?.startPrice);
        const endPrice = finiteNumber(segment?.endPrice);
        const endTime = segment?.endTime == null ? null : finiteNumber(segment.endTime);
        if (startTime == null || startPrice == null || endPrice == null) return [];
        if (segment?.endTime != null && endTime == null) return [];
        return [{ ...segment, startTime, startPrice, endTime, endPrice }];
      }),
    // Chart pattern shading: a polygon of (time, price) points.
    patternFills: (Array.isArray(model.patternFills) ? model.patternFills : [])
      .flatMap((fill) => {
        const points = (Array.isArray(fill?.points) ? fill.points : [])
          .map((point) => ({ time: patternNumber(point?.time), price: patternNumber(point?.price) }));
        if (points.length < 3 || points.some((point) => point.time == null || point.price == null)) return [];
        return [{ ...fill, points }];
      }),
    // Chart pattern circles (touches, the breakout candle) and target arrows.
    patternMarkers: (Array.isArray(model.patternMarkers) ? model.patternMarkers : [])
      .flatMap((marker) => {
        const time = patternNumber(marker?.time);
        const price = patternNumber(marker?.price);
        return time == null || price == null ? [] : [{ ...marker, time, price }];
      }),
    // TOS BOOLEAN_WEDGE_UP / _DOWN plots (shared_StochasX_v20263).
    wedges: (Array.isArray(model.wedges) ? model.wedges : [])
      .flatMap((wedge) => {
        const time = finiteNumber(wedge?.time);
        return time == null ? [] : [{ ...wedge, time }];
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

// Fixed probe price. priceToCoordinate(probe) changes whenever the vertical
// mapping changes - range, inversion or log mode - so one sample detects any
// price-scale movement that would strand cached geometry.
const PRICE_MAPPING_PROBE = 100;

export function nativeGeometry(model, chart, series) {
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
  const wedges = [];
  const markers = [];
  // Chart pattern names and captions: drawn on the top layer with the
  // markers, so candles and other studies' tags never cover them.
  const patternLabels = [];
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

  // The one-sided signal clouds (4x8, 9/20, MACD trend and 🔥 squeeze, every
  // timeframe) are each a 20% cyan or magenta layer from the pane edge to the
  // signal candle. Painted one by one they added up: on NVDA's 09:30 candle
  // (2026-11-19) about six of them stacked into a near-solid cyan column and
  // the cyan candle inside it could not be seen ("I see that box, I cannot see
  // candle"). They are drawn as their UNION instead: per candle and side, one
  // fill from the pane edge to the farthest anchor, at the strongest opacity -
  // the same area, one layer deep. The tags still say which signals fired.
  const cloudColumns = new Map();
  model.oneSidedClouds.forEach((cloud) => {
    const startIndex = lowerBoundBars(model.bars, Number(cloud.startTime) - model.timeframeMinutes * 60 + 1);
    const endIndex = lowerBoundBars(model.bars, Number(cloud.endTime));
    const anchorY = yForPrice(cloud.anchor);
    if (anchorY == null) return;
    const bull = cloud.tone === "bull";
    for (let index = startIndex; index <= Math.max(startIndex, endIndex - 1); index += 1) {
      const bar = model.bars[index];
      if (!bar) break;
      const key = `${bull ? "bull" : "bear"}|${index}`;
      const previous = cloudColumns.get(key);
      if (!previous) {
        cloudColumns.set(key, { index, bull, anchorY, color: cloud.color, opacity: Number(cloud.opacity) || 0 });
        continue;
      }
      previous.anchorY = bull ? Math.max(previous.anchorY, anchorY) : Math.min(previous.anchorY, anchorY);
      previous.opacity = Math.max(previous.opacity, Number(cloud.opacity) || 0);
    }
  });
  nativeCloudUnionRuns([...cloudColumns.values()]).forEach((run) => {
    const firstX = xForTime(model.bars[run.from].time);
    const lastX = xForTime(model.bars[run.to].time);
    if (firstX == null || lastX == null) return;
    const left = firstX - barSpacing / 2;
    const right = lastX + barSpacing / 2;
    if (!visibleX(left, right)) return;
    const edgeY = run.bull ? 0 : height;
    const anchorY = clamp(run.anchorY, 0, height);
    fills.push({
      color: run.color,
      opacity: run.opacity,
      points: [[left, edgeY], [right, edgeY], [right, anchorY], [left, anchorY]],
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
  // Right-hand reserve so a level line stops before its name chip instead of
  // running underneath it. The chips are drawn separately (addIndicatorName in
  // App.jsx) and are RIGHT-ALIGNED against the price axis, so a single shared
  // reserve cannot fit them all: sized for the widest ("44.4K 8/21", ~80px) it
  // leaves a large gap in front of short ones ("pmH", "dL", "9eD", ~30px).
  //
  // When a segment carries its label text the reserve is computed from that
  // text, so every line stops just short of its own chip. Segments without
  // label text keep the shared worst-case reserve.
  const LEVEL_LABEL_GUTTER = 92;
  model.levelSegments.forEach((segment) => {
    const y = yForPrice(segment.price);
    if (y == null) return;
    const startX = xForTime(segment.startTime);
    const x1 = Math.max(0, startX == null ? 0 : startX);
    if (x1 > width) return;
    lines.push({
      x1,
      // An explicit numeric gutter wins (AutoFib's expansion line runs to the
      // axis, like TOS, with no chip in front of it).
      x2: Math.max(x1, width - (Number.isFinite(Number(segment.gutter)) && segment.gutter !== null
        ? Math.max(0, Number(segment.gutter))
        : nativeLevelLabelGutter(segment.labelText, LEVEL_LABEL_GUTTER))),
      y1: y,
      y2: y,
      color: segment.color,
      opacity: 0.95,
      lineWidth: segment.lineWidth || 1,
      dash: Array.isArray(segment.dash) ? segment.dash : [],
    });
  });

  (model.patternFills || []).forEach((fill) => {
    const points = fill.points.map((point) => [xForTime(point.time), yForPrice(point.price)]);
    if (points.some(([x, y]) => x == null || y == null)) return;
    const xs = points.map(([x]) => x);
    if (Math.max(...xs) < 0 || Math.min(...xs) > width) return;
    fills.push({ points, color: fill.color, opacity: fill.opacity ?? 0.1 });
  });

  (model.trendSegments || []).forEach((segment) => {
    const x1 = xForTime(segment.startTime);
    const y1 = yForPrice(segment.startPrice);
    const y2 = yForPrice(segment.endPrice);
    const x2 = segment.endTime == null
      ? width - nativeLevelLabelGutter(segment.labelText, LEVEL_LABEL_GUTTER)
      : xForTime(segment.endTime);
    // x2 === x1 is a vertical line (the pattern's measured move).
    if (x1 == null || x2 == null || y1 == null || y2 == null || x2 < x1) return;
    if (x2 === x1 && y2 === y1) return;
    if (x2 < 0 || x1 > width) return;
    lines.push({
      x1,
      x2,
      y1,
      y2,
      color: segment.color,
      opacity: 0.95,
      lineWidth: segment.lineWidth || 2,
      dash: Array.isArray(segment.dash) ? segment.dash : [],
    });
    // The line's name at its start ("Resistance", "Support", "Trendline"),
    // like his sample pictures (2026-11-21).
    if (segment.startLabel) {
      const above = segment.startLabelSide !== "below";
      // Left of the line's start, level with it, like his sample pictures:
      // outside the pattern and away from the price-axis tags that crowd
      // its right end (COST Monthly, 2026-11-21). On a dark plate. With no
      // room on the left, just past the first touch's ring, above / below.
      const roomLeft = x1 >= 110;
      patternLabels.push({
        x: roomLeft ? x1 - 10 : Math.max(4, Math.min(x1 + 12, width - 150)),
        y: roomLeft ? y1 : y1 + (above ? -12 : 13),
        ...(roomLeft ? { align: "right" } : {}),
        text: String(segment.startLabel),
        color: segment.color,
        font: "800 11px system-ui, sans-serif",
        background: "rgba(6, 10, 18, 0.82)",
      });
    }
    // The pattern's name beside the breakout on the line that broke ("M ▲
    // Range > 1096.50 (Nov 26 open)"), outside the pattern: its axis tag
    // usually sits beside the LIVE tag, which nothing may join.
    if (segment.caption) {
      const above = segment.captionSide !== "below";
      patternLabels.push({
        x: Math.max(4, x2 - 6),
        y: y2 + (above ? -12 : 13),
        align: "right",
        text: String(segment.caption),
        color: segment.captionColor || segment.color,
        font: "800 11px system-ui, sans-serif",
        // A dark plate so other studies' lines do not strike through it.
        background: "rgba(6, 10, 18, 0.82)",
      });
    }
  });

  (model.patternMarkers || []).forEach((marker) => {
    const x = xForTime(marker.time);
    const y = yForPrice(marker.price);
    if (x == null || y == null || !visibleX(x)) return;
    markers.push({
      x,
      y,
      kind: String(marker.kind || "touch"),
      color: marker.color || "#ffffff",
      radius: clamp(Number(marker.radius) || 5, 2, 16),
    });
  });

  const barsByTime = new Map(model.bars.map((bar) => [Number(bar.time), bar]));
  mergeCandleHighlights(model.candleHighlights).forEach((highlight) => {
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

  // A TOS wedge sits just beyond the bar extreme: WEDGE_UP ("^") above the
  // high, WEDGE_DOWN ("v") below the low. Size follows the bar spacing.
  (model.wedges || []).forEach((wedge) => {
    if (!timeIsNearViewport(wedge.time)) return;
    const bar = barsByTime.get(Number(wedge.time));
    const x = xForTime(wedge.time);
    if (!bar || x == null || !visibleX(x)) return;
    const above = wedge.position === "above";
    const y = yForPrice(above ? bar.high : bar.low);
    if (y == null) return;
    const half = clamp(barSpacing * 0.42, 3, 7);
    wedges.push({ x, y, above, half, color: wedge.color || (above ? "#ff3f00" : "#00ff00") });
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
      grade: typeof signal.grade?.letter === "string" && signal.grade.letter
        ? signal.grade.letter
        : undefined,
      gradeFailed: Number.isFinite(Number(signal.grade?.failedAt)),
      gradeBear: signal.grade?.direction === "bear",
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
    wedges,
    markers,
    patternLabels,
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
      geometry.backgroundLabels.forEach((label) => drawPlateLabel(context, label));
      context.restore();
    });
  }
}

// A chart label: optional dark plate behind it, optional right alignment.
function drawPlateLabel(context, label) {
  context.save();
  context.fillStyle = label.color;
  context.font = label.font || "800 8px system-ui, sans-serif";
  context.textAlign = "left";
  context.textBaseline = "middle";
  const textWidth = label.background || label.align === "right" ? context.measureText(label.text).width : 0;
  const x = label.align === "right" ? Math.max(4, label.x - textWidth) : label.x;
  if (label.background) {
    context.fillStyle = label.background;
    roundedRectangle(context, x - 4, label.y - 9, textWidth + 8, 18, 4);
    context.fill();
    context.fillStyle = label.color;
  }
  context.fillText(label.text, x, label.y);
  context.restore();
}

// Chart pattern marks: a ring on each touch, a bold ring on the breakout
// candle, and a filled arrow head at the measured target.
export function drawPatternMarker(context, marker) {
  const { x, y, radius, color } = marker;
  context.save();
  context.setLineDash([]);
  if (marker.kind === "arrowUp" || marker.kind === "arrowDown") {
    const tip = marker.kind === "arrowUp" ? -1 : 1;
    context.fillStyle = color;
    context.beginPath();
    context.moveTo(x, y);
    context.lineTo(x - radius, y - tip * radius * 1.4);
    context.lineTo(x + radius, y - tip * radius * 1.4);
    context.closePath();
    context.fill();
  } else {
    const breakout = marker.kind === "breakout";
    context.beginPath();
    context.arc(x, y, radius, 0, Math.PI * 2);
    context.fillStyle = colorWithOpacity(color, breakout ? 0.18 : 0.3);
    context.fill();
    context.strokeStyle = color;
    context.lineWidth = breakout ? 2.5 : 1.5;
    context.stroke();
  }
  context.restore();
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
    // Mirrors the primitive's interactionActive flag: during a pan/drag the
    // per-draw pane-edge clamp is suspended so bubbles track their candles.
    this.interactionActive = false;
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
      (this.geometry.markers || []).forEach((marker) => drawPatternMarker(context, marker));
      (this.geometry.patternLabels || []).forEach((label) => drawPlateLabel(context, label));
      context.font = "400 11px system-ui, sans-serif";
      context.textAlign = "center";
      context.textBaseline = "middle";
      const labelTops = nativeSignalLabelTops(this.geometry.labels, mediaSize.height, !this.interactionActive);
      // Open chevrons, TOS line weight 5 rendered ~2.5px.
      (this.geometry.wedges || []).forEach((wedge) => {
        const gap = 4;
        const tipY = wedge.above ? wedge.y - gap - wedge.half * 1.4 : wedge.y + gap + wedge.half * 1.4;
        const baseY = wedge.above ? wedge.y - gap : wedge.y + gap;
        context.save();
        context.strokeStyle = wedge.color;
        context.lineWidth = 2.5;
        context.lineJoin = "miter";
        context.lineCap = "butt";
        context.beginPath();
        context.moveTo(wedge.x - wedge.half, baseY);
        context.lineTo(wedge.x, tipY);
        context.lineTo(wedge.x + wedge.half, baseY);
        context.stroke();
        context.restore();
      });
      this.geometry.candleHighlights.forEach((highlight) => {
        const frame = nativeHighlightFrame(highlight);
        if (!frame) return;
        const pulse = nativeHighlightPulse(highlight.flashUntilMs);
        // Only while a brand-new signal flashes (about 3 seconds). A lasting
        // box and flame line around the candle read as part of the candle
        // ("I see box around the candle and it's confused", 2026-11-19); the
        // CALL and 🔥 tags already mark it, like TOS.
        if (!nativeHighlightShows(highlight)) return;
        const fireMotion = highlight.fire ? nativeFireMotion(Date.now(), highlight.x) : null;
        const glowStrength = highlight.intensity * (1 + pulse * 0.72 + (fireMotion?.flicker || 0) * 0.2);
        context.save();
        if (highlight.fire) {
          const glyphPoints = nativeFireGlyphPoints(highlight);

          // A thin frame outside the candle; the candle's own body and wick
          // stay visible inside it.
          context.lineCap = "round";
          context.lineJoin = "round";
          context.shadowColor = colorWithOpacity(highlight.color, 0.8);
          context.shadowBlur = 4 * glowStrength;
          context.strokeStyle = colorWithOpacity(highlight.color, 0.9 + pulse * 0.1);
          context.lineWidth = 1 + pulse * 0.6;
          roundedRectangle(context, frame.left, frame.top, frame.width, frame.height, 2);
          context.stroke();

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
        context.strokeStyle = colorWithOpacity(highlight.color, 0.85 + pulse * 0.15);
        context.shadowColor = colorWithOpacity(highlight.color, 0.75 + pulse * 0.2);
        context.shadowBlur = 4 * glowStrength;
        context.lineWidth = 1 + pulse * 0.6;
        roundedRectangle(context, frame.left, frame.top, frame.width, frame.height, 2);
        context.stroke();
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
        const left = nativeSignalBoxLeft(label.x, boxWidth, mediaSize.width, label.grade);
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
        if (label.grade) {
          const radius = NATIVE_SIGNAL_GRADE_RADIUS;
          const cx = left + boxWidth + radius + NATIVE_SIGNAL_GRADE_GAP;
          const cy = top + boxHeight / 2;
          const palette = label.gradeBear ? NATIVE_SIGNAL_GRADE_COLORS_BEAR : NATIVE_SIGNAL_GRADE_COLORS;
          const gradeColor = palette[label.grade] || palette.B;
          // A signal that has SINCE failed keeps its letter - the grade was
          // true when it was stamped and rewriting it would make the chart
          // lie about its own history - but it is drawn faded and struck
          // through so a dead A+ stops reading as a live one.
          const failed = label.gradeFailed === true;
          context.save();
          if (failed) context.globalAlpha *= NATIVE_SIGNAL_GRADE_FAILED_ALPHA;
          context.beginPath();
          context.arc(cx, cy, radius, 0, Math.PI * 2);
          context.fillStyle = "#0b1116";
          context.fill();
          context.lineWidth = 1.5;
          context.strokeStyle = gradeColor;
          context.stroke();
          context.fillStyle = gradeColor;
          context.font = "bold 8px sans-serif";
          context.fillText(label.grade, cx, cy + 0.5);
          if (failed) {
            // A slash, not a cross: one stroke stays legible at 7px radius
            // and still leaves the letter readable underneath.
            const reach = radius * 0.78;
            context.beginPath();
            context.moveTo(cx - reach, cy + reach);
            context.lineTo(cx + reach, cy - reach);
            context.lineWidth = 1.5;
            context.strokeStyle = gradeColor;
            context.stroke();
          }
          context.restore();
        }
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

  hasVisibleFire(now = Date.now()) {
    // A fire draws (and needs repainting) only during its new-signal flash.
    return this.signalRenderer.geometry?.candleHighlights?.some((highlight) => (
      highlight.fire && nativeHighlightShows(highlight, now)
    )) === true;
  }

  hasActiveAnimation(now = Date.now()) {
    // The High OI wall flash is a CSS glow on its axis chip (App.jsx), not a
    // canvas animation: a canvas pulse repainted every chart ~7x a second
    // forever and slowed chart loading on phones (2026-09-29).
    return !this.interactionActive && (this.hasActiveFlash(now) || this.hasVisibleFire());
  }

  setInteractionActive(active) {
    const nextActive = Boolean(active);
    if (this.interactionActive === nextActive) return;
    this.interactionActive = nextActive;
    // The signal renderer suspends its pane-edge label clamp while a gesture
    // is in flight so every bubble stays glued to its candle mid-pan.
    this.signalRenderer.interactionActive = nextActive;
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
    // The key must cover the VERTICAL mapping too. It originally held only the
    // logical range, pane size and barSpacing, so any price-scale change -
    // autoscale adjusting as new bars arrive, or a manual price drag - left
    // every cached shape at its old Y coordinate while the candles moved.
    // Reported as "indicators are not moving with the chart".
    //
    // Sampling priceToCoordinate captures the whole mapping in the key. TWO
    // probes pin down offset AND scale (one alone misses a zoom centred on
    // the probe), and each is quantised to half a pixel: raw price-range
    // endpoints in the key made every sub-pixel autoscale twitch during live
    // ticking a full recompute per paint, which is the exact churn the memo
    // exists to prevent. A mapping move under half a pixel cannot shift any
    // drawn shape visibly, so quantised probes lose nothing. If either probe
    // is unavailable (empty scale) fall back to the raw range endpoints so a
    // vertical change is never silently missed.
    const quantizedProbe = (price) => {
      const coordinate = Number(this.series.priceToCoordinate?.(price));
      return Number.isFinite(coordinate) ? Math.round(coordinate * 2) / 2 : null;
    };
    const probeA = quantizedProbe(PRICE_MAPPING_PROBE);
    const probeB = quantizedProbe(PRICE_MAPPING_PROBE * 2);
    const priceRange = this.series.priceScale?.()?.getVisibleRange?.() || {};
    const verticalKey = probeA != null && probeB != null
      ? `${probeA}|${probeB}`
      : `${Number(priceRange.from) || 0}|${Number(priceRange.to) || 0}`;
    const viewKey = `${Number(visibleRange.from) || 0}|${Number(visibleRange.to) || 0}`
      + `|${Number(paneSize.width) || 0}|${Number(paneSize.height) || 0}`
      + `|${Number(timeScale.options?.().barSpacing) || 0}`
      + `|${verticalKey}`;
    if (this.model === this.geometryModel && viewKey === this.geometryViewKey) return;
    const geometry = nativeGeometry(this.model, this.chart, this.series);
    this.geometryModel = this.model;
    this.geometryViewKey = viewKey;
    this.backgroundRenderer.setGeometry(geometry);
    this.signalRenderer.setGeometry(geometry);
  }
}
