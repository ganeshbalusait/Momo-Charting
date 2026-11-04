// Sector panel maths: Robinhood-style sector heatmap (trader ask 2026-11-03).
// The layout tests matter most: a treemap that overlaps, leaves gaps or spills
// past the card looks fine in one screenshot and wrong on the next watchlist.

import { describe, test } from "node:test";
import assert from "node:assert/strict";

import {
  SECTOR_CARDS,
  SECTOR_SYMBOLS,
  buildMarketSectorGroups,
  sortSectorGroups,
  buildSectorPage,
  plausibleCap,
  companyName,
  filterSectorTiles,
  sectorPageSymbols,
  sortSectorRows,
  PAGE_SORT_MODES,
  cardHeight,
  formatMarketCap,
  formatSignedPct,
  heatLevel,
  rowCounts,
  rowLayout,
  squarify,
  tileFontSizes,
  tileLabelMode,
} from "./sectorHeatmap.js";


describe("market sector cards (Robinhood: each sector's top stocks)", () => {
  const ROBINHOOD_TECH = ["NVDA", "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "TSM", "META", "AVGO", "SKHY", "MU", "AMD", "ASML", "TCEHY", "PLTR", "CSCO"];
  const quote = (changePct, extra = {}) => ({ lastPrice: 100, change: changePct, changePct, ...extra });

  test("eleven sector cards, each holding many tickers, no ticker twice", () => {
    assert.equal(SECTOR_CARDS.length, 11);
    for (const card of SECTOR_CARDS) {
      assert.ok(card.leaders.length >= 12, `${card.name} has ${card.leaders.length}`);
      assert.match(card.etf, /^XL[A-Z]{1,2}$/);
    }
    const leaders = SECTOR_CARDS.flatMap((card) => card.leaders.map((leader) => leader.symbol));
    const all = SECTOR_CARDS.flatMap((card) => [...card.leaders, ...card.more].map((item) => item.symbol));
    assert.deepEqual(all.filter((symbol, index) => all.indexOf(symbol) !== index), []);
    // The grid polls leaders + ETFs only; a sector's extra names wait for its page.
    assert.equal(new Set(SECTOR_SYMBOLS).size, leaders.length + SECTOR_CARDS.length);
  });

  test("the Technology card is the Robinhood screenshot's card", () => {
    const tech = SECTOR_CARDS.find((card) => card.name === "Technology");
    assert.deepEqual(tech.leaders.map((leader) => leader.symbol), ROBINHOOD_TECH);
    assert.equal(tech.etf, "XLK");
  });

  test("before any quote arrives every card is drawn, with estimated sizes", () => {
    const groups = buildMarketSectorGroups({});
    assert.equal(groups[0].name, "Technology");
    assert.equal(groups[0].tiles.length, 16);
    assert.equal(groups[0].tiles[0].symbol, "NVDA");
    assert.ok(groups[0].tiles.every((tile) => tile.estimatedCap && tile.changePct === null));
    assert.equal(groups[0].changePct, null);
  });

  test("the headline % is the sector ETF's move, not the tiles' average", () => {
    const [tech] = buildMarketSectorGroups({ XLK: quote(1.25), NVDA: quote(3.17), AAPL: quote(-2.04) });
    assert.equal(tech.changePct, 1.25);
    assert.equal(tech.moveSource, "etf");
  });

  test("without an ETF quote the headline falls back to the cap-weighted tiles", () => {
    const [tech] = buildMarketSectorGroups({ NVDA: quote(2, { marketCap: 4.2e12 }), AAPL: quote(-1, { marketCap: 1.4e12 }) });
    assert.equal(tech.moveSource, "tiles");
    assert.ok(Math.abs(tech.changePct - 1.25) < 1e-9); // (2*4.2T - 1*1.4T) / 5.6T
  });

  test("a live market cap replaces the built-in estimate", () => {
    const [tech] = buildMarketSectorGroups({ CSCO: quote(1, { marketCap: 600e9 }), NVDA: quote(1) });
    const csco = tech.tiles.find((tile) => tile.symbol === "CSCO");
    assert.equal(csco.capWeight, 600e9);
    assert.equal(csco.estimatedCap, false);
    assert.equal(csco.weight, Math.sqrt(600e9));
  });

  test("a leader the feed answers with no price is left out instead of drawn grey", () => {
    const quotes = Object.fromEntries(ROBINHOOD_TECH.map((symbol) => [symbol, symbol === "SKHY" ? { lastPrice: null, changePct: null } : quote(1)]));
    const [tech] = buildMarketSectorGroups(quotes);
    assert.equal(tech.tiles.length, 15);
    assert.ok(!tech.tiles.some((tile) => tile.symbol === "SKHY"));
    const [kept] = buildMarketSectorGroups(quotes, { hideUnquoted: false });
    assert.equal(kept.tiles.length, 16);
  });

  test("tile area follows sqrt(cap), as on the Robinhood card", () => {
    const [tech] = buildMarketSectorGroups({});
    const nvda = tech.tiles.find((tile) => tile.symbol === "NVDA");
    const csco = tech.tiles.find((tile) => tile.symbol === "CSCO");
    assert.ok(Math.abs(nvda.weight / csco.weight - Math.sqrt(4400 / 250)) < 1e-9);
  });
});

describe("heatLevel", () => {
  test("five steps, matched to the Robinhood card's tiles", () => {
    assert.deepEqual(heatLevel(0), { tone: "flat", level: 0 });
    assert.deepEqual(heatLevel(0.01), { tone: "flat", level: 0 });
    assert.deepEqual(heatLevel(0.29), { tone: "up", level: 1 });    // GOOGL pale
    assert.deepEqual(heatLevel(0.6), { tone: "up", level: 2 });     // TCEHY
    assert.deepEqual(heatLevel(1.17), { tone: "up", level: 3 });    // AMZN light
    assert.deepEqual(heatLevel(1.9), { tone: "up", level: 4 });     // MSFT mid
    assert.deepEqual(heatLevel(3.17), { tone: "up", level: 5 });    // NVDA full
    assert.deepEqual(heatLevel(2.76), { tone: "up", level: 5 });    // META full
    assert.deepEqual(heatLevel(-2.04), { tone: "down", level: 5 }); // AAPL full orange
    assert.deepEqual(heatLevel(2.02), { tone: "up", level: 4 });    // TSM not yet full green
    assert.deepEqual(heatLevel(-1.8), { tone: "down", level: 4 });
    assert.deepEqual(heatLevel(-14), { tone: "down", level: 5 });
  });

  test("missing or junk values are 'none', not flat", () => {
    for (const value of [null, undefined, "", "abc", NaN]) assert.equal(heatLevel(value).tone, "none");
  });
});

describe("squarify", () => {
  const rect = { x: 0, y: 0, width: 300, height: 150 };
  const items = [500, 300, 200, 120, 80, 60, 40, 20, 10, 5, 3].map((weight, index) => ({ symbol: `T${index}`, weight }));

  test("every tile is placed, inside the card, with areas proportional to weight", () => {
    const layout = squarify(items, rect);
    assert.equal(layout.length, items.length);
    const total = items.reduce((sum, item) => sum + item.weight, 0);
    for (const tile of layout) {
      assert.ok(tile.x >= -1e-6 && tile.y >= -1e-6, "starts inside");
      assert.ok(tile.x + tile.width <= rect.width + 1e-6, "ends inside horizontally");
      assert.ok(tile.y + tile.height <= rect.height + 1e-6, "ends inside vertically");
      const want = (tile.item.weight / total) * rect.width * rect.height;
      assert.ok(Math.abs(tile.width * tile.height - want) < 1e-6 * rect.width * rect.height + 1e-6, `${tile.item.symbol} area`);
    }
  });

  test("tiles cover the card exactly with no overlaps", () => {
    const layout = squarify(items, rect);
    const covered = layout.reduce((sum, tile) => sum + tile.width * tile.height, 0);
    assert.ok(Math.abs(covered - rect.width * rect.height) < 1e-6);
    for (let i = 0; i < layout.length; i += 1) {
      for (let j = i + 1; j < layout.length; j += 1) {
        const a = layout[i];
        const b = layout[j];
        const overlapW = Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x);
        const overlapH = Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y);
        assert.ok(overlapW <= 1e-6 || overlapH <= 1e-6, `${a.item.symbol} overlaps ${b.item.symbol}`);
      }
    }
  });

  test("the biggest tile sits at the top-left", () => {
    const [first] = squarify(items, rect);
    assert.equal(first.item.symbol, "T0");
    assert.equal(first.x, 0);
    assert.equal(first.y, 0);
  });

  test("equal weights give reasonably square tiles", () => {
    const layout = squarify(Array.from({ length: 12 }, (_, index) => ({ symbol: `E${index}`, weight: 1 })), { x: 0, y: 0, width: 400, height: 300 });
    for (const tile of layout) {
      const ratio = Math.max(tile.width / tile.height, tile.height / tile.width);
      assert.ok(ratio < 3, `ratio ${ratio}`);
    }
  });

  test("empty input, zero weights and an empty card produce nothing", () => {
    assert.deepEqual(squarify([], rect), []);
    assert.deepEqual(squarify([{ weight: 0 }, { weight: -1 }], rect), []);
    assert.deepEqual(squarify(items, { x: 0, y: 0, width: 0, height: 100 }), []);
  });
});

