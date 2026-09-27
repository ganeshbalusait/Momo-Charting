// ThinkorSwim keeps a horizontal scrollbar directly beneath the time axis: a
// track that represents the whole loaded tape, a thumb whose width is the share
// of that tape currently on screen, and step arrows at the right end. Dragging
// the thumb is the only time-navigation gesture in TOS that gives the trader an
// absolute sense of "where am I in the history" - the candles alone never do,
// because a zoomed-in 5m view looks identical at 09:35 and at 15:35.
//
// Everything here is pure logical-index arithmetic so it can be unit tested
// without a DOM or a chart instance, matching the other chart* modules. The
// caller owns the Lightweight Charts time scale and simply feeds us the current
// visible logical range plus the bar count.

// A thumb narrower than this is impossible to grab on a touchpad, so once the
// proportional width would fall below it we pin the width and let the thumb
// travel over a correspondingly shorter run of track instead.
export const CHART_SCROLLBAR_MIN_THUMB_PX = 28;

// Never let a scroll gesture push every candle off screen. Both the drag clamp
// and the wheel-pan clamp keep at least this many real bars in view, which is
// the same "a blank chart is never an acceptable resting state" rule the study
// seed retry logic already follows.
export const CHART_SCROLLBAR_MIN_VISIBLE_BARS = 6;

// A track click pages by slightly less than a full screen so the trader keeps
// a sliver of overlap for visual continuity, exactly like TOS.
export const CHART_SCROLLBAR_PAGE_FRACTION = 0.9;

