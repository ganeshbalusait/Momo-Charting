// Sector heatmap for the watchlist (trader ask, 2026-11-03: the Robinhood
// "Sectors" card -- one card per industry, tiles sized by market cap, colored by
// today's move). Everything here is pure so the panel's maths can be tested
// without a browser: grouping, the cap-weighted group move, color buckets, and
// the squarified treemap layout.

export const OTHER_GROUP = "Other";
// The move at which a tile reaches its strongest color. Bigger moves do not get
// "more green", they just stay solid -- the scale is about direction and size
// at a glance, not a precise reading (the tooltip has that).
export const HEAT_CAP_PCT = 3;
// Tiles shown in a collapsed card; the rest fold into one "+N" tile.
export const COLLAPSED_TILE_LIMIT = 14;

const finite = (value) => {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
};
const positive = (value) => {
  const number = finite(value);
  return number !== null && number > 0 ? number : null;
};

function median(values) {
  if (!values.length) return null;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

// symbols: the watchlist, in order. quotes: { SYMBOL: row } from
// /api/watchlist-quotes?profile=1 (changePct, lastPrice, change, industry,
// marketCap). Returns cards biggest-first with "Other" last.
export function buildSectorGroups(symbols, quotes = {}) {
  const seen = new Set();
  const byName = new Map();
  for (const raw of Array.isArray(symbols) ? symbols : []) {
    const symbol = String(raw || "").trim().toUpperCase();
    if (!symbol || seen.has(symbol)) continue;
    seen.add(symbol);
    const quote = quotes?.[symbol] || {};
    const name = String(quote.industry || "").trim() || OTHER_GROUP;
    if (!byName.has(name)) byName.set(name, []);
    byName.get(name).push({
      symbol,
      changePct: finite(quote.changePct),
      change: finite(quote.change),
      lastPrice: finite(quote.lastPrice),
      marketCap: positive(quote.marketCap),
    });
  }

  // A tile with no known cap takes its group's median. A group with no caps at
  // all (leveraged ETFs, mostly) takes the smallest cap on the whole board, so
  // it is drawn evenly and sorts low instead of pretending to be mega-cap.
  const allCaps = [...byName.values()].flat().map((tile) => tile.marketCap).filter((cap) => cap !== null);
  const boardFloor = allCaps.length ? Math.min(...allCaps) : 1;

  const groups = [...byName.entries()].map(([name, members]) => {
    const groupMedian = median(members.map((tile) => tile.marketCap).filter((cap) => cap !== null));
    const fallback = groupMedian ?? boardFloor;
    const tiles = members
      .map((tile) => {
        const capWeight = tile.marketCap ?? fallback;
        // Tile AREA follows the square root of the cap. Linear area made a
        // $140B name a 3px sliver next to NVDA's $4T -- unreadable and
        // unclickable. sqrt keeps the order and the "big names dominate" read
        // (as the Robinhood card does) while every tile stays usable.
        return { ...tile, capWeight, weight: Math.sqrt(capWeight), estimatedCap: tile.marketCap === null };
      })
      .sort((left, right) => right.weight - left.weight || left.symbol.localeCompare(right.symbol));
    const priced = tiles.filter((tile) => tile.changePct !== null);
    // The group move is TRUE market-cap weighted (not sqrt): it should read
    // like an index of the group.
    const pricedWeight = priced.reduce((sum, tile) => sum + tile.capWeight, 0);
    const changePct = pricedWeight > 0
      ? priced.reduce((sum, tile) => sum + tile.changePct * tile.capWeight, 0) / pricedWeight
      : null;
    return {
      name,
      tiles,
      totalWeight: tiles.reduce((sum, tile) => sum + tile.capWeight, 0),
      // Sort key: only REAL caps count, so a few capless ETFs sized at the
      // board floor can never outrank a group of actual companies.
      knownCap: tiles.reduce((sum, tile) => sum + (tile.marketCap ?? 0), 0),
      changePct,
      up: priced.filter((tile) => tile.changePct > 0).length,
      down: priced.filter((tile) => tile.changePct < 0).length,
    };
  });

  return groups.sort((left, right) => {
    if (left.name === OTHER_GROUP) return 1;
    if (right.name === OTHER_GROUP) return -1;
    return right.knownCap - left.knownCap
      || right.totalWeight - left.totalWeight
      || left.name.localeCompare(right.name);
  });
}

// The tiles a card draws: all of them when expanded, otherwise the biggest
// `limit - 1` plus one "+N" tile. The "+N" tile is never bigger than the
// median visible tile, so a 200-name "Other" card is not one giant "+186".
export function visibleTiles(tiles, expanded, limit = COLLAPSED_TILE_LIMIT) {
  const list = Array.isArray(tiles) ? tiles : [];
  if (expanded || list.length <= limit) return list;
  const shown = list.slice(0, limit - 1);
  const hidden = list.slice(limit - 1);
  const hiddenWeight = hidden.reduce((sum, tile) => sum + tile.weight, 0);
  const cap = median(shown.map((tile) => tile.weight)) ?? hiddenWeight;
  return [...shown, {
    symbol: `+${hidden.length}`,
    more: hidden.length,
    weight: Math.min(hiddenWeight, cap),
    changePct: null,
  }];
}

// Color bucket for a move. Discrete buckets (rather than a smooth gradient)
// keep neighbouring tiles readable as "a bit up" vs "a lot up", as in the
// reference screenshot. Returned as a level so CSS owns the actual colors.
export function heatLevel(changePct) {
  const pct = finite(changePct);
  if (pct === null) return { tone: "none", level: 0 };
  const size = Math.abs(pct);
  if (size < 0.05) return { tone: "flat", level: 0 };
  const level = size >= HEAT_CAP_PCT ? 4 : size >= 1.5 ? 3 : size >= 0.75 ? 2 : 1;
  return { tone: pct > 0 ? "up" : "down", level };
}

export function formatSignedPct(value) {
  const pct = finite(value);
  if (pct === null) return "--";
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

export function formatMarketCap(value) {
  const cap = positive(value);
  if (cap === null) return "--";
  if (cap >= 1e12) return `$${(cap / 1e12).toFixed(2)}T`;
  if (cap >= 1e9) return `$${(cap / 1e9).toFixed(1)}B`;
  if (cap >= 1e6) return `$${(cap / 1e6).toFixed(0)}M`;
  return `$${cap.toFixed(0)}`;
}

// --- Squarified treemap (Bruls, Huizing, van Wijk) -------------------------
// Lays `items` (each with a positive `weight`) into the rectangle
// {x, y, width, height}, biggest first from the top-left, keeping tiles as
// close to square as the weights allow. Returns [{ item, x, y, width, height }].

function worstRatio(row, side) {
  if (!row.length || side <= 0) return Infinity;
  const sum = row.reduce((total, area) => total + area, 0);
  const largest = Math.max(...row);
  const smallest = Math.min(...row);
  const sideSquared = side * side;
  const sumSquared = sum * sum;
  return Math.max((sideSquared * largest) / sumSquared, sumSquared / (sideSquared * smallest));
}

export function squarify(items, rect) {
  const width = Math.max(0, Number(rect?.width) || 0);
  const height = Math.max(0, Number(rect?.height) || 0);
  const list = (Array.isArray(items) ? items : [])
    .filter((item) => Number(item?.weight) > 0)
    .sort((left, right) => right.weight - left.weight);
  if (!list.length || width <= 0 || height <= 0) return [];
  const total = list.reduce((sum, item) => sum + item.weight, 0);
  const scale = (width * height) / total;
  const areas = list.map((item) => item.weight * scale);

  const out = [];
  let x = Number(rect?.x) || 0;
  let y = Number(rect?.y) || 0;
  let freeWidth = width;
  let freeHeight = height;
  let index = 0;

  while (index < list.length) {
    const side = Math.min(freeWidth, freeHeight);
    const row = [areas[index]];
    let next = index + 1;
    while (next < list.length && worstRatio([...row, areas[next]], side) <= worstRatio(row, side)) {
      row.push(areas[next]);
      next += 1;
    }
    const rowArea = row.reduce((sum, area) => sum + area, 0);
    // The last row fills whatever is left exactly, so rounding can never leave
    // a sliver or push past the card edge.
    const last = next >= list.length;
    if (freeWidth >= freeHeight) {
      // Lay the row as a column on the left.
      const columnWidth = last ? freeWidth : Math.min(freeWidth, rowArea / freeHeight);
      let cursor = y;
      row.forEach((area, offset) => {
        const tileHeight = offset === row.length - 1 ? y + freeHeight - cursor : area / columnWidth;
        out.push({ item: list[index + offset], x, y: cursor, width: columnWidth, height: tileHeight });
        cursor += tileHeight;
      });
      x += columnWidth;
      freeWidth -= columnWidth;
    } else {
      // Lay the row across the top.
      const rowHeight = last ? freeHeight : Math.min(freeHeight, rowArea / freeWidth);
      let cursor = x;
      row.forEach((area, offset) => {
        const tileWidth = offset === row.length - 1 ? x + freeWidth - cursor : area / rowHeight;
        out.push({ item: list[index + offset], x: cursor, y, width: tileWidth, height: rowHeight });
        cursor += tileWidth;
      });
      y += rowHeight;
      freeHeight -= rowHeight;
    }
    index = next;
  }
  return out;
}

// Card height for `count` tiles at `width` px: enough area that an average
// tile can show its ticker, within sane bounds for the narrow dock column.
export function cardHeight(count, width, expanded = false) {
  const tiles = Math.max(1, Number(count) || 1);
  const span = Math.max(120, Number(width) || 0);
  const wanted = Math.ceil((tiles * 3200) / span);
  return Math.max(60, Math.min(expanded ? 640 : 230, wanted));
}

// What a tile of this pixel size has room to print.
export function tileLabelMode(width, height) {
  if (width >= 46 && height >= 34) return "full";
  if (width >= 30 && height >= 16) return "symbol";
  return "none";
}
