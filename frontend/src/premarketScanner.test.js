import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { premarketScannerFireBadges } from "./premarketScanner.js";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

test("intraday fires show their timeframe, daily fires carry their own session date", () => {
  const badges = premarketScannerFireBadges({
    fires: ["1h", "D"],
    fireDates: { "1h": "2026-10-26T07:00:00-04:00", D: "2026-10-23" },
  });
  assert.deepEqual(badges.map((badge) => badge.text), ["🔥1h", "🔥D 10/23"]);
  assert.match(badges[1].title, /2026-10-23/);
  assert.match(badges[1].title, /previous session/i);
});

test("a row without fires renders no badges", () => {
  assert.deepEqual(premarketScannerFireBadges({}), []);
  assert.deepEqual(premarketScannerFireBadges({ fires: null }), []);
});

test("the scanner polls its own uncached endpoint every 5s", () => {
  assert.match(appSource, /fetch\("\/api\/premarket-scanner", \{ cache: "no-store" \}\)/);
  assert.match(appSource, /const timer = setInterval\(loadPremarketScanner, 5000\);/);
  assert.match(appSource, /tableId="mag7-premarket-scanner"/);
});

test("there is no FORMING badge: every server signal is liveForming=true", () => {
  assert.doesNotMatch(appSource, /premarket-strength-forming/);
  assert.doesNotMatch(appSource, /row\?\.forming/);
});
