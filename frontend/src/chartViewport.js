export function chartOpeningHistorySignature(symbol, bars) {
  const source = Array.isArray(bars) ? bars : [];
  if (!source.length) return "";
  const normalizedSymbol = String(symbol || "").trim().toUpperCase();
  const firstTime = Number(source[0]?.time || 0);
  return `${normalizedSymbol}-${Number.isFinite(firstTime) ? firstTime : 0}`;
}

// Candle DENSITY is the thing that makes a chart read as zoomed out, not the
// candle count on its own. Capping the count at 120 meant a 1920px pane spread
// those 120 bars over the full width at ~15px each - technically "120 candles
// visible", visually fat and zoomed in.
//
// So the pitch is the fixed quantity and the count follows from the pane.
// 6px sits in the middle of the 4-7px target band.
const TRADINGVIEW_DEFAULT_CANDLE_PITCH_PX = 4;
// Floor keeps a narrow panel usable; ceiling stops a 4K pane opening on a
// thousand slivers. A 1200px pane lands ~200, a 1920px pane ~320.
const TRADINGVIEW_DEFAULT_MIN_CANDLES = 150;
const TRADINGVIEW_DEFAULT_MAX_CANDLES = 600;
// Hard ceiling on the applied barSpacing. The old value was 16px, which let a
// wide pane force fat candles no matter how the range was computed.
export const TRADINGVIEW_MAX_BAR_SPACING_PX = 7;
// Let the trader keep zooming out well past the opening density.
export const TRADINGVIEW_MIN_BAR_SPACING_PX = 2;

// Lightweight Charts applies minBarSpacing to every timestamp on the shared
// time scale, not just to candlesticks. A 4H candle can therefore span eight
// logical slots when 30m studies are present. Keeping a fixed 2px logical
// floor turns that into a 16px minimum candle pitch and makes 4H look zoomed
// in even after the correct candle range is selected.
export function chartMinimumLogicalBarSpacing({ bars, timeToIndex } = {}) {
  const slotsPerCandle = chartLogicalSlotsPerCandle({ bars, timeToIndex });
  return Math.max(0.25, TRADINGVIEW_MIN_BAR_SPACING_PX / slotsPerCandle);
}

export function chartLogicalTimeScaleSpacing({
  candlePitch = TRADINGVIEW_DEFAULT_CANDLE_PITCH_PX,
  futureSlots = 0,
  bars,
  timeToIndex,
} = {}) {
  const slotsPerCandle = chartLogicalSlotsPerCandle({ bars, timeToIndex });
  const minBarSpacing = Math.max(0.25, TRADINGVIEW_MIN_BAR_SPACING_PX / slotsPerCandle);
  // Lightweight Charts measures barSpacing and rightOffset in SHARED logical
  // points. A 4H candle spans eight such points when 30m studies are present,
  // so applying a 4px candle pitch directly makes each candle 32px wide. This
  // is the 5m -> 4H switch regression that left only about five days visible
  // whenever the timestamp-based opening window had not settled yet.
  return {
    barSpacing: Math.max(
      minBarSpacing,
      Math.max(0.25, Number(candlePitch) || TRADINGVIEW_DEFAULT_CANDLE_PITCH_PX) / slotsPerCandle,
    ),
    rightOffset: Math.max(0, Number(futureSlots) || 0) * slotsPerCandle,
    minBarSpacing,
    slotsPerCandle,
  };
}

export function chartExplicitSavedLogicalRange({
  latestIndex,
  visibleSpan,
  latestRatio,
  candleCount,
  slotsPerCandle = 1,
  futureSlots = 5,
} = {}) {
  const latest = Number(latestIndex);
  const requestedSpan = Number(visibleSpan);
  const count = Math.max(0, Math.floor(Number(candleCount) || 0));
  const density = Math.max(1, Number(slotsPerCandle) || 1);
  if (!Number.isFinite(latest) || !Number.isFinite(requestedSpan) || requestedSpan < 4 || !count) return null;
  const maximumSpan = Math.max(8, (count + Math.max(0, Number(futureSlots) || 0)) * density);
  const span = Math.max(8, Math.min(requestedSpan, maximumSpan));
  const ratio = Math.max(0.05, Math.min(0.95, Number(latestRatio) || 0.8));
  const from = latest - span * ratio;
  return { from, to: from + span };
}

