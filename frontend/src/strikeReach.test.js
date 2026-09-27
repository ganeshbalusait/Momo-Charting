import assert from "node:assert/strict";
import test from "node:test";

import {
  etMinutesSinceMidnight,
  expiryLabel,
  parseStrikeQuery,
  reachRows,
  strikeReach,
  todayInNewYork,
  upcomingExpiries,
  wallReach,
} from "./strikeReach.js";

const close = (actual, expected, places = 2) =>
  assert.ok(
    Math.abs(actual - expected) < 0.5 * 10 ** -places,
    `expected ${actual} to be ~${expected}`,
  );

// SHOP's real chain, 2026-09-22 (Tue). Its weeklies are Fridays.
const SHOP_MOVES = {
  "2026-09-18": 5.0, // already expired - must be skipped
  "2026-09-25": 7.79,
  "2026-10-02": 12.6372,
  "2026-10-09": 16.4536,
  "2026-10-16": 19.8378,
};
const TODAY = "2026-09-22";

test("expiryLabel reads the calendar date, not the browser zone", () => {
  assert.equal(expiryLabel("2026-09-25"), "Fri 9/25");
  assert.equal(expiryLabel("2026-09-24"), "Thu 9/24");
  assert.equal(expiryLabel("2026-10-02T00:00:00"), "Fri 10/2");
  assert.equal(expiryLabel("junk"), "");
  assert.equal(expiryLabel(null), "");
});

test("reachRows: next three expiries on/after today with 1x and 2x levels", () => {
  const rows = reachRows(SHOP_MOVES, 146.43, TODAY);
  assert.deepEqual(rows.map((r) => r.expiry), ["2026-09-25", "2026-10-02", "2026-10-09"]);
  const [first] = rows;
  assert.equal(first.label, "Fri 9/25");
  close(first.em, 7.79);
  close(first.emPct, 5.32);
  close(first.call1x, 154.22);
  close(first.call2x, 162.01);
  close(first.put1x, 138.64);
  close(first.put2x, 130.85);
});

test("reachRows: today's own expiry counts, unsorted input is sorted", () => {
  const rows = reachRows({ "2026-09-25": 3, "2026-09-22": 1 }, 100, TODAY, 5);
  assert.deepEqual(rows.map((r) => r.expiry), ["2026-09-22", "2026-09-25"]);
});

test("reachRows skips non-positive / NaN moves and survives bad input", () => {
  const rows = reachRows(
    { "2026-09-25": 0, "2026-10-02": -1, "2026-10-09": "abc", "2026-10-16": null, "2026-10-23": 9 },
    100,
    TODAY,
  );
  assert.deepEqual(rows.map((r) => r.expiry), ["2026-10-23"]);
  assert.deepEqual(reachRows(null, 100, TODAY), []);
  assert.deepEqual(reachRows(SHOP_MOVES, 0, TODAY), []);
  assert.deepEqual(reachRows(SHOP_MOVES, NaN, TODAY), []);
  assert.deepEqual(reachRows(SHOP_MOVES, undefined, TODAY), []);
  assert.deepEqual(reachRows({ "not-a-date": 5 }, 100, TODAY), []);
});

test("upcomingExpiries drops past and junk dates", () => {
  assert.deepEqual(upcomingExpiries(["2026-10-02", "x", "2026-09-01", "2026-09-25"], TODAY), [
    "2026-09-25",
    "2026-10-02",
  ]);
  assert.deepEqual(upcomingExpiries(null, TODAY), []);
});

test("upcomingExpiries drops today's own expiry after 16:00 ET, keeps it before", () => {
  const list = ["2026-09-22", "2026-09-25"];
  assert.deepEqual(upcomingExpiries(list, TODAY, 16 * 60), ["2026-09-25"]);
  assert.deepEqual(upcomingExpiries(list, TODAY, 15 * 60 + 59), list);
  // No time given at all: unaffected, today's expiry stays (back-compat).
  assert.deepEqual(upcomingExpiries(list, TODAY), list);
});

test("reachRows also drops today's own expiry after the 16:00 ET cutoff", () => {
  const moves = { "2026-09-22": 1, "2026-09-25": 3 };
  assert.deepEqual(reachRows(moves, 100, TODAY, 5, 16 * 60).map((r) => r.expiry), [
    "2026-09-25",
  ]);
  assert.deepEqual(reachRows(moves, 100, TODAY, 5, 15 * 60).map((r) => r.expiry), [
    "2026-09-22",
    "2026-09-25",
  ]);
});

const SHOP_EXPIRIES = ["2026-09-25", "2026-10-02", "2026-10-09", "2026-10-16"];
const GOOGL_EXPIRIES = ["2026-09-23", "2026-09-24", "2026-09-25", "2026-09-30"];

