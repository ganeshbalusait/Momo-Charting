// Peter view on the Long Term page (2026-11-19, "add one more next to MOS like
// Peter then show me his star sheet and tickers", quality stocks only).
import assert from "node:assert/strict";
import test from "node:test";

import {
  lynchNoSheet, LYNCH_OWN_CHOICES, LYNCH_TILES_NOTE, LYNCH_VERDICT_LABEL,
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

test("the type follows earnings growth: under 10 slow, 10 to 20 steady (his \"stalwart\"), 20 and up fast", () => {
  assert.equal(lynchSheet(row({ epsGrowth: 0.08 })).type, "Slow grower");
  assert.equal(passed(lynchSheet(row({ epsGrowth: 0.08 }))).growth, false);
  assert.equal(lynchSheet(row({ epsGrowth: 0.1 })).type, "Steady grower");
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
  assert.equal(sheet.checks[4].text, "0.43, not used for this kind of business");
  assert.equal(sheet.kind, "Chip maker");
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
  assert.equal(payer.checks[4].text, "1.25 with its 4.0% yearly payout (1 or less is low)");
  assert.equal(plain.checks[4].text, "1.67 (1 or less is low)");
  // P/E 16 on 12% growth + 4% dividend = exactly 1.0: the dividend earns the price star
  assert.equal(passed(lynchSheet(row({ pe: 16, epsGrowth: 0.12, dividendYield: 0.04 }))).price, true);
  assert.equal(passed(lynchSheet(row({ pe: 16, epsGrowth: 0.12 }))).price, false);
  // a tiny or missing yield changes nothing and is not mentioned
  for (const dividendYield of [0, null, undefined, -0.01, "0.04", NaN]) {
    const sheet = lynchSheet(row({ pe: 20, epsGrowth: 0.12, dividendYield }));
    assert.deepEqual([sheet.peg, sheet.dividendPct, sheet.checks[4].text], [1.67, 0, "1.67 (1 or less is low)"], String(dividendYield));
  }
  // growth is still capped at 50 before the dividend is added
  assert.equal(lynchSheet(row({ pe: 26, epsGrowth: 0.8, dividendYield: 0.02 })).peg, 0.5);
});

// Lynch's warning sign: inventory growing faster than sales.
test("inventory turning over 10% slower than last year is flagged; the stars do not change", () => {
  const slow = lynchSheet(row({ inventoryTurnChange: -0.25 }));
  assert.equal(slow.inventoryWarning, "Unsold goods are piling up: it sells through its goods 25% slower than a year ago.");
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
  // audit 11-20 (AMZN): a company off the quality list is never a YES and never gets a dollar figure
  assert.equal(tsla.buy.answer, "OFFLIST");
  assert.match(tsla.buy.why, /^This page only scores quality companies: ones that earn \$20 or more a year on every \$100 put in\./);
  assert.deepEqual([tsla.plan.buyPrice, tsla.plan.buyText, tsla.plan.exitText], [null, "None worked out. It is not on the quality list.", null]);
  assert.equal(mag7[2].sheet.lowQuality, false);
  assert.deepEqual([mag7[3].sheet, mag7[3].reason], [null, "its profit did not grow over the ten years"]);
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
  assert.equal(LYNCH_SCOPE_LABEL.Best, "Peter would buy");
  assert.ok(!LYNCH_SCOPES.includes("Both"), "no group mixes the two checklists");
});

test("the Peter view opens on Peter's best when it has a company, else on Quality", () => {
  assert.equal(defaultLynchScope([row({ signal: "AVOID" })]), "Best", "Warren's verdict does not matter");
  assert.equal(defaultLynchScope([row({ pe: 60 }), row({ debtToEquity: 2 })]), "Quality");
  assert.equal(defaultLynchScope([]), "Quality");
  assert.equal(defaultLynchScope(null), "Quality");
});

// 2026-11-20 "if Peter was there did he buy it or not? Simple question ... Think like you are
// Peter": one answer per card, from his own checks.
test("Would Peter buy it? YES with every check and a low price; WAIT names his price; NO says why", () => {
  const yes = lynchSheet(row()).buy;
  assert.deepEqual(yes, { answer: "YES", why: "Every check passes and the price is low for its growth.", price: null, failedWhy: null });
  // a good business at too high a price: wait, and the price where P/E equals growth (PEG 1)
  const wait = lynchSheet(row({ pe: 40, price: 400 })).buy;                       // PEG 2.0 -> $200
  assert.deepEqual(wait, { answer: "WAIT", why: "A good business, but the price is too high for its growth. Peter's price: $200 or less.", price: 200, failedWhy: null });
  assert.equal(lynchSheet(row({ pe: 42, price: null })).buy.why, "A good business, but the price is too high for its growth.");
  // a failed business check is a NO, whatever the price; the first failed check is the reason
  assert.deepEqual(lynchSheet(row({ debtToEquity: 2 })).buy, { answer: "NO", why: "It owes too much.", price: null, failedWhy: "It owes too much." });
  assert.equal(lynchSheet(row({ epsGrowth: 0.06 })).buy.why, "Its profit grows too slowly.");
  assert.equal(lynchSheet(row({ epsGrowth: 0.8 })).buy.why, "Its profit growth is too fast to last.");
  assert.equal(lynchSheet(row({ salesGrowth: 0.01 })).buy.why, "Its sales are barely growing.");
  assert.equal(lynchSheet(row({ fcfGrowth: -0.1 })).buy.why, "Its spare cash is shrinking.");
  assert.equal(lynchSheet(row({ fcfGrowth: -0.1, pe: 60 })).buy.answer, "NO", "a failed check outranks the price");
  // stock piling up is his warning sign: not a buy today even with full marks
  assert.deepEqual(lynchSheet(row({ inventoryTurnChange: -0.2 })).buy, { answer: "NO", why: "Unsold goods are piling up faster than it sells.", price: null, failedWhy: null });
  // a cyclical: he bought those on the cycle, never on a low price
  const careful = lynchSheet(row({ cyclical: "chips" })).buy;
  assert.equal(careful.answer, "CAREFUL");
  assert.match(careful.why, /good years and bad years/);
  // a bank is judged on the checks that apply to it
  assert.equal(lynchSheet(row({ financial: true, debtToEquity: 5 })).buy.answer, "YES");
});

test("the Peter would buy group is exactly the YES answers", () => {
  const rows = [row({ symbol: "A" }), row({ symbol: "B", pe: 42 }), row({ symbol: "C", debtToEquity: 2 }),
    row({ symbol: "D", inventoryTurnChange: -0.3 }), row({ symbol: "E", cyclical: "chips" })];
  assert.deepEqual(lynchRows(rows, { scope: "Best" }).map((x) => x.row.symbol), ["A"]);
  assert.equal(LYNCH_SCOPE_LABEL.Best, "Peter would buy");
  assert.deepEqual(lynchRows(rows).map((x) => `${x.row.symbol} ${x.sheet.buy.answer}`).sort(), ["A YES", "B WAIT", "C NO", "D NO", "E CAREFUL"]);
});

// 2026-11-20 "I need Peter which prize he will buy and I don't see exit also": every card carries
// his buy price and his exit. Checked the same day against what he wrote: P/E equal to growth is
// his FAIR price, half is "very positive", twice is "very negative" (a yardstick, not a sell order).
test("Peter's buy price is his fair price: where the price-to-profit equals the growth; half of it is very good", () => {
  // ADBE: $220.60 at 12.3 times profit, growing 21%: 220.6 x 21 / 12.3 = $376.63, shown rounded DOWN
  const yes = lynchSheet(row({ price: 220.6, pe: 12.3, epsGrowth: 0.21 })).plan;
  assert.deepEqual([yes.buyPrice, yes.goodPrice, yes.exitFar], [376, 188, 752]);
  assert.equal(yes.buyText, "Today's $220.60 passes his price test. The most he would pay at this year's profit is $376, his fair price; $188 or less he called a very good price.");
  assert.equal(yes.exitText, "He had no set selling price. He kept a fast grower while its profit kept growing, and thought about selling when the growth slowed. A price near $752, twice his fair price, he called very negative. That is a warning level at this year's profit, not a target.");
  // the same business at a higher price: same fair price, today is above it, and he would not own it
  const wait = lynchSheet(row({ price: 600, pe: 33.45, epsGrowth: 0.21 })).plan;
  assert.deepEqual([wait.buyPrice, wait.goodPrice, wait.exitFar], [376, 188, 752]);
  assert.equal(wait.buyText, "$376 or less at this year's profit, his fair price; $188 or less he called a very good price. Today's $600.00 is above it.");
  assert.equal(wait.exitText, "He would not own it at today's price. A price near $752, twice his fair price, he called very negative.");
  assert.match(lynchSheet(row({ price: 800, pe: 44.6, epsGrowth: 0.21 })).plan.exitText, /he called very negative; today's \$800\.00 is already past that\.$/);
  assert.ok(!/\bwill\b/i.test(`${yes.buyText} ${yes.exitText}`), "nothing says where the price will go");
});

test("a very wide gap is flagged as a possible one-off profit jump, and prints no far-away dollar level", () => {
  // ZM-shaped: $83.77 at 7.8 times profit growing 36.4%: fair $390, half $195, today is under even half
  const plan = lynchSheet(row({ price: 83.77, pe: 7.8, epsGrowth: 0.364 })).plan;
  assert.deepEqual([plan.buyPrice, plan.goodPrice, plan.exitFar], [390, 195, null]);
  assert.equal(plan.buyText, "Today's $83.77 passes his price test. The most he would pay at this year's profit is $390, his fair price; $195 or less he called a very good price. Today is under even that. A gap this wide can mean this year's profit had a one-off jump, so check that first.");
  assert.equal(plan.exitText, "He had no set selling price. He kept a fast grower while its profit kept growing, and thought about selling when the growth slowed.");
});

test("a steady grower's 30% to 50% is counted from the price paid, and the card says where today's price stands", () => {
  // 15% growth at 12 times profit, $100: fair price 100 x 15 / 12 = $125; he would buy today at $100
  const yes = lynchSheet(row({ price: 100, pe: 12, epsGrowth: 0.15 }));
  assert.equal(yes.buy.answer, "YES");
  assert.deepEqual([yes.plan.buyPrice, yes.plan.goodPrice, yes.plan.exitLow, yes.plan.exitHigh, yes.plan.exitFar], [125, 62.5, 130, 150, null]);
  assert.equal(yes.plan.exitText, "He had no set selling price, but he often sold a steady grower once it was up 30% to 50% from what he paid. From today's $100.00 that is about $130 to $150.");
  // when 30% to 50% up is still under his fair price, the card says the price test would still pass
  assert.equal(lynchSheet(row({ price: 100, pe: 7.5, epsGrowth: 0.15 })).plan.exitText,
    "He had no set selling price, but he often sold a steady grower once it was up 30% to 50% from what he paid. From today's $100.00 that is about $130 to $150. His price test would still pass there (up to $200), so he might hold longer.");
  // not at this price: he would not own it; the level is worked from his fair price, and today may be past it
  const past = lynchSheet(row({ price: 200, pe: 24, epsGrowth: 0.15 }));
  assert.equal(past.buy.answer, "WAIT");
  assert.deepEqual([past.plan.buyPrice, past.plan.exitLow, past.plan.exitHigh], [125, 163, 188]);
  assert.equal(past.plan.exitText, "He would not own it at today's price. Had he bought at his fair price of $125, a 30% to 50% rise is about $163 to $188; today's $200.00 is already past that.");
  assert.equal(lynchSheet(row({ price: 150, pe: 18, epsGrowth: 0.15 })).plan.exitText,
    "He would not own it at today's price. Had he bought at his fair price of $125, a 30% to 50% rise is about $163 to $188.");
  // more than twice his fair price: no exit dollars at all, and the buy line says how far above
  const far = lynchSheet(row({ price: 300, pe: 36, epsGrowth: 0.15 })).plan;
  assert.equal(far.buyText, "$125 or less at this year's profit, his fair price; $62.50 or less he called a very good price. Today's $300.00 is above it: 2.4 times his fair price.");
  assert.deepEqual([far.exitText, far.exitLow, far.exitHigh], ["He would not own it at today's price.", null, null]);
});

test("the dividend is dollars a share, so the fair price is where the card's own rule flips", () => {
  // 12% growth at 20 times profit, $80: profit $4 a share. No dividend: 4 x 12 = $48.
  assert.equal(lynchSheet(row({ price: 80, pe: 20, epsGrowth: 0.12 })).plan.buyPrice, 48);
  // with a 4% dividend ($3.20 a share): P/E = 12 + 100 x 3.20 / P  ->  P = $67.08 (not 80 x 16 / 20 = $64)
  const payer = lynchSheet(row({ price: 80, pe: 20, epsGrowth: 0.12, dividendYield: 0.04 })).plan;
  assert.equal(payer.buyPrice, 67.08);
  const at = payer.buyPrice;
  assert.ok(Math.abs(at / 4 - (12 + (100 * 3.2) / at)) < 0.01, "at that price the price-to-profit equals growth % + dividend %");
  // a payout bigger than the year's profit is a one-off: left out of the score and of the price
  const special = lynchSheet(row({ price: 80, pe: 20, epsGrowth: 0.12, dividendYield: 0.15 }));
  assert.deepEqual([special.dividendPct, special.peg, special.plan.buyPrice], [0, 1.67, 48]);
  // growth too fast is a NO, so no buy price at all
  assert.equal(lynchSheet(row({ price: 80, pe: 20, epsGrowth: 0.8 })).plan.buyPrice, null);
});

test("no buy price where he would not buy: a failed check, a cyclical, no live price, or profit not in dollars", () => {
  const no = lynchSheet(row({ debtToEquity: 2 })).plan;
  assert.deepEqual([no.buyPrice, no.goodPrice, no.exitText], [null, null, null]);
  assert.equal(no.buyText, "None. He would not buy it while it fails a check: it owes too much.");
  const shelves = lynchSheet(row({ inventoryTurnChange: -0.2 })).plan;
  assert.equal(shelves.buyText, "None. He would not buy it while it fails a check: unsold goods are piling up faster than it sells.");
  // a cyclical: he gave a timing rule, never a price
  const chips = lynchSheet(row({ cyclical: "chips", inventoryTurnChange: 0.05 })).plan;
  assert.deepEqual([chips.buyPrice, chips.goodPrice, chips.exitFar], [null, null, null]);
  assert.equal(chips.buyText, "No set price. He bought this kind of business when its bad years were ending and its profit was low, not because the price looked low.");
  assert.equal(chips.exitText, "He had no set selling price. He sold when the good years looked close to ending; unsold goods piling up was one of his signs.");
  // his own sell sign in the last yearly report is said out loud (yearly figures: "now" would overstate it)
  assert.equal(lynchSheet(row({ cyclical: "chips", inventoryTurnChange: -0.13 })).plan.exitText, "He had no set selling price. He sold when the good years looked close to ending; unsold goods piling up was one of his signs. That sign shows in its last yearly report.");
  // a miner or an airline has no inventory figure here: the page says it cannot check
  assert.equal(lynchSheet(row({ cyclical: "metals & mining" })).plan.exitText, "He had no set selling price. He sold when the good years looked close to ending, for example when the price of what it sells, or its orders, start to fall. This page cannot check that for you.");
  // a cyclical that also fails a check says so beside the buy price
  assert.equal(lynchSheet(row({ cyclical: "chips", epsGrowth: 0.82 })).plan.buyText, "No set price. He bought this kind of business when its bad years were ending and its profit was low, not because the price looked low. It also fails a check: its profit growth is too fast to last.");
  const blind = lynchSheet(row({ price: null })).plan;
  assert.deepEqual([blind.buyPrice, blind.buyText, blind.exitText], [null, "There is no fresh price right now, so no buy price.", "He had no set selling price. He kept a fast grower while its profit kept growing, and thought about selling when the growth slowed."]);
  assert.equal(lynchSheet(row({ price: 0, epsGrowth: 0.15 })).plan.exitText, "He had no set selling price, but he often sold a steady grower once it was up 30% to 50% from what he paid.");
  // growth measured in another currency is not turned into a dollar price
  const hkd = lynchSheet(row({ currency: "HKD" })).plan;
  assert.deepEqual([hkd.buyPrice, hkd.buyText], [null, "No dollar price worked out: its profit is reported in HKD, so its growth is not measured in dollars."]);
  assert.equal(lynchSheet(row({ currency: "USD" })).plan.buyPrice, 366);      // 220 x 20 / 12
});

test("a limit is rounded down, cents are kept under $100, and the answer never disagrees with the line under it", () => {
  // $14.20 at 27 times profit growing 20%: 14.2 x 20 / 27 = $10.5185 -> $10.51, never $11
  assert.match(lynchSheet(row({ price: 14.2, pe: 27, epsGrowth: 0.2 })).plan.buyText, /^\$10\.51 or less at this year's profit/);
  assert.equal(lynchSheet(row({ price: 8.4, pe: 7, epsGrowth: 0.21 })).plan.buyPrice, 25.2);
  // a score of 1.003 is NOT "1 or less": the card reads ABOVE FAIR, not at this price, and above his fair price
  const edge = lynchSheet(row({ price: 220, pe: 20.06, epsGrowth: 0.2 }));
  assert.deepEqual([edge.peg, edge.verdict, edge.buy.answer], [1.01, "FAIR", "WAIT"]);
  assert.match(edge.plan.buyText, /^\$219 or less at this year's profit[\s\S]*Today's \$220\.00 is above it\.$/);
  // and exactly 1 is a YES whose line says it passes
  const exact = lynchSheet(row({ price: 220, pe: 20, epsGrowth: 0.2 }));
  assert.deepEqual([exact.peg, exact.verdict, exact.buy.answer], [1, "CHEAP", "YES"]);
  assert.match(exact.plan.buyText, /^Today's \$220\.00 passes his price test\. The most he would pay at this year's profit is \$220,/);
  assert.equal(lynchSheet(row({ pe: 40.1, epsGrowth: 0.2 })).verdict, "PRICEY", "2.005 is above 2");
});

// "Nvda what is cyclical?"
test("a cyclical card explains the word right under the answer, in two plain sentences", () => {
  const sheet = lynchSheet(row({ cyclical: "chips" }));
  assert.equal(sheet.cyclicalNote, "Cyclical means good years and bad years take turns: a chip maker earns a lot in a good year and far less in a bad one. In a good year its profit is so big that the price looks low next to it, so here a low price is not a reason to buy.");
  assert.equal(sheet.buy.why, sheet.cyclicalNote, "the explanation is the reason shown under ONLY WITH CARE");
  assert.match(lynchSheet(row({ cyclical: "oil & gas" })).cyclicalNote, /take turns: an oil and gas company earns a lot in a good year/);
  assert.match(lynchSheet(row({ cyclical: "something new" })).cyclicalNote, /take turns: a company like this earns a lot in a good year/);
  assert.deepEqual([lynchSheet(row({ cyclical: "autos" })).kind, lynchSheet(row({ cyclical: "oil & gas" })).kind], ["Car maker", "Oil and gas company"]);
  assert.deepEqual([lynchSheet(row({})).cyclicalNote, lynchSheet(row({})).kind], [null, null]);
  assert.ok(!/PEG|P\/E|boom|slump|will fall/i.test(sheet.cyclicalNote), "one vocabulary, no trading words, no forecast");
  // a cyclical that also fails a check says so in the same answer
  assert.equal(lynchSheet(row({ cyclical: "chips", epsGrowth: 0.82 })).buy.why, `${sheet.cyclicalNote} Also: its profit growth is too fast to last.`);
});

// 2026-11-20 audit: the card must read without trading words.
test("the five check lines use plain labels and values, and a shown percent never contradicts its check", () => {
  const sheet = lynchSheet(row());
  assert.deepEqual(sheet.checks.map((c) => `${c.label}: ${c.text}`), [
    "Profit growth: +20% a year",
    "Sales growth: +16% a year",
    "Borrowed: $54 per $100 of owners' money",
    "Cash left over: +16% a year",
    "Price for growth: 0.60 (1 or less is low)",
  ]);
  assert.ok(!/PEG|P\/E|equity|\/yr|Earnings|Cash flow/.test(JSON.stringify(sheet.checks)));
  // 9.92% fails "10% or more": it must not print "+10%"
  const slow = lynchSheet(row({ epsGrowth: 0.0992 }));
  assert.deepEqual([slow.checks[0].passed, slow.checks[0].text], [false, "+9.9% a year"]);
  assert.equal(lynchSheet(row({ salesGrowth: 0.046 })).checks[1].text, "+4.6% a year");
  assert.equal(lynchSheet(row({ fcfGrowth: -0.004 })).checks[3].text, "−0.4% a year");
  assert.equal(lynchSheet(row({ epsGrowth: 0.504 })).checks[0].text, "+50.4% a year, too fast to last");
  const bank = lynchSheet(row({ financial: true }));
  assert.deepEqual([bank.checks[2].text, bank.checks[3].text], ["finance company: Peter skips this check", "finance company: Peter skips this check"]);
  assert.match(bank.plan.buyText, / Debt and cash are not judged for a finance company, so this rests on fewer checks\.$/);
  assert.equal(lynchSheet(row({ debtToEquity: null })).checks[2].text, "number missing");
});

test("the tag names on screen: a score between 1 and 2 is ABOVE his fair price, never 'fair'", () => {
  assert.deepEqual(LYNCH_VERDICT_LABEL, { CHEAP: "CHEAP", FAIR: "ABOVE FAIR", PRICEY: "PRICEY", CYCLICAL: "CYCLICAL" });
  assert.deepEqual(Object.keys(LYNCH_VERDICT_LABEL), LYNCH_TILES);
  assert.match(LYNCH_TILES_NOTE, /These four describe the price only\./);
  assert.match(LYNCH_TILES_NOTE, /A CHEAP company can still fail another check/);
  assert.match(LYNCH_OWN_CHOICES, /^This page's own choices, not Peter's: the 10% to 50% growth band, counting growth above 50% as 50/);
  assert.match(LYNCH_OWN_CHOICES, /He wrote rules of thumb, never one formula/);
});

test("why a Mag7 company has no star sheet", () => {
  // plain sentences: no P/E, filings, stale or ETF on the card (2026-11-20 audit)
  assert.equal(lynchMissing(row({ signal: "NO_PRICE" })), "there is no fresh price for it right now");
  assert.equal(lynchMissing(row({ signal: "STALE" })), "its company reports are out of date");
  assert.equal(lynchMissing(row({ signal: "SHORT_HISTORY" })), "it has too few years of company reports");
  assert.equal(lynchMissing(row({ signal: "CHECK_DATA" })), "its company reports could not be read");
  assert.equal(lynchMissing(row({ signal: "ETF" })), "this is a fund (a basket of many companies), not one company");
  assert.equal(lynchMissing(row({ pe: null })), "it made no profit, so its price cannot be compared with its profit");
  assert.equal(lynchMissing(row({ epsGrowth: null })), "its profit growth cannot be measured: it lost money at the start of the ten years, or a year is missing");
  assert.equal(lynchMissing(row({ epsGrowth: -0.1 })), "its profit did not grow over the ten years");
  for (const signal of ["NO_PRICE", "STALE", "SHORT_HISTORY", "CHECK_DATA", "ETF"]) assert.ok(!/P\/E|filing|stale|ETF/i.test(lynchMissing(row({ signal }))), signal);
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

// 2026-11-20 "Why I don't see data for tsla": the card said only "no earnings growth figure".
// Tesla lost money in 2016, the start of the ten years, so a growth rate cannot be measured.
test("a company with no star sheet still answers the question: CANNOT TELL, why, and what you pay", () => {
  const tsla = lynchNoSheet(row({ symbol: "TSLA", quality: "LOW", pe: 402.86, epsGrowth: null }));
  assert.deepEqual(tsla, {
    answer: "CANNOT TELL",
    why: "Its profit growth cannot be measured: it lost money at the start of the ten years, or a year is missing.",
    pays: "You pay $403 for every $1 it earns in a year.",
  });
  assert.equal(lynchNoSheet(row({ epsGrowth: -0.05, pe: 30 })).why, "Its profit did not grow over the ten years.");
  assert.equal(lynchNoSheet(row({ pe: null })).pays, null, "no profit, nothing to say about the price");
  assert.equal(lynchNoSheet(row({ pe: 8.44, epsGrowth: null })).pays, "You pay $8.44 for every $1 it earns in a year.");
  assert.equal(lynchNoSheet(row({})), null, "a company with a sheet has no such card");
});
