import assert from "node:assert/strict";
import test from "node:test";

import { buildHighOiContractList } from "./highOiContractList.js";

const rows = [
  // Calls above spot (89.89), mixed expiries and deltas.
  { side: "CALL", strike: 100, delta: 0.38, volume: 5_900, open_interest: 68_000, last: 5.09, expiry: "2026-08-21", days_to_expiration: 15 },
  { side: "CALL", strike: 120, delta: 0.15, volume: 831, open_interest: 16_000, last: 1.72, expiry: "2026-08-21", days_to_expiration: 15 },
  { side: "CALL", strike: 110, delta: 0.25, volume: 238, open_interest: 6_900, last: 2.91, expiry: "2026-08-21", days_to_expiration: 15 },
  // Below the 0.14 delta floor with small size: a lottery strike that must
  // not rank (large OI outside the band stays via the MomoX high-OI rule).
  { side: "CALL", strike: 200, delta: 0.02, volume: 40, open_interest: 900, last: 0.05, expiry: "2026-08-21", days_to_expiration: 15 },
  // Beyond the next monthly OPEX (2026-08-21): out of the monthly window.
  { side: "CALL", strike: 105, delta: 0.30, volume: 100, open_interest: 77_000, last: 4.28, expiry: "2026-09-18", days_to_expiration: 43 },
  { side: "PUT", strike: 90, delta: -0.45, volume: 1_400, open_interest: 3_700, last: 8.70, expiry: "2026-08-21", days_to_expiration: 15 },
  { side: "PUT", strike: 85, delta: -0.24, volume: 11_000, open_interest: 8_800, last: 1.15, expiry: "2026-08-07", days_to_expiration: 1 },
  { side: "PUT", strike: 87.5, delta: -0.41, volume: 1_500, open_interest: 1_500, last: 6.72, expiry: "2026-08-21", days_to_expiration: 15 },
  // Zero OI never ranks even with a qualifying delta.
  { side: "PUT", strike: 80, delta: -0.30, volume: 10, open_interest: 0, last: 0.4, expiry: "2026-08-21", days_to_expiration: 15 },
];

const now = new Date(Date.UTC(2026, 7, 6));

test("ranks by open interest per side and displays far-to-near around spot", () => {
  const model = buildHighOiContractList({ rows, underlyingPrice: 89.89, now });

  // Ranked by OI (68k, 16k, 6.9k) but rendered descending by strike.
  assert.deepEqual(model.calls.map((row) => row.strike), [120, 110, 100]);
  assert.deepEqual(model.puts.map((row) => row.strike), [90, 87.5, 85]);
  assert.equal(model.peakOi, 68_000);
});

test("applies the absolute delta floor to both sides", () => {
  const model = buildHighOiContractList({ rows, underlyingPrice: 89.89, now });
  // The 0.02-delta 200 call carries the largest raw OI but is filtered out.
  assert.equal(model.calls.some((row) => row.strike === 200), false);

  const openFloor = buildHighOiContractList({ rows, underlyingPrice: 89.89, minDelta: 0, now });
  assert.equal(openFloor.calls.some((row) => row.strike === 200), true);
});

test("monthly scope stops at the next monthly OPEX, front scope pins one expiry", () => {
  const monthly = buildHighOiContractList({ rows, underlyingPrice: 89.89, now });
  assert.equal(monthly.monthlyExpiry, "2026-08-21");
  assert.equal(monthly.calls.some((row) => row.expiry === "2026-09-18"), false);

  const front = buildHighOiContractList({
    rows, underlyingPrice: 89.89, scope: "front", frontExpiry: "2026-08-07", now,
  });
  assert.deepEqual(front.puts.map((row) => row.strike), [85]);
  assert.equal(front.calls.length, 0);
});

test("limits each side to the requested count and reports OI totals", () => {
  const model = buildHighOiContractList({ rows, underlyingPrice: 89.89, topPerSide: 2, now });

  assert.equal(model.calls.length, 2);
  assert.equal(model.puts.length, 2);
  // Top two per side: calls 68k + 16k, puts 8.8k + 3.7k.
  assert.equal(model.callOi, 84_000);
  assert.equal(model.putOi, 12_500);
  assert.equal(model.putCallRatio.toFixed(2), "0.15");
});

test("excludes small ITM contracts above the 0.5 delta cap", () => {
  const withItm = [
    ...rows,
    // Modest ITM positions (well under the high-OI exception): never listed.
    { side: "CALL", strike: 80, delta: 0.92, volume: 561, open_interest: 4_100, last: 10.15, expiry: "2026-08-07", days_to_expiration: 1 },
    { side: "PUT", strike: 100, delta: -0.62, volume: 82, open_interest: 800, last: 13.98, expiry: "2026-08-21", days_to_expiration: 15 },
  ];
  const model = buildHighOiContractList({ rows: withItm, underlyingPrice: 89.89, now });
  assert.equal(model.calls.some((row) => row.strike === 80), false);
  assert.equal(model.puts.some((row) => row.strike === 100), false);

  const uncapped = buildHighOiContractList({ rows: withItm, underlyingPrice: 89.89, maxDelta: 1, now });
  assert.equal(uncapped.calls.some((row) => row.strike === 80), true);
});

