// Sector panel maths: the watchlist as a sector heatmap (trader ask 2026-11-03).
// The layout tests matter most: a treemap that overlaps, leaves gaps or spills
// past the card looks fine in one screenshot and wrong on the next watchlist.

import { describe, test } from "node:test";
import assert from "node:assert/strict";

import {
  OTHER_GROUP,
  buildSectorGroups,
  SECTORS,
  cardHeight,
  formatMarketCap,
  formatSignedPct,
  heatLevel,
  rowCounts,
  rowLayout,
  sectorFor,
  squarify,
  tileFontSizes,
  tileLabelMode,
  visibleTiles,
} from "./sectorHeatmap.js";
import { readFileSync } from "node:fs";

const q = (industry, changePct, marketCap) => ({ industry, changePct, marketCap, lastPrice: 100, change: 1 });

describe("buildSectorGroups", () => {
  test("groups by industry, biggest card first, Other last", () => {
    const groups = buildSectorGroups(["NVDA", "MSFT", "ZZZZ", "AMD", "CRM"], {
      NVDA: q("Semis", 3, 4e12),
      AMD: q("Semis", 1, 3e11),
      MSFT: q("Software", 1.9, 3.5e12),
      CRM: q("Software", -1, 2.5e11),
      ZZZZ: q(null, 5, 9e12), // no industry -> Other, even when huge
    }, { groupBy: "industry" });
    assert.deepEqual(groups.map((group) => group.name), ["Semis", "Software", OTHER_GROUP]);
    assert.deepEqual(groups[0].tiles.map((tile) => tile.symbol), ["NVDA", "AMD"]);
  });

  test("a symbol with no quote yet still gets a grey tile in Other", () => {
    const [group] = buildSectorGroups(["NEWT"], {});
    assert.equal(group.name, OTHER_GROUP);
    assert.equal(group.tiles[0].changePct, null);
    assert.equal(heatLevel(group.tiles[0].changePct).tone, "none");
    assert.equal(group.changePct, null);
  });

  test("the group move is market-cap weighted", () => {
    const [group] = buildSectorGroups(["BIG", "SMALL"], {
      BIG: q("Semis", 1, 900),
      SMALL: q("Semis", 11, 100),
    });
    assert.ok(Math.abs(group.changePct - 2) < 1e-9); // (1*900 + 11*100) / 1000
    assert.equal(group.up, 2);
    assert.equal(group.down, 0);
  });

  test("unpriced tiles do not drag the group move toward zero", () => {
    const [group] = buildSectorGroups(["A", "B"], {
      A: q("Semis", 2, 100),
      B: { industry: "Semis", marketCap: 100 },
    });
    assert.equal(group.changePct, 2);
  });

  test("tile area follows sqrt(cap) so a small name next to a mega-cap stays usable", () => {
    const [group] = buildSectorGroups(["NVDA", "MU"], { NVDA: q("Semis", 1, 4.4e12), MU: q("Semis", 1, 1.4e11) });
    const share = group.tiles[1].weight / (group.tiles[0].weight + group.tiles[1].weight);
    assert.ok(share > 0.12, `MU share ${share}`);       // linear would be ~3%
    assert.ok(group.tiles[0].weight > group.tiles[1].weight);
  });

  test("a missing cap is sized as the group median and flagged as estimated", () => {
    const [group] = buildSectorGroups(["A", "B", "C", "D"], {
      A: q("Semis", 1, 100),
      B: q("Semis", 1, 300),
      C: q("Semis", 1, 500),
      D: q("Semis", 1, null),
    });
    const d = group.tiles.find((tile) => tile.symbol === "D");
    assert.equal(d.capWeight, 300);
    assert.equal(d.weight, Math.sqrt(300));
    assert.equal(d.estimatedCap, true);
    assert.equal(group.tiles.find((tile) => tile.symbol === "A").estimatedCap, false);
  });

  test("a group with no caps at all sorts below capped groups instead of above", () => {
    const groups = buildSectorGroups(["SOXL", "TSLL", "NVTS"], {
      SOXL: q("ETF-Lev", 6, null),
      TSLL: q("ETF-Lev", 4, null),
      NVTS: q("Semis", 2, 2e9),
    }, { groupBy: "industry" });
    assert.deepEqual(groups.map((group) => group.name), ["Semis", "ETF-Lev"]);
    assert.ok(groups[1].tiles.every((tile) => tile.capWeight === 2e9));
  });

  test("duplicate and blank watchlist entries are dropped, case-insensitively", () => {
    const groups = buildSectorGroups(["nvda", "NVDA", " ", "", null, "NVDA "], { NVDA: q("Semis", 1, 1) }, { groupBy: "industry" });
    const symbols = groups.flatMap((group) => group.tiles.map((tile) => tile.symbol));
    assert.deepEqual(symbols, ["NVDA"]);
  });
});

