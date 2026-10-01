import assert from "node:assert/strict";
import test from "node:test";

import {
  buildHighOiContractList,
  expectedMoveFromExpiries,
  formatCompactVolume,
  formatDelta,
  formatMark,
  HIGH_OI_IMPORTANCE_BREAKS,
  highOiImportance,
  highOiWindowEndDate,
  nextMonthlyOpexDate,
} from "./highOiContractList.js";

// MSFT chain from the live app on 2026-08-20 (spot 482.28), the day the list
// was validated against the Trading Alphas / MomoX daily sheet. The sheet
// splits the board AT SPOT: above it only call contracts count, at/below it
// only put contracts. The deep-ITM rows here (480/470/450 calls, 500/490
// puts) are the exact contracts that used to pollute the wrong side.
const MSFT_ROWS = [
  // Call contracts above spot -> call walls.
  { side: "CALL", strike: 525, delta: 0.10, volume: 90, open_interest: 30_700, last: 0.55, expiry: "2026-09-18", days_to_expiration: 29 },
  { side: "CALL", strike: 520, delta: 0.00, volume: 613, open_interest: 11_183, last: 0.01, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "CALL", strike: 510, delta: 0.23, volume: 131, open_interest: 17_676, last: 4.72, expiry: "2026-09-18", days_to_expiration: 29 },
  { side: "CALL", strike: 505, delta: 0.06, volume: 40, open_interest: 3_500, last: 0.30, expiry: "2026-09-18", days_to_expiration: 29 },
  { side: "CALL", strike: 500, delta: 0.03, volume: 4_693, open_interest: 47_262, last: 0.09, expiry: "2026-08-21", days_to_expiration: 1 },
  // Same strike on the later monthly with LESS OI: must lose the strike.
  { side: "CALL", strike: 500, delta: 0.18, volume: 120, open_interest: 19_100, last: 2.05, expiry: "2026-09-18", days_to_expiration: 29 },
  { side: "CALL", strike: 495, delta: 0.09, volume: 200, open_interest: 5_300, last: 0.45, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "CALL", strike: 490, delta: 0.18, volume: 2_511, open_interest: 9_330, last: 0.83, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "CALL", strike: 485, delta: 0.35, volume: 1_100, open_interest: 3_900, last: 1.60, expiry: "2026-08-21", days_to_expiration: 1 },
  // Deep-ITM call contracts BELOW spot: positioning history, never call walls.
  { side: "CALL", strike: 480, delta: 0.61, volume: 499, open_interest: 24_161, last: 4.72, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "CALL", strike: 470, delta: 0.65, volume: 155, open_interest: 19_669, last: 21.65, expiry: "2026-09-18", days_to_expiration: 29 },
  { side: "CALL", strike: 450, delta: 0.82, volume: 156, open_interest: 16_469, last: 37.29, expiry: "2026-09-18", days_to_expiration: 29 },
  // Put contracts at/below spot -> put walls.
  { side: "PUT", strike: 480, delta: -0.40, volume: 2_896, open_interest: 8_442, last: 2.35, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 477.5, delta: -0.30, volume: 300, open_interest: 1_700, last: 1.55, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 475, delta: -0.20, volume: 1_978, open_interest: 5_441, last: 1.01, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 470, delta: -0.09, volume: 1_258, open_interest: 6_428, last: 0.41, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 465, delta: -0.04, volume: 245, open_interest: 6_296, last: 0.17, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 460, delta: -0.02, volume: 649, open_interest: 13_048, last: 0.08, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 455, delta: -0.02, volume: 110, open_interest: 2_100, last: 0.06, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 450, delta: -0.02, volume: 2_088, open_interest: 12_694, last: 0.04, expiry: "2026-08-21", days_to_expiration: 1 },
  // ITM put contracts ABOVE spot: never put walls.
  { side: "PUT", strike: 500, delta: -1.00, volume: 572, open_interest: 6_593, last: 17.90, expiry: "2026-08-21", days_to_expiration: 1 },
  { side: "PUT", strike: 490, delta: -0.83, volume: 240, open_interest: 5_478, last: 8.25, expiry: "2026-08-21", days_to_expiration: 1 },
  // Zero OI never ranks.
  { side: "PUT", strike: 440, delta: -0.01, volume: 10, open_interest: 0, last: 0.02, expiry: "2026-08-21", days_to_expiration: 1 },
];

