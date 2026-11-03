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
// Tiles shown in a collapsed card; the rest fold into one "+N" tile. The
// Robinhood card shows about 16.
export const COLLAPSED_TILE_LIMIT = 16;

// Broad sectors, as Robinhood / thinkorswim show them (trader, 2026-11-03: "I
// want same as robinhood or tos sector heat map"). Every label in the hand map
// (momx/industries.py) and the Finnhub/FMP labels the lookup produces map to
// one of these; the "Industries" switch still shows his own fine-grained
// buckets. Order here is the fallback order for equal-sized sectors.
export const SECTORS = [
  "Technology", "Communication", "Consumer Cyclical", "Consumer Defensive",
  "Financials", "Healthcare", "Industrials", "Energy", "Utilities",
  "Materials", "Real Estate", "ETFs",
];

const SECTOR_LABELS = {
  Technology: [
    "Semis", "Software", "Networking", "Tech-HW", "AI-Infra", "IT Services",
    "AI - Data Centers", "AI - Networking", "AI - Cloud", "Cybersecurity",
    "Quantum", "Technology", "Semiconductors", "Communications",
    "Electrical Equipment & Parts", "Computers",
  ],
  Communication: ["Interactive Media", "Entertainment", "Telecom", "Media", "Telecommunication", "Communication Services"],
  "Consumer Cyclical": [
    "Apparel", "Retail", "Autos", "E-Commerce", "Travel", "Restaurants", "EV",
    "Auto-Tech", "Gaming", "Homebuilders", "Automobiles", "Auto Components",
    "Hotels, Restaurants & Leisure", "Textiles, Apparel & Luxury Goods",
    "Leisure Products", "Distributors", "Diversified Consumer Services",
  ],
  "Consumer Defensive": ["Beverages", "Food", "Consumer", "Tobacco", "Cannabis", "Food Products", "Consumer products"],
  Financials: ["Banks", "Fintech", "Capital Markets", "Crypto", "Insurance", "Banking", "Financial Services"],
  Healthcare: ["Biotech", "Pharma", "Healthcare", "Health Care", "Life Sciences Tools & Services", "Biotechnology", "Pharmaceuticals"],
  Industrials: [
    "Industrials", "Defense", "Airlines", "Transport", "Space", "Air Mobility",
    "Drones", "Robotics", "Aerospace & Defense", "Machinery", "Electrical Equipment",
    "Construction", "Building", "Logistics & Transportation", "Marine", "Road & Rail",
    "Commercial Services & Supplies", "Professional Services",
    "Trading Companies & Distributors", "Industrial Conglomerates",
    "Transportation Infrastructure",
  ],
  Energy: ["Oil & Gas", "Uranium", "Energy", "Solar", "Clean Energy", "Nuclear", "Oil & Gas Services"],
  Utilities: ["Utilities", "AI - Power"],
  Materials: ["Metals", "Gold", "Chemicals", "Metals & Mining", "Packaging", "Paper & Forest"],
  "Real Estate": ["Real Estate", "Real Estate Management & Development"],
  ETFs: ["ETF-Lev", "Commodity ETF", "Index ETF", "ETF", "Fund", "Asset Management", "Exchange Traded Fund"],
};

const SECTOR_BY_LABEL = new Map(
  Object.entries(SECTOR_LABELS).flatMap(([sector, labels]) => labels.map((label) => [label.toLowerCase(), sector])),
);

// Keyword fallback for provider labels not listed above, checked in order.
const SECTOR_KEYWORDS = [
  [/etf|fund|trust/, "ETFs"],
  [/semi|software|tech|cyber|cloud|computer|electronic|internet/, "Technology"],
  [/bank|financ|insur|capital|credit|crypto|asset|broker/, "Financials"],
  [/pharma|bio|health|medic|drug|life science/, "Healthcare"],
  [/oil|gas|energy|solar|coal|uranium|nuclear/, "Energy"],
  [/utilit|power|electric util/, "Utilities"],
  [/metal|mining|gold|silver|chemical|steel|material/, "Materials"],
  [/real estate|reit|propert/, "Real Estate"],
  [/media|telecom|entertain|communic|broadcast|publish/, "Communication"],
  [/retail|apparel|auto|restaurant|hotel|leisure|travel|home|consumer cyclical|e-commerce/, "Consumer Cyclical"],
  [/food|beverage|tobacco|household|grocer|consumer/, "Consumer Defensive"],
  [/aero|defen|airline|transport|machin|industrial|construct|logistic|rail|marine|building/, "Industrials"],
];

// The broad sector for an industry label, or null when it cannot be placed.
export function sectorFor(industry) {
  const label = String(industry || "").trim();
  if (!label) return null;
  const exact = SECTOR_BY_LABEL.get(label.toLowerCase());
  if (exact) return exact;
  const lower = label.toLowerCase();
  const hit = SECTOR_KEYWORDS.find(([pattern]) => pattern.test(lower));
  return hit ? hit[1] : null;
}

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
// marketCap). groupBy: "sector" (broad, Robinhood/TOS style; default) or
// "industry" (the trader's own labels). Returns cards biggest-first with
// "Other" last.
export function buildSectorGroups(symbols, quotes = {}, { groupBy = "sector" } = {}) {
  const seen = new Set();
  const byName = new Map();
  for (const raw of Array.isArray(symbols) ? symbols : []) {
    const symbol = String(raw || "").trim().toUpperCase();
    if (!symbol || seen.has(symbol)) continue;
    seen.add(symbol);
    const quote = quotes?.[symbol] || {};
    const industry = String(quote.industry || "").trim();
    const name = (groupBy === "industry" ? industry : sectorFor(industry)) || OTHER_GROUP;
    if (!byName.has(name)) byName.set(name, []);
    byName.get(name).push({
      symbol,
      industry: industry || null,
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

// Card height for `count` tiles at `width` px. Two floors: enough area per
// tile that an average one can print its ticker (narrow dock column), and a
// Robinhood-like shape so a wide card is a block, not a one-row strip.
export function cardHeight(count, width, expanded = false) {
  const tiles = Math.max(1, Number(count) || 1);
  const span = Math.max(120, Number(width) || 0);
  const byArea = Math.ceil((tiles * 4200) / span);
  const aspect = tiles >= 10 ? 0.45 : tiles >= 6 ? 0.38 : tiles >= 3 ? 0.3 : tiles === 2 ? 0.24 : 0.18;
  const byShape = Math.round(span * aspect);
  return Math.max(72, Math.min(expanded ? 720 : 340, Math.max(byArea, byShape)));
}

// Font sizes (px) for a tile: big tiles get big bold tickers, as on the
// Robinhood card, small ones shrink down to a readable floor.
export function tileFontSizes(width, height) {
  const symbol = Math.round(Math.max(9, Math.min(20, width / 4.2, height / 2.6)));
  return { symbol, pct: Math.max(8, Math.round(symbol * 0.85)) };
}

// What a tile of this pixel size has room to print.
export function tileLabelMode(width, height) {
  if (width >= 46 && height >= 34) return "full";
  if (width >= 30 && height >= 16) return "symbol";
  return "none";
}
