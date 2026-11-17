// Ticker trader (2026-11-13): helpers for the screen. Pure, so node:test can run them.
// The trading itself is done on the server (momx/ticker_trader.py), simulated only.

export const TICKER_TRADER_URL = "/api/momx-scanner/ticker-trader";
export const TICKER_TRADER_POLL_MS = 10000;
export const TICKER_TRADER_KEY = "bot.tickerTrader";
export const TICKER_CHIPS = ["SPY", "QQQ", "NVDA", "TSLA", "AAPL", "AMD"];
export const SIMULATED_NOTE = "Simulated inside the app. No order goes to any broker.";

const TICKER = /^[A-Z.]{1,6}$/;

/** What he typed, as the server wants it: capitals, no spaces, no leading $. */
export function cleanTicker(raw) {
  return String(raw ?? "").trim().replace(/^\$/, "").toUpperCase();
}

/** "" when the ticker can be sent, else the plain-words reason. */
export function tickerProblem(raw) {
  const ticker = cleanTicker(raw);
  if (!ticker) return "Type a ticker first.";
  return TICKER.test(ticker) ? "" : "A ticker is letters only, up to 6 (for example NVDA).";
}

export const sideLabel = (side) => (String(side || "").toUpperCase() === "PUT" ? "Put" : "Call");

/** "$10,186" - whole dollars, as on the tiles. */
export function money(value) {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return "–";
  return (n < 0 ? "−$" : "$") + Math.abs(Math.round(n)).toLocaleString("en-US");
}

/** "+$186" / "−$142" */
export function signedMoney(value) {
  const n = Number(value);
  if (value === null || value === undefined || !Number.isFinite(n)) return "–";
  const whole = Math.round(n);
  return (whole > 0 ? "+$" : whole < 0 ? "−$" : "$") + Math.abs(whole).toLocaleString("en-US");
}

export function price(value) {
  const n = Number(value);
  return value === null || value === undefined || !Number.isFinite(n) ? "–" : n.toFixed(2);
}

export const tone = (value) => (Number(value) > 0 ? "is-up" : Number(value) < 0 ? "is-down" : "");

/** "10:05 ET" from the server's ET stamp (its own clock digits: no time-zone maths). */
export function timeEt(stamp) {
  const m = /T(\d{2}):(\d{2})/.exec(String(stamp || ""));
  if (!m) return "";
  return `${Number(m[1])}:${m[2]} ET`;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "Nov 13" from "2026-11-13..." */
export function dayLabel(stamp) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(stamp || ""));
  if (!m || !MONTHS[Number(m[2]) - 1]) return "";
  return `${MONTHS[Number(m[2]) - 1]} ${Number(m[3])}`;
}

/** "NVDA 165 Call · Nov 14" */
export function contractLabel(trade) {
  if (!trade) return "";
  const strike = Number(trade.strike);
  const parts = [trade.ticker, Number.isFinite(strike) ? String(strike) : "", sideLabel(trade.side)].filter(Boolean).join(" ");
  const expiry = dayLabel(trade.expiry);
  return expiry ? `${parts} · ${expiry}` : parts;
}

/** The pill beside the ticker: what the run is doing, in his words. */
export function statusLabel(run) {
  if (!run) return "";
  if (run.open) return `In a trade · ${sideLabel(run.side).toUpperCase()}`;
  if (run.status === "waiting") return "Waiting to buy";
  if (run.status === "stopped") return "Stopped";
  return "No trade open";
}

/** The small line under "NVDA · Call" in the list. */
export function runLine(run) {
  if (!run) return "";
  if (run.open || run.status === "waiting") return run.note || statusLabel(run);
  const count = Number(run.tradeCount) || 0;
  if (!count) return run.note || "No trades yet";
  const wins = Number(run.wins) || 0;
  return `${count} ${count === 1 ? "trade" : "trades"} · ${wins} ${wins === 1 ? "win" : "wins"}`;
}

export function winsLabel(run) {
  const count = Number(run?.tradeCount) || 0;
  return count ? `${Number(run.wins) || 0} of ${count}` : "–";
}

