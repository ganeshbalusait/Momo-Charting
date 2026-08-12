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
