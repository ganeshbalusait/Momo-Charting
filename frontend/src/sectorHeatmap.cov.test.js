// Unit (2026-11-04 coverage): the Sectors page's single quote request and
// the "feed down" grid. The page sends every card's leaders plus the 11 ETFs
// in ONE GET every 8 s, so the URL must stay small and duplicate-free; and when
// the quote feed answers nothing, every card must still draw (grey tiles)
// rather than going blank.
import test from "node:test";
import assert from "node:assert/strict";

import {
  SECTOR_CARDS,
  SECTOR_SYMBOLS,
  buildMarketSectorGroups,
  buildSectorPage,
  formatSignedPct,
  heatLevel,
  sectorPageSymbols,
  sortSectorGroups,
} from "./sectorHeatmap.js";

test("the grid request lists every symbol once and stays far below URL limits", () => {
  const url = `/api/watchlist-quotes?profile=1&symbols=${encodeURIComponent(SECTOR_SYMBOLS.join(","))}`;
  assert.ok(url.length < 8 * 1024, `grid URL is ${url.length} bytes`);
  assert.equal(new Set(SECTOR_SYMBOLS).size, SECTOR_SYMBOLS.length);
  assert.equal(SECTOR_CARDS.length, 11);
  for (const card of SECTOR_CARDS) {
    assert.equal(card.leaders.length, 16, `${card.name} has ${card.leaders.length} leaders`);
    assert.ok(SECTOR_SYMBOLS.includes(card.etf), `${card.name}'s ETF ${card.etf} is not quoted`);
  }
});

test("no ticker sits on two cards (one tap = one sector)", () => {
  const owner = new Map();
  for (const card of SECTOR_CARDS) {
    for (const { symbol } of [...card.leaders, ...card.more]) {
      assert.ok(!owner.has(symbol) || owner.get(symbol) === card.name, `${symbol} is on ${owner.get(symbol)} and ${card.name}`);
      owner.set(symbol, card.name);
    }
  }
});

test("each sector page's extra request is its own names, small, and never repeats a leader", () => {
  for (const card of SECTOR_CARDS) {
    const more = sectorPageSymbols(card.name);
    assert.ok(more.length >= 20, `${card.name} page adds only ${more.length} names`);
    const leaders = new Set(card.leaders.map((leader) => leader.symbol));
    assert.deepEqual(more.filter((symbol) => leaders.has(symbol)), []);
    assert.ok(encodeURIComponent(more.join(",")).length < 2048);
  }
  assert.deepEqual(sectorPageSymbols("No such sector"), []);
});

test("feed down: with no quotes every card still draws its 16 grey tiles", () => {
  const groups = buildMarketSectorGroups({});
  assert.equal(groups.length, 11);
  for (const group of groups) {
    assert.equal(group.tiles.length, 16, `${group.name} lost tiles with no quotes`);
    assert.ok(group.tiles.every((tile) => heatLevel(tile.changePct).tone === "none"));
    assert.equal(group.moveSource, "none");
    assert.equal(formatSignedPct(group.changePct), "--");
  }
  assert.deepEqual(sortSectorGroups(groups, "high").map((g) => g.name), groups.map((g) => g.name),
    "unpriced cards keep their default order under any sort");
});

test("a name the feed answered without a price is hidden; one not asked yet stays grey", () => {
  const quotes = { NVDA: { lastPrice: null, changePct: null }, XLK: { lastPrice: 250, changePct: 1.2 } };
  const tech = buildMarketSectorGroups(quotes).find((group) => group.name === "Technology");
  assert.ok(!tech.tiles.some((tile) => tile.symbol === "NVDA"));
  assert.ok(tech.tiles.some((tile) => tile.symbol === "AAPL" && tile.changePct === null));
  assert.equal(tech.changePct, 1.2);
  assert.equal(tech.moveSource, "etf");
});

test("the Technology page holds 36+ names and its Semiconductors chip group is real", () => {
  const page = buildSectorPage("Technology", {});
  assert.ok(page.tiles.length >= 36, `only ${page.tiles.length} names`);
  const semis = page.subgroups.find((group) => group.label === "Semiconductors");
  assert.ok(semis && semis.symbols.includes("NVDA"));
  const onPage = new Set(page.tiles.map((tile) => tile.symbol));
  assert.deepEqual(semis.symbols.filter((symbol) => !onPage.has(symbol)), [], "a chip names a stock the page never shows");
});
