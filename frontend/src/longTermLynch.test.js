// Peter view on the Long Term page (2026-11-19, "add one more next to MOS like
// Peter then show me his star sheet and tickers", quality stocks only).
import assert from "node:assert/strict";
import test from "node:test";

import {
  LYNCH_BACKTEST_NOTE, LYNCH_SCOPES, LYNCH_SCOPE_LABEL, LYNCH_TILES, defaultLynchScope, lynchCounts, lynchMissing, lynchRows, lynchSheet,
} from "./longTermLynch.js";

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

// 2026-11-19 "fix all issue": Lynch's own formula adds the dividend to the growth.
test("the dividend yield joins the growth in the PEG, so a dividend payer reads cheaper", () => {
  // P/E 20, growth 12%: plain PEG 1.67 (FAIR). With a 4% dividend: 20 / 16 = 1.25.
  const plain = lynchSheet(row({ pe: 20, epsGrowth: 0.12 }));
  const payer = lynchSheet(row({ pe: 20, epsGrowth: 0.12, dividendYield: 0.04 }));
  assert.deepEqual([plain.peg, payer.peg], [1.67, 1.25]);
  assert.equal(payer.dividendPct, 4);
  assert.match(payer.checks[4].text, /PEG 1\.25 with the 4\.0% dividend/);
  assert.equal(plain.checks[4].text, "PEG 1.67");
  // P/E 16 on 12% growth + 4% dividend = exactly 1.0: the dividend earns the price star
  assert.equal(passed(lynchSheet(row({ pe: 16, epsGrowth: 0.12, dividendYield: 0.04 }))).price, true);
  assert.equal(passed(lynchSheet(row({ pe: 16, epsGrowth: 0.12 }))).price, false);
  // a tiny or missing yield changes nothing and is not mentioned
  for (const dividendYield of [0, null, undefined, -0.01, "0.04", NaN]) {
    const sheet = lynchSheet(row({ pe: 20, epsGrowth: 0.12, dividendYield }));
    assert.deepEqual([sheet.peg, sheet.dividendPct, sheet.checks[4].text], [1.67, 0, "PEG 1.67"], String(dividendYield));
  }
  // growth is still capped at 50 before the dividend is added
  assert.equal(lynchSheet(row({ pe: 26, epsGrowth: 0.8, dividendYield: 0.02 })).peg, 0.5);
});

// Lynch's warning sign: inventory growing faster than sales.
test("inventory turning over 10% slower than last year is flagged; the stars do not change", () => {
  const slow = lynchSheet(row({ inventoryTurnChange: -0.25 }));
  assert.equal(slow.inventoryWarning, "Inventory is turning over 25% slower than a year ago: it is growing faster than sales.");
  assert.deepEqual([slow.stars, slow.of], [5, 5]);
  assert.match(lynchSheet(row({ inventoryTurnChange: -0.1 })).inventoryWarning, /10% slower/);
  for (const inventoryTurnChange of [-0.09, 0, 0.3, null, undefined, "x", NaN]) {
    assert.equal(lynchSheet(row({ inventoryTurnChange })).inventoryWarning, null, String(inventoryTurnChange));
  }
});

// 2026-11-19 "add mag7 tickers and watchlist tickers separate also".
test("Mag7 lists every Mag7 company, quality or not; Watchlist keeps to quality names", () => {
  const lists = { Mag7: ["AAPL", "TSLA", "AMZN", "SPY", "NVDA"], Watchlist: ["ADBE", "TSLA", "HD"] };
  const rows = [
    row({ symbol: "AAPL", pe: 39, epsGrowth: 0.15 }),
    row({ symbol: "TSLA", quality: "LOW", pe: 30, epsGrowth: 0.3 }),            // scored, but not a quality company
    row({ symbol: "AMZN", quality: "LOW", pe: 30, epsGrowth: -0.05 }),          // earnings did not grow: no sheet
    row({ symbol: "SPY", signal: "ETF", quality: null, pe: null, epsGrowth: null }),
    row({ symbol: "NVDA", cyclical: "chips", pe: 18, epsGrowth: 0.4 }),
    row({ symbol: "ADBE" }),
    row({ symbol: "HD", quality: "LOW" }),
  ];
  const mag7 = lynchRows(rows, { scope: "Mag7", lists });
  assert.deepEqual(mag7.map((x) => x.row.symbol), ["TSLA", "NVDA", "AAPL", "AMZN"], "sheets first (stars, PEG), then the ones with no sheet; ETFs left out");
  const tsla = mag7[0].sheet;
  assert.deepEqual([tsla.lowQuality, tsla.verdict, tsla.stars], [true, "CHEAP", 5]);
  assert.equal(mag7[2].sheet.lowQuality, false);
  assert.deepEqual([mag7[3].sheet, mag7[3].reason], [null, "earnings did not grow"]);
  assert.deepEqual(lynchCounts(mag7), { CHEAP: 1, FAIR: 0, PRICEY: 1, CYCLICAL: 1 });
  assert.deepEqual(lynchRows(rows, { scope: "Mag7", lists, tile: "CHEAP" }).map((x) => x.row.symbol), ["TSLA"], "a tile drops the cards with no sheet");
  // Watchlist: members only, quality only (TSLA and HD are on it but not quality)
  assert.deepEqual(lynchRows(rows, { scope: "Watchlist", lists }).map((x) => x.row.symbol), ["ADBE"]);
  // no scope: the quality list, as before
  assert.deepEqual(lynchRows(rows).map((x) => x.row.symbol), ["ADBE", "NVDA", "AAPL"]);
  // a list the server did not send is empty, never everything
  assert.deepEqual(lynchRows(rows, { scope: "Mag7", lists: {} }), []);
  assert.deepEqual(lynchRows(rows, { scope: "Mag7" }), []);
  assert.deepEqual(LYNCH_SCOPES, ["Best", "Quality", "Mag7", "Watchlist"]);
});