/** The ladder of the open trade, top to bottom: T3, T2, T1, Entry, Stop. */
export function ladder(trade) {
  if (!trade) return [];
  const hits = new Map((trade.hits || []).map((h) => [h.target, h]));
  const reasons = new Map((trade.targets || []).map((t) => [t.label, t.reason]));
  const rows = ["T3", "T2", "T1"].map((label) => {
    const hit = hits.get(label);
    return {
      key: label, kind: "target", hit: Boolean(hit), label: hit ? `${label} ✓` : label, value: price(trade[label.toLowerCase()]),
      note: hit ? `hit ${timeEt(hit.at)} · ${hit.sold ? `sold ${hit.sold}` : "none sold"}` : reasons.get(label) || "",
    };
  });
  rows.push({ key: "Entry", kind: "entry", hit: false, label: "Entry", value: `${price(trade.entrySpot)} · ask $${price(trade.entry)}`,
    note: `${trade.qty} ${Number(trade.qty) === 1 ? "contract" : "contracts"}` });
  const moved = trade.stopKind === "break-even" ? "moved to break-even" : trade.stopKind === "T1" ? "moved to T1" : "option down 50%";
  rows.push({ key: "Stop", kind: "stop", hit: false, label: "Stop", value: price(trade.stopStock), note: moved });
  return rows;
}

/** The open trade's gain so far on the last bid, as "+38%"; "" with no bid yet. */
export function openGain(trade) {
  const bid = Number(trade?.bid);
  const entry = Number(trade?.entry);
  if (!trade || trade.bid === null || trade.bid === undefined || !Number.isFinite(bid) || !(entry > 0)) return "";
  const pct = Math.round((bid / entry - 1) * 100);
  return `${pct > 0 ? "+" : pct < 0 ? "−" : ""}${Math.abs(pct)}%`;
}

/** Closed trades, newest first. */
export function tradeRows(run) {
  return [...(run?.trades || [])].reverse().map((t) => ({
    key: t.id, title: `${dayLabel(t.day)} · ${Number(t.strike)} ${sideLabel(t.side)}`, pl: t.pl, plText: signedMoney(t.pl), tone: tone(t.pl),
    reason: t.reason || "", when: `${timeEt(t.boughtAt)} to ${timeEt(t.closedAt)}`, balanceAfter: money(t.balanceAfter),
  }));
}

/** The link that opens the chart on this ticker (appLinks.js openAppLink). */
export function chartLink(ticker) {
  return { label: `${ticker} chart`, view: "Charts & OI", ticker };
}

/** What to say when a read or a button press failed. */
export function failureText(status, body) {
  if (body && typeof body.error === "string" && body.error && status !== 403) return body.error;
  if (status === 403) return "Only the owner can use the Ticker trader.";
  if (status === 404 || status === 503 || status === 502) return "The Ticker trader starts with the next app restart.";
  return "The Ticker trader did not answer. It will try again.";
}

// Account size (2026-11-13, his "So I Change it 10k,5k, 2k or 100K", "What ever I want").
export const SIZE_CHOICES = [2000, 5000, 10000, 100000];
export const DEFAULT_SIZE = 10000;
export const MIN_SIZE = 500;
export const MAX_SIZE = 1000000;
export const SIZE_KEY = "bot.tickerTrader.size";

/** "$2K" / "$100K" on the quick buttons. */
export const sizeChip = (n) => `$${Math.round(Number(n) / 1000)}K`;

/** Whole dollars from "$5,000", "5000" or " 5,000 "; null when it is not a plain amount.
 *  "10k" is NOT accepted: digits, commas and one leading $ only. */
export function parseSize(raw) {
  const text = String(raw ?? "").trim().replace(/^\$/, "").replace(/,/g, "").trim();
  if (!/^\d+(\.\d+)?$/.test(text)) return null;
  const n = Math.round(Number(text));
  return Number.isFinite(n) ? n : null;
}

/** "" when this size can be sent, else the plain-words reason. */
export function sizeProblem(raw) {
  if (!String(raw ?? "").trim()) return "Type an account size (for example 10000).";
  const n = parseSize(raw);
  if (n === null) return "Account size is a dollar amount, digits only (for example 10000 or $10,000).";
  if (n < MIN_SIZE) return `Account size must be at least ${money(MIN_SIZE)}.`;
  if (n > MAX_SIZE) return `Account size can be at most ${money(MAX_SIZE)}.`;
  return "";
}

