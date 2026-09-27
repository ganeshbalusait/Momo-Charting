import test from "node:test";
import assert from "node:assert/strict";

import {
  MOBILE_OPTIONS_SECTIONS,
  buildMobileQuickOptionRows,
  mobileOptionsSectionRequestMode,
  mobileQuickOptionMark,
} from "./mobileQuickOptions.js";

test("mobile quick options keeps a small strike window centered on ATM", () => {
  const rows = [];
  for (let strike = 90; strike <= 110; strike += 1) {
    rows.push({ side: "CALL", strike, volume: strike, open_interest: 100 });
    rows.push({ side: "PUT", strike, volume: strike, open_interest: 100 });
  }
  const result = buildMobileQuickOptionRows(rows, 100, 7);
  assert.deepEqual(result.map((row) => row.strike), [97, 98, 99, 100, 101, 102, 103]);
  assert.equal(result.every((row) => row.call && row.put), true);
});

test("mobile quick options stays inside the listed range near an edge", () => {
  const rows = [100, 105, 110, 115, 120].flatMap((strike) => [
    { side: "CALL", strike },
    { side: "PUT", strike },
  ]);
  assert.deepEqual(
    buildMobileQuickOptionRows(rows, 100, 3).map((row) => row.strike),
    [100, 105, 110],
  );
});

test("mobile quick options resolves mark without inventing a quote", () => {
  assert.equal(mobileQuickOptionMark({ mark: 1.25, bid: 1.1, ask: 1.3 }), 1.25);
  assert.ok(Math.abs(mobileQuickOptionMark({ bid: 1.1, ask: 1.3 }) - 1.2) < Number.EPSILON * 2);
  assert.equal(mobileQuickOptionMark(null), null);
});

test("mobile Options research sections keep the fast chain isolated", () => {
  assert.deepEqual(MOBILE_OPTIONS_SECTIONS.map((section) => section.key), [
    "chain", "heatmap", "flow", "levels", "news",
  ]);
  assert.equal(mobileOptionsSectionRequestMode("chain"), "none");
  assert.equal(mobileOptionsSectionRequestMode("levels"), "chain");
  assert.equal(mobileOptionsSectionRequestMode("heatmap"), "analytics");
  assert.equal(mobileOptionsSectionRequestMode("flow"), "analytics");
  assert.equal(mobileOptionsSectionRequestMode("news"), "news");
});
