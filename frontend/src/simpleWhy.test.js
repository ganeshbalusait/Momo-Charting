// "Why is this here?" in simple words, for LEAP OI Scanner and AGX Flow rows (2026-11-20, Ganesh: "I need
// justification why ... chose that stocks. Build like this 10 kid can understand").
import assert from "node:assert/strict";
import test from "node:test";

import { MARKS, chanceText, flowWhy, leapWhy } from "./simpleWhy.js";

const INTC = { ticker: "INTC", side: "CALL", strike: 100, expiry: "2027-06-17", days: 210, oi: 49045, oiChange: 58,
  oiChangeSince: "2026-11-18T20:04:00-05:00", delta: 0.19, bid: 2.25, ask: 2.31, spot: 54.6, entry: true,
  flow: { volume: 1200, premium: 273600, fill: "ask", unusual: false } };

const lines = (why) => why.checks.map((c) => `${MARKS[c.mark]} ${c.text}`);

test("chance in plain words from delta", () => {
  assert.equal(chanceText(0.19), "about 1 in 5");
  assert.equal(chanceText(-0.31), "about 1 in 3");
  assert.equal(chanceText(0.5), "about 5 in 10");
  assert.equal(chanceText(-1), "almost certain, as things stand today");
  assert.equal(chanceText(null), null);
});

test("a LEAP row says why it is on the list, what it costs and what has to happen, then not advice", () => {
  const why = leapWhy(INTC, { rank: 0, price: 54.6 });
  assert.equal(why.verdict, "More people hold this call than any other one on this list.");
  assert.deepEqual(lines(why), [
    "✓ 49,045 contracts are open. That is how many of these bets people are holding right now (each one covers 100 shares).",
    "✓ 58 new contracts were opened since Nov 18, 2026: more people joined this bet.",
    "• Delta 0.19: the chance it ends up worth something is about 1 in 5.",
    "• To make money by Jun 17, 2027, the stock has to be above 102.28 (up 87.3% from 54.60 today). One contract costs about $228.",
    "✓ ENTRY: our option chain marks this as the usual starting choice for that date.",
    "• Today 1,200 traded, about $273,600; the last trade looked like a buyer (it paid the asking price).",
  ]);
  assert.match(why.meaning, /A Call is a bet that pays if the stock goes UP/);
  assert.match(why.meaning, /coin flip/);
  assert.match(why.notAdvice, /not advice[\s\S]*paper trading/);
});

test("a put, a closed position, no trades and a lower place read correctly", () => {
  const why = leapWhy({ ...INTC, side: "PUT", strike: 40, oiChange: -32, entry: false, flow: null }, { rank: 2, price: 54.6 });
  assert.equal(why.verdict, "Number 3 on this list for how many people hold it.");
  const text = lines(why).join("\n");
  assert.match(text, /✗ 32 contracts were closed since Nov 18, 2026: some people left this bet\./);
  assert.match(text, /has to be below 37\.72 \(down 30\.9% from 54\.60 today\)/);
  assert.match(text, /No trades on this contract seen today yet\./);
  assert.doesNotMatch(text, /ENTRY/);
  assert.match(why.meaning, /A Put is a bet that pays if the stock goes DOWN/);
});

test("an AGX Flow row: money, unusual, bought or sold, time left and delta, in plain words", () => {
  const why = flowWhy({ ticker: "INTC", side: "PUT", strike: 95, expiry: "2026-12-18", days: 29, volume: 7650, oi: 2880,
    premium: 29_900_000, fill: "mid", delta: -1, newVolume: 0 });
  assert.equal(why.verdict, "About $30 million was spent on this Put today, far more than usual.");
  assert.deepEqual(lines(why), [
    "✓ $30 million spent today: 7,650 contracts, each covering 100 shares.",
    "✓ 7,650 traded today, but only 2,880 were open before: 2.7 times more. That is unusual.",
    "• The last trade was between the bid and the ask, so we can't tell if a buyer or a seller led.",
    "• It ends on Dec 18, 2026, in 29 days.",
    "• Delta 1.00: this option already moves almost like the stock itself, so it may be insurance or a way to hold the stock, not a bet on a big move.",
  ]);
  assert.match(why.meaning, /hedge/);
});

test("a short, ordinary, bought flow row warns that short bets lose value fast", () => {
  const why = flowWhy({ ticker: "NVDA", side: "CALL", strike: 185, expiry: "2026-11-20", days: 1, volume: 900, oi: 5000,
    premium: 180_000, fill: "ask", delta: 0.2, newVolume: 300 });
  const text = lines(why).join("\n");
  assert.equal(why.verdict, "About $180,000 was spent on this Call today.");
  assert.match(text, /✗ 900 traded today and 5,000 were already open, so this is not unusual\./);
  assert.match(text, /Bought\)\. This is a guess\./);
  assert.match(text, /✓ 300 more traded in the last few minutes\./);
  assert.match(text, /✗ It ends on Nov 20, 2026, in 1 day\. Short bets can lose their value fast\./);
});

test("no line ever says what to buy or that a stock will go up", () => {
  const all = JSON.stringify([leapWhy(INTC, { rank: 0 }), flowWhy({ side: "CALL", volume: 1, oi: 0, premium: 1 })]).toLowerCase();
  assert.doesNotMatch(all, /you should|will go up|will rise|buy this|sure|guarantee/);
  assert.equal(leapWhy(null), null);
  assert.equal(flowWhy(null), null);
});
