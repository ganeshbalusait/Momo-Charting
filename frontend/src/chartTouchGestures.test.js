import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
const cssSource = readFileSync(new URL("./index.css", import.meta.url), "utf8");

// The vertTouchDrag assertion below passed for the entire time the phone chart
// could not be moved up or down: the OPTION was set, but nothing ever set
// `touch-action` on the chart, so it computed to `auto` and the BROWSER claimed
// every vertical drag as a scroll of the nearest scrollable ancestor
// (.scanner-results-view). Lightweight Charts never saw the moves. A JS-only
// guard is therefore not enough - the CSS half has to be asserted too.
test("the chart surface owns touch gestures instead of the browser", () => {
  const rule = /@media \(any-pointer: coarse\)[^{]*\{[\s\S]{0,400}?\.oi-finder-chart-canvas\s*\{[^}]*touch-action:\s*none/;
  assert.match(cssSource, rule);
  // It must NOT sit on .oi-finder-candle-chart: that wrapper also contains the
  // drawing rail, which needs normal tap handling.
  assert.doesNotMatch(cssSource, /\.oi-finder-candle-chart\s*\{[^}]*touch-action:\s*none/);
});

// TradingView mobile: long-press raises the crosshair, drag moves it, RELEASE
// dismisses it. The library default (OnNextTap) left the crosshair latched, so
// the OHLC readout froze on the inspected bar and later drags moved the
// crosshair instead of panning.
test("the mobile crosshair releases when the finger lifts", () => {
  assert.match(appSource, /trackingMode:\s*\{\s*exitMode:\s*TrackingModeExitMode\.OnTouchEnd\s*\}/);
  assert.match(appSource, /import \{[^}]*TrackingModeExitMode[^}]*\} from "lightweight-charts"/);
});

// Lightweight Charts 5.x derives the "treat a vertical finger drag as page
// scroll" behaviour of BOTH the candle pane and the right price axis from the
// single handleScroll.vertTouchDrag option (PaneWidget and PriceAxisWidget each
// build their MouseEventHandler with `() => !handleScroll.vertTouchDrag`).
// Turning it off to keep a swipe over the candles scrolling the page therefore
// also froze the price axis, so on a phone nothing could move the chart up or
// down. TradingView gives the chart the gesture; so do we.
test("a vertical finger drag drives the chart instead of scrolling the page", () => {
  assert.match(appSource, /horzTouchDrag: true,[\s\S]{0,900}?\n\s*vertTouchDrag: true,/);
  // The prop that turned it off is gone: neither passed, declared, nor read.
  assert.doesNotMatch(appSource, /vertTouchDrag: !allowPageScroll/);
  assert.doesNotMatch(appSource, /^\s+allowPageScroll(,| = false,)$/m);
});

// The custom body pan is mouse-only, so a touch drag is panned natively by the
// library and nothing claims the axes the way applyBodyDragTimePan does. Left
// unclaimed, the autoscale pass treats the pan as an idle view and snaps the
// candles back mid-gesture.
test("a touch pan claims manual ownership of the axes it moved", () => {
  assert.match(
    appSource,
    /if \(touchDragActive\) \{[\s\S]{0,400}?claimManualTimeNavigation\(\);[\s\S]{0,400}?manualPriceNavigationRef\.current = true;/,
  );
});

test("the touch-drag flag is armed on pointerdown and cleared when the drag ends", () => {
  assert.match(appSource, /touchDragActive = event\.pointerType === "touch";/);
  // chartPointerOnTimeScale joined this reset group when the bottom time axis
  // was exempted from the custom body pan (it previously armed BOTH the custom
  // logical pan and the library's own scaleTimeTo, two writers per mousemove).
  // Every pointer flag must still be cleared together when a drag ends.
  assert.match(appSource, /chartPointerActive = false;\s*\n\s*chartPointerOnPriceScale = false;\s*\n\s*chartPointerOnTimeScale = false;\s*\n\s*touchDragActive = false;/);
});

// Regression guard for the rest of the TradingView-parity gesture set: pinch to
// zoom, and a drag on either axis scales that axis.
test("pinch zoom and axis drag scaling stay enabled", () => {
  assert.match(appSource, /handleScale: \{[\s\S]{0,600}?axisPressedMouseMove: true,[\s\S]{0,600}?pinch: true,/);
});

test("a steady mobile long press opens the prefilled price-alert editor", () => {
  assert.match(appSource, /touchAlertTimer = window\.setTimeout\(\(\) => \{[\s\S]{0,700}?OI_PRICE_ALERT_DRAFT_EVENT[\s\S]{0,700}?CHART_TOUCH_ALERT_HOLD_MS/);
  assert.match(appSource, /touchAlertGestureShouldCancel\([\s\S]{0,300}?clearTouchAlertHold\(\)/);
  assert.match(appSource, /chartHost\.addEventListener\("pointerdown", beginTouchAlertHold, \{ capture: true \}\)/);
  assert.match(appSource, /chartHost\.removeEventListener\("pointerdown", beginTouchAlertHold, \{ capture: true \}\)/);
});