export function chartDefaultHistorySlots({
  timeframeMinutes,
  isBigScreen = false,
  chartWidth = 0,
} = {}) {
  // SETTLED against the user's own reference. A 20-candle branch for
  // timeframeMinutes >= 240 has been added, removed and re-added by three
  // sessions today; every session has disclaimed it. It is dropped here on
  // evidence rather than preference:
  //
  //  - The user supplied their TradingView 4H chart as the target. TradingView
  //    draws ONE candle width on every timeframe; a 4H candle is not wider
  //    than a 5m one. A per-timeframe count contradicts the reference.
  //  - It left 4H/D/W opening on 20 candles while 5m/1h opened on 166 in the
  //    same pane - an 8x inconsistency with no basis in the reference.
  //  - The user objected to this exact view by name ("higher timeframe zoomed
  //    out 20 last candles") and reported "still zoomed in" repeatedly.
  //
  // The complaint it was presumably added to fix had a different cause:
  // oiChartNeedsInitialStudySeed only seeded 4H, so higher timeframes rendered
  // off the ~5-day one-minute tape and had just 17-20 candles IN EXISTENCE.
  // Capping the viewport matched that symptom without addressing it. With the
  // seed fixed the full tape is present, so the cap only hides it.
  //
  // Anyone restoring a per-timeframe count should bring a reference that
  // contradicts the one above, and settle it with the user first.
  const width = Number(chartWidth) || 0;
  if (width > 0) {
    // Count follows from the pane at a fixed density, so a wider chart shows
    // MORE history at the same candle width - which is what "zoomed out" means
    // visually. Deriving the count first and letting the pitch fall out of it
    // is what produced 15px candles on a big screen.
    const slots = Math.round(width / TRADINGVIEW_DEFAULT_CANDLE_PITCH_PX);
    return Math.max(
      TRADINGVIEW_DEFAULT_MIN_CANDLES,
      Math.min(TRADINGVIEW_DEFAULT_MAX_CANDLES, slots),
    );
  }
  // Width-less first paint and headless callers use the same lower-timeframe
  // fallback. Higher timeframes returned their explicit 20-candle view above.
  return 150;
}

export function chartTrailingSessionHistorySlots({
  bars,
  sessionCount = 5,
  sessionKey,
} = {}) {
  const source = Array.isArray(bars) ? bars : [];
  const requestedSessions = Math.max(1, Math.floor(Number(sessionCount) || 1));
  if (!source.length || typeof sessionKey !== "function") return 0;

  const sessions = new Set();
  let historySlots = 0;
  for (let index = source.length - 1; index >= 0; index -= 1) {
    const key = String(sessionKey(source[index], index) || "").trim();
    if (!key) continue;
    if (!sessions.has(key) && sessions.size >= requestedSessions) break;
    sessions.add(key);
    historySlots += 1;
  }
  return historySlots;
}

export function chartDefaultFutureSlots({ historySlots, isBigScreen = false } = {}) {
  const history = Math.max(1, Math.floor(Number(historySlots) || 1));
  // A projection area is useful for forward session lines and signal labels,
  // but it is empty space: every slot spent here is a candle not shown. The
  // big-screen branch used history * 0.9 capped at 32, so in a ~600px
  // workspace pane 32 of ~99 slots - a THIRD of the chart - were blank, and
  // the chart read as zoomed in however many candles were loaded.
  //
  // Keep only 5-10 empty bars after the latest candle. The previous 18% rule
  // could reserve more than twenty slots and make the real candles look
  // squeezed left even when the historical range itself was correct.
  return Math.max(5, Math.min(10, Math.round(history * 0.08)));
}

export function chartLogicalSlotsPerCandle({ bars, timeToIndex } = {}) {
  const source = Array.isArray(bars) ? bars : [];
  if (source.length < 2 || typeof timeToIndex !== "function") return 1;
  const deltas = [];
  const firstSample = Math.max(1, source.length - 12);
  for (let index = firstSample; index < source.length; index += 1) {
    const previous = Number(timeToIndex(Number(source[index - 1]?.time || 0)));
    const current = Number(timeToIndex(Number(source[index]?.time || 0)));
    const delta = current - previous;
    if (Number.isFinite(delta) && delta > 0) deltas.push(delta);
  }
  if (!deltas.length) return 1;
  deltas.sort((left, right) => left - right);
  return Math.max(1, deltas[Math.floor(deltas.length / 2)]);
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
  const density = chartLogicalSlotsPerCandle({ bars: source, timeToIndex });
  if (latest - from < minimumResolvedSpan) {
    // Detecting the clamp was right; returning null was not. The caller's
    // fallback re-frames from barSpacing alone and does not restore the
    // requested candle count, so a clamped scale left the chart on whatever
    // slice the clamp produced. Measured live on MSFT 4H: 1,107 candles
    // loaded, "150 candles ago" clamped to index 1055, and the chart opened
    // on the last 52 - the persistent "still zoomed in" report.
    //
    // `latest` is trustworthy even mid-load (the newest candle is always on
    // the scale), and density is sampled from the most recent bars, which are
    // on it too. So derive the window by index instead of abandoning it.
    const indexFrom = latest - history * density;
    if (!Number.isFinite(indexFrom)) return null;
    return { from: indexFrom, to: latest + projection * density };
  }
  // Use the actual candle timestamps. Higher-timeframe charts can share the
  // time scale with denser 30-minute study points, so subtracting 72 logical
  // indexes may show only nine 4H candles instead of the requested 72.
  // Scale the right-side gap by that same logical density: eight requested
  // blank 4H bars must remain eight candle-widths even when 30m study points
  // place eight logical indexes between adjacent 4H candles.
  const slotsPerCandle = chartLogicalSlotsPerCandle({ bars: source, timeToIndex });
  return { from, to: latest + projection * slotsPerCandle };
}