test("parseStrikeQuery reads the trader's shorthand", () => {
  assert.deepEqual(parseStrikeQuery("160C Thu", GOOGL_EXPIRIES), {
    strike: 160,
    side: "C",
    expiry: "2026-09-24",
  });
  assert.deepEqual(parseStrikeQuery("160c", SHOP_EXPIRIES), {
    strike: 160,
    side: "C",
    expiry: "2026-09-25",
  });
  assert.deepEqual(parseStrikeQuery("95P 9/25", SHOP_EXPIRIES), {
    strike: 95,
    side: "P",
    expiry: "2026-09-25",
  });
  assert.deepEqual(parseStrikeQuery("370 C Wed", GOOGL_EXPIRIES), {
    strike: 370,
    side: "C",
    expiry: "2026-09-23",
  });
  assert.deepEqual(parseStrikeQuery("160", SHOP_EXPIRIES), {
    strike: 160,
    side: "C",
    expiry: "2026-09-25",
  });
  assert.deepEqual(parseStrikeQuery("  147.5 put 10/9 ", SHOP_EXPIRIES), {
    strike: 147.5,
    side: "P",
    expiry: "2026-10-09",
  });
  assert.deepEqual(parseStrikeQuery("$160C Friday", SHOP_EXPIRIES), {
    strike: 160,
    side: "C",
    expiry: "2026-09-25",
  });
});

test("parseStrikeQuery: an optional leading symbol or leading weekday", () => {
  assert.deepEqual(parseStrikeQuery("SHOP 160C Fri", SHOP_EXPIRIES), {
    strike: 160,
    side: "C",
    expiry: "2026-09-25",
  });
  assert.deepEqual(parseStrikeQuery("Fri 160C", SHOP_EXPIRIES), {
    strike: 160,
    side: "C",
    expiry: "2026-09-25",
  });
  assert.deepEqual(parseStrikeQuery("shop 160 c fri", SHOP_EXPIRIES), {
    strike: 160,
    side: "C",
    expiry: "2026-09-25",
  });
  // A trailing unknown word is still unreadable - only the front is lenient.
  assert.equal(parseStrikeQuery("160 shop", SHOP_EXPIRIES), null);
});

test("parseStrikeQuery: a typed year must equal the expiry's actual year", () => {
  assert.deepEqual(parseStrikeQuery("160C 9/25/26", SHOP_EXPIRIES), {
    strike: 160,
    side: "C",
    expiry: "2026-09-25",
  });
  assert.deepEqual(parseStrikeQuery("160C 9/25/2026", SHOP_EXPIRIES), {
    strike: 160,
    side: "C",
    expiry: "2026-09-25",
  });
  assert.deepEqual(parseStrikeQuery("160C 9/25/27", SHOP_EXPIRIES), {
    strike: 160,
    side: "C",
    expiry: null,
    unmatchedExpiry: "9/25/27",
  });
});

test("parseStrikeQuery: a bare strike defaults to the nearest expiry that HAS a move", () => {
  const listed = ["2026-09-24", "2026-09-25"]; // union: 24 has no move, 25 does
  const withMoves = ["2026-09-25"];
  assert.deepEqual(parseStrikeQuery("160C", listed, withMoves), {
    strike: 160,
    side: "C",
    expiry: "2026-09-25",
  });
  // No preferred list given: falls back to the old behavior (first listed).
  assert.deepEqual(parseStrikeQuery("160C", listed), {
    strike: 160,
    side: "C",
    expiry: "2026-09-24",
  });
});

test("parseStrikeQuery: a weekday the symbol does not list is flagged, not swapped", () => {
  // SHOP only lists Fridays: "Thu" must not quietly become Friday.
  assert.deepEqual(parseStrikeQuery("160C Thu", SHOP_EXPIRIES), {
    strike: 160,
    side: "C",
    expiry: null,
    unmatchedExpiry: "thu",
  });
  assert.equal(parseStrikeQuery("160C 9/24", SHOP_EXPIRIES).expiry, null);
});

test("parseStrikeQuery returns null for what it cannot read", () => {
  assert.equal(parseStrikeQuery("", SHOP_EXPIRIES), null);
  assert.equal(parseStrikeQuery("   ", SHOP_EXPIRIES), null);
  assert.equal(parseStrikeQuery("abc", SHOP_EXPIRIES), null);
  assert.equal(parseStrikeQuery("C160", SHOP_EXPIRIES), null);
  assert.equal(parseStrikeQuery("160 banana", SHOP_EXPIRIES), null);
  assert.equal(parseStrikeQuery("160 C P", SHOP_EXPIRIES), null);
  assert.equal(parseStrikeQuery("0C", SHOP_EXPIRIES), null);
  assert.equal(parseStrikeQuery(null, SHOP_EXPIRIES), null);
  assert.equal(parseStrikeQuery(160, SHOP_EXPIRIES), null);
  // No expiries at all: still reads the strike, just no expiry to resolve.
  assert.deepEqual(parseStrikeQuery("160C", null), { strike: 160, side: "C", expiry: null });
});

