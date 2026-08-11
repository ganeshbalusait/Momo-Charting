export function chartOpeningHistorySignature(symbol, bars) {
  const source = Array.isArray(bars) ? bars : [];
  if (!source.length) return "";
  const normalizedSymbol = String(symbol || "").trim().toUpperCase();
  const firstTime = Number(source[0]?.time || 0);
  return `${normalizedSymbol}-${Number.isFinite(firstTime) ? firstTime : 0}`;
}

// TradingView frames candles at roughly this width and lets the pane decide
// how many fit, which is why a wider window there shows MORE history rather
// than fatter candles.
const TRADINGVIEW_CANDLE_PITCH_PX = 7;

export function chartDefaultHistorySlots({
  timeframeMinutes,
  isBigScreen = false,
  chartWidth = 0,
} = {}) {
  const width = Number(chartWidth) || 0;
  if (width > 0) {
    // Derive the candle count from the pane instead of a fixed number. The
    // fixed counts made a BIG screen show FEWER candles than a normal one
    // (32 vs 72 intraday, 80 vs 120 on 4H), so the more screen the user gave
    // the chart the more zoomed-in it looked - the opposite of TradingView.
    // Reserve ~22% of the pane for the forward projection area.
    const slots = Math.round((width * 0.78) / TRADINGVIEW_CANDLE_PITCH_PX);
    return Math.max(60, Math.min(400, slots));
  }
  const minutes = Number(timeframeMinutes || 0);
  // Width-less fallback (headless/first paint before layout settles).
  if (minutes === 240) return isBigScreen ? 120 : 160;
  return isBigScreen ? 32 : 72;
}

export function chartDefaultFutureSlots({ historySlots, isBigScreen = false } = {}) {
  const history = Math.max(1, Math.floor(Number(historySlots) || 1));
  // A projection area is useful for forward session lines and signal labels,
  // but the former 1.18× normal-chart setting put up to seven hours of empty
  // 5-minute space after every ticker. Keep a compact live-chart edge while
  // retaining a little more room in the intentional big-screen workspace.
  if (isBigScreen) return Math.max(10, Math.min(32, Math.round(history * 0.9)));
  return Math.max(8, Math.min(24, Math.round(history * 0.3)));
}

export function chartCandleLogicalWindow({
  bars,
  historySlots,
  futureSlots = 0,
  timeToIndex,
} = {}) {
  const source = Array.isArray(bars) ? bars : [];
  if (!source.length || typeof timeToIndex !== "function") return null;
  const history = Math.max(1, Math.min(source.length, Math.floor(Number(historySlots) || 1)));
  const projection = Math.max(0, Number(futureSlots) || 0);
  const firstTime = Number(source[source.length - history]?.time || 0);
  const latestTime = Number(source.at(-1)?.time || 0);
  const from = Number(timeToIndex(firstTime));
  const latest = Number(timeToIndex(latestTime));
  if (![from, latest].every(Number.isFinite) || latest < from) return null;
  // timeToIndex is called with findNearest, so when the series is not yet
  // fully on the time scale it silently CLAMPS an out-of-range timestamp to
  // the nearest index it does have. That collapses the window: on 2026-08-10
  // every 4H chart opened showing ~4 enormous candles despite a 1,099-bar
  // series, because "120 candles ago" resolved to a point next to the latest.
  //
  // Reject a scale that is still clamping older timestamps to its current
  // edge. A healthy shared scale is at least one logical slot per candle (and
  // commonly eight slots per 4H candle because of 30m studies). During the
  // regression a 131-candle request resolved to only 13 slots, which cleared
  // the old absolute 12-slot floor and left five giant candles on screen.
  const MINIMUM_USABLE_SPAN = 12;
  const minimumResolvedSpan = Math.min(
    history - 1,
    Math.max(MINIMUM_USABLE_SPAN, Math.floor(history * 0.5)),
  );
  if (latest - from < minimumResolvedSpan) return null;
  // Use the actual candle timestamps. Higher-timeframe charts can share the
  // time scale with denser 30-minute study points, so subtracting 72 logical
  // indexes may show only nine 4H candles instead of the requested 72.
  return { from, to: latest + projection };
}

