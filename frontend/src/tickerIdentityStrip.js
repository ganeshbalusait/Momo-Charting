// Pure logic for the header ticker identity strip:
//   State Street SPDR S&P 500 ETF Trust · 771.12 · -0.03% · BEAR ↓
// Kept out of App.jsx so the bias/format/merge rules stay unit-testable.

function finiteNumber(value) {
  if (value == null || value === "") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export function tickerStripBias(changePct) {
  const pct = finiteNumber(changePct);
  if (pct == null || pct === 0) return { label: "FLAT", tone: "flat", arrow: "→" };
  return pct > 0
    ? { label: "BULL", tone: "bull", arrow: "↑" }
    : { label: "BEAR", tone: "bear", arrow: "↓" };
}

export function formatTickerStripPrice(value) {
  const price = finiteNumber(value);
  if (price == null) return "--";
  return price.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Today's dollar move, shown next to the price: 498.74 +11.24 +2.31%
export function formatTickerStripChange(value) {
  const change = finiteNumber(value);
  if (change == null) return "--";
  return `${change > 0 ? "+" : change < 0 ? "-" : ""}${Math.abs(change).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function formatTickerStripPercent(value) {
  const pct = finiteNumber(value);
  if (pct == null) return "--%";
  return `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

export function normalizeTickerStripPayload(payload, symbol) {
  const normalizedSymbol = String(symbol || payload?.symbol || "").trim().toUpperCase();
  return {
    symbol: normalizedSymbol,
    name: String(payload?.name || "").trim(),
    lastPrice: finiteNumber(payload?.lastPrice),
    change: finiteNumber(payload?.change),
    changePct: finiteNumber(payload?.changePct),
    closePrice: finiteNumber(payload?.closePrice),
  };
}

// Live stream ticks only carry price; recompute the day change against the
// REST close so the percent stays in sync between polls.
export function mergeTickerStripLivePrice(strip, livePrice) {
  const price = finiteNumber(livePrice);
  if (!strip || price == null || price <= 0) return strip;
  const close = finiteNumber(strip.closePrice);
  const next = { ...strip, lastPrice: price };
  if (close != null && close > 0) {
    next.change = Number((price - close).toFixed(4));
    next.changePct = Number((((price - close) / close) * 100).toFixed(4));
  }
  const unchanged = ["lastPrice", "change", "changePct"].every((key) => Object.is(strip[key], next[key]));
  return unchanged ? strip : next;
}
