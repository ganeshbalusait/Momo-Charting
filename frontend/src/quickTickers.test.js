import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { buildQuickTickers } from "./quickTickers.js";

const PINNED = ["SPY", "QQQ", "SLV"];

// The complaint this exists for: add a ticker, it must appear on the rail.
test("a ticker added to the watchlist appears on the rail", () => {
  const rail = buildQuickTickers(PINNED, ["AAPL", "XXX"]);
  assert.ok(rail.includes("XXX"));
});

test("a ticker removed from the watchlist disappears from the rail", () => {
  const before = buildQuickTickers(PINNED, ["AAPL", "EA"]);
  const after = buildQuickTickers(PINNED, ["AAPL"]);
  assert.ok(before.includes("EA"));
  assert.ok(!after.includes("EA"));
});

// SPY/QQQ/SLV/USO are reference instruments, wanted one tap away whether or not
// they are being traded, so they survive not being on the watchlist.
test("pinned names stay even when the watchlist does not contain them", () => {
  const rail = buildQuickTickers(PINNED, ["AAPL"]);
  assert.deepEqual(rail.slice(0, 3), PINNED);
});

test("pinned names come first, so the common ones need no scrolling", () => {
  const rail = buildQuickTickers(PINNED, ["AAPL", "QQQ", "ZZZZ"]);
  assert.deepEqual(rail, ["SPY", "QQQ", "SLV", "AAPL", "ZZZZ"]);
});

test("a ticker on both lists appears once, in its pinned position", () => {
  const rail = buildQuickTickers(["SPY", "AAPL"], ["AAPL", "MSFT"]);
  assert.deepEqual(rail, ["SPY", "AAPL", "MSFT"]);
});

test("case and stray spacing are normalised", () => {
  assert.deepEqual(buildQuickTickers([" spy "], ["aapl"]), ["SPY", "AAPL"]);
});

test("missing or ragged input never throws", () => {
  assert.deepEqual(buildQuickTickers(null, null), []);
  assert.deepEqual(buildQuickTickers(undefined, ["AAPL"]), ["AAPL"]);
  assert.deepEqual(buildQuickTickers(PINNED, [null, undefined, "", "AAPL"]), [...PINNED, "AAPL"]);
});

// A hardcoded rail is the bug. If the constant is ever rendered directly again,
// the rail silently stops following the watchlist.
test("the phone Options rail renders the derived list, not the constant", () => {
  const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  const rail = appSource.match(
    /<nav className="mobile-quick-options-tickers"[\s\S]{0,200}?\.map\(\(ticker\)/,
  );
  assert.ok(rail, "could not find the phone Options rail");
  assert.ok(
    !/OI_FINDER_QUICK_TICKERS\.map\(\(ticker\)/.test(rail[0]),
    "the phone Options rail is rendering the hardcoded constant again",
  );
});
