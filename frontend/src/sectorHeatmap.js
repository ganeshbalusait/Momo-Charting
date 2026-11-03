// Sector heatmap, Robinhood "Sectors" style (trader ask, 2026-11-03): one card
// per sector holding that sector's top stocks, tiles sized by market cap,
// colored by today's move, headline % = the sector ETF. Everything here is pure
// so the panel's maths can be tested without a browser: the cards, color
// steps, and the two layouts (Robinhood rows, thinkorswim treemap).

// Read off the Robinhood card: AVGO +3.15% and META +2.76% are both the full
// green, AAPL -2.04% is already the full orange.
export const HEAT_CAP_PCT = 2.5;
export const DOWN_CAP_PCT = 2;
// Tiles shown in a collapsed card; the rest fold into one "+N" tile. The
// Robinhood card shows 16.
export const COLLAPSED_TILE_LIMIT = 16;

// The sector cards, Robinhood-style (trader, 2026-11-03: Robinhood shows "each
// sector multiple tickers" -- every card is that sector's top stocks, not just
// the watchlist). Each card: its name, the SPDR sector ETF whose move is the
// card's headline %, and its ~16 largest names with an approximate market cap
// in $B. The cap only sizes the tile until the real one arrives from the
// market-cap cache (momx/industry_lookup.py); tile area is sqrt(cap) anyway,
// so an estimate a few months stale changes nothing visible.
//
// Technology is exactly the card in the trader's Robinhood screenshot, which
// (unlike GICS) counts GOOGL/GOOG, AMZN and META as tech; the other cards
// therefore leave those names out so no ticker appears twice.
export const SECTOR_CARDS = [
  { name: "Technology", etf: "XLK", leaders: "NVDA:4400 AAPL:3900 MSFT:3800 GOOGL:2200 GOOG:2200 AMZN:2400 TSM:1200 META:1800 AVGO:1600 SKHY:500 MU:140 AMD:310 ASML:300 TCEHY:600 PLTR:420 CSCO:250" },
  { name: "Financials", etf: "XLF", leaders: "JPM:820 V:680 MA:530 BAC:390 WFC:270 GS:240 MS:230 AXP:220 C:190 SCHW:170 BLK:170 SPGI:160 PGR:150 COF:140 HOOD:110 COIN:90" },
  { name: "Healthcare", etf: "XLV", leaders: "LLY:900 JNJ:460 ABBV:390 UNH:300 ABT:230 MRK:230 TMO:200 ISRG:190 AMGN:170 BSX:150 PFE:145 GILD:140 SYK:140 DHR:140 VRTX:120 NVO:240" },
  { name: "Consumer Cyclical", etf: "XLY", leaders: "TSLA:1400 HD:380 MCD:220 BKNG:170 TJX:150 LOW:140 SBUX:100 NKE:95 ABNB:80 ORLY:80 CMG:60 MAR:75 RCL:85 DASH:110 GM:60 F:45" },
  { name: "Communication", etf: "XLC", leaders: "NFLX:510 TMUS:250 DIS:200 T:190 VZ:170 CMCSA:130 SPOT:130 RBLX:90 TTWO:45 EA:50 CHTR:50 WBD:40 RDDT:40 BIDU:35 LYV:33 TKO:30" },
  { name: "Industrials", etf: "XLI", leaders: "GE:300 CAT:250 RTX:230 UBER:190 BA:170 HON:130 UNP:140 GEV:170 ETN:140 DE:130 LMT:110 ADP:120 PH:95 UPS:80 WM:90 NOC:85" },
  { name: "Consumer Defensive", etf: "XLP", leaders: "WMT:830 COST:420 PG:360 KO:300 PM:250 PEP:200 MO:100 MDLZ:80 CL:70 TGT:45 KMB:40 KDP:45 MNST:65 KHC:30 GIS:30 HSY:35" },
  { name: "Energy", etf: "XLE", leaders: "XOM:490 CVX:270 COP:120 EOG:65 WMB:70 KMI:60 EPD:70 SLB:50 ET:60 MPC:55 PSX:55 OXY:45 VLO:45 BKR:45 HAL:22 FANG:42" },
  { name: "Utilities", etf: "XLU", leaders: "NEE:160 CEG:110 SO:100 DUK:95 VST:65 AEP:60 SRE:55 D:50 EXC:45 PCG:35 XEL:42 PEG:42 ED:36 ETR:38 WEC:35 NRG:30" },
  { name: "Real Estate", etf: "XLRE", leaders: "PLD:105 WELL:110 AMT:95 EQIX:80 SPG:60 O:52 DLR:55 PSA:50 CCI:45 CBRE:48 VICI:35 EXR:30 IRM:30 AVB:30 EQR:26 VTR:32" },
  { name: "Materials", etf: "XLB", leaders: "LIN:220 SHW:85 NEM:90 ECL:75 FCX:60 APD:65 CRH:70 CTVA:42 NUE:32 VMC:37 MLM:35 DD:32 PPG:26 DOW:18 IFF:20 ALB:10" },
].map((card) => ({
  ...card,
  leaders: card.leaders.split(" ").map((pair) => {
    const [symbol, capB] = pair.split(":");
    return { symbol, cap: Number(capB) * 1e9 };
  }),
}));