// 2026-11-20 "Confused here which one buy?" then "Peter one don't mixed with Warren":
// Peter's best is Peter's own answer - every judged star earned and the price CHEAP. Warren's
// verdict plays no part anywhere in the Peter view.
test("Peter's best lists the companies with every star and a CHEAP price, whatever Warren's rule says", () => {
  const rows = [
    row({ symbol: "ZM", signal: "AVOID", pe: 7.8, epsGrowth: 0.37 }),            // full marks, Warren skips: still listed
    row({ symbol: "QCOM", signal: "WAIT" }),                                    // full marks
    row({ symbol: "ADBE", signal: "BUY" }),                                     // full marks
    row({ symbol: "BRBR", signal: "BUY", debtToEquity: 2 }),                    // CHEAP but lost the debt star
    row({ symbol: "COST", signal: "BUY", pe: 52 }),                             // all other stars, PRICEY
    row({ symbol: "MU", signal: "BUY", cyclical: "chips" }),                    // 4/4 but a cyclical is never CHEAP
    row({ symbol: "AXP", signal: "WAIT", financial: true }),                    // a bank: 3 of 3 judged stars, CHEAP
    row({ symbol: "TSLA", signal: "BUY", quality: "LOW" }),                     // not a quality company
  ];
  assert.deepEqual(lynchRows(rows, { scope: "Best" }).map((x) => x.row.symbol), ["ZM", "ADBE", "QCOM", "AXP"]);
  assert.deepEqual(lynchRows(rows, { scope: "Best", query: "qcom" }).map((x) => x.row.symbol), ["QCOM"]);
  assert.deepEqual(lynchRows([row({ pe: 60 })], { scope: "Best" }), []);
  assert.equal(LYNCH_SCOPES[0], "Best");
  assert.equal(LYNCH_SCOPE_LABEL.Best, "Peter's best");
  assert.ok(!LYNCH_SCOPES.includes("Both"), "no group mixes the two checklists");
});

test("the Peter view opens on Peter's best when it has a company, else on Quality", () => {
  assert.equal(defaultLynchScope([row({ signal: "AVOID" })]), "Best", "Warren's verdict does not matter");
  assert.equal(defaultLynchScope([row({ pe: 60 }), row({ debtToEquity: 2 })]), "Quality");
  assert.equal(defaultLynchScope([]), "Quality");
  assert.equal(defaultLynchScope(null), "Quality");
});

test("why a Mag7 company has no star sheet", () => {
  assert.equal(lynchMissing(row({ signal: "NO_PRICE" })), "no live price");
  assert.equal(lynchMissing(row({ signal: "STALE" })), "filings are stale");
  assert.equal(lynchMissing(row({ signal: "SHORT_HISTORY" })), "not enough years of filings");
  assert.equal(lynchMissing(row({ pe: null })), "no profit, so no P/E");
  assert.equal(lynchMissing(row({ epsGrowth: null })), "no earnings growth figure");
  assert.equal(lynchMissing(row({ epsGrowth: -0.1 })), "earnings did not grow");
  assert.equal(lynchMissing(row({})), null);
});

test("the page carries the back-test result in plain words, numbers as the run printed them", () => {
  assert.match(LYNCH_BACKTEST_NOTE, /CHEAP tag did NOT beat the quality group/);
  for (const figure of ["98 of today's companies", "452 yearly picks", "72% vs all quality 74%", "59% vs 66%", "82% vs 85%", "73% vs 82%"]) {
    assert.ok(LYNCH_BACKTEST_NOTE.includes(figure), figure);
  }
  assert.match(LYNCH_BACKTEST_NOTE, /survivors only/);
  assert.match(LYNCH_BACKTEST_NOTE, /Not advice\.$/);
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
