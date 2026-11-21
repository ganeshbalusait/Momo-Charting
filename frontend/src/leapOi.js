// LEAP OI Scanner page helpers (2026-11-17): pure functions so node --test can check them.

export const MAG7 = ["AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA"];
export const MAX_TICKERS = 400; // Mag7 + the whole watchlist (2026-11-17: "what about other tickers from watchlist")

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
// Two separate boards (2026-11-17 "give me separate mag7 and watchlist not mag7 + watchlist"): "mag7" is the seven
// names; "watchlist" is the watchlist WITHOUT them, so Mag7's huge open interest does not fill its lists.
export function boardTickers(board, watchlist) {
  const list = board === "mag7" ? MAG7 : (Array.isArray(watchlist) ? watchlist : [])
    .filter((raw) => !MAG7.includes(String(raw || "").trim().toUpperCase()));
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

// Mid price (2026-11-17 "give me mid prize also"): halfway between bid and ask, to the cent. null when either side is
// missing or the ask is 0 (no real market).
export function midPrice(bid, ask) {
  if (bid === null || bid === undefined || ask === null || ask === undefined) return null;
  const b = Number(bid);
  const a = Number(ask);
  if (!Number.isFinite(b) || !Number.isFinite(a) || a <= 0 || b < 0) return null;
  return Math.round(((b + a) / 2) * 100) / 100;
}

export function midText(row) {
  return priceText(midPrice(row?.bid, row?.ask));
}

// What one contract costs at the mid: mid x 100 shares. "$278", "$1,240", or a dash.
export function costText(row) {
  const mid = midPrice(row?.bid, row?.ask);
  return mid === null ? "—" : `$${Math.round(mid * 100).toLocaleString("en-US")}`;
}

// When the bid, ask and delta were read (2026-11-20: the NVDA card at 5:39 PM showed Mid 1.95 from a 12:53 PM read,
// before NVDA fell 7%). Open interest is once a day, but option prices move, so a read more than 15 minutes old says
// its time: "Prices from 12:53 PM ET", or "Prices from Thu 3:58 PM ET" for another day. Empty when fresh or unknown.
export const PRICE_AGE_NOTE_MS = 15 * 60 * 1000;

export function priceAgeText(row, nowMs = Date.now()) {
  const read = new Date(row?.readAt || "");
  if (Number.isNaN(read.getTime()) || nowMs - read.getTime() < PRICE_AGE_NOTE_MS) return "";
  const zone = { timeZone: "America/New_York" };
  const time = read.toLocaleTimeString("en-US", { ...zone, hour: "numeric", minute: "2-digit" });
  const sameDay = read.toLocaleDateString("en-US", zone) === new Date(nowMs).toLocaleDateString("en-US", zone);
  const day = sameDay ? "" : `${read.toLocaleDateString("en-US", { ...zone, weekday: "short" })} `;
  return `Prices from ${day}${time} ET`;
}

// How wide the market is: (ask - bid) as a percent of the mid. "5.4%" or a dash.
export function spreadText(row) {
  const mid = midPrice(row?.bid, row?.ask);
  if (mid === null || mid <= 0) return "—";
  return `${(((Number(row.ask) - Number(row.bid)) / mid) * 100).toFixed(1)}%`;
}

// Open interest change since the previous day's read (2026-11-17 "All six"): "+12,400", "−3,100", "no change", or ""
// when there is no earlier day to compare with (never a made-up 0).
export function oiChangeText(change) {
  if (change === null || change === undefined) return "";
  const n = Number(change);
  if (!Number.isFinite(n)) return "";
  if (n === 0) return "no change";
  return `${n > 0 ? "+" : "−"}${Math.abs(Math.round(n)).toLocaleString("en-US")}`;
}

export function oiChangeTone(change) {
  const n = Number(change);
  if (change === null || change === undefined || !Number.isFinite(n) || n === 0) return "is-flat";
  return n > 0 ? "is-up" : "is-down";
}

// "since Nov 17" from the earlier day's read time, for the OI change label.
export function sinceText(stamp) {
  const d = new Date(stamp || "");
  if (Number.isNaN(d.getTime())) return "since yesterday";
  return `since ${d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "America/New_York" })}`;
}

// Tap a contract (2026-11-17 "All six"): the facts card. Break-even at expiry (call: strike + mid, put: strike - mid)
// and how far the stock has to move to get there, from the price the row was read at. Facts only, not a forecast.
export function contractFacts(row) {
  const mid = midPrice(row?.bid, row?.ask);
  const strike = Number(row?.strike);
  const spot = Number(row?.spot);
  const put = row?.side === "PUT";
  const breakeven = mid === null || !Number.isFinite(strike) ? null : Math.round((put ? strike - mid : strike + mid) * 100) / 100;
  const move = breakeven === null || !Number.isFinite(spot) || spot <= 0 ? null : Math.round(((breakeven / spot) - 1) * 1000) / 10;
  return { mid, breakeven, move, cost: mid === null ? null : Math.round(mid * 100) };
}

export function moveText(move) {
  if (move === null || move === undefined || !Number.isFinite(Number(move))) return "—";
  const n = Number(move);
  return `${n > 0 ? "+" : n < 0 ? "−" : ""}${Math.abs(n).toFixed(1)}%`;
}

// Recent searches (2026-11-17 "All six"): newest first, no repeats, at most RECENT_MAX. Pure; the page keeps the
// list in this browser only.
export const RECENT_MAX = 5;
export const RECENT_KEY = "agxTickerSearchRecent";

export function addRecent(list, ticker) {
  const clean = cleanTicker(ticker);
  const rest = (Array.isArray(list) ? list : []).map((t) => cleanTicker(t)).filter((t) => t && t !== clean);
  return (clean ? [clean, ...rest] : rest).filter((t, i, all) => all.indexOf(t) === i).slice(0, RECENT_MAX);
}

export function readRecent() {
  try {
    return addRecent(JSON.parse(window.localStorage.getItem(RECENT_KEY) || "[]"), "");
  } catch {
    return [];
  }
}

export function saveRecent(list) {
  try { window.localStorage.setItem(RECENT_KEY, JSON.stringify(list)); } catch { /* private window */ }
}

// By-ticker order (2026-11-17 "who is dominating"): A-Z, or the most call-heavy / put-heavy tickers first.
export const BYTK_SORTS = [
  { id: "az", label: "A–Z" },
  { id: "calls", label: "Most call-heavy" },
  { id: "puts", label: "Most put-heavy" },
];

export function sortByTicker(list, mode) {
  const rows = Array.isArray(list) ? [...list] : [];
  if (mode !== "calls" && mode !== "puts") return rows.sort((a, b) => String(a.ticker).localeCompare(String(b.ticker)));
  const share = (line) => {
    const c = Number(line?.callOi) || 0;
    const p = Number(line?.putOi) || 0;
    if (!(c + p)) return -1;                                // nothing open: last
    return (mode === "calls" ? c : p) / (c + p);
  };
  const total = (line) => (Number(line?.callOi) || 0) + (Number(line?.putOi) || 0);
  return rows.sort((a, b) => share(b) - share(a) || total(b) - total(a) || String(a.ticker).localeCompare(String(b.ticker)));
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
  const base = `Reading Schwab… ${tickers.length - waiting} of ${tickers.length} tickers ready`;
  // The whole watchlist is read one ticker every ~3.5 s (gentle on Schwab), once a day.
  return waiting > 10 ? `${base} · about ${Math.max(1, Math.round((waiting * 3.5) / 60))} min left, once a day` : base;
}

// Who has more, calls or puts (2026-11-17 "who is dominating call or put?"). A side "leads" from 55% of the total;
// closer than that reads as even. A fact about what is open or traded, never a direction call.
export function dominance(calls, puts) {
  const c = Math.max(0, Number(calls) || 0);
  const p = Math.max(0, Number(puts) || 0);
  const total = c + p;
  if (!total) return { callPct: 50, putPct: 50, leader: "none", text: "No calls or puts yet" };
  const callPct = Math.round((c / total) * 100);
  const putPct = 100 - callPct;
  if (callPct >= 55) return { callPct, putPct, leader: "calls", text: `Calls lead · ${callPct}%` };
  if (putPct >= 55) return { callPct, putPct, leader: "puts", text: `Puts lead · ${putPct}%` };
  return { callPct, putPct, leader: "even", text: `About even · calls ${callPct}% / puts ${putPct}%` };
}

// Board totals from the By-ticker lines.
export function boardTotals(byTicker) {
  const list = Array.isArray(byTicker) ? byTicker : [];
  return {
    calls: list.reduce((sum, line) => sum + (Number(line?.callOi) || 0), 0),
    puts: list.reduce((sum, line) => sum + (Number(line?.putOi) || 0), 0),
  };
}

// "Left out: 34 ETFs · 41 stocks under $10" from the board's excluded list (2026-11-17 "No need spy, qqq and small
// stocks and etf"). Empty when nothing was left out. Shared by LEAP OI Scanner and AGX Flow.
export function leftOutText(excluded, minPrice = 10) {
  const list = Array.isArray(excluded) ? excluded : [];
  const etfs = list.filter((x) => x?.reason === "ETF").length;
  const small = list.length - etfs;
  const parts = [];
  if (etfs) parts.push(`${etfs} ETF${etfs === 1 ? "" : "s"}`);
  if (small) parts.push(`${small} stock${small === 1 ? "" : "s"} under $${Math.round(Number(minPrice) || 10)}`);
  return parts.length ? `Left out: ${parts.join(" · ")}` : "";
}

// Search any ticker (2026-11-17 "Add search ticker also"): "nvda", " $NVDA " -> "NVDA"; "" when it is not a ticker.
// Same shape the server accepts (letters, . and -, up to 6), so a search never asks for something it will drop.
const SEARCH_TICKER = /^[A-Z][A-Z.-]{0,5}$/;
export function cleanTicker(text) {
  const ticker = String(text ?? "").trim().replace(/^\$/, "").trim().toUpperCase();
  return SEARCH_TICKER.test(ticker) ? ticker : "";
}

// Why a searched ticker shows nothing, in plain words ("" when it has something to show, or is still being read).
// Shared by LEAP OI Scanner and AGX Flow; `page` names the page in the sentence.
export function searchNote(payload, ticker, page = "This page") {
  if (!payload || !ticker) return "";
  const left = (payload.excluded || []).find((x) => x?.ticker === ticker);
  if (left) {
    return left.reason === "ETF"
      ? `${ticker} is an ETF. ${page} shows stocks only.`
      : `${ticker} is under $${Math.round(Number(payload.minPrice) || 10)}, so ${page} leaves it off.`;
  }
  const line = (payload.tickers || []).find((x) => x?.ticker === ticker);
  if (!line) return "";
  if (line.state === "error") return line.error || `Schwab did not answer for ${ticker}.`;    // LEAP OI Scanner
  if (line.state === undefined && line.error && !line.fetchedAt) return line.error;          // AGX Flow, nothing read
  if (line.state === "ready" && !line.rows) return `No ${ticker} strikes match these filters. Try another Months out, Delta or Side.`;
  return "";
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

// By-ticker list (2026-11-17, "what about other tickers from watchlist"): search by ticker start, then show at most
// `limit` lines unless the trader asks for all.

// Every ticker shows (2026-11-17 "Still I don't see full loading": a 40-ticker preview behind "Show all" read as not loaded).
export function filterByTicker(list, query) {
  const rows = Array.isArray(list) ? list : [];
  const q = String(query || "").trim().toUpperCase();
  const matched = q ? rows.filter((line) => String(line?.ticker || "").startsWith(q)) : rows;
  return { shown: matched, total: matched.length };
}
