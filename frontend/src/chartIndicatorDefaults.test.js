import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

test("new and legacy chart profiles open with optional overlay groups disabled", () => {
  assert.match(appSource, /FOCUSED_CHART_INDICATOR_SETTINGS_VERSION = "focused-chart-indicators-v17"/);
  [
    "mtfMacdClouds",
    "cloudBands5m",
    "cloudMaxMtf",
    "autoFibSingleTf",
    "squeezeMomentumLower",
    "mtfSqueeze410Lower",
    "pivotPoints",
    "personsPivots",
    "mtfMaLevels",
  ].forEach((key) => {
    assert.match(appSource, new RegExp(`${key}: false`));
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
