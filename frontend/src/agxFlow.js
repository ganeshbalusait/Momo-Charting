// AGX Flow page logic (2026-11-17): formatting and filtering for the options
// flow board read from GET /api/agx-flow. Pure functions, tested with node --test.

// Stocks only (2026-11-17 "No need spy, qqq and small stocks and etf"): no SPY/QQQ; the server also drops ETFs and
// stocks under $10 from every board.
export const MAG7_BOARD = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"];
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

// A ticker being re-read that already has numbers is "updating", not "waiting" (2026-11-17 "Not loading properly":
// every 5 minutes each card flipped to "reading…" while still showing its numbers).
export function hasData(t) {
  return Boolean(t?.fetchedAt);
}

// pending = tickers with no numbers yet (the "Reading Schwab… (n of m)" line); updating = being re-read, numbers shown.
export function readingProgress(tickers) {
  const list = Array.isArray(tickers) ? tickers : [];
  const pending = list.filter((t) => t?.pending && !hasData(t)).length;
  const updating = list.filter((t) => t?.pending && hasData(t)).length;
  return { pending, updating, done: list.length - pending, total: list.length };
}

// "Reading Schwab… (n of m)", plus roughly how long is left when many tickers wait (two readers, ~2 s a ticker).
export function readingText(progress) {
  if (!progress?.pending) return "";
  const base = `Reading Schwab… (${progress.done} of ${progress.total})`;
  return progress.pending > 15 ? `${base} · about ${Math.max(1, Math.round((progress.pending * 2) / 60))} min left` : base;
}

// "Updated 1:25 PM ET" from the oldest read on the board.
export function updatedText(tickers) {
  const times = (Array.isArray(tickers) ? tickers : [])
    .map((t) => new Date(t?.fetchedAt || ""))
    .filter((d) => !Number.isNaN(d.getTime()));
  if (!times.length) return "";
  const oldest = new Date(Math.min(...times.map((d) => d.getTime())));
  return `Updated ${oldest.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" })} ET`;
}

// Expiry and order (2026-11-17 "All six"): the server applies both before it picks the top 60, so a short-dated
// filter is not left with the few rows that happened to make the all-expiry top 60. 2026-11-19 "I see only 30 days
// add up to 12 months": the board reads 12 months out, with bands past 30 days so LEAP flow is easy to find.
export const EXPIRY_FILTERS = [
  { id: "all", label: "All" }, { id: "week", label: "This week" }, { id: "next", label: "Next week" }, { id: "month", label: "30 days" },
  { id: "m3", label: "1–3 months" }, { id: "m6", label: "3–6 months" }, { id: "m12", label: "6–12 months" },
];
export const ORDERS = [{ id: "premium", label: "Biggest $" }, { id: "hot", label: "Hot right now" }];

export function flowQuery(symbols, { expiry = "all", order = "premium" } = {}) {
  let url = `/api/agx-flow?symbols=${encodeURIComponent(symbols.join(","))}`;
  if (expiry && expiry !== "all") url += `&expiry=${encodeURIComponent(expiry)}`;
  if (order && order !== "premium") url += `&order=${encodeURIComponent(order)}`;
  return url;
}

// A stable React key for a flow row.
export function rowKey(row) {
  return `${row?.ticker}|${row?.side}|${row?.expiry}|${row?.strike}`;
}

// Watchlist board (2026-11-17, "what about other tickers from watchlist"): Mag7 first (read first), then
// the whole watchlist, no repeats, at most WATCHLIST_MAX.
export const WATCHLIST_MAX = 400;

export function watchlistBoard(watchlist) {
  const out = [];
  // Separate boards (2026-11-17 "give me separate mag7 and watchlist"): the watchlist without the Mag7 names.
  for (const raw of Array.isArray(watchlist) ? watchlist : []) {
    const ticker = String(raw || "").trim().toUpperCase();
    if (SYMBOL.test(ticker) && !MAG7_BOARD.includes(ticker) && !out.includes(ticker)) out.push(ticker);
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