describe("Robinhood row layout", () => {
  const caps = { NVDA: 4.4, AAPL: 3.9, MSFT: 3.8, GOOGL: 2.2, GOOG: 2.2, AMZN: 2.4, TSM: 1.2, META: 1.8, AVGO: 1.6, SKHY: 0.5, MU: 0.14, AMD: 0.3, ASML: 0.3, TCEHY: 0.6, PLTR: 0.4, CSCO: 0.25 };
  const items = Object.entries(caps).map(([symbol, cap]) => ({ symbol, weight: Math.sqrt(cap) }));
  const rect = { x: 0, y: 0, width: 530, height: 280 };

  test("16 tiles make 5 / 5 / 6 rows, as on the Robinhood card", () => {
    assert.deepEqual(rowCounts(16, 530), [5, 5, 6]);
    const layout = rowLayout(items, rect);
    assert.deepEqual([0, 1, 2].map((row) => layout.filter((tile) => tile.row === row).length), [5, 5, 6]);
  });

  test("the top row holds the biggest names, biggest first, left to right", () => {
    const top = rowLayout(items, rect).filter((tile) => tile.row === 0);
    assert.deepEqual(top.map((tile) => tile.item.symbol).slice(0, 3), ["NVDA", "AAPL", "MSFT"]);
    for (let i = 1; i < top.length; i += 1) assert.ok(top[i].x > top[i - 1].x);
  });

  test("rows get shorter going down and every row spans the full width", () => {
    const layout = rowLayout(items, rect);
    const rowHeights = [0, 1, 2].map((row) => layout.find((tile) => tile.row === row).height);
    assert.ok(rowHeights[0] > rowHeights[1] && rowHeights[1] > rowHeights[2], rowHeights.join(","));
    for (const row of [0, 1, 2]) {
      const tiles = layout.filter((tile) => tile.row === row);
      const span = tiles.reduce((sum, tile) => sum + tile.width, 0);
      assert.ok(Math.abs(span - rect.width) < 1e-6);
    }
    const covered = layout.reduce((sum, tile) => sum + tile.width * tile.height, 0);
    assert.ok(Math.abs(covered - rect.width * rect.height) < 1e-6);
  });

  test("the bottom row is never squashed below the floor", () => {
    const skewed = [{ symbol: "BIG", weight: 1000 }, ...Array.from({ length: 10 }, (_, i) => ({ symbol: `s${i}`, weight: 1 }))];
    const layout = rowLayout(skewed, rect, { minRowHeight: 30 });
    for (const tile of layout) assert.ok(tile.height >= 30 - 1e-6, `${tile.item.symbol} ${tile.height}`);
  });

  test("few tiles stay on one row; a narrow card uses more rows", () => {
    assert.deepEqual(rowCounts(4, 600), [4]);
    assert.deepEqual(rowCounts(16, 330), [5, 5, 6]);
    assert.deepEqual(rowCounts(30, 330), [6, 6, 6, 6, 6]);
    assert.deepEqual(rowLayout([], rect), []);
  });
});