export function chartLayoutProfileStorageKey(isMaximized, timeframeKey, workspaceScope = "") {
  const timeframe = String(timeframeKey || "5m").trim() || "5m";
  const screen = isMaximized ? "fullscreen" : "standard";
  const scope = String(workspaceScope || "").trim().replace(/[^a-zA-Z0-9:_-]/g, "");
  return scope ? `${screen}:${scope}:${timeframe}` : `${screen}:${timeframe}`;
}

export const CHART_PANE_SIZING_VERSION = 2;

export function defaultChartPaneFactors(isBigScreen = false) {
  // Keep the price chart dominant without making the two lower studies appear
  // to have different importance. The same rule applies to normal, big-screen,
  // and detached-chart surfaces.
  return isBigScreen ? [7.5, 1.1, 1.1] : [6, 1.2, 1.2];
}

export function restoreChartPaneFactors({
  paneFactors,
  paneSizingVersion,
  isBigScreen = false,
} = {}) {
  const defaults = defaultChartPaneFactors(isBigScreen);
  const saved = Array.isArray(paneFactors) ? paneFactors.map(Number) : [];
  const validFactor = (value) => Number.isFinite(value) && value > 0;
  const main = validFactor(saved[0]) ? saved[0] : defaults[0];
  const lower = [saved[1], saved[2]];

  if (Number(paneSizingVersion) >= CHART_PANE_SIZING_VERSION) {
    return [
      main,
      validFactor(lower[0]) ? lower[0] : defaults[1],
      validFactor(lower[1]) ? lower[1] : defaults[2],
    ];
  }

  // Layouts saved before v2 intentionally used unequal lower panes. Preserve
  // their total lower-study space but distribute it evenly on the first load.
  const savedLower = lower.filter(validFactor);
  const equalLower = savedLower.length
    ? savedLower.reduce((total, factor) => total + factor, 0) / savedLower.length
    : defaults[1];
  return [main, equalLower, equalLower];
}

export function chartLayoutAutosaveContextMatches(queuedProfileKey, currentProfileKey) {
  const queued = String(queuedProfileKey || "");
  const current = String(currentProfileKey || "");
  return Boolean(queued) && queued === current;
}

export function chartZoomOutMaximumHalfRange({
  candleCount,
  futureSlots = 0,
  minimumHalfRange = 24,
} = {}) {
  const candles = Math.max(1, Math.floor(Number(candleCount) || 0));
  const projection = Math.max(0, Math.floor(Number(futureSlots) || 0));
  const minimum = Math.max(4, Number(minimumHalfRange) || 24);
  // Allow room for both loaded candles and the intentional future projection,
  // while keeping a higher-timeframe chart bounded by its own candle count.
  return Math.max(minimum, (candles + projection) * 1.25);
}

export function sanitizeChartLayoutVisibleSpan({
  visibleSpan,
  candleCount,
  futureSlots = 0,
  minimumSpan = 8,
} = {}) {
  const savedSpan = Number(visibleSpan);
  const minimum = Math.max(1, Number(minimumSpan) || 8);
  if (!Number.isFinite(savedSpan) || savedSpan < minimum) return null;
  const maximum = chartZoomOutMaximumHalfRange({ candleCount, futureSlots }) * 2;
  return Math.max(minimum, Math.min(savedSpan, maximum));
}

export function chartIndicatorProfileStorageKey(timeframeKey) {
  return String(timeframeKey || "5m").trim() || "5m";
}

export function workspaceCompanionWidthAtPointer({
  containerLeft,
  containerWidth,
  pointerX,
  minimumChainWidth = 340,
  minimumChartWidth = 420,
  dividerWidth = 10,
}) {
  const left = Number(containerLeft);
  const width = Number(containerWidth);
  const x = Number(pointerX);
  if (![left, width, x].every(Number.isFinite) || width <= 0) return null;
  const minimum = Math.max(240, Number(minimumChainWidth) || 340);
  const maximum = Math.max(
    minimum,
    width - Math.max(320, Number(minimumChartWidth) || 420) - Math.max(0, Number(dividerWidth) || 0),
  );
  return Math.round(Math.min(maximum, Math.max(minimum, left + width - x)));
}

