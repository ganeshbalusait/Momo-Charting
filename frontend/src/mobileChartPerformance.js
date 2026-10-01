export const MOBILE_CHART_BREAKPOINT_PX = 760;
export const MOBILE_CHART_HISTORY_PRELOAD_BARS = 20;

export function isMobileChartWidth(width, breakpoint = MOBILE_CHART_BREAKPOINT_PX) {
  const parsedWidth = Number(width);
  const parsedBreakpoint = Number(breakpoint);
  return Number.isFinite(parsedWidth)
    && Number.isFinite(parsedBreakpoint)
    && parsedWidth > 0
    && parsedWidth <= parsedBreakpoint;
}

export function initialChartChainOpen({ embedded = false, isMobile = false, saved = null } = {}) {
  if (embedded) return true;
  // The phone has a dedicated Options destination. Mounting the full desktop
  // chain below the chart added hundreds of nodes and a second viewport of
  // layout work to every chart gesture, even though it was off-screen.
  if (isMobile) return false;
  return saved == null ? true : String(saved) === "true";
}

export function shouldAutoLoadDeepChartHistory({ isMobile = false, requested = false } = {}) {
  return !isMobile || requested;
}

export function shouldRequestDeepChartHistory({
  isMobile = false,
  historyLoading = false,
  alreadyRequested = false,
  logicalRange = null,
  barCount = 0,
  preloadBars = MOBILE_CHART_HISTORY_PRELOAD_BARS,
} = {}) {
  if (!isMobile || !historyLoading || alreadyRequested) return false;
  const from = Number(logicalRange?.from);
  const to = Number(logicalRange?.to);
  const count = Math.max(0, Math.floor(Number(barCount) || 0));
  const threshold = Math.max(0, Number(preloadBars) || 0);
  if (!Number.isFinite(from) || !Number.isFinite(to) || !(to > from) || count <= 0) return false;
  // Logical index zero is the first compact candle. Begin the deep pull just
  // before it reaches the viewport so the extra tape arrives while the user is
  // still reading the overlap, like TradingView's paged history.
  return from <= Math.min(threshold, Math.max(0, count - 1));
}