const SPOT = 482.28;
// 2026-08-20: the 8/21 monthly OPEX is tomorrow, so the sheet window extends
// to the 9/18 monthly.
const now = new Date(Date.UTC(2026, 7, 20));

test("splits the board at spot: call walls above with call OI, put walls below with put OI", () => {
  const model = buildHighOiContractList({ rows: MSFT_ROWS, underlyingPrice: SPOT, now });
  assert.ok(model.calls.every((row) => row.side === "CALL" && row.strike > SPOT));
  assert.ok(model.puts.every((row) => row.side === "PUT" && row.strike <= SPOT));
  // 480 appears exactly once — as the PUT wall with the put contract's 8.4K,
  // never the ITM call contract's 24.2K.
  assert.equal(model.calls.some((row) => row.strike === 480), false);
  const put480 = model.puts.find((row) => row.strike === 480);
  assert.equal(put480.openInterest, 8_442);
  // 500/490 appear only as CALL walls; their ITM put contracts stay off the board.
  assert.equal(model.puts.some((row) => row.strike >= 485), false);
  assert.equal(model.calls.find((row) => row.strike === 500).openInterest, 47_262);
});

test("top 8 per side by OI, rendered strike-descending, like the daily sheet", () => {
  const model = buildHighOiContractList({ rows: MSFT_ROWS, underlyingPrice: SPOT, now });
  // Call candidates by OI: 500 47.3k, 525 30.7k, 510 17.7k, 520 11.2k,
  // 490 9.3k, 495 5.3k, 485 3.9k, 505 3.5k — all eight, shown descending.
  assert.deepEqual(model.calls.map((row) => row.strike), [525, 520, 510, 505, 500, 495, 490, 485]);
  // Put candidates by OI: 460, 450, 480, 470, 465, 475, 455, then 477.5 —
  // 477.5 (1.7k) beats nothing else so it is the eighth.
  assert.deepEqual(model.puts.map((row) => row.strike), [480, 477.5, 475, 470, 465, 460, 455, 450]);
  assert.equal(model.calls.length, 8);
  assert.equal(model.puts.length, 8);
});

test("dominant expiry per strike: largest OI wins the strike", () => {
  const model = buildHighOiContractList({ rows: MSFT_ROWS, underlyingPrice: SPOT, now });
  const call500 = model.calls.find((row) => row.strike === 500);
  assert.equal(call500.openInterest, 47_262);
  assert.equal(call500.expiry, "2026-08-21");
  assert.equal(model.calls.filter((row) => row.strike === 500).length, 1);
});

test("window extends past an imminent monthly OPEX to the following monthly", () => {
  // 8/20 with OPEX on 8/21: the sheets list 9/18 walls beside the weeklies.
  assert.equal(highOiWindowEndDate(new Date(Date.UTC(2026, 7, 20))).toISOString().slice(0, 10), "2026-09-18");
  // Mid-cycle (8/6, OPEX 15 days out): the window stays at the near monthly.
  assert.equal(highOiWindowEndDate(new Date(Date.UTC(2026, 7, 6))).toISOString().slice(0, 10), "2026-08-21");
  assert.equal(nextMonthlyOpexDate(new Date(Date.UTC(2026, 7, 20))).toISOString().slice(0, 10), "2026-08-21");

  const model = buildHighOiContractList({ rows: MSFT_ROWS, underlyingPrice: SPOT, now });
  assert.equal(model.monthlyExpiry, "2026-09-18");
  assert.equal(model.calls.some((row) => row.expiry === "2026-09-18"), true);

  // Mid-cycle the September walls fall out of scope entirely.
  const midCycle = buildHighOiContractList({ rows: MSFT_ROWS, underlyingPrice: SPOT, now: new Date(Date.UTC(2026, 7, 6)) });
  assert.equal(midCycle.monthlyExpiry, "2026-08-21");
  assert.equal(midCycle.calls.some((row) => row.expiry === "2026-09-18"), false);
});

