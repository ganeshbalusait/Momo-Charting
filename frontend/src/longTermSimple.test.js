// "In simple words" on the Long Term page (2026-11-20, Ganesh: "I need
// justification why Warren or Peter chose that stocks. Build like this 10 kid
// can understand or who don't know any stock market").
import assert from "node:assert/strict";
import test from "node:test";

import { SIMPLE_FOOT, simpleWhy } from "./longTermSimple.js";

const row = (over = {}) => ({
  symbol: "ADBE", name: "Adobe", signal: "BUY", quality: "HIGH", young: false, financial: false, price: 220.6,
  pe: 12.3, roic: 0.4, roe: 0.62, epsGrowth: 0.21, salesGrowth: 0.17, fcfGrowth: 0.16, debtToEquity: 0.54,
  sticker: 537.6, safetyBuy: 268.8, buyBelow: 268.8, reason: "price $221 ≤ Safety Buy $269", ...over,
});
const marks = (side) => side.lines.map((l) => l.mark);
const text = (side) => side.lines.map((l) => l.text).join("\n");
const JARGON = /\b(ROIC|ROE|EPS|PEG|FCF|MOS|P\/E|margin of safety|sticker|TTM|equity)\b/;

test("a BUY: Warren would buy, every line is a tick, and no line uses a trading word", () => {
  const { warren } = simpleWhy(row());
  assert.equal(warren.headline, "Warren would buy this. It is a great business and today it is on sale.");
  assert.deepEqual(marks(warren), ["yes", "yes", "yes", "yes", "info", "yes"]);
  assert.match(text(warren), /For every \$100 put into the business, it earns \$40 a year\. Warren wants \$15 or more\./);
  assert.match(text(warren), /It sells 17% more every year\. Warren wants 10% or more\./);
  assert.match(text(warren), /Its profit grows 21% a year\./);
  assert.match(text(warren), /The cash it has left over grows 16% a year\./);
  assert.match(text(warren), /It owes \$54 for every \$100 it owns\. That is a fair amount\./);
  assert.match(text(warren), /A fair price is about \$538 a share\. Warren only buys at a big discount: \$269 or less\. Today it costs \$221, so it is on sale\./);
  assert.equal(warren.todo, "What his rule says: this is a price where he would buy.");
  assert.ok(!JARGON.test(`${warren.headline}\n${text(warren)}\n${warren.todo}`), "plain words only");
});

test("a WAIT says the price to wait for; an AVOID says why in the rule's own words", () => {
  const wait = simpleWhy(row({ signal: "WAIT", price: 400, reason: "price above safety buy" })).warren;
  assert.equal(wait.headline, "Warren would wait. It is a good business, but the price is too high today.");
  assert.match(text(wait), /Today it costs \$400, so it is not on sale yet\./);
  assert.equal(wait.lines.at(-1).mark, "no");
  assert.equal(wait.todo, "What his rule says: do nothing now. Look again if the price drops to $269 or less.");
  const avoid = simpleWhy(row({ signal: "AVOID", roic: 0.09, salesGrowth: -0.03, sticker: null, safetyBuy: null, buyBelow: null, reason: "debt 3.6× FCF" })).warren;
  assert.equal(avoid.headline, "Warren would skip this one.");
  assert.match(text(avoid), /it earns \$9 a year\. Warren wants \$15 or more\./);
  assert.match(text(avoid), /It sells 3% less every year\./);
  assert.deepEqual(marks(avoid).slice(0, 2), ["no", "no"]);
  assert.deepEqual(avoid.lines.at(-1), { mark: "no", text: "The rule it failed: debt 3.6× FCF." });
  assert.equal(avoid.todo, "What his rule says: leave it alone.");
});

test("a bank is judged on what it earns on its owners' money, and has no cash line", () => {
  const { warren } = simpleWhy(row({ financial: true, roe: 0.17, roic: 0.02 }));
  assert.match(text(warren), /For every \$100 its owners have in it, it earns \$17 a year\./);
  assert.ok(!/cash it has left over/.test(text(warren)));
});