/** The size this device used last time, else $10,000. Storage can be missing or throw. */
export function savedSize(storage) {
  try {
    const raw = storage ? storage.getItem(SIZE_KEY) : null;
    const n = parseSize(raw);
    return n !== null && !sizeProblem(String(n)) ? n : DEFAULT_SIZE;
  } catch {
    return DEFAULT_SIZE;
  }
}

export function saveSize(storage, n) {
  try { if (storage) storage.setItem(SIZE_KEY, String(n)); } catch { /* private window: not remembered */ }
}

// ---- Journal and AI review (2026-11-13, his "need AI model to improve also tradezella journal also so I know how
// I see improve"): GET .../ticker-trader/journal?id=<run|all> (momx/ticker_journal.py). The Scorecard's own tiles,
// daily bars, calendar and curve maths (botDesk.js) draw it; these helpers add what only this journal has.

export const JOURNAL_URL = `${TICKER_TRADER_URL}/journal`;
export const NOTE_URL = `${TICKER_TRADER_URL}/note`;
export const REVIEW_URL = `${TICKER_TRADER_URL}/review`;
export const JOURNAL_POLL_MS = 60000;
export const REVIEW_LABEL = "AI review, can be wrong. Only you see this.";
export const NOTE_MAX = 2000;
export const ALL_SCOPE = "all";

export function journalUrl(scope) {
  return `${JOURNAL_URL}?id=${encodeURIComponent(String(scope || ALL_SCOPE))}`;
}

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** The one account every ticker spends from (2026-11-16): what it is worth now, the cash left, up or down.
 * null = the server has not sent an account (an older server). */
export function accountTiles(account) {
  const a = account || {};
  if (![a.startBalance, a.cash, a.value].every(isNum)) return null;
  const open = Number(a.openTrades) || 0;
  return {
    value: money(a.value), from: `from ${money(a.startBalance)}`,
    cash: money(a.cash), inTrades: open ? `${money(a.invested)} in ${open} open ${open === 1 ? "trade" : "trades"}` : "nothing in trades",
    net: signedMoney(a.net), tone: tone(a.net), start: a.startBalance,
  };
}

/** Average win, average loss, expectancy, best and worst trade: the small tiles under the five big ones. */
export function journalStats(doc) {
  const s = doc?.stats || {};
  const n = s.n || 0;
  const cell = (key, label, value, sub = "") => ({ key, label, value: n && isNum(value) ? signedMoney(value) : "–", tone: n ? tone(value) : "", sub });
  return [
    cell("avgWin", "Average win", s.avgWin, `${s.wins || 0} won`),
    cell("avgLoss", "Average loss", s.avgLoss, `${s.losses || 0} lost`),
    cell("expectancy", "Expectancy", s.expectancy, "per trade"),
    cell("best", "Best trade", s.bestTrade),
    cell("worst", "Worst trade", s.worstTrade),
  ];
}

/** The equity curve as the points botDesk.deskCurve draws: each closed trade, its balance above or below the start. */
export function equityDays(doc) {
  const start = Number(doc?.startBalance) || 0;
  return (doc?.equity || []).filter((p) => p && isNum(p.balance)).map((p, i) => ({
    day: String(p.id || i), cum: Math.round((p.balance - start) * 100) / 100, pnl: isNum(p.pl) ? p.pl : 0, balance: p.balance, at: p.at,
  }));
}

const BREAKDOWN_TITLES = [["side", "Call vs Put"], ["time", "Time of day bought (ET)"], ["outcome", "Which target was reached"],
  ["hold", "Hold time"], ["ticker", "By ticker"]];

/** The breakdown tables: each bucket's trades, win rate and net. A bucket with no trades shows dashes. */
export function breakdownTables(doc) {
  const groups = doc?.breakdowns || {};
  return BREAKDOWN_TITLES.filter(([key]) => Array.isArray(groups[key]) && groups[key].length).map(([key, title]) => ({
    key, title,
    rows: groups[key].map((r) => ({
      key: String(r.key), label: String(r.label ?? r.key), trades: Number(r.trades) || 0,
      win: r.trades && isNum(r.winRatePct) ? `${Math.round(r.winRatePct)}%` : "–",
      net: r.trades ? signedMoney(r.pnl) : "–", tone: r.trades ? tone(r.pnl) : "",
    })),
  }));
}

