import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

test("frequent dashboard polling uses the compact payload", () => {
  assert.match(
    appSource,
    /if \(!includeHeavy\) params\.set\("compact", "true"\);/,
  );
  assert.match(appSource, /const timer = setInterval\(loadDashboard, intervalMs\);/);
});

test("historical dashboard sections wait until a view needs them", () => {
  assert.match(
    appSource,
    /const heavyDashboardViews = new Set\(\[[\s\S]*?"Scanner"[\s\S]*?"OI Scanner"[\s\S]*?"News Feed"[\s\S]*?\]\);/,
  );
  assert.match(
    appSource,
    /window\.setTimeout\(\(\) => loadDashboard\(\{ includeHeavy: true \}\), 1800\)/,
  );
});

test("a requested history load is retried after an in-flight compact poll", () => {
  assert.match(
    appSource,
    /if \(includeHeavy\) dashboardHeavyRequestPending\.current = true;/,
  );
  assert.match(
    appSource,
    /queueMicrotask\(\(\) => loadDashboard\(\{ includeHeavy: true \}\)\);/,
  );
});