// Every symbol the panel needs quoted: all leaders plus the sector ETFs.
export const SECTOR_SYMBOLS = [...new Set(SECTOR_CARDS.flatMap((card) => [...card.leaders.map((leader) => leader.symbol), card.etf]))];

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

// quotes: { SYMBOL: row } from /api/watchlist-quotes?profile=1 (lastPrice,
// change, changePct, marketCap). Returns the cards in Robinhood's order.
//
// hideUnquoted: once quotes have arrived, a leader the quote feed has no price
// for (a new listing, an OTC ADR) is left out instead of drawn as a dead grey
// tile. Before the first response everything is shown, grey.
export function buildMarketSectorGroups(quotes = {}, { hideUnquoted = true, cards = SECTOR_CARDS } = {}) {
  const loaded = Object.keys(quotes || {}).length > 0;
  return cards.map((card) => {
    const tiles = card.leaders
      .map(({ symbol, cap }) => {
        const quote = quotes?.[symbol] || {};
        const liveCap = positive(quote.marketCap);
        const capWeight = liveCap ?? cap;
        return {
          symbol,
          changePct: finite(quote.changePct),
          change: finite(quote.change),
          lastPrice: finite(quote.lastPrice),
          marketCap: capWeight,
          estimatedCap: liveCap === null,
          capWeight,
          // Tile AREA follows sqrt(cap), as the Robinhood card visibly does
          // (MSFT and GOOGL are far closer in size than their caps).
          weight: Math.sqrt(capWeight),
        };
      })
      .filter((tile) => !(hideUnquoted && loaded && tile.lastPrice === null))
      .sort((left, right) => right.weight - left.weight || left.symbol.localeCompare(right.symbol));

    const priced = tiles.filter((tile) => tile.changePct !== null);
    const pricedWeight = priced.reduce((sum, tile) => sum + tile.capWeight, 0);
    const tilesMove = pricedWeight > 0
      ? priced.reduce((sum, tile) => sum + tile.changePct * tile.capWeight, 0) / pricedWeight
      : null;
    // Headline % = the sector ETF's move today (the whole sector, as Robinhood
    // shows it); the cap-weighted move of the tiles only if the ETF has no quote.
    const etfMove = finite(quotes?.[card.etf]?.changePct);
    return {
      name: card.name,
      etf: card.etf,
      tiles,
      changePct: etfMove ?? tilesMove,
      moveSource: etfMove !== null ? "etf" : tilesMove !== null ? "tiles" : "none",
      up: priced.filter((tile) => tile.changePct > 0).length,
      down: priced.filter((tile) => tile.changePct < 0).length,
    };
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
  // Five steps, matched to the Robinhood card: GOOGL +0.29% pale, AMZN +1.17%
  // light, MSFT +1.90% / TSM +2.02% mid, NVDA +3.17% full.
  // Robinhood's orange saturates sooner than its green (AAPL -2.04% is full
  // orange while MSFT +1.90% / TSM +2.02% are not yet full green).
  const full = pct > 0 ? HEAT_CAP_PCT : DOWN_CAP_PCT;
  const level = size >= full ? 5 : size >= 1.5 ? 4 : size >= 1 ? 3 : size >= 0.5 ? 2 : 1;
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

// --- Robinhood row layout --------------------------------------------------
// The Robinhood card is not a squarified treemap: it is full-width ROWS, the
// biggest names in a tall top row, then shorter rows of smaller names (16 tiles
// = 5 / 5 / 6), each row sorted biggest-first left to right. Row height follows
// the row's total weight, tile width the tile's share of its row.

export function rowCounts(count, width) {
  const n = Math.max(0, Math.floor(Number(count) || 0));
  if (!n) return [];
  const maxPerRow = Math.max(2, Math.min(7, Math.floor((Number(width) || 0) / 50)));
  const rows = Math.min(n, Math.max(Math.ceil(n / maxPerRow), n <= 5 ? 1 : n <= 10 ? 2 : 3));
  const base = Math.floor(n / rows);
  const extra = n % rows;
  // Later (shorter) rows take the extra tiles: their names are smaller.
  return Array.from({ length: rows }, (_, index) => base + (index >= rows - extra ? 1 : 0));
}

export function rowLayout(items, rect, { minRowHeight = 30 } = {}) {
  const width = Math.max(0, Number(rect?.width) || 0);
  const height = Math.max(0, Number(rect?.height) || 0);
  const list = (Array.isArray(items) ? items : [])
    .filter((item) => Number(item?.weight) > 0)
    .sort((left, right) => right.weight - left.weight);
  if (!list.length || width <= 0 || height <= 0) return [];
  const originX = Number(rect?.x) || 0;
  const originY = Number(rect?.y) || 0;

  const rows = [];
  let cursor = 0;
  for (const size of rowCounts(list.length, width)) {
    rows.push(list.slice(cursor, cursor + size));
    cursor += size;
  }
  const sums = rows.map((row) => row.reduce((total, item) => total + item.weight, 0));
  const total = sums.reduce((a, b) => a + b, 0);
  let heights = sums.map((sum) => (height * sum) / total);
  // Lift rows below the floor (so the bottom row can still print a ticker)
  // and take the difference proportionally from the rows above the floor.
  const floor = Math.min(minRowHeight, height / rows.length);
  for (let pass = 0; pass < rows.length; pass += 1) {
    const low = heights.map((h) => h < floor - 1e-9);
    if (!low.some(Boolean)) break;
    const fixed = low.filter(Boolean).length * floor;
    const freeSum = sums.reduce((acc, sum, index) => acc + (low[index] ? 0 : sum), 0);
    heights = heights.map((h, index) => (low[index] ? floor : ((height - fixed) * sums[index]) / freeSum));
  }

  const out = [];
  let y = originY;
  rows.forEach((row, rowIndex) => {
    const rowHeight = rowIndex === rows.length - 1 ? originY + height - y : heights[rowIndex];
    let x = originX;
    row.forEach((item, index) => {
      const tileWidth = index === row.length - 1 ? originX + width - x : (width * item.weight) / sums[rowIndex];
      out.push({ item, x, y, width: tileWidth, height: rowHeight, row: rowIndex });
      x += tileWidth;
    });
    y += rowHeight;
  });
  return out;
}

// Card height for `count` tiles at `width` px. Two floors: enough area per
// tile that an average one can print its ticker (narrow dock column), and a
// Robinhood-like shape so a wide card is a block, not a one-row strip.
export function cardHeight(count, width, expanded = false) {
  const tiles = Math.max(1, Number(count) || 1);
  const span = Math.max(120, Number(width) || 0);
  const byArea = Math.ceil((tiles * 4200) / span);
  const aspect = tiles >= 10 ? 0.5 : tiles >= 6 ? 0.38 : tiles >= 3 ? 0.3 : tiles === 2 ? 0.24 : 0.18;
  const byShape = Math.round(span * aspect);
  return Math.max(72, Math.min(expanded ? 720 : 340, Math.max(byArea, byShape)));
}

// Font sizes (px) for a tile: big tiles get big bold tickers, as on the
// Robinhood card, small ones shrink down to a readable floor.
export function tileFontSizes(width, height, style = "tos") {
  if (style === "rh") {
    // Robinhood: ticker and % the same size, ticker bold, % regular.
    const symbol = Math.round(Math.max(10, Math.min(18, width / 4.4, height / 3.2)));
    return { symbol, pct: symbol };
  }
  const symbol = Math.round(Math.max(9, Math.min(20, width / 4.2, height / 2.6)));
  return { symbol, pct: Math.max(8, Math.round(symbol * 0.85)) };
}

// What a tile of this pixel size has room to print. Robinhood drops the % on
// its short bottom row (MU, AMD, ASML... print the ticker only).
export function tileLabelMode(width, height, style = "tos") {
  if (width >= 46 && height >= (style === "rh" ? 52 : 34)) return "full";
  if (width >= 30 && height >= 16) return "symbol";
  return "none";
}
