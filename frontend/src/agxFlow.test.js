import test from "node:test";
import assert from "node:assert/strict";

import {
  MAG7_BOARD,
  contractLabel,
  expiryLabel,
  fillLabel,
  filterRows,
  flowQuery,
  formatCount,
  formatDelta,
  formatPremium,
  formatStrike,
  formatVolOi,
  parseTickers,
  readingProgress,
  splitPct,
} from "./agxFlow.js";

test("premium reads like Unusual Whales", () => {
  assert.equal(formatPremium(1_200_000), "$1.2M");
  assert.equal(formatPremium(1_000_000), "$1M");
  assert.equal(formatPremium(450_400), "$450K");
  assert.equal(formatPremium(2_500_000_000), "$2.5B");
  assert.equal(formatPremium(900), "$900");
  assert.equal(formatPremium(null), "--");
  assert.equal(formatPremium("x"), "--");
});

test("contract, strike and expiry labels", () => {
  assert.equal(contractLabel({ strike: 185, side: "CALL" }), "185 Call");
  assert.equal(contractLabel({ strike: 172.5, side: "PUT" }), "172.5 Put");
  assert.equal(formatStrike(undefined), "--");
  assert.equal(expiryLabel("2026-11-21", 4), "Nov 21 · 4d");
  assert.equal(expiryLabel("bad", 4), "--");
});

test("fill guess labels", () => {
  assert.equal(fillLabel("ask"), "Bought");
  assert.equal(fillLabel("bid"), "Sold");
  assert.equal(fillLabel("mid"), "Mid");
  assert.equal(fillLabel(undefined), "Mid");
});

test("numbers: count, delta, vol/oi", () => {
  assert.equal(formatCount(12345), "12,345");
  assert.equal(formatCount(null), "--");
  assert.equal(formatDelta(-0.304), "-0.30");
  assert.equal(formatDelta(null), "--");
  assert.equal(formatVolOi(5), "5.0x");
  assert.equal(formatVolOi(12.4), "12x");
  assert.equal(formatVolOi(null), "new");                 // no open interest yet
});

test("split bar: call share of premium", () => {
  assert.equal(splitPct(300, 100), 75);
  assert.equal(splitPct(0, 0), 50);
  assert.equal(splitPct(null, 500), 0);
  assert.equal(splitPct(-5, 5), 0);
});

test("filter rows by side and minimum premium", () => {
  const rows = [
    { side: "CALL", premium: 1_200_000 },
    { side: "PUT", premium: 600_000 },
    { side: "CALL", premium: 150_000 },
    { side: "PUT", premium: 50_000 },
  ];
  assert.equal(filterRows(rows, { side: "both", minPremium: 100_000 }).length, 3);
  assert.deepEqual(filterRows(rows, { side: "calls", minPremium: 500_000 }), [rows[0]]);
  assert.deepEqual(filterRows(rows, { side: "puts", minPremium: 1_000_000 }), []);
  assert.deepEqual(filterRows(null), []);
});

test("ticker box cleans what the trader types", () => {
  assert.deepEqual(parseTickers("aapl, nvda  tsla,nvda,<x>,123,BRK.B"), ["AAPL", "NVDA", "TSLA", "BRK.B"]);
  assert.equal(parseTickers(Array.from({ length: 30 }, (_, i) => `T${String.fromCharCode(65 + i % 26)}${String.fromCharCode(65 + Math.floor(i / 26))}`).join(",")).length, 20);
  assert.deepEqual(parseTickers(""), []);
});

