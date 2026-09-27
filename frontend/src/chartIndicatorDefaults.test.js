import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

test("new and legacy chart profiles open with focused overlays and both lower panes", () => {
  assert.match(appSource, /FOCUSED_CHART_INDICATOR_SETTINGS_VERSION = "focused-chart-indicators-v18"/);
  [
    "mtfMacdClouds",
    "cloudBands5m",
    "cloudMaxMtf",
    "autoFibSingleTf",
    "squeezeMomentumLower",
    "pivotPoints",
    "personsPivots",
  ].forEach((key) => {
    assert.match(appSource, new RegExp(`${key}: false`));
  });
  // mtfMaLevels moved to the on-by-default set. A browser with no saved profile
  // takes the early return in the indicatorSettings initialiser, which returns
  // DEFAULT_OI_CHART_INDICATORS without running a single migration - so a false
  // default there is unreachable by the one-shot visibility migration and left
  // the study permanently dark on any origin the trader had not used before.
  [
    "mtfAdxCloudsLower",
    "mtfCloudLabelLower",
    "mtfSqueeze410Lower",
    "mtfMaLevels",
  ].forEach((key) => {
    assert.match(appSource, new RegExp(`${key}: true`));
  });
  assert.match(
    appSource,
    /migrateTosMtfSignalVisibility\(saved\),\s*\.\.\.migrateFocusedChartIndicatorSettings\(saved\)/,
  );
  assert.match(
    appSource,
    /migrateTosMtfSignalVisibility\(profile\.indicatorSettings\),\s*\.\.\.migrateFocusedChartIndicatorSettings\(profile\.indicatorSettings\)/,
  );
});

test("reset uses the focused defaults instead of enabling every study", () => {
  assert.match(
    appSource,
    /const resetIndicatorProfile = \(\) => \{\s*const nextSettings = \{ \.\.\.DEFAULT_OI_CHART_INDICATORS \};/,
  );
});

test("the mouse crosshair shows a TradingView-style horizontal price guide", () => {
  assert.match(
    appSource,
    /horzLine:\s*\{\s*visible: true,\s*labelVisible: true,/,
  );
  assert.match(appSource, /livePriceLineRef\.current\?\.applyOptions/);
});