test("missing numbers drop their line; a fund and a row without data say so plainly", () => {
  const thin = simpleWhy(row({ salesGrowth: null, fcfGrowth: null, debtToEquity: null })).warren;
  assert.equal(thin.lines.length, 3);
  assert.equal(simpleWhy(row({ signal: "ETF" })).warren.headline, "This is a fund (a basket of many companies), not one business, so Warren's checklist does not fit it.");
  assert.deepEqual(simpleWhy(row({ signal: "ETF" })).warren.lines, []);
  assert.equal(simpleWhy(row({ signal: "NO_PRICE" })).warren.headline, "We cannot say today: there is no fresh price for it.");
  assert.equal(simpleWhy(row({ signal: "STALE" })).warren.headline, "We cannot say yet: there is not enough fresh information on this company.");
  assert.equal(simpleWhy(null), null);
});

test("Peter: a fast grower at a low price, the sum shown as a child would do it", () => {
  const { peter } = simpleWhy(row());
  assert.equal(peter.headline, "Peter would like this. It is growing fast and the price is low for that growth.");
  assert.deepEqual(marks(peter), ["yes", "yes", "yes", "yes", "yes"]);
  assert.match(text(peter), /Its profit grows 21% a year\. Peter likes 10% to 50%\./);
  assert.match(text(peter), /It owes \$54 for every \$100 it owns\. Peter wants \$80 or less\./);
  assert.match(text(peter), /You pay \$12\.30 for every \$1 it earns in a year, and it grows 21% a year\. 12\.3 ÷ 21 = 0\.59\. Under 1 is a low price for the growth\./);
  assert.match(peter.todo, /^What his rule says: worth a closer look\. In our test, the cheap ones did not do better than other good companies/);
  assert.ok(!JARGON.test(`${peter.headline}\n${text(peter)}\n${peter.todo}`), "plain words only");
});

test("Peter: fair, expensive, cyclical, a dividend, slow shelves, and a company off the quality list", () => {
  assert.match(simpleWhy(row({ pe: 30 })).peter.headline, /^Peter would call the price fair\./);
  const pricey = simpleWhy(row({ pe: 60 })).peter;
  assert.match(pricey.headline, /^Peter would call this expensive\./);
  assert.equal(pricey.lines.at(-1).mark, "no");
  assert.equal(pricey.todo, "What his rule says: wait for a lower price.");
  const chips = simpleWhy(row({ cyclical: "chips" })).peter;
  assert.match(chips.headline, /^Peter would be careful\. This kind of business \(chips\) has good years and bad years/);
  assert.equal(chips.lines.at(-1).mark, "info");
  assert.match(chips.lines.at(-1).text, /a low price can be a trap/);
  assert.match(text(simpleWhy(row({ dividendYield: 0.02 })).peter), /grows 21% a year and pays you 2\.0% a year\. 12\.3 ÷ \(21 \+ 2\.0\) = 0\.53\./);
  const shelves = simpleWhy(row({ inventoryTurnChange: -0.2 })).peter;
  assert.deepEqual(shelves.lines.at(-1), { mark: "no", text: "Its shelves are filling up: unsold goods are growing faster than what it sells." });
  const low = simpleWhy(row({ quality: "LOW", roic: 0.12 })).peter;
  assert.deepEqual(low.lines[0], { mark: "info", text: "It earns less than $20 a year on every $100 put in, so it is not on the quality list." });
  // growth above 50% is a cross, said plainly
  assert.match(text(simpleWhy(row({ epsGrowth: 0.82 })).peter), /Its profit grows 82% a year\. That is too fast to last\./);
});

test("Peter has no opinion when the company cannot be scored, and says why", () => {
  const { peter } = simpleWhy(row({ epsGrowth: -0.05 }));
  assert.equal(peter.headline, "Peter has no opinion here: earnings did not grow.");
  assert.deepEqual([peter.lines, peter.todo], [[], null]);
});

test("the footer says these explain and do not tell you what to do", () => {
  assert.match(SIMPLE_FOOT, /They explain; they do not tell you what to do with your money\.$/);
});
