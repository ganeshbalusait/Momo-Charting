// LEAP OI Scanner page helpers (2026-11-17): pure functions so node --test can check them.

export const MAG7 = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"];
export const MAX_TICKERS = 25;

export const RANGES = [
  { id: "leap", label: "LEAP 3–12", from: 3, to: 12 },
  { id: "all", label: "1–12", from: 1, to: 12 },
  { id: "near", label: "1–3", from: 1, to: 3 },
];

export const DELTAS = [
  { id: "min", label: "0.10 and up", minDelta: 0.1, entryOnly: false },
  { id: "entry", label: "ENTRY only", minDelta: 0.1, entryOnly: true },
  { id: "mid", label: "0.20 and up", minDelta: 0.2, entryOnly: false },
];

export const SIDES = [
  { id: "both", label: "Both" },
  { id: "calls", label: "Calls" },
  { id: "puts", label: "Puts" },
];

// Mag7 first, then the watchlist, no repeats, at most MAX_TICKERS.
export function boardTickers(board, watchlist) {
  const list = board === "mag7" ? MAG7 : [...MAG7, ...(Array.isArray(watchlist) ? watchlist : [])];
  const out = [];
  for (const raw of list) {
    const ticker = String(raw || "").trim().toUpperCase();
    if (/^[A-Z][A-Z.-]{0,5}$/.test(ticker) && !out.includes(ticker)) out.push(ticker);
  }
  return out.slice(0, MAX_TICKERS);
}

export function scannerUrl({ tickers, range, delta, side }) {
  const r = RANGES.find((item) => item.id === range) || RANGES[0];
  const d = DELTAS.find((item) => item.id === delta) || DELTAS[0];
  const params = new URLSearchParams({
    symbols: tickers.join(","),
    from: String(r.from),
    to: String(r.to),
    side: SIDES.some((item) => item.id === side) ? side : "both",
    min_delta: d.minDelta.toFixed(2),
  });
  if (d.entryOnly) params.set("entry_only", "1");
  return `/api/leap-oi-scanner?${params.toString()}`;
}

export function formatStrike(strike) {
  const n = Number(strike);
  if (!Number.isFinite(n)) return "";
  return Number.isInteger(n) ? String(n) : String(Number(n.toFixed(2)));
}

export function contractLabel(row) {
  if (!row) return "";
  return `${formatStrike(row.strike)} ${row.side === "PUT" ? "Put" : "Call"}`;
}

// Same as the chat: 0.195..0.20 shows three places so 0.198 never reads as 0.20 (ENTRY is UNDER 0.20).
export function deltaText(delta) {
  if (delta === null || delta === undefined || !Number.isFinite(Number(delta))) return "—";
  const d = Number(delta);
  const size = Math.abs(d);
  return size >= 0.195 && size < 0.2 ? d.toFixed(3) : d.toFixed(2);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function expiryLabel(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
  if (!m) return "";
  return `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])} '${m[1].slice(2)}`;
}

export function formatOi(oi) {
  const n = Number(oi);
  return Number.isFinite(n) ? n.toLocaleString("en-US") : "—";
}

export function priceText(value) {
  const n = Number(value);
  return value === null || value === undefined || !Number.isFinite(n) ? "—" : n.toFixed(2);
}

export function awayText(away) {
  const n = Number(away);
  if (away === null || away === undefined || !Number.isFinite(n)) return "";
  return `${n > 0 ? "+" : ""}${n.toFixed(1)}%`;
}

// OI bar width against the biggest row on screen, 4..100.
export function barWidth(oi, top) {
  const n = Number(oi);
  const t = Number(top);
  if (!Number.isFinite(n) || !Number.isFinite(t) || t <= 0) return 4;
  return Math.max(4, Math.min(100, Math.round((n / t) * 100)));
}

export function progressText(payload) {
  const tickers = Array.isArray(payload?.tickers) ? payload.tickers : [];
  const waiting = tickers.filter((t) => t.state === "pending").length;
  if (!tickers.length || !waiting) return "";
  return `Reading Schwab… ${tickers.length - waiting} of ${tickers.length} tickers ready`;
}

// "Read 8:31 AM ET" from the oldest ready ticker's read time.
export function readAtText(payload) {
  const times = (payload?.tickers || [])
    .filter((t) => t.state === "ready" && t.readAt)
    .map((t) => new Date(t.readAt))
    .filter((d) => !Number.isNaN(d.getTime()));
  if (!times.length) return "";
  const oldest = new Date(Math.min(...times.map((d) => d.getTime())));
  const text = oldest.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" });
  return `Read from Schwab ${text} ET`;
}
