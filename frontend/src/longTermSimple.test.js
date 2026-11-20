// "In simple words" on the Long Term page (2026-11-20, Ganesh: "I need
// justification why Warren or Peter chose that stocks. Build like this 10 kid
// can understand or who don't know any stock market"; then "if Peter was there
// did he buy it or not? Simple question" and "Did Peter use profit target?").
import assert from "node:assert/strict";
import test from "node:test";

import { SIMPLE_FOOT, SIMPLE_FOOT_ONE, simpleWhy } from "./longTermSimple.js";

const row = (over = {}) => ({
  symbol: "ADBE", name: "Adobe", signal: "BUY", quality: "HIGH", young: false, financial: false, price: 220.6,
  pe: 12.3, roic: 0.4, roe: 0.62, epsGrowth: 0.21, salesGrowth: 0.17, fcfGrowth: 0.16, debtToEquity: 0.54,
  sticker: 537.6, safetyBuy: 268.8, buyBelow: 268.8, reason: "price $221 ≤ Safety Buy $269", ...over,
});
const marks = (side) => side.lines.map((l) => l.mark);
const text = (side) => side.lines.map((l) => l.text).join("\n");
const all = (side) => [side.headline, text(side), side.todo || "", side.sell || ""].join("\n");
const JARGON = /\b(ROIC|ROE|EPS|PEG|FCF|MOS|P\/E|margin of safety|sticker|TTM|equity)\b/;