test("reading progress and query", () => {
  assert.deepEqual(readingProgress([{ pending: true }, { pending: false }, {}]), { pending: 1, updating: 0, done: 2, total: 3 });
  assert.deepEqual(readingProgress(undefined), { pending: 0, updating: 0, done: 0, total: 0 });
  assert.equal(flowQuery(["AAPL", "BRK.B"]), "/api/agx-flow?symbols=AAPL%2CBRK.B");
  assert.deepEqual(MAG7_BOARD, ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"]);   // no SPY/QQQ (2026-11-17)
});

test("watchlist board: the watchlist without the Mag7 names, no repeats, at most 400", async () => {
  const { watchlistBoard, MAG7_BOARD: board } = await import("./agxFlow.js");
  assert.deepEqual(watchlistBoard(["pltr", "NVDA", "bad ticker", "SOFI", "pltr"]), ["PLTR", "SOFI"]);
  const lots = Array.from({ length: 500 }, (_, i) => `${String.fromCharCode(65 + (i % 26))}${String.fromCharCode(65 + Math.floor(i / 26) % 26)}${String.fromCharCode(65 + Math.floor(i / 676))}`);
  assert.equal(watchlistBoard(lots).length, 400);
  assert.deepEqual(watchlistBoard(null), []);
  assert.equal(board.length, 7);
});

test("strip keeps the 12 tickers with the most option dollars when the board is big", async () => {
  const { stripTickers } = await import("./agxFlow.js");
  const few = [{ ticker: "A", callPremium: 1 }, { ticker: "B", callPremium: 5 }];
  assert.deepEqual(stripTickers(few), few);
  const many = Array.from({ length: 30 }, (_, i) => ({ ticker: `T${i}`, callPremium: i * 10, putPremium: i }));
  const shown = stripTickers(many);
  assert.equal(shown.length, 12);
  assert.equal(shown[0].ticker, "T29");
});

test("a ticker being re-read keeps its numbers: updating, not waiting (2026-11-17 'Not loading properly')", async () => {
  const { readingProgress: progress, hasData, updatedText } = await import("./agxFlow.js");
  const board = [
    { ticker: "AAPL", pending: false, fetchedAt: "2026-11-17T13:25:43-05:00" },
    { ticker: "AMZN", pending: true, fetchedAt: "2026-11-17T13:20:02-05:00" },  // refresh queued, old numbers shown
    { ticker: "NEW", pending: true, fetchedAt: null },                        // never read yet
  ];
  assert.deepEqual(progress(board), { pending: 1, updating: 1, done: 2, total: 3 });
  assert.equal(hasData(board[1]), true);
  assert.equal(hasData(board[2]), false);
  assert.equal(updatedText(board), "Updated 1:20 PM ET");
  assert.equal(updatedText([{ fetchedAt: null }]), "");
  assert.deepEqual(progress(board.slice(0, 2)), { pending: 0, updating: 1, done: 2, total: 2 });
});

test("whole-watchlist first read says roughly how long is left", async () => {
  const { readingText } = await import("./agxFlow.js");
  assert.equal(readingText({ pending: 3, done: 6, total: 9 }), "Reading Schwab… (6 of 9)");
  assert.equal(readingText({ pending: 360, done: 9, total: 369 }), "Reading Schwab… (9 of 369) · about 12 min left");
  assert.equal(readingText({ pending: 0, done: 9, total: 9 }), "");
});

test("flow query carries expiry and order only when they are not the defaults (2026-11-17 'All six')", async () => {
  const { flowQuery, EXPIRY_FILTERS, ORDERS } = await import("./agxFlow.js");
  assert.equal(flowQuery(["NVDA", "AAPL"]), "/api/agx-flow?symbols=NVDA%2CAAPL");
  assert.equal(flowQuery(["NVDA"], { expiry: "all", order: "premium" }), "/api/agx-flow?symbols=NVDA");
  assert.equal(flowQuery(["NVDA"], { expiry: "week" }), "/api/agx-flow?symbols=NVDA&expiry=week");
  assert.equal(flowQuery(["NVDA"], { expiry: "next", order: "hot" }), "/api/agx-flow?symbols=NVDA&expiry=next&order=hot");
  assert.deepEqual(EXPIRY_FILTERS.map((e) => e.label), ["All", "This week", "Next week", "30 days", "1–3 months", "3–6 months", "6–12 months"]);
  assert.deepEqual(ORDERS.map((o) => o.label), ["Biggest $", "Hot right now"]);
});


test("2026-11-19 'add up to 12 months': each month band asks the server for its own window", async () => {
  const { EXPIRY_FILTERS } = await import("./agxFlow.js");
  for (const id of ["m3", "m6", "m12"]) {
    assert.ok(EXPIRY_FILTERS.some((e) => e.id === id), id);
    assert.equal(flowQuery(["NVDA"], { expiry: id }), `/api/agx-flow?symbols=NVDA&expiry=${id}`);
  }
});

test("2026-11-20 'I see data 0': overnight with every ticker at $0 the page says why", async () => {
  const { overnightEmpty } = await import("./agxFlow.js");
  const zero = { ticker: "NVDA", callPremium: 0, putPremium: 0, fetchedAt: "2026-11-19T23:49:00-05:00" };
  assert.equal(overnightEmpty({ marketOpen: false }, [zero, { ...zero, ticker: "AAPL" }]), true);
  assert.equal(overnightEmpty({ marketOpen: true }, [zero]), false, "in market hours $0 is real");
  assert.equal(overnightEmpty({ marketOpen: false }, [zero, { ...zero, callPremium: 5 }]), false);
  assert.equal(overnightEmpty({ marketOpen: false }, []), false);
  assert.equal(overnightEmpty(null, [zero]), false);
});
