// LEAP OI Scanner: the stock's current price on every card (2026-11-19, "Add current prices also").
import assert from "node:assert/strict";
import test from "node:test";

import { awayFrom, changeTone, quotesFrom, stockPrice, stockText, tickersOnScreen } from "./leapLivePrice.js";

test("every ticker on screen is asked once, in first-seen order", () => {
  const payload = {
    biggestCall: { ticker: "nvda" }, biggestPut: { ticker: "AAPL" },
    entries: [{ ticker: "INTC" }, { ticker: "NVDA" }], rows: [{ ticker: "CRWV" }, { ticker: "" }],
    byTicker: [{ ticker: "SOFI" }, { ticker: "AAPL" }],
  };
  assert.deepEqual(tickersOnScreen(payload), ["NVDA", "AAPL", "INTC", "CRWV", "SOFI"]);
  assert.deepEqual(tickersOnScreen(null), []);
});

test("a quote reply becomes price and day change; a ticker with no price is left out", () => {
  const q = quotesFrom({ rows: [
    { symbol: "intc", lastPrice: 54.6, changePct: 1.234 },
    { symbol: "CRWV", lastPrice: 43.9, changePct: null },
    { symbol: "SOFI", lastPrice: null, changePct: 2 },
    { symbol: "WULF", lastPrice: 0 },
  ] });
  assert.deepEqual(q, { INTC: { price: 54.6, changePct: 1.234 }, CRWV: { price: 43.9, changePct: null } });
  assert.deepEqual(quotesFrom(null), {});
});

test("the card shows the live price with today's change, and says 'at read' when there is no live price", () => {
  const quotes = { INTC: { price: 54.6, changePct: 1.234 }, CRWV: { price: 43.9, changePct: -2.05 } };
  assert.equal(stockText(stockPrice({ ticker: "INTC", spot: 50 }, quotes)), "54.60 (+1.2%)");
  assert.equal(stockText(stockPrice({ ticker: "CRWV", spot: 50 }, quotes)), "43.90 (-2.0%)");
  assert.equal(stockText(stockPrice({ ticker: "SOFI", spot: 27.5 }, quotes)), "27.50 at read");
  assert.equal(stockText(stockPrice({ ticker: "SOFI", spot: null }, quotes)), "—");
  assert.equal(stockText({ price: 10, live: true, changePct: null }), "10.00");
});

test("From price is worked out against the price shown", () => {
  const row = { ticker: "INTC", strike: 100, spot: 50, away: 100 };
  assert.equal(awayFrom(row, stockPrice(row, { INTC: { price: 54.6, changePct: 0 } })), 83.2);
  assert.equal(awayFrom(row, stockPrice(row, {})), 100);
  assert.equal(awayFrom({ ticker: "X", strike: 0 }, { price: 5, live: true, changePct: null }), null);
  assert.equal(awayFrom(row, { price: null, live: false, changePct: null }), null);
});

test("the day's change is coloured only for a live price", () => {
  assert.equal(changeTone({ price: 1, live: true, changePct: 0.5 }), "is-up");
  assert.equal(changeTone({ price: 1, live: true, changePct: -0.5 }), "is-down");
  assert.equal(changeTone({ price: 1, live: true, changePct: 0 }), "");
  assert.equal(changeTone({ price: 1, live: false, changePct: null }), "");
});
