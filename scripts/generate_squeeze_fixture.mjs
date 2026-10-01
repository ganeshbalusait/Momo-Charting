// Regenerate the golden fixture that pins premarket_scanner.py's squeeze
// detection to the JavaScript the chart actually runs.
//
//   node scripts/generate_squeeze_fixture.mjs
//
// Never hand-edit tests/fixtures/squeeze_release_expected.json. If the Python
// disagrees with it, the Python is wrong: this file is the chart.
import { readFileSync, writeFileSync } from "node:fs";

import { squeezeReleaseEvents } from "../frontend/src/squeezeRelease.js";

const BARS_PATH = "tests/fixtures/squeeze_release_bars.json";
const EXPECTED_PATH = "tests/fixtures/squeeze_release_expected.json";

const bars = JSON.parse(readFileSync(BARS_PATH, "utf8"));
const expected = {};
for (const minutes of [60, 120, 240, 1440]) {
  expected[String(minutes)] = squeezeReleaseEvents(bars, minutes);
}

writeFileSync(EXPECTED_PATH, `${JSON.stringify(expected, null, 2)}\n`);
console.log(
  Object.entries(expected).map(([key, value]) => `${key}: ${value.length}`).join(", "),
);
