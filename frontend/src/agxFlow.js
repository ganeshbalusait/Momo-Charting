// AGX Flow page logic (2026-11-17): formatting and filtering for the options
// flow board read from GET /api/agx-flow. Pure functions, tested with node --test.

export const MAG7_BOARD = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "SPY", "QQQ"];
export const SIDE_FILTERS = ["both", "calls", "puts"];
export const MIN_PREMIUMS = [100_000, 500_000, 1_000_000];
export const POLL_MS = 60_000;
export const MAX_SYMBOLS = 20;
const SYMBOL = /^[A-Z][A-Z.-]{0,5}$/;

// "$1.2M", "$450K", "$900". Missing -> "--".
export function formatPremium(value) {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return "--";
  const abs = Math.abs(n);
  if (abs >= 1e9) return `$${trim(n / 1e9)}B`;
  if (abs >= 1e6) return `$${trim(n / 1e6)}M`;
  if (abs >= 1e3) return `$${Math.round(n / 1e3)}K`;
  return `$${Math.round(n)}`;
}

function trim(x) {
  return x >= 100 ? x.toFixed(0) : x.toFixed(1).replace(/\.0$/, "");
}

export function formatCount(value) {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return "--";
  return Math.round(n).toLocaleString("en-US");
}

// 185 -> "185", 172.5 -> "172.5"
export function formatStrike(strike) {
  const n = Number(strike);
  if (!Number.isFinite(n)) return "--";
  return Number.isInteger(n) ? String(n) : String(Number(n.toFixed(2)));
}

// "185 Call" / "170 Put"
export function contractLabel(row) {
  return `${formatStrike(row?.strike)} ${row?.side === "PUT" ? "Put" : "Call"}`;
}

// "2026-11-21" + 4 days -> "Nov 21 · 4d"
export function expiryLabel(expiry, days) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(expiry || ""));
  if (!match) return "--";
  const month = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(match[2]) - 1] || "";
  const d = Number(days);
  return `${month} ${Number(match[3])}${Number.isFinite(d) ? ` · ${d}d` : ""}`;
}

// The fill is a guess from where the last trade printed.
export function fillLabel(fill) {
  if (fill === "ask") return "Bought";
  if (fill === "bid") return "Sold";
  return "Mid";
}

export function formatDelta(delta) {
  const n = Number(delta);
  if (delta === null || delta === undefined || !Number.isFinite(n)) return "--";
  return n.toFixed(2);
}

export function formatVolOi(value) {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return "new";
  return `${n >= 10 ? n.toFixed(0) : n.toFixed(1)}x`;
}

// Call share of the premium, 0..100; 50 when there is none.
export function splitPct(callPremium, putPremium) {
  const c = Math.max(0, Number(callPremium) || 0);
  const p = Math.max(0, Number(putPremium) || 0);
  if (c + p <= 0) return 50;
  return Math.round((c / (c + p)) * 100);
}

export function filterRows(rows, { side = "both", minPremium = 0 } = {}) {
  return (Array.isArray(rows) ? rows : []).filter((row) => {
    if (side === "calls" && row?.side !== "CALL") return false;
    if (side === "puts" && row?.side !== "PUT") return false;
    return (Number(row?.premium) || 0) >= minPremium;
  });
}

// "aapl, nvda  tsla" -> ["AAPL","NVDA","TSLA"]; garbage dropped; at most 20.
export function parseTickers(text) {
  const out = [];
  for (const raw of String(text || "").toUpperCase().split(/[\s,]+/)) {
    if (SYMBOL.test(raw) && !out.includes(raw)) out.push(raw);
    if (out.length >= MAX_SYMBOLS) break;
  }
  return out;
}

// { pending, total } for the "Reading Schwab… (n of m)" line.
export function readingProgress(tickers) {
  const list = Array.isArray(tickers) ? tickers : [];
  const pending = list.filter((t) => t?.pending).length;
  return { pending, done: list.length - pending, total: list.length };
}

export function flowQuery(symbols) {
  return `/api/agx-flow?symbols=${encodeURIComponent(symbols.join(","))}`;
}

// A stable React key for a flow row.
export function rowKey(row) {
  return `${row?.ticker}|${row?.side}|${row?.expiry}|${row?.strike}`;
}

// Watchlist board (2026-11-17, "what about other tickers from watchlist"): Mag7 + SPY/QQQ first (read first), then
// the whole watchlist, no repeats, at most WATCHLIST_MAX.
export const WATCHLIST_MAX = 400;

export function watchlistBoard(watchlist) {
  const out = [];
  for (const raw of [...MAG7_BOARD, ...(Array.isArray(watchlist) ? watchlist : [])]) {
    const ticker = String(raw || "").trim().toUpperCase();
    if (SYMBOL.test(ticker) && !out.includes(ticker)) out.push(ticker);
    if (out.length >= WATCHLIST_MAX) break;
  }
  return out;
}

// With a big board the strip shows only the STRIP_MAX tickers with the most option dollars today.
export const STRIP_MAX = 12;

export function stripTickers(tickers) {
  const list = Array.isArray(tickers) ? tickers : [];
  if (list.length <= STRIP_MAX) return list;
  const total = (t) => (Number(t?.callPremium) || 0) + (Number(t?.putPremium) || 0);
  return [...list].sort((a, b) => total(b) - total(a)).slice(0, STRIP_MAX);
}
