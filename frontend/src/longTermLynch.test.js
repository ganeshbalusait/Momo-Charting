// Peter view on the Long Term page (2026-11-19, "add one more next to MOS like
// Peter then show me his star sheet and tickers", quality stocks only).
import assert from "node:assert/strict";
import test from "node:test";

import { LYNCH_TILES, lynchCounts, lynchRows, lynchSheet } from "./longTermLynch.js";

const row = (over = {}) => ({
  symbol: "ADBE", name: "Adobe", signal: "BUY", quality: "HIGH", young: false, financial: false, price: 220,
  pe: 12, epsGrowth: 0.2, salesGrowth: 0.16, fcfGrowth: 0.16, debtToEquity: 0.54, ...over,
});
const passed = (sheet) => Object.fromEntries(sheet.checks.map((c) => [c.key, c.passed]));

test("a quality grower at a low P/E earns all five stars and reads CHEAP", () => {
  const sheet = lynchSheet(row());
  assert.equal(sheet.peg, 0.6);
  assert.deepEqual([sheet.stars, sheet.of, sheet.verdict, sheet.type], [5, 5, "CHEAP", "Fast grower"]);
  assert.deepEqual(sheet.checks.map((c) => c.key), ["growth", "sales", "debt", "cash", "price"]);
});

test("PEG is P/E over the growth percent: 1 or less CHEAP, up to 2 FAIR, above PRICEY", () => {
  assert.equal(lynchSheet(row({ pe: 20 })).verdict, "CHEAP"); // 20 / 20 = 1.0
  assert.equal(lynchSheet(row({ pe: 20.2 })).verdict, "FAIR");
  assert.equal(lynchSheet(row({ pe: 40 })).verdict, "FAIR"); // 2.0
  assert.equal(lynchSheet(row({ pe: 41 })).verdict, "PRICEY");
  assert.equal(passed(lynchSheet(row({ pe: 41 }))).price, false);
});

test("growth above 50% a year loses the growth star and counts as 50 in the PEG", () => {
  const sheet = lynchSheet(row({ epsGrowth: 0.82, pe: 18.3 }));
  assert.equal(passed(sheet).growth, false);
  assert.equal(sheet.peg, 0.37); // 18.3 / 50, never 18.3 / 82
  assert.match(sheet.checks[0].text, /too fast to last/);
});

test("the type follows earnings growth: under 10 slow, 10 to 20 stalwart, 20 and up fast", () => {
  assert.equal(lynchSheet(row({ epsGrowth: 0.08 })).type, "Slow grower");
  assert.equal(passed(lynchSheet(row({ epsGrowth: 0.08 }))).growth, false);
  assert.equal(lynchSheet(row({ epsGrowth: 0.1 })).type, "Stalwart");
  assert.equal(lynchSheet(row({ epsGrowth: 0.2 })).type, "Fast grower");
});

test("each star has its own line: weak sales, heavy debt and shrinking cash flow each cost one", () => {
  assert.deepEqual(passed(lynchSheet(row({ salesGrowth: 0.03 }))), { growth: true, sales: false, debt: true, cash: true, price: true });
  assert.equal(passed(lynchSheet(row({ debtToEquity: 0.81 }))).debt, false);
  assert.equal(passed(lynchSheet(row({ debtToEquity: 0.8 }))).debt, true);
  assert.equal(passed(lynchSheet(row({ fcfGrowth: -0.02 }))).cash, false);
  // a missing figure is a miss, never a free star (negative equity has no debt ratio)
  const blank = lynchSheet(row({ debtToEquity: null, salesGrowth: null, fcfGrowth: null }));
  assert.deepEqual([blank.stars, blank.of], [2, 5]);
});

test("a bank is not judged on debt or cash flow: those stars are left out, not failed", () => {
  const sheet = lynchSheet(row({ financial: true, debtToEquity: 1.67, fcfGrowth: null }));
  assert.deepEqual([passed(sheet).debt, passed(sheet).cash], [null, null]);
  assert.deepEqual([sheet.stars, sheet.of], [3, 3]);
});