test("no delta filter of any kind: tiny, unknown, and sentinel deltas all rank on OI", () => {
  // The 0.03-delta 500 call is the biggest wall on the board — it leads.
  const model = buildHighOiContractList({ rows: MSFT_ROWS, underlyingPrice: SPOT, now });
  assert.equal(model.calls.find((row) => row.strike === 500).importance, 5);
  // Schwab's overnight ±999 sentinel changes nothing.
  const sentinel = MSFT_ROWS.map((row) => ({ ...row, delta: 999 }));
  const overnight = buildHighOiContractList({ rows: sentinel, underlyingPrice: SPOT, now });
  assert.deepEqual(
    overnight.calls.map((row) => row.strike),
    model.calls.map((row) => row.strike),
  );
});

test("with no EM band, falls back to the top-N by OI per side", () => {
  const model = buildHighOiContractList({ rows: MSFT_ROWS, underlyingPrice: SPOT, topPerSide: 2, now });
  // No expectedMove -> no band -> the 2 biggest-OI walls each side.
  assert.deepEqual(model.calls.map((row) => row.strike), [525, 500]);
  assert.deepEqual(model.puts.map((row) => row.strike), [460, 450]);
  assert.equal(model.callOi, 77_962);
  assert.equal(model.putOi, 25_742);
});

test("a strike exactly at spot is a put wall (support being stood on)", () => {
  const rows = [
    { side: "CALL", strike: 480, delta: 0.5, volume: 1, open_interest: 9_000, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "PUT", strike: 480, delta: -0.5, volume: 1, open_interest: 4_000, expiry: "2026-08-21", days_to_expiration: 1 },
    { side: "CALL", strike: 485, delta: 0.3, volume: 1, open_interest: 2_000, expiry: "2026-08-21", days_to_expiration: 1 },
  ];
  const model = buildHighOiContractList({ rows, underlyingPrice: 480, now });
  assert.deepEqual(model.calls.map((row) => row.strike), [485]);
  assert.deepEqual(model.puts.map((row) => row.strike), [480]);
  assert.equal(model.puts[0].openInterest, 4_000);
});

test("importance is scored against the side leader", () => {
  const model = buildHighOiContractList({ rows: MSFT_ROWS, underlyingPrice: SPOT, now });
  // Calls lead with 47.3k: 30.7k -> 5 (65%), 17.7k -> 4 (37%), 3.5k -> 1 (7%).
  assert.equal(model.calls.find((row) => row.strike === 525).importance, 5);
  assert.equal(model.calls.find((row) => row.strike === 510).importance, 4);
  // 7% of the leader is a faint wall, not a small one: the sheet's own MSFT
  // panel scores 3.7k against a 48.3k leader (7.7%) as Imp 1.
  assert.equal(model.calls.find((row) => row.strike === 505).importance, 1);
  // Puts lead with 13.0k: 8.4k -> 5 (65%), 1.7k -> 2 (13%).
  assert.equal(model.puts.find((row) => row.strike === 480).importance, 5);
  assert.equal(model.puts.find((row) => row.strike === 477.5).importance, 2);
});