export function chartLayoutProfileStorageKey(isMaximized, timeframeKey, workspaceScope = "") {
  const timeframe = String(timeframeKey || "5m").trim() || "5m";
  const screen = isMaximized ? "fullscreen" : "standard";
  const scope = String(workspaceScope || "").trim().replace(/[^a-zA-Z0-9:_-]/g, "");
  return scope ? `${screen}:${scope}:${timeframe}` : `${screen}:${timeframe}`;
}

export const CHART_PANE_SIZING_VERSION = 2;
// v1 chart-layout viewports were saved before candle pitch was converted into
// the shared logical-point scale. A saved 4H range could therefore restore the
// five-day, fat-candle view even after the opening-zoom fix. Ignore those old
// horizontal ranges once; the next explicit Save writes a corrected v2 range.
// Bumped to 3 on 2026-08-11. Saved viewports written earlier were captured
// while a 20-candle higher-timeframe cap was live, so 4H/D/W profiles hold a
// span of roughly twenty candles. A saved profile is re-applied on every study
// rebuild (App.jsx: restoreChartLayoutProfile), which snapped the chart back
// to that narrow span AFTER the opening framing had already run correctly -
// and it lives in localStorage, so no reload could clear it. Bumping the
// version retires those entries without anyone editing browser storage; the
// Save button writes v3 immediately.
export const CHART_LAYOUT_VIEWPORT_VERSION = 3;

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

export function chartShouldFrameAutomaticViewport({
  initialView = false,
  restoredViewport = false,
  manualNavigation = false,
  followsLatest = false,
  studyStage = 0,
  previousStudyStage = -1,
} = {}) {
  if (restoredViewport || manualNavigation) return false;
  if (initialView) return true;
  // Initial indicators may add denser timestamps one stage at a time. Reframe
  // only when that opening ladder reaches a stage not already handled. A live
  // quote restarts at stage zero, which must not reset the current zoom.
  return Boolean(followsLatest) && Number(studyStage) > Number(previousStudyStage);
}

// v2 deliberately abandons the v1 payload rather than migrating it.
//
// v1 stored the visible span in LOGICAL INDEX units. Indexes shift whenever
// study series add or drop points and when the shallow fast-paint tape is
// replaced by full history, so save -> restore -> remeasure -> save was not
// idempotent: it drifted, which is the 2026-08-05 layout ratchet that
// chartViewport.js documents a rule against. Every stored v1 entry is
// therefore untrustworthy, and a live one was pinning charts to a range set
// hours earlier - the "still zoomed in on any ticker" report.
//
// v2 stores SECONDS, which no reflow can change. Changing the key also means
// stale v1 entries are ignored without anyone having to clear storage.
export const OI_CHART_TIME_VIEWPORT_SESSION_KEY = "oiFinderChartTimeViewports.v2";

// A minute of chart is the smallest span worth restoring; below that a stray
// pinch would persist as an unusable view.
const MINIMUM_VIEWPORT_SPAN_SECONDS = 60;

export function readChartTimeViewport(storage, viewportKey) {
  const key = String(viewportKey || "").trim();
  if (!key) return null;
  try {
    const parsed = JSON.parse(storage?.getItem?.(OI_CHART_TIME_VIEWPORT_SESSION_KEY) || "{}");
    const entry = parsed?.[key];
    const spanSeconds = Number(entry?.spanSeconds);
    const forwardSeconds = Number(entry?.forwardSeconds);
    if (!Number.isFinite(spanSeconds) || spanSeconds < MINIMUM_VIEWPORT_SPAN_SECONDS) return null;
    return {
      spanSeconds,
      forwardSeconds: Number.isFinite(forwardSeconds) && forwardSeconds >= 0 ? forwardSeconds : 0,
    };
  } catch {
    return null;
  }
}