test("strikeReach: SHOP 160C at 146.43 needed 1.74x the move (stretch)", () => {
  const r = strikeReach({ strike: 160, side: "C", spot: 146.43, em: 7.79 });
  close(r.need, 13.57);
  close(r.needPct, 9.27);
  close(r.multiple, 1.74);
  assert.equal(r.band, "stretch");
});

test("strikeReach: GOOGL 370C at 361.17 needed 1.46x the move (stretch)", () => {
  const r = strikeReach({ strike: 370, side: "C", spot: 361.17, em: 6.04 });
  close(r.need, 8.83);
  close(r.multiple, 1.46);
  assert.equal(r.band, "stretch");
});

test("strikeReach: puts measure the move DOWN to the strike", () => {
  const inside = strikeReach({ strike: 140, side: "P", spot: 146.43, em: 7.79 });
  close(inside.need, 6.43);
  close(inside.multiple, 0.83);
  assert.equal(inside.band, "inside");
  const far = strikeReach({ strike: 125, side: "p", spot: 146.43, em: 7.79 });
  assert.equal(far.band, "far");
  close(far.multiple, 2.75);
});

test("strikeReach: in the money is its own band", () => {
  const call = strikeReach({ strike: 140, side: "C", spot: 146.43, em: 7.79 });
  assert.equal(call.band, "itm");
  close(call.need, -6.43);
  assert.equal(strikeReach({ strike: 150, side: "P", spot: 146.43, em: 7.79 }).band, "itm");
  // Exactly at the money is not a move still needed.
  assert.equal(strikeReach({ strike: 100, side: "C", spot: 100, em: 2 }).band, "itm");
});

test("strikeReach: boundaries are inclusive of 1x and 2x", () => {
  assert.equal(strikeReach({ strike: 102, side: "C", spot: 100, em: 2 }).band, "inside");
  assert.equal(strikeReach({ strike: 104, side: "C", spot: 100, em: 2 }).band, "stretch");
  assert.equal(strikeReach({ strike: 104.01, side: "C", spot: 100, em: 2 }).band, "far");
});

test("strikeReach: bands the rounded multiple, not the raw float", () => {
  // 155 vs 147.60 with a 7.40 move is exactly 1x to a trader; the raw float
  // is 1.0000000000000007, which is > 1 and would have banded "stretch".
  const atOne = strikeReach({ strike: 155, side: "C", spot: 147.6, em: 7.4 });
  assert.equal(atOne.band, "inside");
  assert.equal(atOne.multiple, 1);

  // Exactly 2x, where the raw float lands a hair on either side of 2.
  const atTwo = strikeReach({ strike: 147.6 + 2 * 7.4, side: "C", spot: 147.6, em: 7.4 });
  assert.equal(atTwo.band, "stretch");
  assert.equal(atTwo.multiple, 2);

  // 2.01x is genuinely past the 2x line, rounding or not.
  const past = strikeReach({ strike: 147.6 + 2.01 * 7.4, side: "C", spot: 147.6, em: 7.4 });
  assert.equal(past.band, "far");
  close(past.multiple, 2.01);
});

test("strikeReach: bad inputs give null and never throw", () => {
  assert.equal(strikeReach(), null);
  assert.equal(strikeReach(null), null);
  assert.equal(strikeReach({ strike: 160, side: "C", spot: 146.43, em: 0 }), null);
  assert.equal(strikeReach({ strike: 160, side: "C", spot: NaN, em: 7 }), null);
  assert.equal(strikeReach({ strike: "x", side: "C", spot: 146, em: 7 }), null);
  assert.equal(strikeReach({ strike: 160, side: "X", spot: 146, em: 7 }), null);
  assert.equal(strikeReach({ strike: 160, spot: 146, em: 7 }), null);
});

test("todayInNewYork uses the exchange's date", () => {
  // 02:30 UTC on the 23rd is still the evening of the 22nd in New York.
  assert.equal(todayInNewYork(Date.UTC(2026, 8, 23, 2, 30)), "2026-09-22");
  assert.equal(todayInNewYork(Date.UTC(2026, 8, 23, 14, 0)), "2026-09-23");
});