describe("sizing and labels", () => {
  test("card height grows with tile count and stays within bounds", () => {
    assert.equal(cardHeight(1, 300), 72);
    assert.ok(cardHeight(14, 300) > cardHeight(6, 300));
    assert.equal(cardHeight(200, 300), 340);
    assert.equal(cardHeight(200, 300, true), 720);
  });

  test("a wide card keeps a block shape instead of a one-row strip", () => {
    assert.ok(cardHeight(16, 600) >= 600 * 0.45);
    assert.ok(cardHeight(4, 600) >= 600 * 0.3);
    assert.ok(cardHeight(16, 1900) <= 340);
  });

  test("big tiles get big tickers, small ones a readable floor", () => {
    assert.equal(tileFontSizes(260, 310).symbol, 20);
    assert.equal(tileFontSizes(30, 16).symbol, 9);
    const mid = tileFontSizes(80, 50);
    assert.ok(mid.symbol > 9 && mid.symbol < 20 && mid.pct < mid.symbol);
  });

  test("Robinhood drops the % on its short bottom row; ticker and % share a size", () => {
    assert.equal(tileLabelMode(90, 45, "rh"), "symbol");
    assert.equal(tileLabelMode(90, 70, "rh"), "full");
    const rh = tileFontSizes(130, 160, "rh");
    assert.equal(rh.symbol, rh.pct);
    assert.ok(rh.symbol <= 18);
  });

  test("small tiles drop the % first, then the ticker", () => {
    assert.equal(tileLabelMode(60, 40), "full");
    assert.equal(tileLabelMode(40, 20), "symbol");
    assert.equal(tileLabelMode(20, 12), "none");
  });

  test("formatting", () => {
    assert.equal(formatSignedPct(3.171), "+3.17%");
    assert.equal(formatSignedPct(-2.04), "-2.04%");
    assert.equal(formatSignedPct(null), "--");
    assert.equal(formatMarketCap(4.2e12), "$4.20T");
    assert.equal(formatMarketCap(250e9), "$250.0B");
    assert.equal(formatMarketCap(null), "--");
  });
});