export function storeChartTimeViewport(storage, viewportKey, viewport) {
  const key = String(viewportKey || "").trim();
  const spanSeconds = Number(viewport?.spanSeconds);
  const forwardSeconds = Number(viewport?.forwardSeconds);
  if (!key || typeof storage?.setItem !== "function"
    || !Number.isFinite(spanSeconds) || spanSeconds < MINIMUM_VIEWPORT_SPAN_SECONDS) return false;
  try {
    const parsed = JSON.parse(storage.getItem?.(OI_CHART_TIME_VIEWPORT_SESSION_KEY) || "{}");
    const entries = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    entries[key] = {
      spanSeconds,
      forwardSeconds: Number.isFinite(forwardSeconds) && forwardSeconds >= 0 ? forwardSeconds : 0,
    };
    storage.setItem(OI_CHART_TIME_VIEWPORT_SESSION_KEY, JSON.stringify(entries));
    return true;
  } catch {
    return false;
  }
}

/** Restored window: the remembered ZOOM, always anchored to the newest candle.
 *
 * Remembering the scroll POSITION as well is what made a range saved days
 * earlier strand the chart in old data (an AMZN 4H chart reopened on
 * 2026-08-06). The user asked for TradingView-style zoom memory, so the span
 * is what persists; the view still follows the live edge.
 */
export function chartTimeViewportRange(latestCandleTime, viewport) {
  const latest = Number(latestCandleTime);
  const spanSeconds = Number(viewport?.spanSeconds);
  if (!Number.isFinite(latest) || latest <= 0) return null;
  if (!Number.isFinite(spanSeconds) || spanSeconds < MINIMUM_VIEWPORT_SPAN_SECONDS) return null;
  const forwardSeconds = Math.max(0, Math.min(
    Number(viewport?.forwardSeconds) || 0,
    spanSeconds * 0.5,
  ));
  const to = latest + forwardSeconds;
  return { from: to - spanSeconds, to };
}

export function clearChartTimeViewport(storage, viewportKey) {
  const key = String(viewportKey || "").trim();
  if (!key || typeof storage?.setItem !== "function") return false;
  try {
    const parsed = JSON.parse(storage.getItem?.(OI_CHART_TIME_VIEWPORT_SESSION_KEY) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed) || !(key in parsed)) return true;
    delete parsed[key];
    storage.setItem(OI_CHART_TIME_VIEWPORT_SESSION_KEY, JSON.stringify(parsed));
    return true;
  } catch {
    return false;
  }
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

export function chartZoomLogicalRange({
  logicalRange,
  direction,
  firstCandleIndex,
  latestCandleIndex,
  futureSlots = 0,
  minimumSpan = 8,
} = {}) {
  const from = Number(logicalRange?.from);
  const to = Number(logicalRange?.to);
  if (![from, to].every(Number.isFinite) || to <= from) return null;
  const span = to - from;
  const minimum = Math.max(2, Number(minimumSpan) || 8);

  if (direction === "in") {
    const nextSpan = Math.max(minimum, span * 0.68);
    const center = (from + to) / 2;
    return { from: center - nextSpan / 2, to: center + nextSpan / 2 };
  }

  const first = Number(firstCandleIndex);
  const latest = Number(latestCandleIndex);
  if (![first, latest].every(Number.isFinite) || latest < first) {
    const center = (from + to) / 2;
    const nextSpan = Math.max(minimum, span * 2);
    return { from: center - nextSpan / 2, to: center + nextSpan / 2 };
  }

  // TradingView's zoom-out keeps the live edge on screen and spends the new
  // space on older candles.  The previous center-based zoom kept doubling
  // into future whitespace; after a few clicks every candle was crushed
  // against the left edge even though hundreds of history bars were loaded.
  const projection = Math.max(0, Number(futureSlots) || 0);
  const maximumTo = latest + projection;
  const maximumSpan = Math.max(minimum, maximumTo - first);
  const nextSpan = Math.min(maximumSpan, Math.max(minimum, span * 2));
  if (nextSpan >= maximumSpan - 0.001) return { from: first, to: maximumTo };

  const liveRatio = latest >= from && latest <= to
    ? (latest - from) / span
    : 0.78;
  const anchorRatio = Math.max(0.62, Math.min(0.9, liveRatio));
  let nextFrom = latest - nextSpan * anchorRatio;
  let nextTo = nextFrom + nextSpan;
  if (nextTo > maximumTo) {
    const shift = nextTo - maximumTo;
    nextFrom -= shift;
    nextTo -= shift;
  }
  if (nextFrom < first) {
    const shift = first - nextFrom;
    nextFrom += shift;
    nextTo += shift;
  }
  return { from: nextFrom, to: nextTo };
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
