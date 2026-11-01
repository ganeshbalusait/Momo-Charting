import assert from "node:assert/strict";
import test from "node:test";
import { strategyTags } from "./momxFilters.js";
import { setMomxSimpleSetup } from "./momxFilters.js";
// These tests pin the FULL tag set; the Simple Setup column (2026-09-28) has its own tests.
setMomxSimpleSetup(false);

// "add it" (2026-09-28): the ⚠ warning also rides on V2 / V3 / Daily 2.
const now = Date.parse("2026-09-28T10:00:00-04:00");
const fading = { last: 100, m5: { state: "fading", vwap: 99 }, adx: { "30m": { plus: 30, minus: 10 } } };

test("Daily 2 / V2 rows now carry the warning", () => {
  const row = { symbol: "AAPL", grade: { letter: "A" }, strategy: { v2: true, daily2: { rank: 1 } }, ...fading };
  const tags = strategyTags(row, now);
  const warn = tags.find((t) => t.key === "warn");
  assert.ok(warn, JSON.stringify(tags.map((t) => t.key)));
  assert.equal(warn.text, "⚠ fading");
  assert.equal(tags.filter((t) => t.key === "warn").length, 1);
});

test("no setup, no warning (unchanged)", () => {
  const tags = strategyTags({ symbol: "X", grade: { letter: "B" }, ...fading }, now);
  assert.equal(tags.find((t) => t.key === "warn"), undefined);
});