export function clampExpandedPriceRangeToCandles({
  candleLow,
  candleHigh,
  low,
  high,
  maxSpanMultiple = 3,
} = {}) {
  const cLow = Number(candleLow);
  const cHigh = Number(candleHigh);
  const eLow = Number(low);
  const eHigh = Number(high);
  if (![cLow, cHigh, eLow, eHigh].every(Number.isFinite) || cHigh < cLow) return { low, high };
  // OI walls and stacked signal bubbles may widen the scale, but the candles
  // must stay the dominant content. Without a cap, a short pane with many
  // bubbles inflated the range to ~10x the candle span, squeezing candles
  // into a thin drifting band while live updates re-derived the expansion.
  const candleSpan = Math.max(cHigh - cLow, Math.abs(cHigh || 1) * 0.001, 0.02);
  const maxSpan = candleSpan * Math.max(1, Number(maxSpanMultiple) || 3);
  const expandedLow = Math.min(eLow, cLow);
  const expandedHigh = Math.max(eHigh, cHigh);
  if (expandedHigh - expandedLow <= maxSpan) return { low: expandedLow, high: expandedHigh };
  const belowExpansion = cLow - expandedLow;
  const aboveExpansion = expandedHigh - cHigh;
  const totalExpansion = Math.max(belowExpansion + aboveExpansion, 1e-9);
  const factor = Math.max(0, maxSpan - candleSpan) / totalExpansion;
  return {
    low: cLow - belowExpansion * factor,
    high: cHigh + aboveExpansion * factor,
  };
}

export function priceRangeNeedsUpdate(currentRange, nextRange, relativeTolerance = 0.0005) {
  const currentFrom = Number(currentRange?.from);
  const currentTo = Number(currentRange?.to);
  const nextFrom = Number(nextRange?.from);
  const nextTo = Number(nextRange?.to);
  if (![nextFrom, nextTo].every(Number.isFinite) || nextTo <= nextFrom) return false;
  if (![currentFrom, currentTo].every(Number.isFinite) || currentTo <= currentFrom) return true;
  const span = Math.max(Math.abs(currentTo - currentFrom), Math.abs(nextTo - nextFrom), 1e-9);
  const tolerance = span * Math.max(0, Number(relativeTolerance) || 0);
  return Math.abs(currentFrom - nextFrom) > tolerance
    || Math.abs(currentTo - nextTo) > tolerance;
}

export function chartBodyDragLogicalRange(
  logicalRange,
  {
    startX,
    currentX,
    plotWidth,
    firstCandleIndex,
    latestCandleIndex,
    minimumVisibleCandles = 0,
  } = {},
) {
  const from = Number(logicalRange?.from);
  const to = Number(logicalRange?.to);
  const origin = Number(startX);
  const cursor = Number(currentX);
  const width = Number(plotWidth);
  if (![from, to, origin, cursor, width].every(Number.isFinite) || to <= from || width <= 0) return null;
  const logicalShift = -((cursor - origin) / width) * (to - from);
  const shifted = { from: from + logicalShift, to: to + logicalShift };
  const first = Number(firstCandleIndex);
  const latest = Number(latestCandleIndex);
  const requestedVisible = Math.max(0, Number(minimumVisibleCandles) || 0);
  if (![first, latest].every(Number.isFinite) || latest < first || requestedVisible <= 0) return shifted;

  // TradingView permits projection space, but a single fast drag should not
  // strand the complete tape outside the pane. Keep a small candle foothold
  // at either edge; the trader can still pan through the entire history.
  const visibleCandles = Math.max(1, Math.min(requestedVisible, latest - first + 1));
  const maximumFrom = latest - visibleCandles + 1;
  const minimumTo = first + visibleCandles - 1;
  if (shifted.from > maximumFrom) {
    const correction = maximumFrom - shifted.from;
    return { from: shifted.from + correction, to: shifted.to + correction };
  }
  if (shifted.to < minimumTo) {
    const correction = minimumTo - shifted.to;
    return { from: shifted.from + correction, to: shifted.to + correction };
  }
  return shifted;
}

