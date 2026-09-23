// Golden fixture for the Python squeeze-release port.
//
// Runs the REAL frontend detector (frontend/src/squeezeRelease.js) over the
// frozen bar tape and saves its output; tests/test_premarket_scanner.py then
// asserts premarket_scanner.squeeze_release_events reproduces it exactly.
// Never edit the expected file by hand: the chart is the source of truth.
//
// Run from the repo root: node scripts/generate_squeeze_fixture.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { squeezeReleaseEvents } from "../frontend/src/squeezeRelease.js";

const bars = JSON.parse(readFileSync("tests/fixtures/squeeze_release_bars.json", "utf8"));
const expected = {};
for (const minutes of [60, 120, 240, 1440]) {
  expected[String(minutes)] = squeezeReleaseEvents(bars, minutes);
}
writeFileSync("tests/fixtures/squeeze_release_expected.json", `${JSON.stringify(expected, null, 2)}\n`);
console.log(Object.entries(expected).map(([k, v]) => `${k}: ${v.length}`).join(", "));