test("etMinutesSinceMidnight reads the exchange's clock", () => {
  // 20:00 UTC in September (EDT, UTC-4) is 16:00 ET = 960 minutes.
  assert.equal(etMinutesSinceMidnight(Date.UTC(2026, 8, 22, 20, 0)), 16 * 60);
  assert.equal(etMinutesSinceMidnight(Date.UTC(2026, 8, 22, 19, 59)), 16 * 60 - 1);
  assert.equal(etMinutesSinceMidnight(Date.UTC(2026, 8, 22, 4, 30)), 30);
});

// --- wallReach ---
//
// INOD on 2026-09-22, the case that asked for this: spot 72.00, the big call
// wall is 75 on the Oct 16 monthly, the put wall 70 on the Friday weekly.
// `calls`/`puts` arrive strike-DESCENDING, exactly as buildHighOiContractList
// returns them.
const INOD_WALLS = {
  calls: [
    { strike: 80, openInterest: 900, expiry: "2026-10-16" },
    { strike: 75, openInterest: 155, expiry: "2026-10-16" },
  ],
  puts: [
    { strike: 70, openInterest: 2600, expiry: "2026-09-25" },
    { strike: 65, openInterest: 2617, expiry: "2026-10-16" },
  ],
};
const INOD_MOVES = { "2026-09-25": 6.15, "2026-10-16": 12.00 };

test("wallReach: picks the NEAREST wall each side, not the biggest", () => {
  const out = wallReach({
    walls: INOD_WALLS, spot: 72, expectedMoves: INOD_MOVES, todayIso: "2026-09-22",
  });
  assert.equal(out.call.strike, 75); // not 80
  assert.equal(out.put.strike, 70);  // not 65, though 65 holds more OI
  assert.equal(out.call.openInterest, 155);
  assert.equal(out.put.openInterest, 2600);
});

test("wallReach: measures each wall against its OWN expiry's move", () => {
  const out = wallReach({
    walls: INOD_WALLS, spot: 72, expectedMoves: INOD_MOVES, todayIso: "2026-09-22",
  });
  // 75 is +3.00 away; the Oct 16 move is 12.00 -> 0.25x, NOT 3/6.15 = 0.49x.
  close(out.call.multiple, 0.25);
  assert.equal(out.call.emExpiry, "2026-10-16");
  assert.equal(out.call.emFromOtherExpiry, false);
  close(out.put.multiple, 0.33); // 2.00 / 6.15, the Friday weekly
  assert.equal(out.put.emExpiry, "2026-09-25");
});

test("wallReach: a wall whose expiry has no move borrows one and SAYS so", () => {
  const out = wallReach({
    walls: INOD_WALLS, spot: 72,
    expectedMoves: { "2026-09-25": 6.15 }, // nothing for Oct 16
    todayIso: "2026-09-22",
  });
  assert.equal(out.call.emFromOtherExpiry, true);
  assert.equal(out.call.emLabel, "Fri 9/25");
  close(out.call.multiple, 0.49); // 3.00 / 6.15
});

test("wallReach: no expected moves at all still names the walls", () => {
  const out = wallReach({
    walls: INOD_WALLS, spot: 72, expectedMoves: {}, todayIso: "2026-09-22",
  });
  assert.equal(out.call.strike, 75);
  assert.equal(out.call.multiple, null);
  assert.equal(out.call.band, null);
});

test("wallReach: a put wall exactly at spot counts as support, not skipped", () => {
  const out = wallReach({
    walls: { calls: [], puts: [{ strike: 72, openInterest: 400, expiry: "2026-09-25" }] },
    spot: 72, expectedMoves: INOD_MOVES, todayIso: "2026-09-22",
  });
  assert.equal(out.put.strike, 72);
  assert.equal(out.put.band, "itm"); // need is 0 - the price is standing on it
  assert.equal(out.call, null);
});

test("wallReach: bands match strikeReach (a far wall reads far)", () => {
  const out = wallReach({
    walls: { calls: [{ strike: 95, openInterest: 500, expiry: "2026-09-25" }], puts: [] },
    spot: 72, expectedMoves: INOD_MOVES, todayIso: "2026-09-22",
  });
  close(out.call.multiple, 3.74); // 23.00 / 6.15
  assert.equal(out.call.band, "far");
});

test("wallReach: garbage in, null out - never throws", () => {
  assert.equal(wallReach(null), null);
  assert.equal(wallReach({ walls: INOD_WALLS, spot: 0 }), null);
  assert.equal(wallReach({ walls: null, spot: 72 }), null);
  assert.equal(wallReach({ walls: {}, spot: 72, expectedMoves: INOD_MOVES }), null);
  assert.equal(
    wallReach({ walls: { calls: [{ strike: "x" }], puts: [] }, spot: 72 }),
    null,
  );
});
