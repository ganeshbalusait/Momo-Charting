import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

test("a ticker change releases both manually owned chart axes", () => {
  assert.match(
    appSource,
    /setBarsOwnerSymbol\([\s\S]*?manualTimeNavigationRef\.current = false;[\s\S]*?manualPriceNavigationRef\.current = false;[\s\S]*?loadChart\(\);/,
  );
});

test("a timeframe change releases both manually owned chart axes", () => {
  assert.match(
    appSource,
    /saveChartLayoutProfile\(false\);\s*manualTimeNavigationRef\.current = false;\s*manualPriceNavigationRef\.current = false;\s*setChartTimeframe\(timeframe\.key\);/,
  );
});

test("price-anchored overlay synchronization is event-driven instead of a permanent frame loop", () => {
  assert.match(
    appSource,
    /const syncPriceAnchoredOverlays = \(\) => \{\s*mappingSyncFrame = 0;/,
  );
  assert.match(
    appSource,
    /const scheduleDrawingGeometry = \(\) => \{\s*priceAnchoredOverlaysDirty = true;\s*queuePriceAnchoredOverlaySync\(\);/,
  );
  assert.doesNotMatch(
    appSource,
    /const syncPriceAnchoredOverlays = \(\) => \{\s*mappingSyncFrame = requestAnimationFrame\(syncPriceAnchoredOverlays\)/,
  );
});

test("a live time gap queues its full TOS backfill when a chart request is already in flight", () => {
  assert.match(
    appSource,
    /if \(requestInFlight\) \{[\s\S]*?if \(refreshServerHistory\) queuedFullHistoryRefresh = true;[\s\S]*?return;/,
  );
  assert.match(
    appSource,
    /if \(!cancelled[\s\S]*?\(queuedFullHistoryRefresh \|\| queuedFullTapeLoad\)[\s\S]*?loadChart\(true, refreshHistory, loadFullTape\);/,
  );
});

test("history promotion requests a complete tape instead of another delta", () => {
  assert.match(
    appSource,
    /if \(status\?\.historyReady\) \{[\s\S]*?loadChart\(true, false, true\);/,
  );
});


// ---- toolbar pan arrows -----------------------------------------------------

test("the pan arrows render as a pair immediately before jump-to-latest", () => {
  assert.match(
    appSource,
    /oi-finder-chart-pan[\s\S]*?<ChevronLeft size=\{16\} \/>[\s\S]*?oi-finder-chart-pan[\s\S]*?<ChevronRight size=\{16\} \/>[\s\S]*?oi-finder-jump-latest/,
  );
});

test("jump-to-latest does not wear the same glyph as the pan-right arrow", () => {
  // A 4x capture of the live toolbar (desktop and phone) showed "<  >  >": the
  // new pan-right arrow and jump-to-latest were both <ChevronRight size={16} />
  // and sat side by side, and pan-right is disabled at the live edge - which is
  // where the chart opens - so one of the two identical arrows was always
  // greyed out. ChevronsRight is the glyph OiChartScrollbar already uses for
  // its live-edge button, so the toolbar borrows the app's own vocabulary.
  const jump = appSource.match(/className="oi-finder-chart-latest oi-finder-jump-latest[\s\S]*?<\/button>/);
  assert.ok(jump, "the jump-to-latest button must exist");
  assert.match(jump[0], /<ChevronsRight size=\{16\} \/>/);
  assert.doesNotMatch(jump[0], /<ChevronRight /);
});

test("a pan step moves the time axis only and can never write a price scale", () => {
  const step = appSource.match(/const panChartStep = \(direction\) => \{[\s\S]*?\n  \};/);
  assert.ok(step, "panChartStep must exist");
  assert.match(step[0], /timeScale\.setVisibleLogicalRange\(nextRange\);/);
  // The native body drag is disabled in this app because it slides a manual
  // price scale and every price study visibly drifts. A click-to-pan control
  // that reintroduced that would be the same bug with a button on it.
  assert.doesNotMatch(step[0], /priceScale|setVisibleRange\(|autoScale|fitVisibleCandlePriceRange/);
});

test("a press-and-hold repeat cannot outlive its press", () => {
  // Every teardown path: release outside the button, alt-tab, and unmount.
  assert.match(appSource, /window\.addEventListener\("pointerup", release\);/);
  assert.match(appSource, /window\.addEventListener\("blur", release\);/);
  assert.match(appSource, /useEffect\(\(\) => stopPanHold, \[\]\);/);
  const stop = appSource.match(/const stopPanHold = \(\) => \{[\s\S]*?\n  \};/);
  assert.ok(stop, "stopPanHold must exist");
  assert.match(stop[0], /window\.clearTimeout\(hold\.timer\)/);
  assert.match(stop[0], /hold\.detach\(\)/);
});
