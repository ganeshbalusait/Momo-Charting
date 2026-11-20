// LEAP OI Scanner: the stock's CURRENT price on every card (2026-11-19, "Add current prices also"). The board's copy of
// each chain is up to 12 hours old, so its stock price is too; the page asks /api/watchlist-quotes once a minute and
// shows the live price, the day's change, and "From price" worked out against it. When no live price came back, the
// card falls back to the price at the chain read and says so.

export const LIVE_PRICE_POLL_MS = 60 * 1000;

/** Unique tickers on screen (heroes, ENTRY rows, all rows, By ticker), upper-case, in first-seen order. */
export function tickersOnScreen(payload) {
  const seen = new Set();
  const add = (ticker) => { const t = String(ticker || "").trim().toUpperCase(); if (t) seen.add(t); };
  add(payload?.biggestCall?.ticker);
  add(payload?.biggestPut?.ticker);
  for (const list of [payload?.entries, payload?.rows, payload?.byTicker]) {
    for (const row of Array.isArray(list) ? list : []) add(row?.ticker);
  }
  return [...seen];
}

/** {TICKER: {price, changePct}} from a /api/watchlist-quotes reply; tickers with no usable price are left out. */
export function quotesFrom(reply) {
  const out = {};
  for (const row of Array.isArray(reply?.rows) ? reply.rows : []) {
    const price = Number(row?.lastPrice);
    if (!row?.symbol || row.lastPrice === null || row.lastPrice === undefined || !Number.isFinite(price) || price <= 0) continue;
    const pct = Number(row.changePct);
    out[String(row.symbol).toUpperCase()] = { price, changePct: row.changePct === null || row.changePct === undefined || !Number.isFinite(pct) ? null : pct };
  }
  return out;
}

/** The stock price to show for a row: {price, live, changePct}. Live when a quote came back, else the price at the read. */
export function stockPrice(row, quotes) {
  const quote = quotes?.[String(row?.ticker || "").toUpperCase()];
  if (quote) return { price: quote.price, live: true, changePct: quote.changePct };
  const spot = Number(row?.spot);
  return row?.spot === null || row?.spot === undefined || !Number.isFinite(spot) || spot <= 0
    ? { price: null, live: false, changePct: null }
    : { price: spot, live: false, changePct: null };
}

/** "54.60 (+1.2%)" live, "54.60 at read" from the copy, "—" when there is no price at all. */
export function stockText(stock) {
  if (!stock || stock.price === null) return "—";
  const price = stock.price.toFixed(2);
  if (!stock.live) return `${price} at read`;
  if (stock.changePct === null) return price;
  return `${price} (${stock.changePct > 0 ? "+" : ""}${stock.changePct.toFixed(1)}%)`;
}

/** "From price" against the stock price shown: (strike / price - 1) in percent, one decimal; null without both. */
export function awayFrom(row, stock) {
  const strike = Number(row?.strike);
  if (!stock || stock.price === null || !Number.isFinite(strike) || strike <= 0) return null;
  return Math.round((strike / stock.price - 1) * 1000) / 10;
}

/** The tone class for the day's change: "is-up", "is-down" or "". */
export function changeTone(stock) {
  if (!stock?.live || stock.changePct === null || stock.changePct === 0) return "";
  return stock.changePct > 0 ? "is-up" : "is-down";
}
