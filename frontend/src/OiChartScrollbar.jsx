import { useCallback, useEffect, useLayoutEffect, useRef } from "react";
import { ChevronLeft, ChevronRight, ChevronsRight } from "lucide-react";

import {
  chartScrollbarArrowStep,
  chartScrollbarLiveEdgeRange,
  chartScrollbarMetrics,
  chartScrollbarPageStep,
  chartScrollbarRangeFromThumb,
} from "./chartScrollbar";

// A held arrow starts repeating quickly - TOS parity. The first press still
// steps a single nudge so a plain click stays precise.
const ARROW_REPEAT_DELAY_MS = 220;
const ARROW_REPEAT_INTERVAL_MS = 16;
const ARROW_STEP_BARS = 3;

// The TOS scrollbar under the time axis.
//
// Nothing here lives in React state. The parent publishes the visible window
// through `applyRef`, and the thumb is repositioned by writing to its style
// directly. That matters: this renders inside OiFinderCandleChart, a component
// of several thousand lines, so routing a per-frame scroll position through
// setState re-renders that whole tree on every pixel of a drag.
export function OiChartScrollbar({ applyRef, onScrollTo, onInteractionStart, onInteractionEnd, label }) {
  const trackRef = useRef(null);
  const thumbRef = useRef(null);
  const viewRef = useRef(null);
  const dragRef = useRef(null);
  const repeatRef = useRef({ timeout: 0, interval: 0 });

  const currentMetrics = useCallback(() => chartScrollbarMetrics({
    logicalRange: viewRef.current ? { from: viewRef.current.from, to: viewRef.current.to } : null,
    barCount: viewRef.current?.barCount,
    futureSlots: viewRef.current?.futureSlots,
    trackWidthPx: trackRef.current?.clientWidth || 0,
  }), []);

  const paint = useCallback(() => {
    const thumb = thumbRef.current;
    if (!thumb) return;
    if (!viewRef.current) {
      thumb.style.visibility = "hidden";
      return;
    }
    const metrics = currentMetrics();
    thumb.style.visibility = "visible";
    thumb.style.left = `${metrics.thumbLeftPx}px`;
    thumb.style.width = `${metrics.thumbWidthPx}px`;
    // A chart showing its whole tape still gets a thumb - a full-width one that
    // cannot be dragged. An empty rail would read as a broken scrollbar, and
    // that is the state every chart loads in.
    thumb.classList.toggle("is-full", metrics.disabled);
  }, [currentMetrics]);

  useEffect(() => {
    if (!applyRef) return undefined;
    applyRef.current = (view) => {
      viewRef.current = view || null;
      paint();
    };
    return () => {
      if (applyRef.current) applyRef.current = null;
    };
  }, [applyRef, paint]);

  useLayoutEffect(() => {
    const track = trackRef.current;
    if (!track) return undefined;
    paint();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(paint);
    observer.observe(track);
    return () => observer.disconnect();
  }, [paint]);

  const stopRepeat = useCallback(() => {
    if (repeatRef.current.timeout) window.clearTimeout(repeatRef.current.timeout);
    if (repeatRef.current.interval) window.clearInterval(repeatRef.current.interval);
    repeatRef.current = { timeout: 0, interval: 0 };
  }, []);

  useEffect(() => stopRepeat, [stopRepeat]);

  // Ending a drag is safety-critical, not bookkeeping: onInteractionStart pauses
  // the native TOS primitive's animation loop, so any release path that misses
  // the matching end leaves the entire chart frozen. The listeners therefore
  // live on window and are torn down from the one place that owns them.
  const endDrag = useCallback(() => {
    const drag = dragRef.current;
    if (!drag) return;
    dragRef.current = null;
    thumbRef.current?.classList.remove("is-grabbed");
    window.removeEventListener("pointermove", drag.onMove);
    window.removeEventListener("pointerup", drag.onRelease);
    window.removeEventListener("pointercancel", drag.onRelease);
    onInteractionEnd?.();
  }, [onInteractionEnd]);

  const handleThumbPointerDown = useCallback((event) => {
    if (event.button != null && event.button !== 0) return;
    const metrics = currentMetrics();
    if (!viewRef.current || metrics.disabled) return;
    event.preventDefault();
    event.stopPropagation();
    endDrag();
    const drag = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startLeft: metrics.thumbLeftPx,
    };
    drag.onMove = (nativeEvent) => {
      const view = viewRef.current;
      if (!dragRef.current || !view) return;
      nativeEvent.preventDefault?.();
      const next = chartScrollbarRangeFromThumb({
        thumbLeftPx: drag.startLeft + (nativeEvent.clientX - drag.startX),
        logicalRange: { from: view.from, to: view.to },
        barCount: view.barCount,
        futureSlots: view.futureSlots,
        trackWidthPx: trackRef.current?.clientWidth || 0,
      });
      if (next) onScrollTo?.(next);
    };
    drag.onRelease = () => endDrag();
    dragRef.current = drag;
    thumbRef.current?.classList.add("is-grabbed");
    window.addEventListener("pointermove", drag.onMove, { passive: false });
    window.addEventListener("pointerup", drag.onRelease, { passive: true });
    window.addEventListener("pointercancel", drag.onRelease, { passive: true });
    onInteractionStart?.();
  }, [currentMetrics, endDrag, onInteractionStart, onScrollTo]);

  // Losing the window mid-drag must also release the chart's animation loop.
  useEffect(() => {
    const releaseOnBlur = () => endDrag();
    window.addEventListener("blur", releaseOnBlur);
    return () => {
      window.removeEventListener("blur", releaseOnBlur);
      endDrag();
    };
  }, [endDrag]);

  const scrollBy = useCallback((direction, bars) => {
    const view = viewRef.current;
    if (!view) return;
    const next = chartScrollbarArrowStep({
      logicalRange: { from: view.from, to: view.to },
      direction,
      bars,
      barCount: view.barCount,
      futureSlots: view.futureSlots,
    });
    if (next) onScrollTo?.(next);
  }, [onScrollTo]);

  const beginArrowRepeat = useCallback((event, direction) => {
    if (event.button != null && event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    scrollBy(direction, ARROW_STEP_BARS);
    stopRepeat();
    repeatRef.current.timeout = window.setTimeout(() => {
      repeatRef.current.interval = window.setInterval(
        () => scrollBy(direction, ARROW_STEP_BARS),
        ARROW_REPEAT_INTERVAL_MS,
      );
    }, ARROW_REPEAT_DELAY_MS);
  }, [scrollBy, stopRepeat]);

  // Clicking the empty track on either side of the thumb pages that way.
  const handleTrackPointerDown = useCallback((event) => {
    if (event.button != null && event.button !== 0) return;
    const view = viewRef.current;
    const track = trackRef.current;
    const metrics = currentMetrics();
    if (!view || !track || metrics.disabled) return;
    event.preventDefault();
    event.stopPropagation();
    const clickX = event.clientX - track.getBoundingClientRect().left;
    const next = chartScrollbarPageStep({
      logicalRange: { from: view.from, to: view.to },
      direction: clickX < metrics.thumbLeftPx ? -1 : 1,
      barCount: view.barCount,
      futureSlots: view.futureSlots,
    });
    if (next) onScrollTo?.(next);
  }, [currentMetrics, onScrollTo]);

  const jumpToLive = useCallback((event) => {
    event?.preventDefault?.();
    event?.stopPropagation?.();
    const view = viewRef.current;
    if (!view) return;
    const next = chartScrollbarLiveEdgeRange({
      logicalRange: { from: view.from, to: view.to },
      barCount: view.barCount,
      futureSlots: view.futureSlots,
    });
    if (next) onScrollTo?.(next);
  }, [onScrollTo]);

  return (
    <div
      className="oi-chart-scrollbar"
      role="group"
      aria-label={label ? `${label} chart time scroll` : "Chart time scroll"}
    >
      <button
        type="button"
        className="oi-chart-scrollbar-arrow"
        aria-label="Scroll back in time"
        onPointerDown={(event) => beginArrowRepeat(event, -1)}
        onPointerUp={stopRepeat}
        onPointerLeave={stopRepeat}
        onPointerCancel={stopRepeat}
      >
        <ChevronLeft size={11} />
      </button>
      <div className="oi-chart-scrollbar-track" ref={trackRef} onPointerDown={handleTrackPointerDown}>
        <div
          className="oi-chart-scrollbar-thumb"
          ref={thumbRef}
          role="scrollbar"
          aria-orientation="horizontal"
          aria-label="Chart time window"
          tabIndex={0}
          style={{ visibility: "hidden" }}
          onPointerDown={handleThumbPointerDown}
          onDoubleClick={jumpToLive}
          onKeyDown={(event) => {
            if (event.key === "ArrowLeft") { event.preventDefault(); scrollBy(-1, ARROW_STEP_BARS); }
            if (event.key === "ArrowRight") { event.preventDefault(); scrollBy(1, ARROW_STEP_BARS); }
            if (event.key === "End") jumpToLive(event);
          }}
        />
      </div>
      <button
        type="button"
        className="oi-chart-scrollbar-arrow"
        aria-label="Scroll forward in time"
        onPointerDown={(event) => beginArrowRepeat(event, 1)}
        onPointerUp={stopRepeat}
        onPointerLeave={stopRepeat}
        onPointerCancel={stopRepeat}
      >
        <ChevronRight size={11} />
      </button>
      <button
        type="button"
        className="oi-chart-scrollbar-arrow is-live"
        aria-label="Jump to the latest candle"
        title="Jump to the latest candle"
        onClick={jumpToLive}
      >
        <ChevronsRight size={11} />
      </button>
    </div>
  );
}
