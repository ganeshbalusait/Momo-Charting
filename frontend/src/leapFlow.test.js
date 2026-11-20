// LEAP OI Scanner: today's trades on each card, in beginner words (2026-11-20).
import assert from "node:assert/strict";
import test from "node:test";

import { flowCell } from "./leapFlow.js";

test("a contract bought at the ask reads 'Bought' with the count and dollars", () => {
  const cell = flowCell({ flow: { volume: 1200, premium: 273600, fill: "ask", unusual: false, at: "2026-11-19T16:04:00-05:00" } });
  assert.equal(cell.text, "1,200 contracts · $274K");
  assert.equal(cell.word, "Bought");
  assert.equal(cell.tone, "is-bought");
  assert.match(cell.title, /paid the asking price/);
  assert.match(cell.title, /A guess, not exchange data \(read 4:04 PM ET\)\./);
  assert.equal(cell.unusual, false);
});

test("sold at the bid, in-between, and unusual read plainly", () => {
  const sold = flowCell({ flow: { volume: 800, premium: 2_500_000, fill: "bid", unusual: true } });
  assert.deepEqual([sold.word, sold.tone, sold.text, sold.unusual], ["Sold", "is-sold", "800 contracts · $2.5M", true]);
  assert.match(sold.title, /Unusual: more traded today than were open before/);
  assert.equal(flowCell({ flow: { volume: 5, premium: 300, fill: "mid" } }).word, "Can't tell");
  assert.equal(flowCell({ flow: { volume: 5, premium: 300, fill: "???" } }).word, "Can't tell");
});

test("no trades says so, never a zero that looks like data", () => {
  for (const row of [{}, { flow: null }, { flow: { volume: 0 } }, null]) {
    const cell = flowCell(row);
    assert.equal(cell.text, "No trades seen today");
    assert.equal(cell.word, "");
  }
});
