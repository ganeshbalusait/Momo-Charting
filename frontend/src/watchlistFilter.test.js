import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { filterWatchlistSymbols, normalizeWatchlistSearch } from "./watchlistFilter.js";

const WATCHLIST = ["BILI", "CART", "BABA", "AAPL", "ACHR", "BKNG", "AMGN", "GOOG", "GOOGL", "MSTR"];

test("an empty search shows the whole watchlist, not an empty one", () => {
  assert.deepEqual(filterWatchlistSymbols(WATCHLIST, ""), WATCHLIST);
  assert.deepEqual(filterWatchlistSymbols(WATCHLIST, "   "), WATCHLIST);
  assert.deepEqual(filterWatchlistSymbols(WATCHLIST, null), WATCHLIST);
  assert.deepEqual(filterWatchlistSymbols(WATCHLIST, undefined), WATCHLIST);
});

test("search is case-insensitive and ignores stray spacing", () => {
  assert.deepEqual(filterWatchlistSymbols(WATCHLIST, "aapl"), ["AAPL"]);
  assert.deepEqual(filterWatchlistSymbols(WATCHLIST, "  AaPl  "), ["AAPL"]);
});

// Substring, not prefix: "GOO" has to find both listings, and a partial like
// "MST" has to find MSTR, or the search is useless on a 358-ticker list.
test("matching is substring, so partial and mid-symbol queries work", () => {
  assert.deepEqual(filterWatchlistSymbols(WATCHLIST, "GOO"), ["GOOG", "GOOGL"]);
  assert.deepEqual(filterWatchlistSymbols(WATCHLIST, "MST"), ["MSTR"]);
  assert.deepEqual(filterWatchlistSymbols(WATCHLIST, "BA"), ["BABA"]);
});

test("a query that matches nothing returns nothing, and the caller says so", () => {
  assert.deepEqual(filterWatchlistSymbols(WATCHLIST, "ZZZZ"), []);
});

test("a missing or ragged list never throws", () => {
  assert.deepEqual(filterWatchlistSymbols(null, "A"), []);
  assert.deepEqual(filterWatchlistSymbols(undefined, "A"), []);
  assert.deepEqual(filterWatchlistSymbols([null, undefined, "AAPL"], "AAPL"), ["AAPL"]);
});

test("normalize is what the empty-state message renders, so it is upper and trimmed", () => {
  assert.equal(normalizeWatchlistSearch("  msft "), "MSFT");
  assert.equal(normalizeWatchlistSearch(null), "");
});

// The panel must not render an empty box on a no-match search - that reads as
// "your watchlist is gone" rather than "no match", the same trap the news panel
// had earlier today.
test("the Watchlist panel explains a no-match search instead of rendering blank", () => {
  const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  assert.match(appSource, /watchlistSearchTerm && !visibleWatchlistSymbols\.length/);
  assert.match(appSource, /No ticker in your watchlist matches/);
});

test("the Watchlist grid renders the filtered list, not the raw one", () => {
  const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  assert.match(appSource, /\{visibleWatchlistSymbols\.map\(\(symbol\) => \{/);
});