export function chartBodyDragPriceRange(
  priceRange,
  { startY, currentY, plotHeight } = {},
) {
  const from = Number(priceRange?.from);
  const to = Number(priceRange?.to);
  const origin = Number(startY);
  const cursor = Number(currentY);
  const height = Number(plotHeight);
  if (![from, to, origin, cursor, height].every(Number.isFinite) || to <= from || height <= 0) return null;
  // Dragging down slides the plot down: the same price maps lower on screen,
  // so the visible range moves up by the dragged fraction of its span.
  const priceShift = ((cursor - origin) / height) * (to - from);
  return { from: from + priceShift, to: to + priceShift };
}

export const OI_CHART_PANE_FACTORS_STORAGE_KEY = "oiFinderChartPaneFactors";

// Pane stretch factors are the one viewport property that auto-persists.
// Framing (time span / price range) stays explicit-save-only: cycling those
// through storage is what caused the 2026-08-05 layout ratchet. Factors are
// absolute values with no clamp-and-re-expand loop, so they are safe.
export function readStoredChartPaneFactors(storage, profileKey) {
  const key = String(profileKey || "").trim();
  if (!key) return null;
  try {
    const raw = storage?.getItem?.(OI_CHART_PANE_FACTORS_STORAGE_KEY);
    const parsed = typeof raw === "string" && raw.trim() ? JSON.parse(raw) : null;
    const entry = parsed && typeof parsed === "object" ? parsed[key] : null;
    const factors = Array.isArray(entry?.paneFactors) ? entry.paneFactors.map(Number) : [];
    if (!factors.length || !factors.every((value) => Number.isFinite(value) && value > 0)) {
      return null;
    }
    return {
      paneFactors: factors,
      paneSizingVersion: Number(entry?.paneSizingVersion) || 0,
    };
  } catch {
    return null;
  }
}

export function storeChartPaneFactors(storage, profileKey, paneFactors) {
  const key = String(profileKey || "").trim();
  const factors = Array.isArray(paneFactors) ? paneFactors.map(Number) : [];
  if (
    !key
    || typeof storage?.setItem !== "function"
    || !factors.length
    || !factors.every((value) => Number.isFinite(value) && value > 0)
  ) return false;
  try {
    const raw = storage.getItem?.(OI_CHART_PANE_FACTORS_STORAGE_KEY);
    const parsed = typeof raw === "string" && raw.trim() ? JSON.parse(raw) : null;
    const entries = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    entries[key] = {
      paneFactors: factors,
      paneSizingVersion: CHART_PANE_SIZING_VERSION,
      savedAt: new Date().toISOString(),
    };
    storage.setItem(OI_CHART_PANE_FACTORS_STORAGE_KEY, JSON.stringify(entries));
    return true;
  } catch {
    return false;
  }
}

export function paneFactorsMateriallyDiffer(current, reference, relativeTolerance = 0.01) {
  const left = Array.isArray(current) ? current.map(Number) : null;
  const right = Array.isArray(reference) ? reference.map(Number) : null;
  if (!left || !right || left.length !== right.length) return true;
  return left.some((value, index) => {
    const other = right[index];
    if (!Number.isFinite(value) || !Number.isFinite(other)) return true;
    const scale = Math.max(Math.abs(value), Math.abs(other), 1e-9);
    return Math.abs(value - other) / scale > relativeTolerance;
  });
}

export function chartAnchorTranslation(referenceAnchor, currentAnchor) {
  const values = [referenceAnchor?.x, referenceAnchor?.y, currentAnchor?.x, currentAnchor?.y];
  if (values.some((value) => value == null)) return null;
  const [referenceX, referenceY, currentX, currentY] = values.map(Number);
  if (![referenceX, referenceY, currentX, currentY].every(Number.isFinite)) return null;
  return {
    x: currentX - referenceX,
    y: currentY - referenceY,
  };
}