test("the +/-2*EM band excludes far mega-walls when the near band is dense", () => {
  // NVDA-like dense near chain (>=8 strikes each side within +/-2*EM) plus far
  // walls with huge OI. Because the band is dense, no fill happens, so the far
  // 180/170/140 (past +/-2*EM of 208) are excluded despite their big OI - the
  // NVDA case the trader flagged.
  const rows = [];
  const nearPutOi = { 190: 67_000, 200: 45_000, 195: 31_000 };
  for (const strike of [185, 187.5, 190, 192.5, 195, 197.5, 200, 202.5, 205, 207.5]) {
    rows.push({ side: "PUT", strike, open_interest: nearPutOi[strike] || 5_000, volume: 1, expiry: "2026-09-18", days_to_expiration: 24 });
  }
  for (const strike of [180, 170, 140]) {
    rows.push({ side: "PUT", strike, open_interest: 66_000, volume: 1, expiry: "2026-09-18", days_to_expiration: 24 });
  }
  for (const strike of [210, 215, 220]) {
    rows.push({ side: "CALL", strike, open_interest: 30_000, volume: 1, expiry: "2026-09-18", days_to_expiration: 24 });
  }
  const model = buildHighOiContractList({ rows, underlyingPrice: 208, expectedMove: 12.63, now });
  const putStrikes = model.puts.map((row) => row.strike);
  for (const far of [180, 170, 140]) assert.equal(putStrikes.includes(far), false, `${far} should be out of band`);
  // The near band's biggest-OI strikes are kept.
  for (const near of [190, 200, 195]) assert.equal(putStrikes.includes(near), true);
});

test("front scope pins a single expiry", () => {
  const model = buildHighOiContractList({
    rows: MSFT_ROWS, underlyingPrice: SPOT, scope: "front", frontExpiry: "2026-09-18", now,
  });
  assert.ok(model.calls.every((row) => row.expiry === "2026-09-18"));
  assert.deepEqual(model.calls.map((row) => row.strike), [525, 510, 505, 500]);
});

test("carries this week's size as frontAlt when a later cycle dominates the strike", () => {
  const stacked = [
    { side: "CALL", strike: 315, delta: 0.30, volume: 2_000, open_interest: 15_000, last: 1.2, expiry: "2026-08-07", days_to_expiration: 1 },
    { side: "CALL", strike: 315, delta: 0.28, volume: 1_500, open_interest: 18_000, last: 2.4, expiry: "2026-08-21", days_to_expiration: 15 },
    // Tiny front OI below the 20% floor must not stack.
    { side: "CALL", strike: 320, delta: 0.20, volume: 100, open_interest: 500, last: 0.4, expiry: "2026-08-07", days_to_expiration: 1 },
    { side: "CALL", strike: 320, delta: 0.18, volume: 900, open_interest: 12_000, last: 1.1, expiry: "2026-08-21", days_to_expiration: 15 },
  ];
  const model = buildHighOiContractList({ rows: stacked, underlyingPrice: 312.8, now: new Date(Date.UTC(2026, 7, 6)) });
  const wall315 = model.calls.find((row) => row.strike === 315);
  assert.equal(wall315.openInterest, 18_000);
  assert.equal(wall315.frontAlt.openInterest, 15_000);
  assert.equal(wall315.frontAlt.expiry, "2026-08-07");
  const wall320 = model.calls.find((row) => row.strike === 320);
  assert.equal(wall320.frontAlt, undefined);
});

test("without a spot the split is impossible — fall back to contract-type grouping", () => {
  const model = buildHighOiContractList({ rows: MSFT_ROWS, underlyingPrice: 0, now });
  assert.ok(model.calls.length > 0);
  assert.ok(model.puts.length > 0);
});

test("falls back to all expiries when nothing lists inside the window", () => {
  const quarterlyOnly = [
    { side: "CALL", strike: 105, delta: 0.30, volume: 100, open_interest: 77_000, expiry: "2026-12-18", days_to_expiration: 120 },
    { side: "PUT", strike: 80, delta: -0.30, volume: 50, open_interest: 12_000, expiry: "2026-12-18", days_to_expiration: 120 },
  ];
  const model = buildHighOiContractList({ rows: quarterlyOnly, underlyingPrice: 89.89, now: new Date(Date.UTC(2026, 7, 6)) });
  assert.equal(model.calls.length, 1);
  assert.equal(model.puts.length, 1);
});