describe("sector card sort (trader, 2026-11-06: sort by high/low)", () => {
  const g = (name, changePct) => ({ name, changePct });
  const groups = [g("Technology", 1.1), g("Financials", -0.49), g("Healthcare", null), g("Energy", -0.6), g("Industrials", 1.08)];

  test("default keeps the Robinhood order", () => {
    assert.deepEqual(sortSectorGroups(groups, "default").map((x) => x.name), groups.map((x) => x.name));
  });

  test("high puts the best move first, low the worst; no-quote cards go last", () => {
    assert.deepEqual(sortSectorGroups(groups, "high").map((x) => x.name), ["Technology", "Industrials", "Financials", "Energy", "Healthcare"]);
    assert.deepEqual(sortSectorGroups(groups, "low").map((x) => x.name), ["Energy", "Financials", "Industrials", "Technology", "Healthcare"]);
  });

  test("never mutates its input, and survives junk", () => {
    const copy = groups.map((x) => x.name);
    sortSectorGroups(groups, "high");
    assert.deepEqual(groups.map((x) => x.name), copy);
    assert.deepEqual(sortSectorGroups(null, "high"), []);
  });
});

describe("a sector's own page (trader, 2026-11-06: tap a sector, like Robinhood)", () => {
  const quote = (changePct, extra = {}) => ({ lastPrice: 50, change: changePct, changePct, ...extra });

  test("the page holds the 16 card names plus the rest of the sector's big names", () => {
    const page = buildSectorPage("Technology", {});
    assert.ok(page.tiles.length >= 36, `${page.tiles.length} names`);
    const symbols = page.tiles.map((tile) => tile.symbol);
    for (const leader of SECTOR_CARDS[0].leaders) assert.ok(symbols.includes(leader.symbol), leader.symbol);
    assert.ok(symbols.includes("ORCL") && symbols.includes("CRM"));
    assert.equal(buildSectorPage("Nope", {}), null);
  });

  test("every sector has a page with more names than its card", () => {
    for (const card of SECTOR_CARDS) {
      const page = buildSectorPage(card.name, {});
      assert.ok(page.tiles.length > card.leaders.length + 15, `${card.name}: ${page.tiles.length}`);
      assert.deepEqual(sectorPageSymbols(card.name), card.more.map((item) => item.symbol));
    }
  });

  test("names the page has not been quoted for yet stay (grey) instead of vanishing", () => {
    const page = buildSectorPage("Technology", { NVDA: quote(2), XLK: quote(1.1) });
    assert.ok(page.tiles.some((tile) => tile.symbol === "ORCL" && tile.changePct === null));
    assert.equal(page.changePct, 1.1);
  });

  test("the list sorts by size, best move or worst move; no-quote names last", () => {
    const tiles = [
      { symbol: "A", weight: 3, changePct: -1 },
      { symbol: "B", weight: 9, changePct: 2 },
      { symbol: "C", weight: 5, changePct: null },
      { symbol: "D", weight: 1, changePct: 4 },
    ];
    assert.deepEqual(PAGE_SORT_MODES, ["size", "high", "low"]);
    assert.deepEqual(sortSectorRows(tiles, "size").map((t) => t.symbol), ["B", "C", "A", "D"]);
    assert.deepEqual(sortSectorRows(tiles, "high").map((t) => t.symbol), ["D", "B", "A", "C"]);
    assert.deepEqual(sortSectorRows(tiles, "low").map((t) => t.symbol), ["A", "B", "D", "C"]);
    assert.deepEqual(tiles.map((t) => t.symbol), ["A", "B", "C", "D"]); // input untouched
  });
});

