import assert from "node:assert/strict";
import test from "node:test";

import { buildHighOiContractList, nextMonthlyOpexDate } from "./highOiContractList.js";

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

  // Ranked by OI but rendered descending by strike. The 90 put is ITM against
  // the 89.89 anchor, so it belongs to the call side of the board and never
  // lists as support.
  assert.deepEqual(model.calls.map((row) => row.strike), [200, 120, 110, 100]);
  assert.deepEqual(model.puts.map((row) => row.strike), [87.5, 85]);
  assert.equal(model.peakOi, 68_000);
});

test("the delta floor is opt-in: MomoX ranks pure OI by default", () => {
  // MomoX's High OI list carries no delta band - a 0.02-delta strike holding
  // real size is exactly the far wall it prints (MSFT 530 at spot 484).
  const model = buildHighOiContractList({ rows, underlyingPrice: 89.89, now });
  assert.equal(model.calls.some((row) => row.strike === 200), true);

  // Raising the floor by hand still filters the lottery strike out.
  const banded = buildHighOiContractList({ rows, underlyingPrice: 89.89, minDelta: 0.14, now });
  assert.equal(banded.calls.some((row) => row.strike === 200), false);
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
  // Top two per side: calls 68k + 16k, puts 8.8k + 1.5k (the 3.7k 90 put is
  // ITM against the anchor and never reaches the put ladder).
  assert.equal(model.callOi, 84_000);
  assert.equal(model.putOi, 10_300);
  assert.equal(model.putCallRatio.toFixed(2), "0.12");
});