// 2026-11-19 "add a cyclical warning label": a chip, oil or metals company
// looks cheapest when its earnings are at the top of the cycle.
test("a cyclical is never called CHEAP: it reads CYCLICAL and the price star is left out", () => {
  const sheet = lynchSheet(row({ symbol: "MU", cyclical: "chips", pe: 9.1, epsGrowth: 0.21 }));
  assert.deepEqual([sheet.verdict, sheet.type, sheet.cyclical], ["CYCLICAL", "Cyclical · chips", "chips"]);
  assert.equal(sheet.peg, 0.43, "the PEG is still shown");
  assert.equal(passed(sheet).price, null);
  assert.match(sheet.checks[4].text, /PEG 0\.43, not judged/);
  assert.deepEqual([sheet.stars, sheet.of], [4, 4]);
  // a pricey cyclical is CYCLICAL too: one tile holds them all
  assert.equal(lynchSheet(row({ cyclical: "oil & gas", pe: 60 })).verdict, "CYCLICAL");
  // rows from a server that does not send the mark yet behave as before
  assert.equal(lynchSheet(row({ cyclical: undefined })).verdict, "CHEAP");
  assert.equal(lynchSheet(row({ cyclical: null })).verdict, "CHEAP");
  assert.equal(lynchSheet(row({ cyclical: "" })).verdict, "CHEAP");
  const list = lynchRows([row({ symbol: "MU", cyclical: "chips" }), row({})]);
  assert.deepEqual(lynchCounts(list), { CHEAP: 1, FAIR: 0, PRICEY: 0, CYCLICAL: 1 });
  assert.deepEqual(lynchRows([row({ symbol: "MU", cyclical: "chips" }), row({})], { tile: "CYCLICAL" }).map((x) => x.row.symbol), ["MU"]);
});

test("only quality companies with a price, a profit and a growth record get a sheet", () => {
  assert.equal(lynchSheet(row({ quality: "LOW" })), null);
  assert.equal(lynchSheet(row({ young: true })), null);
  for (const signal of ["STALE", "SHORT_HISTORY", "NO_PRICE", "ETF", "CHECK_DATA"]) assert.equal(lynchSheet(row({ signal })), null, signal);
  assert.equal(lynchSheet(row({ pe: null })), null);
  assert.equal(lynchSheet(row({ epsGrowth: null })), null);
  assert.equal(lynchSheet(row({ epsGrowth: -0.02 })), null, "shrinking earnings have no PEG");
  assert.equal(lynchSheet(row({ signal: "AVOID" })).verdict, "CHEAP", "a Rule #1 AVOID can still be on Peter's list");
  assert.equal(lynchSheet(null), null);
});

test("the list is most stars first, then lowest PEG; a tile or the search narrows it", () => {
  const rows = [
    row({ symbol: "COST", name: "Costco", pe: 52.8, epsGrowth: 0.15 }),          // PEG 3.52, 4 stars
    row({ symbol: "MSFT", name: "Microsoft", pe: 25.8, epsGrowth: 0.23 }),       // PEG 1.12, 4 stars
    row({ symbol: "DECK", name: "Deckers", pe: 10.5, epsGrowth: 0.36 }),         // PEG 0.29, 5 stars
    row({}),                                                                     // ADBE 0.6, 5 stars
    row({ symbol: "TXN", quality: "LOW" }),
  ];
  const list = lynchRows(rows);
  assert.deepEqual(list.map((x) => x.row.symbol), ["DECK", "ADBE", "MSFT", "COST"]);
  assert.deepEqual(lynchCounts(list), { CHEAP: 2, FAIR: 1, PRICEY: 1, CYCLICAL: 0 });
  assert.deepEqual(LYNCH_TILES, ["CHEAP", "FAIR", "PRICEY", "CYCLICAL"]);
  assert.deepEqual(lynchRows(rows, { tile: "PRICEY" }).map((x) => x.row.symbol), ["COST"]);
  assert.deepEqual(lynchRows(rows, { query: "micro" }).map((x) => x.row.symbol), ["MSFT"]);
  assert.deepEqual(lynchRows(rows, { query: "deck" }).map((x) => x.row.symbol), ["DECK"]);
  assert.deepEqual(lynchRows(null), []);
});