describe("broad sectors (Robinhood / TOS grouping)", () => {
  test("sector mode is the default and folds industries into sectors", () => {
    const groups = buildSectorGroups(["NVDA", "MSFT", "AAPL", "CSCO", "META", "JPM", "SOXL", "SPY"], {
      NVDA: q("Semis", 3.17, 4.4e12),
      MSFT: q("Software", 1.9, 3.8e12),
      AAPL: q("Tech-HW", -2.04, 3.9e12),
      CSCO: q("Networking", 0.5, 2.5e11),
      META: q("Interactive Media", 2.76, 1.8e12),
      JPM: q("Banks", 0.4, 7e11),
      SOXL: q("ETF-Lev", 9, null),
      SPY: q("Index ETF", 0.6, null),
    });
    assert.deepEqual(groups.map((group) => group.name), ["Technology", "Communication", "Financials", "ETFs"]);
    assert.deepEqual(groups[0].tiles.map((tile) => tile.symbol).sort(), ["AAPL", "CSCO", "MSFT", "NVDA"]);
    assert.equal(groups[0].tiles.find((tile) => tile.symbol === "NVDA").industry, "Semis");
  });

  test("industry mode keeps the trader's own buckets", () => {
    const groups = buildSectorGroups(["NVDA", "MSFT"], { NVDA: q("Semis", 1, 2), MSFT: q("Software", 1, 1) }, { groupBy: "industry" });
    assert.deepEqual(groups.map((group) => group.name), ["Semis", "Software"]);
  });

  test("every hand-map industry label lands in a sector", () => {
    const source = readFileSync(new URL("../../momx/industries.py", import.meta.url), "utf8");
    const table = source.slice(source.indexOf("INDUSTRY_SYMBOLS"), source.indexOf("INDUSTRY_MAP"));
    const labels = [...table.matchAll(/^\s{4}"([^"]+)":\s*\(/gm)].map((match) => match[1]);
    assert.ok(labels.length > 40, `parsed ${labels.length} labels`);
    const unplaced = labels.filter((label) => !SECTORS.includes(sectorFor(label)));
    assert.deepEqual(unplaced, []);
  });

  test("provider labels are placed exactly or by keyword; blanks are Other", () => {
    assert.equal(sectorFor("Insurance"), "Financials");
    assert.equal(sectorFor("Logistics & Transportation"), "Industrials");
    assert.equal(sectorFor("Specialty Retail"), "Consumer Cyclical");
    assert.equal(sectorFor("Medical Devices"), "Healthcare");
    assert.equal(sectorFor(""), null);
    assert.equal(sectorFor("Zzyzx"), null);
    const [group] = buildSectorGroups(["ODD"], { ODD: q("Zzyzx", 1, 1) });
    assert.equal(group.name, OTHER_GROUP);
  });
});

describe("visibleTiles", () => {
  const tiles = Array.from({ length: 20 }, (_, index) => ({ symbol: `S${index}`, weight: 100 - index, changePct: 1 }));

  test("collapsed: the biggest 13 plus one +N tile", () => {
    const shown = visibleTiles(tiles, false, 14);
    assert.equal(shown.length, 14);
    assert.equal(shown.at(-1).symbol, "+7");
    assert.equal(shown.at(-1).more, 7);
  });

  test("the +N tile is never bigger than the median visible tile", () => {
    const shown = visibleTiles(tiles, false, 14);
    const visibleWeights = shown.slice(0, -1).map((tile) => tile.weight).sort((a, b) => a - b);
    assert.ok(shown.at(-1).weight <= visibleWeights[Math.floor(visibleWeights.length / 2)]);
  });

  test("expanded or short lists are shown whole", () => {
    assert.equal(visibleTiles(tiles, true, 14).length, 20);
    assert.equal(visibleTiles(tiles.slice(0, 5), false, 14).length, 5);
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