test("ITM strikes never rank, however large: the anchor decides the side", () => {
  const withItm = [
    ...rows,
    // Huge ITM positions on both sides. MomoX lists calls only above the
    // anchor and puts only below it, so neither may appear at any delta.
    { side: "CALL", strike: 80, delta: 0.92, volume: 561, open_interest: 410_000, last: 10.15, expiry: "2026-08-07", days_to_expiration: 1 },
    { side: "PUT", strike: 100, delta: -0.62, volume: 82, open_interest: 380_000, last: 13.98, expiry: "2026-08-21", days_to_expiration: 15 },
  ];
  const model = buildHighOiContractList({ rows: withItm, underlyingPrice: 89.89, now });
  assert.equal(model.calls.some((row) => row.strike === 80), false);
  assert.equal(model.puts.some((row) => row.strike === 100), false);
  assert.equal(model.calls.every((row) => row.strike > 89.89), true);
  assert.equal(model.puts.every((row) => row.strike < 89.89), true);

  const uncapped = buildHighOiContractList({ rows: withItm, underlyingPrice: 89.89, maxDelta: 1, now });
  assert.equal(uncapped.calls.some((row) => row.strike === 80), false);
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
    // Same 85 put on a second expiry with smaller OI: must not appear twice.
    { side: "PUT", strike: 85, delta: -0.21, volume: 2_273, open_interest: 3_097, last: 3.10, expiry: "2026-08-21", days_to_expiration: 15 },
  ];
  const model = buildHighOiContractList({ rows: withDuplicates, underlyingPrice: 89.89, now });
  const eightyFive = model.puts.filter((row) => row.strike === 85);
  assert.equal(eightyFive.length, 1);
  assert.equal(eightyFive[0].openInterest, 8_800);
  assert.equal(eightyFive[0].expiry, "2026-08-07");
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

// --- MomoX parity ---------------------------------------------------------
// Fixtures transcribed from the MomoX MSFT and SPY High OI panels (2026-08-20
// session). They pin both the strike selection and the Imp 1-5 column.

const msftRows = [
  { side: "CALL", strike: 530, delta: 0.03, volume: 613, open_interest: 16_600, last: 0.98, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "CALL", strike: 525, delta: 0.14, volume: 1_131, open_interest: 30_700, last: 1.44, expiry: "2026-09-18", days_to_expiration: 29 },
  { side: "CALL", strike: 520, delta: 0.04, volume: 4_693, open_interest: 11_500, last: 0.09, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "CALL", strike: 510, delta: 0.23, volume: 499, open_interest: 17_600, last: 4.72, expiry: "2026-09-18", days_to_expiration: 29 },
  { side: "CALL", strike: 500, delta: 0.09, volume: 155, open_interest: 48_300, last: 0.09, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "CALL", strike: 495, delta: 0.31, volume: 88, open_interest: 3_900, last: 9.10, expiry: "2026-09-18", days_to_expiration: 29 },
  { side: "CALL", strike: 490, delta: 0.24, volume: 156, open_interest: 8_400, last: 1.12, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "CALL", strike: 485, delta: 0.45, volume: 572, open_interest: 3_700, last: 2.55, expiry: "2026-08-21", days_to_expiration: 1 },
  // Deep ITM calls carrying more OI than every wall above: must never list.
  { side: "CALL", strike: 400, delta: 0.97, volume: 12, open_interest: 90_000, last: 84.4, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 480, delta: -0.42, volume: 240, open_interest: 8_500, last: 1.90, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 475, delta: -0.28, volume: 2_896, open_interest: 5_200, last: 0.94, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 470, delta: -0.24, volume: 1_978, open_interest: 6_000, last: 4.15, expiry: "2026-09-18", days_to_expiration: 29 },
  { side: "PUT", strike: 465, delta: -0.12, volume: 1_258, open_interest: 4_700, last: 0.31, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 460, delta: -0.09, volume: 245, open_interest: 13_300, last: 0.17, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 455, delta: -0.06, volume: 649, open_interest: 3_300, last: 0.11, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 450, delta: -0.04, volume: 2_088, open_interest: 11_800, last: 0.08, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 440, delta: -0.09, volume: 410, open_interest: 6_900, last: 1.02, expiry: "2026-09-18", days_to_expiration: 29 },
  // Deep ITM puts above the anchor: must never list.
  { side: "PUT", strike: 560, delta: -0.98, volume: 5, open_interest: 75_000, last: 75.6, expiry: "2026-08-21", days_to_expiration: 1 },
];

const msftNow = new Date(Date.UTC(2026, 7, 20));

test("MomoX MSFT parity: calls above the anchor, puts below, nothing crosses", () => {
  const model = buildHighOiContractList({ rows: msftRows, underlyingPrice: 484.42, now: msftNow });

  assert.deepEqual(model.calls.map((row) => row.strike), [530, 525, 520, 510, 500, 495, 490, 485]);
  assert.deepEqual(model.puts.map((row) => row.strike), [480, 475, 470, 465, 460, 455, 450, 440]);
  // Header totals match the MomoX chips: Call 140.7k / Put 59.7k.
  assert.equal(model.callOi, 140_700);
  assert.equal(model.putOi, 59_700);
});

test("MomoX MSFT parity: the Imp column reproduces the reference panel", () => {
  const model = buildHighOiContractList({ rows: msftRows, underlyingPrice: 484.42, now: msftNow });

  assert.deepEqual(model.calls.map((row) => row.imp), [3, 5, 3, 4, 5, 1, 2, 1]);
  assert.deepEqual(model.puts.map((row) => row.imp), [5, 4, 4, 4, 5, 3, 5, 5]);
});

test("MomoX SPY parity: the Imp column reproduces the reference panel", () => {
  const spyRows = [
    { side: "CALL", strike: 795, delta: 0.04, volume: 100, open_interest: 15_000, last: 0.2, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "CALL", strike: 790, delta: 0.06, volume: 100, open_interest: 47_200, last: 0.3, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "CALL", strike: 785, delta: 0.09, volume: 100, open_interest: 45_600, last: 0.5, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "CALL", strike: 780, delta: 0.13, volume: 100, open_interest: 51_900, last: 0.8, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "CALL", strike: 775, delta: 0.20, volume: 100, open_interest: 58_500, last: 1.3, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "CALL", strike: 773, delta: 0.24, volume: 100, open_interest: 12_700, last: 1.7, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "CALL", strike: 770, delta: 0.29, volume: 100, open_interest: 28_900, last: 2.2, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "CALL", strike: 765, delta: 0.38, volume: 100, open_interest: 17_100, last: 3.4, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "PUT", strike: 760, delta: -0.44, volume: 100, open_interest: 48_800, last: 3.1, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "PUT", strike: 755, delta: -0.33, volume: 100, open_interest: 47_200, last: 2.2, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "PUT", strike: 750, delta: -0.24, volume: 100, open_interest: 57_500, last: 1.5, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "PUT", strike: 745, delta: -0.17, volume: 100, open_interest: 32_000, last: 1.0, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "PUT", strike: 740, delta: -0.12, volume: 100, open_interest: 33_300, last: 0.7, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "PUT", strike: 737, delta: -0.10, volume: 100, open_interest: 22_000, last: 0.6, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "PUT", strike: 735, delta: -0.08, volume: 100, open_interest: 50_800, last: 0.5, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "PUT", strike: 732, delta: -0.07, volume: 100, open_interest: 27_600, last: 0.4, expiry: "2026-08-21", days_to_expiration: 1 },
  ];
  const model = buildHighOiContractList({ rows: spyRows, underlyingPrice: 764.63, now: msftNow });

  assert.deepEqual(model.calls.map((row) => row.imp), [3, 5, 5, 5, 5, 2, 4, 3]);
  assert.deepEqual(model.puts.map((row) => row.imp), [5, 5, 5, 5, 5, 4, 5, 4]);
});

test("the anchor, not the live tick, decides which side a strike belongs to", () => {
  // MomoX pins the split at BMO, so an intraday rally does not flip the 485
  // call into a put row and churn the ladder mid-session.
  const rallied = buildHighOiContractList({
    rows: msftRows, underlyingPrice: 496.5, anchorPrice: 484.42, now: msftNow,
  });
  assert.equal(rallied.anchor, 484.42);
  assert.equal(rallied.calls.some((row) => row.strike === 485), true);
  assert.equal(rallied.puts.some((row) => row.strike === 485), false);

  // Without an explicit anchor the live price is used.
  const unanchored = buildHighOiContractList({ rows: msftRows, underlyingPrice: 496.5, now: msftNow });
  assert.equal(unanchored.anchor, 496.5);
  assert.equal(unanchored.calls.some((row) => row.strike === 485), false);
});

test("the monthly window rolls forward once the current OPEX is inside a week", () => {
  // On 2026-08-20 MomoX still lists the 2026-09-18 cycle; stopping at the
  // 2026-08-21 OPEX one day out would have hidden every one of those walls.
  assert.equal(nextMonthlyOpexDate(new Date(Date.UTC(2026, 7, 20))).toISOString().slice(0, 10), "2026-09-18");
  // Early in the cycle the window still stops at the current month OPEX.
  assert.equal(nextMonthlyOpexDate(new Date(Date.UTC(2026, 7, 3))).toISOString().slice(0, 10), "2026-08-21");
});