function finiteNumber(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function normalizeLogicalRange(logicalRange) {
  const from = finiteNumber(logicalRange?.from);
  const to = finiteNumber(logicalRange?.to);
  if (from === null || to === null) return null;
  const span = to - from;
  if (!(span > 0)) return null;
  return { from, to, span };
}

// The last logical index the chart is willing to render: the final loaded bar
// plus whatever forward breathing room `rightOffset` reserves. Future slots are
// part of the track because the trader can legitimately park the view out
// there, and a thumb that could not reach the right edge would read as broken.
export function chartScrollbarLastIndex({ barCount, futureSlots = 0 } = {}) {
  const bars = Math.max(0, Math.floor(finiteNumber(barCount) ?? 0));
  const future = Math.max(0, finiteNumber(futureSlots) ?? 0);
  return Math.max(0, bars - 1) + future;
}

// The track always contains the current window, even when the trader has zoomed
// out past the loaded tape or panned into negative logical space. Growing the
// track instead of clamping the view keeps the thumb inside its rail without
// ever fighting a gesture the chart itself allowed.
export function chartScrollbarTrackRange({ logicalRange, barCount, futureSlots = 0 } = {}) {
  const range = normalizeLogicalRange(logicalRange);
  const lastIndex = chartScrollbarLastIndex({ barCount, futureSlots });
  if (!range) return { from: 0, to: lastIndex, span: lastIndex };
  const from = Math.min(0, range.from);
  const to = Math.max(lastIndex, range.to);
  return { from, to, span: to - from };
}

// Where to paint the thumb, in pixels along the track.
export function chartScrollbarMetrics({
  logicalRange,
  barCount,
  futureSlots = 0,
  trackWidthPx,
  minThumbPx = CHART_SCROLLBAR_MIN_THUMB_PX,
} = {}) {
  const range = normalizeLogicalRange(logicalRange);
  const width = finiteNumber(trackWidthPx);
  if (!range || width === null || !(width > 0)) {
    return { disabled: true, thumbLeftPx: 0, thumbWidthPx: 0, trackFrom: 0, trackTo: 0, trackSpan: 0 };
  }
  const track = chartScrollbarTrackRange({ logicalRange, barCount, futureSlots });
  if (!(track.span > 0)) {
    return { disabled: true, thumbLeftPx: 0, thumbWidthPx: width, trackFrom: track.from, trackTo: track.to, trackSpan: 0 };
  }
  const minimumThumb = Math.min(Math.max(0, finiteNumber(minThumbPx) ?? 0), width);
  const proportional = (width * range.span) / track.span;
  const thumbWidthPx = Math.min(width, Math.max(minimumThumb, proportional));
  const travel = width - thumbWidthPx;
  // The window can only slide across the part of the track it does not already
  // fill. When it fills the whole track there is nothing to scroll, so the
  // thumb sits at the left edge spanning everything.
  const scrollableSpan = track.span - range.span;
  const ratio = scrollableSpan > 0
    ? Math.min(1, Math.max(0, (range.from - track.from) / scrollableSpan))
    : 0;
  return {
    disabled: scrollableSpan <= 0,
    thumbLeftPx: Math.min(travel, Math.max(0, ratio * travel)),
    thumbWidthPx,
    trackFrom: track.from,
    trackTo: track.to,
    trackSpan: track.span,
  };
}

// Inverse of chartScrollbarMetrics: given where the trader dragged the thumb,
// which logical window does that represent? Span is preserved - dragging the
// scrollbar scrolls, it never zooms.
export function chartScrollbarRangeFromThumb({
  thumbLeftPx,
  logicalRange,
  barCount,
  futureSlots = 0,
  trackWidthPx,
  minThumbPx = CHART_SCROLLBAR_MIN_THUMB_PX,
} = {}) {
  const range = normalizeLogicalRange(logicalRange);
  if (!range) return null;
  const metrics = chartScrollbarMetrics({ logicalRange, barCount, futureSlots, trackWidthPx, minThumbPx });
  if (!(metrics.trackSpan > 0)) return null;
  const width = finiteNumber(trackWidthPx) ?? 0;
  const travel = width - metrics.thumbWidthPx;
  const scrollableSpan = metrics.trackSpan - range.span;
  if (!(travel > 0) || !(scrollableSpan > 0)) return { from: range.from, to: range.to };
  const left = Math.min(travel, Math.max(0, finiteNumber(thumbLeftPx) ?? 0));
  const from = metrics.trackFrom + (left / travel) * scrollableSpan;
  return { from, to: from + range.span };
}

// Shared clamp for every scroll gesture (thumb drag, arrow, page, wheel pan).
// Zoom is deliberately not clamped here - applySmoothWheelZoom owns its own
// span limits and this must never silently resize a window it was only asked
// to move.
export function clampChartScrollLogicalRange({
  logicalRange,
  barCount,
  futureSlots = 0,
  minVisibleBars = CHART_SCROLLBAR_MIN_VISIBLE_BARS,
} = {}) {
  const range = normalizeLogicalRange(logicalRange);
  if (!range) return null;
  const lastIndex = chartScrollbarLastIndex({ barCount, futureSlots });
  // Keep at least `minVisibleBars` of real tape on screen at both extremes. A
  // window wider than the tape itself cannot satisfy that, so fall back to the
  // window span and let it sit wherever it lands.
  const keep = Math.min(Math.max(0, finiteNumber(minVisibleBars) ?? 0), range.span);
  const minFrom = -(range.span - keep);
  const maxFrom = Math.max(minFrom, lastIndex - keep);
  const from = Math.min(maxFrom, Math.max(minFrom, range.from));
  return { from, to: from + range.span };
}

function shiftLogicalRange(range, deltaBars) {
  return { from: range.from + deltaBars, to: range.to + deltaBars };
}

// Wheel pan: convert a pixel delta into a logical shift using the chart's own
// bar spacing, so one wheel notch moves the same visual distance whether the
// trader is zoomed to 2px or 16px per bar.
export function chartWheelPanLogicalRange({
  logicalRange,
  deltaPx,
  barSpacingPx,
  barCount,
  futureSlots = 0,
  minVisibleBars = CHART_SCROLLBAR_MIN_VISIBLE_BARS,
} = {}) {
  const range = normalizeLogicalRange(logicalRange);
  const delta = finiteNumber(deltaPx);
  if (!range || delta === null || delta === 0) return null;
  const spacing = finiteNumber(barSpacingPx);
  const safeSpacing = spacing !== null && spacing > 0 ? spacing : 6;
  return clampChartScrollLogicalRange({
    logicalRange: shiftLogicalRange(range, delta / safeSpacing),
    barCount,
    futureSlots,
    minVisibleBars,
  });
}

// Clicking the track on either side of the thumb pages one screen that way.
export function chartScrollbarPageStep({
  logicalRange,
  direction,
  barCount,
  futureSlots = 0,
  pageFraction = CHART_SCROLLBAR_PAGE_FRACTION,
  minVisibleBars = CHART_SCROLLBAR_MIN_VISIBLE_BARS,
} = {}) {
  const range = normalizeLogicalRange(logicalRange);
  if (!range) return null;
  const sign = Number(direction) < 0 ? -1 : 1;
  const fraction = finiteNumber(pageFraction);
  const safeFraction = fraction !== null && fraction > 0 ? fraction : CHART_SCROLLBAR_PAGE_FRACTION;
  return clampChartScrollLogicalRange({
    logicalRange: shiftLogicalRange(range, sign * range.span * safeFraction),
    barCount,
    futureSlots,
    minVisibleBars,
  });
}

// The step arrows at the right end nudge a fixed number of bars, and repeat
// while held.
export function chartScrollbarArrowStep({
  logicalRange,
  direction,
  bars = 1,
  barCount,
  futureSlots = 0,
  minVisibleBars = CHART_SCROLLBAR_MIN_VISIBLE_BARS,
} = {}) {
  const range = normalizeLogicalRange(logicalRange);
  if (!range) return null;
  const sign = Number(direction) < 0 ? -1 : 1;
  const step = Math.max(1, Math.abs(finiteNumber(bars) ?? 1));
  return clampChartScrollLogicalRange({
    logicalRange: shiftLogicalRange(range, sign * step),
    barCount,
    futureSlots,
    minVisibleBars,
  });
}

// Double-clicking the thumb (and the TOS "jump to now" affordance) returns the
// current span to the live edge, keeping the same forward breathing room the
// chart normally reserves.
export function chartScrollbarLiveEdgeRange({ logicalRange, barCount, futureSlots = 0 } = {}) {
  const range = normalizeLogicalRange(logicalRange);
  if (!range) return null;
  const lastIndex = chartScrollbarLastIndex({ barCount, futureSlots });
  return { from: lastIndex - range.span, to: lastIndex };
}
