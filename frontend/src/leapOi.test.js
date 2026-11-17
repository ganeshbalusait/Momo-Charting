import assert from "node:assert/strict";
import test from "node:test";

import {
  MAG7, awayText, barWidth, boardTickers, contractLabel, deltaText, expiryLabel, formatOi, priceText,
  progressText, readAtText, scannerUrl,
} from "./leapOi.js";

test("board tickers: Mag7 first, watchlist after, no repeats, junk dropped, capped at 25", () => {
  assert.deepEqual(boardTickers("mag7", ["PLTR"]), MAG7);
  assert.deepEqual(boardTickers("both", ["pltr", "NVDA", "", "BAD TICKER", "amd"]), [...MAG7, "PLTR", "AMD"]);
  const many = Array.from({ length: 40 }, (_, i) => `T${String.fromCharCode(65 + (i % 26))}${i >= 26 ? "X" : ""}`);
  assert.equal(boardTickers("both", many).length, 25);
  assert.deepEqual(boardTickers("both", null), MAG7);
});

test("scanner url carries range, delta, side and the entry-only switch", () => {
  const url = scannerUrl({ tickers: ["NVDA", "AAPL"], range: "leap", delta: "min", side: "both" });
  assert.equal(url, "/api/leap-oi-scanner?symbols=NVDA%2CAAPL&from=3&to=12&side=both&min_delta=0.10");
  const entry = scannerUrl({ tickers: ["NVDA"], range: "near", delta: "entry", side: "calls" });
  assert.match(entry, /from=1&to=3&side=calls&min_delta=0\.10&entry_only=1$/);
  assert.match(scannerUrl({ tickers: ["NVDA"], range: "x", delta: "mid", side: "sideways" }), /from=3&to=12&side=both&min_delta=0\.20$/);
});

test("contract, delta, expiry and number text", () => {
  assert.equal(contractLabel({ strike: 172.5, side: "CALL" }), "172.5 Call");
  assert.equal(contractLabel({ strike: 140, side: "PUT" }), "140 Put");
  assert.equal(deltaText(0.198), "0.198");
  assert.equal(deltaText(-0.196), "-0.196");
  assert.equal(deltaText(0.2), "0.20");
  assert.equal(deltaText(0.1), "0.10");
  assert.equal(deltaText(null), "—");
  assert.equal(expiryLabel("2027-01-15"), "Jan 15 '27");
  assert.equal(expiryLabel(""), "");
  assert.equal(formatOi(123456), "123,456");
  assert.equal(priceText(null), "—");
  assert.equal(priceText(0), "0.00");
  assert.equal(awayText(12.34), "+12.3%");
  assert.equal(awayText(-5), "-5.0%");
  assert.equal(awayText(null), "");
});

test("bar width is a share of the top row, never vanishing", () => {
  assert.equal(barWidth(50, 100), 50);
  assert.equal(barWidth(1, 100000), 4);
  assert.equal(barWidth(10, 0), 4);
  assert.equal(barWidth(200, 100), 100);
});

test("progress text only while tickers are still being read", () => {
  assert.equal(progressText({ tickers: [{ state: "ready" }, { state: "pending" }, { state: "error" }] }), "Reading Schwab… 2 of 3 tickers ready");
  assert.equal(progressText({ tickers: [{ state: "ready" }] }), "");
  assert.equal(progressText(null), "");
});

test("read time shows the oldest ready read in ET", () => {
  const text = readAtText({ tickers: [
    { state: "ready", readAt: "2026-11-17T08:31:00-05:00" },
    { state: "ready", readAt: "2026-11-17T08:45:00-05:00" },
    { state: "pending" },
  ] });
  assert.equal(text, "Read from Schwab 8:31 AM ET");
  assert.equal(readAtText({ tickers: [] }), "");
});