test("MomoX high-OI exception: extreme deltas stay when the size is dominant", () => {
  const withCollapsed = [
    ...rows,
    // TSLA 332.5 case: 0DTE delta collapsed to ~0 at the close while the wall
    // still holds a fifth of the side's leader — it must stay on the chart.
    { side: "CALL", strike: 95.5, delta: 0.009, volume: 120, open_interest: 27_000, last: 0.01, expiry: "2026-08-07", days_to_expiration: 0 },
  ];
  const model = buildHighOiContractList({ rows: withCollapsed, underlyingPrice: 89.89, now });
  assert.equal(model.calls.some((row) => row.strike === 95.5 && row.openInterest === 27_000), true);
});

test("carries this week's size as frontAlt when a later cycle dominates the strike", () => {
  const stacked = [
    // MomoX AAPL example: 315 calls hold 15k on the front week and 18k on the
    // later monthly — the chart must print both figures.
    { side: "CALL", strike: 315, delta: 0.30, volume: 2_000, open_interest: 15_000, last: 1.2, expiry: "2026-08-07", days_to_expiration: 1 },
    { side: "CALL", strike: 315, delta: 0.28, volume: 1_500, open_interest: 18_000, last: 2.4, expiry: "2026-08-21", days_to_expiration: 15 },
    // Tiny front OI below the 20% floor must not stack.
    { side: "CALL", strike: 320, delta: 0.20, volume: 100, open_interest: 500, last: 0.4, expiry: "2026-08-07", days_to_expiration: 1 },
    { side: "CALL", strike: 320, delta: 0.18, volume: 900, open_interest: 12_000, last: 1.1, expiry: "2026-08-21", days_to_expiration: 15 },
  ];
  const model = buildHighOiContractList({ rows: stacked, underlyingPrice: 312.8, now });
  const wall315 = model.calls.find((row) => row.strike === 315);
  assert.equal(wall315.openInterest, 18_000);
  assert.equal(wall315.expiry, "2026-08-21");
  assert.equal(wall315.frontAlt.openInterest, 15_000);
  assert.equal(wall315.frontAlt.expiry, "2026-08-07");
  const wall320 = model.calls.find((row) => row.strike === 320);
  assert.equal(wall320.frontAlt, undefined);
});

test("keeps one row per strike: the dominant expiry wins", () => {
  const withDuplicates = [
    ...rows,
    // Same 90 put on a second expiry with smaller OI: must not appear twice.
    { side: "PUT", strike: 90, delta: -0.48, volume: 2_273, open_interest: 3_097, last: 3.10, expiry: "2026-08-07", days_to_expiration: 1 },
  ];
  const model = buildHighOiContractList({ rows: withDuplicates, underlyingPrice: 89.89, now });
  const ninety = model.puts.filter((row) => row.strike === 90);
  assert.equal(ninety.length, 1);
  assert.equal(ninety[0].openInterest, 3_700);
  assert.equal(ninety[0].expiry, "2026-08-21");
});

test("skips zero-open-interest contracts", () => {
  const model = buildHighOiContractList({ rows, underlyingPrice: 89.89, now });
  assert.equal(model.puts.some((row) => row.strike === 80), false);
});

test("falls back to all expiries when nothing lists inside the monthly window", () => {
  const quarterlyOnly = [
    { side: "CALL", strike: 105, delta: 0.30, volume: 100, open_interest: 77_000, last: 4.28, expiry: "2026-09-18", days_to_expiration: 43 },
    { side: "PUT", strike: 80, delta: -0.30, volume: 50, open_interest: 12_000, last: 2.10, expiry: "2026-09-18", days_to_expiration: 43 },
  ];
  const model = buildHighOiContractList({ rows: quarterlyOnly, underlyingPrice: 89.89, now });
  assert.equal(model.calls.length, 1);
  assert.equal(model.calls[0].expiry, "2026-09-18");
  assert.equal(model.puts.length, 1);
});

test("falls back to raw OI ranking when the provider omits every delta", () => {
  const noGreeks = [
    { side: "CALL", strike: 100, delta: 0, volume: 500, open_interest: 9_000, last: 1.2, expiry: "2026-08-21", days_to_expiration: 15 },
    { side: "PUT", strike: 80, delta: 0, volume: 300, open_interest: 4_000, last: 0.9, expiry: "2026-08-21", days_to_expiration: 15 },
  ];
  const model = buildHighOiContractList({ rows: noGreeks, underlyingPrice: 89.89, now });
  assert.equal(model.calls.length, 1);
  assert.equal(model.puts.length, 1);
});

test("returns an empty model without rows", () => {
  const model = buildHighOiContractList({ rows: [], underlyingPrice: 89.89, now });
  assert.deepEqual(model.calls, []);
  assert.deepEqual(model.puts, []);
  assert.equal(model.putCallRatio, 0);
  assert.equal(model.peakOi, 0);
});