test("a BUY: Would Warren buy it? Yes, every line is a tick, and no line uses a trading word", () => {
  const { warren } = simpleWhy(row());
  assert.equal(warren.headline, "Would Warren buy it? Yes. It is a great business and today it is on sale.");
  assert.deepEqual(marks(warren), ["yes", "yes", "yes", "yes", "info", "yes"]);
  assert.match(text(warren), /For every \$100 put into the business, it earns \$40 a year\. Warren wants \$15 or more\./);
  assert.match(text(warren), /It sells 17% more every year\. Warren wants 10% or more\./);
  assert.match(text(warren), /Its profit grows 21% a year\./);
  assert.match(text(warren), /The cash it has left over grows 16% a year\./);
  assert.match(text(warren), /It has borrowed \$54 for every \$100 of its owners' money\. That is a fair amount\./);
  assert.match(text(warren), /A fair price is about \$538 a share\. Warren only buys at a big discount: \$269 or less\. Today it costs \$221, so it is on sale\./);
  assert.equal(warren.todo, "What his rule says: this is a price where he would buy.");
  assert.ok(!JARGON.test(all(warren)), "plain words only");
});

test("Warren's rule has one sell point, the fair price, and no other profit target", () => {
  assert.equal(simpleWhy(row()).warren.sell,
    "When would he sell? This rule sells when the price reaches the fair price, about $538. It has no other profit target, and Warren himself likes to keep a great business for many years.");
  assert.match(simpleWhy(row({ signal: "WAIT", price: 400 })).warren.sell, /about \$538/);
  assert.equal(simpleWhy(row({ signal: "AVOID" })).warren.sell, null, "nothing to sell when he would not buy");
  assert.equal(simpleWhy(row({ sticker: null })).warren.sell, null);
});

test("a WAIT says the price to wait for; an AVOID says why in the rule's own words", () => {
  const wait = simpleWhy(row({ signal: "WAIT", price: 400, reason: "price above safety buy" })).warren;
  assert.equal(wait.headline, "Would Warren buy it? Not yet. It is a good business, but the price is too high today.");
  assert.match(text(wait), /Today it costs \$400, so it is not on sale yet\./);
  assert.equal(wait.lines.at(-1).mark, "no");
  assert.equal(wait.todo, "What his rule says: do nothing now. Look again if the price drops to $269 or less.");
  const avoid = simpleWhy(row({ signal: "AVOID", roic: 0.09, salesGrowth: -0.03, sticker: null, safetyBuy: null, buyBelow: null, reason: "debt 3.6× FCF" })).warren;
  assert.equal(avoid.headline, "Would Warren buy it? No. It does not pass his checklist.");
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
  assert.equal(simpleWhy(row({ signal: "NO_PRICE" })).warren.headline, "Would Warren buy it? We cannot say today: there is no fresh price for it.");
  assert.equal(simpleWhy(row({ signal: "STALE" })).warren.headline, "Would Warren buy it? We cannot say yet: there is not enough fresh information on this company.");
  assert.equal(simpleWhy(null), null);
});

test("Would Peter buy it? Yes: every check, a low price, the sum shown as a child would do it", () => {
  const { peter } = simpleWhy(row());
  assert.equal(peter.headline, "Would Peter buy it? Yes. Every check passes and the price is low for its growth.");
  assert.deepEqual(marks(peter), ["yes", "yes", "yes", "yes", "yes"]);
  assert.match(text(peter), /Its profit grows 21% a year\. Peter likes 10% to 50%\./);
  assert.match(text(peter), /It has borrowed \$54 for every \$100 of its owners' money\. Peter wants \$80 or less\./);
  assert.match(text(peter), /You pay \$12\.30 for every \$1 it earns in a year, and its profit grows 21% a year\. Divide the first by the second: 12\.3 ÷ 21 = 0\.59\. 1 or less is a low price for the growth\./);
  // "which prize he will buy": 220.6 x 21 / 12.3 = $377
  assert.equal(peter.todo, "Peter's buy price: Today's $220.60 passes his price test. The most he would pay at this year's profit is $376, his fair price; $188 or less he called a very good price. In our test, the cheap ones did not do better than other good companies, so this is his checklist, not a promise.");
  assert.ok(!JARGON.test(all(peter)), "plain words only");
});

test("Would Peter buy it? Not at this price names his price; No says why; a cyclical needs care", () => {
  // P/E 30 on 21% growth = 1.43: his price is where the two are equal, 220.6 / 1.43 = $154
  const wait = simpleWhy(row({ pe: 30 })).peter;
  assert.equal(wait.headline, "Would Peter buy it? Not at this price. A good business, but the price is too high for its growth.");
  assert.equal(wait.todo, "Peter's buy price: $154 or less at this year's profit, his fair price; $77.00 or less he called a very good price. Today's $220.60 is above it.");
  assert.equal(wait.lines.at(-1).mark, "no");
  const no = simpleWhy(row({ debtToEquity: 2 })).peter;
  assert.equal(no.headline, "Would Peter buy it? No. It owes too much.");
  assert.deepEqual([no.todo, no.sell], ["Peter's buy price: None. He would not buy it while it fails a check: it owes too much.", null]);
  const chips = simpleWhy(row({ cyclical: "chips" })).peter;
  // "Nvda what is cyclical?": the headline explains the word right after the answer
  assert.equal(chips.headline, "Would Peter buy it? Only with care. Cyclical means good years and bad years take turns: a chip maker earns a lot in a good year and far less in a bad one. In a good year its profit is so big that the price looks low next to it, so here a low price is not a reason to buy.");
  assert.equal(chips.lines.at(-1).mark, "info");
  assert.match(chips.lines.at(-1).text, /a low price can be a trap: it can look cheapest when its best year is ending\.$/);
  assert.equal(chips.todo, "Peter's buy price: No set price. He bought this kind of business when its bad years were ending and its profit was low, not because the price looked low.");
  assert.equal(chips.sell, "When would he sell? He had no set selling price. He sold when the good years looked close to ending, for example when the price of what it sells, or its orders, start to fall. This page cannot check that for you.");
  const shelves = simpleWhy(row({ inventoryTurnChange: -0.2 })).peter;
  assert.equal(shelves.headline, "Would Peter buy it? No. Unsold goods are piling up faster than it sells.");
  assert.deepEqual(shelves.lines.at(-1), { mark: "no", text: "Its shelves are filling up: unsold goods are growing faster than what it sells." });
  assert.equal(simpleWhy(row({ epsGrowth: 0.82 })).peter.headline, "Would Peter buy it? No. Its profit growth is too fast to last.");
});

test("Peter: a dividend joins the sum, and a company off the quality list is marked", () => {
  assert.match(text(simpleWhy(row({ dividendYield: 0.02 })).peter), /its profit grows 21% a year and pays you 2\.0% a year\. Divide the first by the second: 12\.3 ÷ \(21 \+ 2\.0\) = 0\.53\./);
  // growth above 50% is counted as 50, and the box says whose choice that is
  assert.match(text(simpleWhy(row({ epsGrowth: 0.82, pe: 18.3 })).peter), /its profit grows 82% a year \(the page counts at most 50%\)\. Divide the first by the second: 18\.3 ÷ 50 = 0\.37\./);
  const low = simpleWhy(row({ quality: "LOW", roic: 0.12 })).peter;
  assert.deepEqual(low.lines[0], { mark: "info", text: "It earns less than $20 a year on every $100 put in, so it is not on the quality list." });
});

// "Did Peter use profit target?" He did not have a fixed one; what he did depended on the kind of company.
test("when Peter would sell: no profit target, and it depends on the kind of company", () => {
  // fast grower (21%): kept while it grows; his "very negative" level is twice his buy price: 2 x $376.63
  const fast = simpleWhy(row()).peter.sell;
  assert.equal(fast, "When would he sell? He had no set selling price. He kept a fast grower while its profit kept growing, and thought about selling when the growth slowed. A price near $752, twice his fair price, he called very negative. That is a warning level at this year's profit, not a target.");
  // steady grower (15%) at 12 times profit, $220.60: he would buy today, so 30% to 50% above today's price
  assert.equal(simpleWhy(row({ epsGrowth: 0.15, pe: 12 })).peter.sell,
    "When would he sell? He had no set selling price, but he often sold a steady grower once it was up 30% to 50% from what he paid. From today's $220.60 that is about $287 to $331.");
  assert.match(simpleWhy(row({ cyclical: "chips", inventoryTurnChange: -0.13 })).peter.sell, /unsold goods piling up was one of his signs\. That sign shows in its last yearly report\.$/);
  // a company off the quality list: the box says the page does not score it, and gives no price
  const off = simpleWhy(row({ quality: "LOW" })).peter;
  assert.match(off.headline, /^Would Peter buy it\? This page does not score it\. This page only scores quality companies/);
  assert.deepEqual([off.todo, off.sell], ["Peter's buy price: None worked out. It is not on the quality list.", null]);
  assert.ok(!JARGON.test(fast));
});

test("Peter cannot tell when the company cannot be scored, and says why", () => {
  const { peter } = simpleWhy(row({ epsGrowth: -0.05 }));
  assert.equal(peter.headline, "Would Peter buy it? He cannot tell: its profit did not grow over the ten years.");
  assert.deepEqual([peter.lines, peter.todo, peter.sell], [[{ mark: "info", text: "You pay $12.30 for every $1 it earns in a year." }], null, null]);
  // Tesla 11-20: no growth rate because it lost money at the start of the ten years
  const tsla = simpleWhy(row({ epsGrowth: null, pe: 402.86 })).peter;
  assert.equal(tsla.headline, "Would Peter buy it? He cannot tell: its profit growth cannot be measured: it lost money at the start of the ten years, or a year is missing.");
  assert.deepEqual(tsla.lines, [{ mark: "info", text: "You pay $403 for every $1 it earns in a year." }]);
});

test("the footer says these explain and do not tell you what to do", () => {
  assert.match(SIMPLE_FOOT, /They explain; they do not tell you what to do with your money\.$/);
});

// 2026-11-20 "Peter one don't mixed with Warren": each view explains its own checklist only.
test("asked for one side, the other is left out", () => {
  const peterOnly = simpleWhy(row(), "peter");
  assert.equal(peterOnly.warren, null);
  assert.match(peterOnly.peter.headline, /^Would Peter buy it\? Yes\./);
  assert.ok(!/Warren/.test(all(peterOnly.peter)), "no Warren in Peter's words");
  const warrenOnly = simpleWhy(row(), "warren");
  assert.equal(warrenOnly.peter, null);
  assert.match(warrenOnly.warren.headline, /^Would Warren buy it\? Yes\./);
  assert.ok(!/Peter/.test(all(warrenOnly.warren)), "no Peter in Warren's words");
  assert.ok(simpleWhy(row()).warren && simpleWhy(row()).peter, "no side named: both");
  assert.match(SIMPLE_FOOT_ONE.peter, /Peter Lynch's checklist/);
  assert.ok(!/Warren/.test(SIMPLE_FOOT_ONE.peter) && !/Peter/.test(SIMPLE_FOOT_ONE.warren));
});
