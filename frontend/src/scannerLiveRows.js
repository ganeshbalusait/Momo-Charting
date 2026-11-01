import { liveHighLowCell, livePctChange } from "./momxCells.js";

// A chart stream fallback arrives once a minute. Beyond 90s, show the actual
// board value instead of letting a disconnected quote keep controlling H/L.
export const LIVE_QUOTE_MAX_AGE_MS = 90000;

function timeMs(value) {
  if (typeof value === "number" && Number.isFinite(value)) return value < 1e11 ? value * 1000 : value;
  return Date.parse(value || "");
}

export function liveQuote(quote, nowMs = Date.now()) {
  const last = Number(quote?.last);
  let at = Date.parse(quote?.lastAt || "");
  // tradeTime describes last, unlike quoteTime (which can change on bid/ask).
  // A CHART_EQUITY fallback has its own lastAt; the held tradeTime is unrelated.
  const tradeAt = Number(quote?.tradeTime);
  if (quote?.lastSource !== "chart" && Number.isFinite(tradeAt) && tradeAt > 0) {
    at = Number.isFinite(at) ? Math.min(at, tradeAt) : tradeAt;
  }
  if (!(last > 0) || !Number.isFinite(last) || !Number.isFinite(at)
      || at > nowMs + 5000 || nowMs - at > LIVE_QUOTE_MAX_AGE_MS) return null;
  return { last, at };
}

/** One projector per board: bounds survive ticks and rebuilds in the SAME
 * forming 2h bar, then reset on a new bar. No historical rows are rewritten. */
export function createLiveRowProjector() {
  const cache = new Map();
  return (row, rawQuote, nowMs, pctSessionOk) => {
    if (!row?.symbol) return row;
    const quote = liveQuote(rawQuote, nowMs);
    const sourceAt = timeMs(row.marketDataAt);
    if (!quote || (Number.isFinite(sourceAt) && quote.at < sourceAt)) return row;
    const held = cache.get(row.symbol);
    const barAt = row.highLow?.barAt;
    const sameWindow = held && (barAt != null
      ? held.barAt === barAt : held.base === row);
    // Out-of-order replies cannot rewind the visible price or the range.
    const current = sameWindow && held.at > quote.at ? held.quote : quote;
    if (sameWindow && held.base === row && held.last === current.last && held.pctOk === pctSessionOk) {
      held.at = current.at;
      held.quote = current;
      return held.out;
    }
    const cell = sameWindow && held.highLow && row.highLow
      ? { ...row.highLow, hh: Math.max(row.highLow.hh, held.highLow.hh), ll: Math.min(row.highLow.ll, held.highLow.ll) }
      : row.highLow;
    const highLow = liveHighLowCell(cell, current.last);
    const pctChange = livePctChange(row.prevClose, current.last, pctSessionOk);
    const out = highLow || pctChange != null
      ? { ...row, ...(highLow ? { highLow } : {}), ...(pctChange != null ? { pctChange } : {}) }
      : row;
    cache.set(row.symbol, { base: row, barAt, highLow, at: current.at, quote: current,
      last: current.last, pctOk: pctSessionOk, out });
    return out;
  };
}