/** "What to improve": the server's lines with their trade counts; under 10 trades each says it is too few to judge. */
export function improveItems(doc) {
  return (doc?.improve || []).map((x) => ({
    key: String(x.key), title: String(x.title || ""), text: String(x.text || ""),
    count: `${Number(x.trades) || 0} ${Number(x.trades) === 1 ? "trade" : "trades"}`,
    judge: x.enough ? "" : String(x.judge || "Too few trades to judge"),
  }));
}

export const OUTCOME_LABELS = { stopped: "Stopped before T1", T1: "Reached T1", T2: "Reached T2", T3: "Reached T3", early: "Closed before T1" };

/** "12 min" / "1 h 05 min" */
export function holdLabel(minutes) {
  const m = Number(minutes);
  if (minutes === null || minutes === undefined || !Number.isFinite(m) || m < 0) return "–";
  if (m < 1) return "under 1 min";
  if (m < 60) return `${Math.round(m)} min`;
  const h = Math.floor(m / 60);
  return `${h} h ${String(Math.round(m - h * 60)).padStart(2, "0")} min`;
}

/** The journal's trade list, newest first. */
export function journalTrades(doc) {
  return (doc?.trades || []).map((t) => ({
    key: String(t.id), id: String(t.id), title: `${dayLabel(t.day || t.closedAt)} · ${t.ticker} ${Number(t.strike)} ${sideLabel(t.side)}`,
    when: `${timeEt(t.boughtAt)} to ${timeEt(t.closedAt)}`, pl: t.pl, plText: signedMoney(t.pl), tone: tone(t.pl),
    outcome: OUTCOME_LABELS[t.outcome] || "", hasNote: Boolean(t.note), best: t.id === doc.bestId, worst: t.id === doc.worstId,
  }));
}

/** One trade's card: the plan it was bought on, what was hit and when, every sell, and the chart button for that day. */
export function tradeDetail(t) {
  if (!t) return null;
  const hits = new Map((t.hits || []).map((h) => [h.target, h]));
  const reasons = new Map((t.targets || []).map((x) => [x.label, x.reason]));
  const levels = [
    { key: "Entry", label: "Entry", value: `${price(t.entrySpot)} · ask $${price(t.entry)}`, note: `${timeEt(t.boughtAt)} · ${t.qty} ${Number(t.qty) === 1 ? "contract" : "contracts"}` },
    { key: "Stop", label: "Stop", value: price(t.stopStart ?? t.stopStock), note: "option down 50%" },
    ...["T1", "T2", "T3"].map((label) => {
      const hit = hits.get(label);
      return { key: label, label: hit ? `${label} ✓` : label, value: price(t[label.toLowerCase()]), hit: Boolean(hit),
        note: hit ? `hit ${timeEt(hit.at)}${hit.sold ? ` · sold ${hit.sold} at $${price(hit.price)}` : ""}` : reasons.get(label) || "not reached" };
    }),
  ];
  return {
    id: String(t.id), title: contractLabel(t), plText: signedMoney(t.pl), tone: tone(t.pl),
    pct: isNum(t.plPct) ? `${t.plPct > 0 ? "+" : t.plPct < 0 ? "−" : ""}${Math.abs(t.plPct).toFixed(1)}%` : "",
    outcome: OUTCOME_LABELS[t.outcome] || "", hold: holdLabel(t.holdMinutes), levels,
    events: (t.events || []).map((e, i) => ({ key: `${i}`, at: timeEt(e.at), text: String(e.text || "") })),
    link: { label: `${t.ticker} chart`, view: "Charts & OI", ticker: t.ticker, timeframe: "5m", day: String(t.day || "").slice(0, 10) || undefined },
    note: String(t.note || ""), noteAt: t.noteAt || null,
  };
}

/** "" when a note can be saved, else the reason in plain words. */
export function noteProblem(text, max = NOTE_MAX) {
  if (typeof text !== "string") return "A note is plain text.";
  return text.length > max ? `A note can be at most ${max.toLocaleString("en-US")} characters.` : "";
}

/** Ask the server for today's AI review? Only once per opening, only with closed trades, and only when today has
 * neither a paragraph nor a recorded try (the server keeps it to one AI call per run per day either way). */
export function shouldAskReview(doc, asked) {
  return Boolean(doc && !asked && !doc.review && !doc.reviewStatus && (doc.stats?.n || 0) > 0);
}