describe("sector page like Robinhood's: about, sub-group chips, company names", () => {
  test("every sector has a description and chips that cover its stocks exactly once", () => {
    for (const card of SECTOR_CARDS) {
      assert.ok(card.about.length > 30, `${card.name} about`);
      assert.ok(card.groups.length >= 3, `${card.name} has ${card.groups.length} chips`);
      const all = [...card.leaders, ...card.more].map((item) => item.symbol).sort();
      const chipped = card.groups.flatMap((group) => group.symbols).sort();
      assert.deepEqual(chipped, all, `${card.name} chips`);
    }
  });

  test("a chip filters the page's stocks; All shows every one", () => {
    const page = buildSectorPage("Technology", {});
    const semis = filterSectorTiles(page, "Semiconductors").map((tile) => tile.symbol);
    assert.ok(semis.includes("NVDA") && semis.includes("AMD") && !semis.includes("MSFT"));
    assert.equal(filterSectorTiles(page, "All").length, page.tiles.length);
    assert.equal(filterSectorTiles(page, "Nope").length, page.tiles.length);
  });

  test("company names read like Robinhood's list", () => {
    assert.equal(companyName("NVIDIA CORP"), "NVIDIA");
    assert.equal(companyName("APPLE INC"), "Apple");
    assert.equal(companyName("MICROSOFT CORP"), "Microsoft");
    assert.equal(companyName("ALPHABET INC CL A"), "Alphabet Class A");
    assert.equal(companyName("JPMORGAN CHASE & CO"), "JPMorgan Chase");
    assert.equal(companyName("AT&T INC"), "AT&T");
    assert.equal(companyName(""), null);
    assert.equal(companyName("NVDA", "NVDA"), null);
  });

  test("the page carries the name from the quote, or null until it arrives", () => {
    const page = buildSectorPage("Technology", { NVDA: { lastPrice: 1, changePct: 1, name: "NVIDIA CORP" } });
    assert.equal(page.tiles.find((tile) => tile.symbol === "NVDA").name, "NVIDIA");
    assert.equal(page.tiles.find((tile) => tile.symbol === "AAPL").name, null);
  });
});

describe("live market caps are sanity-checked (trader, 2026-11-04: SKHY filled the Technology card)", () => {
  test("a cap within 4x of the estimate is used; a currency mix-up is ignored", () => {
    assert.equal(plausibleCap(3.2e12, 4.4e12), 3.2e12);
    assert.equal(plausibleCap(1.3e14, 5e11), null);   // SK Hynix in won read as dollars
    assert.equal(plausibleCap(1e9, 5e11), null);      // absurdly small
    assert.equal(plausibleCap(null, 5e11), null);
  });

  test("a bogus live cap cannot blow up a tile", () => {
    const [tech] = buildMarketSectorGroups({ SKHY: { lastPrice: 1, changePct: -0.08, marketCap: 1.3e14 }, NVDA: { lastPrice: 1, changePct: 2, marketCap: 4.5e12 } });
    const skhy = tech.tiles.find((tile) => tile.symbol === "SKHY");
    const nvda = tech.tiles.find((tile) => tile.symbol === "NVDA");
    assert.equal(skhy.capWeight, 500e9);       // fell back to the built-in estimate
    assert.equal(skhy.estimatedCap, true);
    assert.equal(nvda.capWeight, 4.5e12);      // a sane live cap is used
    assert.equal(tech.tiles[0].symbol, "NVDA");
  });
});