test("returns an empty model without rows", () => {
  const model = buildHighOiContractList({ rows: [], underlyingPrice: SPOT, now });
  assert.deepEqual(model.calls, []);
  assert.deepEqual(model.puts, []);
  assert.equal(model.putCallRatio, 0);
  assert.equal(model.peakOi, 0);
});

test("expected move skips a SPENT expiry but keeps a live same-day one", () => {
  assert.equal(expectedMoveFromExpiries({ "2026-08-17": 0.915, "2026-08-19": 7.525 }), 7.525);
  assert.equal(expectedMoveFromExpiries({ "2026-08-18": 2.87, "2026-08-19": 4.19 }), 2.87);
});

test("expected move falls back when there is nothing to compare against", () => {
  assert.equal(expectedMoveFromExpiries({ "2026-08-17": 0.915 }), 0.915);
  assert.equal(expectedMoveFromExpiries({}), 0);
  assert.equal(expectedMoveFromExpiries(null), 0);
});

// --- Imp column, pinned to the reference sheets ---------------------------
// The two panels below are transcribed straight off the 2026-08-20 MomoX /
// Trading Alphas sheets, OI and Imp together. They are the only source that
// fixes HIGH_OI_IMPORTANCE_BREAKS, so they must stay exact: every other Imp
// assertion in this file is scored against our own chain and would happily
// agree with a wrong formula.

const SHEET_NOW = new Date(Date.UTC(2026, 7, 20));

const sheetRow = (side, strike, openInterest, expiry = "2026-08-21") => ({
  side,
  strike,
  delta: 999,
  volume: 100,
  open_interest: openInterest,
  last: 1,
  expiry,
  days_to_expiration: expiry === "2026-08-21" ? 1 : 29,
});

test("sheet parity: the MSFT Imp column is reproduced exactly", () => {
  // MSFT panel, BMO 484.42. Call 140.8k / Put 59.8k in its header.
  const rows = [
    sheetRow("CALL", 530, 16_600),
    sheetRow("CALL", 525, 30_700, "2026-09-18"),
    sheetRow("CALL", 520, 11_500),
    sheetRow("CALL", 510, 17_600, "2026-09-18"),
    sheetRow("CALL", 500, 48_300),
    sheetRow("CALL", 495, 3_900, "2026-09-18"),
    sheetRow("CALL", 490, 8_400),
    sheetRow("CALL", 485, 3_700),
    sheetRow("PUT", 480, 8_500),
    sheetRow("PUT", 475, 5_200),
    sheetRow("PUT", 470, 6_000, "2026-09-18"),
    sheetRow("PUT", 465, 4_700),
    sheetRow("PUT", 460, 13_300),
    sheetRow("PUT", 455, 3_300),
    sheetRow("PUT", 450, 11_800),
    sheetRow("PUT", 440, 6_900, "2026-09-18"),
  ];
  const model = buildHighOiContractList({ rows, underlyingPrice: 484.42, now: SHEET_NOW });

  assert.deepEqual(model.calls.map((row) => row.strike), [530, 525, 520, 510, 500, 495, 490, 485]);
  assert.deepEqual(model.calls.map((row) => row.importance), [3, 5, 3, 4, 5, 1, 2, 1]);
  assert.deepEqual(model.puts.map((row) => row.strike), [480, 475, 470, 465, 460, 455, 450, 440]);
  assert.deepEqual(model.puts.map((row) => row.importance), [5, 4, 4, 4, 5, 3, 5, 5]);
  // The sheet header sums the printed walls only.
  assert.equal(model.callOi, 140_700);
  assert.equal(model.putOi, 59_700);
});

test("sheet parity: the SPY Imp column is reproduced exactly", () => {
  // SPY panel, BMO 764.63.
  const rows = [
    sheetRow("CALL", 795, 15_000),
    sheetRow("CALL", 790, 47_200),
    sheetRow("CALL", 785, 45_600),
    sheetRow("CALL", 780, 51_900),
    sheetRow("CALL", 775, 58_500),
    sheetRow("CALL", 773, 12_700),
    sheetRow("CALL", 770, 28_900),
    sheetRow("CALL", 765, 17_100),
    sheetRow("PUT", 760, 48_800),
    sheetRow("PUT", 755, 47_200),
    sheetRow("PUT", 750, 57_500),
    sheetRow("PUT", 745, 32_000),
    sheetRow("PUT", 740, 33_300),
    sheetRow("PUT", 737, 22_000),
    sheetRow("PUT", 735, 50_800),
    sheetRow("PUT", 732, 27_600),
  ];
  const model = buildHighOiContractList({ rows, underlyingPrice: 764.63, now: SHEET_NOW });

  assert.deepEqual(model.calls.map((row) => row.importance), [3, 5, 5, 5, 5, 2, 4, 3]);
  assert.deepEqual(model.puts.map((row) => row.importance), [5, 5, 5, 5, 5, 4, 5, 4]);
});

test("importance breaks stay in step with the python alert ladder", () => {
  // oi_auto_alerts.IMPORTANCE_BREAKS must carry the same four numbers.
  assert.deepEqual(HIGH_OI_IMPORTANCE_BREAKS, [0.125, 0.225, 0.35, 0.5]);
  assert.equal(highOiImportance(1, 100), 1);
  assert.equal(highOiImportance(13, 100), 2);
  assert.equal(highOiImportance(23, 100), 3);
  assert.equal(highOiImportance(36, 100), 4);
  assert.equal(highOiImportance(50, 100), 5);
});

// --- Board display formatters (the Delta / Vol / Mark columns) ---

test("formatDelta: two decimals, signed, and a dead-feed zero prints a dash", () => {
  assert.equal(formatDelta(0.43), "0.43");
  assert.equal(formatDelta(-0.34), "-0.34");
  assert.equal(formatDelta(0.4321), "0.43");
  assert.equal(formatDelta(1), "1.00");
  // Overnight feeds report delta 0 until the market wakes; a printed 0.00
  // would read as a real greek.
  assert.equal(formatDelta(0), "-");
  assert.equal(formatDelta(null), "-");
  assert.equal(formatDelta(undefined), "-");
  assert.equal(formatDelta(Number.NaN), "-");
  // A real-but-tiny negative delta must not print "-0.00".
  assert.equal(formatDelta(-0.004), "0.00");
  assert.equal(formatDelta(0.004), "0.00");
});

test("formatCompactVolume: MomoX 18k / 863 style, zero prints a dash", () => {
  assert.equal(formatCompactVolume(863), "863");
  assert.equal(formatCompactVolume(999), "999");
  assert.equal(formatCompactVolume(1_000), "1k");
  assert.equal(formatCompactVolume(1_499), "1k");
  assert.equal(formatCompactVolume(1_500), "2k");
  assert.equal(formatCompactVolume(18_312), "18k");
  assert.equal(formatCompactVolume(0), "-");
  assert.equal(formatCompactVolume(null), "-");
  assert.equal(formatCompactVolume(undefined), "-");
  assert.equal(formatCompactVolume(Number.NaN), "-");
});

test("formatMark: option price to two decimals, zero prints a dash", () => {
  assert.equal(formatMark(0.55), "0.55");
  assert.equal(formatMark(4.7), "4.70");
  assert.equal(formatMark(12.345), "12.35");
  assert.equal(formatMark(0), "-");
  assert.equal(formatMark(null), "-");
  assert.equal(formatMark(undefined), "-");
  assert.equal(formatMark(Number.NaN), "-");
});
