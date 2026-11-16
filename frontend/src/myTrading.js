// My Trading (2026-11-11, owner only): the owner's REAL Schwab option trades,
// read once a day after the close (momx/schwab_history.py), turned into plain
// numbers by momx/my_insights.py and served by GET /api/my-insights?days=.
// Pure helpers, so node:test can run them. Every line DESCRIBES what happened;
// none of them tells the trader what to do.

export const MY_TRADING_VIEW = "My Trading";
export const MY_TRADING_PERIODS = [7, 30, 90];
export const MY_TRADING_PERIOD_KEY = "myTrading.days";
export const MY_TRADING_REFRESH_URL = "/api/my-insights/refresh";
export const TOO_FEW = 10;
export const TOO_FEW_LINE = "too few trades to judge";

export function myTradingUrl(days) {
  return `/api/my-insights?days=${readPeriod(days)}`;
}

/** The remembered period, or 30 days. Anything else the browser holds is ignored. */
export function readPeriod(stored) {
  const n = Number(stored);
  return MY_TRADING_PERIODS.includes(n) ? n : 30;
}

const num = (v) => (v === null || v === undefined || v === "" ? NaN : Number(v)); // Number(null) is 0, not missing

/** "+$1,350" / "−$420" / "$0". A missing number is a dash, never a zero. */
export function mtMoney(v) {
  const n = num(v);
  if (!Number.isFinite(n)) return "–";
  const sign = n > 0 ? "+" : n < 0 ? "−" : "";
  return sign + "$" + Math.round(Math.abs(n)).toLocaleString("en-US");
}

/** A cost: always a plain dollar amount ("$84"). */
export function mtCost(v) {
  const n = num(v);
  return Number.isFinite(n) ? "$" + Math.round(Math.abs(n)).toLocaleString("en-US") : "–";
}

/** Win rate, spread %, width %. The server documents these as fractions (0.5 = 50%, 1.5 = 150%),
 *  so the value is always multiplied by 100; nothing is guessed from its size. */
export function mtFraction(v, digits = 0) {
  const n = num(v);
  if (!Number.isFinite(n)) return "–";
  return (n * 100).toFixed(digits) + "%";
}

export function mtFactor(v) {
  const n = num(v);
  if (v === "inf" || n === Infinity) return "no losses";
  return Number.isFinite(n) ? n.toFixed(2) : "–";
}

export function mtTone(v) {
  const n = num(v);
  return !Number.isFinite(n) || n === 0 ? "" : n > 0 ? "is-up" : "is-down";
}

/** "11-10 15:05 ET" from an ET stamp ("2026-11-10T15:05:52-05:00"). */
export function mtClock(stamp) {
  const s = String(stamp || "");
  return s.length >= 16 && s[10] === "T" ? `${s.slice(5, 10)} ${s.slice(11, 16)} ET` : "";
}

export function mtMinutes(v) {
  const n = num(v);
  if (!Number.isFinite(n)) return "–";
  if (n < 60) return `${Math.round(n)} min`;
  if (n < 60 * 24) return `${(n / 60).toFixed(n < 600 ? 1 : 0)} h`;
  return `${(n / 1440).toFixed(1)} days`;
}

/** How many trades a number rests on, and whether that is enough to read anything into it. */
export function tradeCount(stats) {
  const n = num(stats && (stats.trades ?? stats.n ?? stats.count));
  return Number.isFinite(n) ? n : 0;
}

export function isTooFew(stats) {
  return tradeCount(stats) < TOO_FEW;
}

export function basisLine(stats, periodLabel) {
  const n = tradeCount(stats);
  const base = `${n} ${n === 1 ? "trade" : "trades"}${periodLabel ? ", " + periodLabel : ""}`;
  return n < TOO_FEW ? `${base} · ${TOO_FEW_LINE}` : base;
}

/** "last 30 days (10-12 to 11-11)". */
export function periodLabel(doc) {
  const p = (doc && doc.period) || {};
  if (p.label) return String(p.label);
  const days = readPeriod(p.days ?? (doc && doc.days));
  const from = String(p.from || p.start || "").slice(5, 10);
  const to = String(p.to || p.end || "").slice(5, 10);
  return `last ${days} days` + (from && to ? ` (${from} to ${to})` : "");
}

const summaryOf = (doc) => (doc && (doc.summary || doc.totals || doc.overall)) || {};

/** The five tiles at the top. With fewer than 10 trades the averages are shown greyed, with the reason. */
export function myTiles(doc) {
  const s = summaryOf(doc);
  const n = tradeCount(s);
  const few = n < TOO_FEW;
  const sub = few ? TOO_FEW_LINE : "";
  return [
    { key: "pnl", label: "Profit / loss", value: mtMoney(s.pnl ?? s.totalPnl), tone: mtTone(s.pnl ?? s.totalPnl), sub: "after fees", dim: false },
    { key: "winRate", label: "Win rate", value: mtFraction(s.winRate), tone: "", sub: sub || `${num(s.wins) || 0} won, ${num(s.losses) || 0} lost`, dim: few },
    { key: "expectancy", label: "Per trade on average", value: mtMoney(s.expectancy), tone: few ? "" : mtTone(s.expectancy), sub: sub || `wins ${mtMoney(s.avgWin)}, losses ${mtMoney(s.avgLoss)}`, dim: few },
    { key: "profitFactor", label: "Profit factor", value: mtFactor(s.profitFactor), tone: "", sub: sub || "dollars won per dollar lost", dim: few },
    { key: "trades", label: "Trades", value: String(n), tone: "", sub: holdLine(doc, s) || "closed round trips", dim: false },
  ];
}

/** "typical hold 42 min · wins 30 min, losses 1.5 h" (medians from the server). */
export function holdLine(doc, s = summaryOf(doc)) {
  const h = (doc && doc.holdTime) || {};
  const all = s.medianHoldMinutes ?? s.holdMinutes ?? h.all;
  if (all === null || all === undefined) return "";
  const parts = [`typical hold ${mtMinutes(all)}`];
  if (h.wins != null && h.losses != null) parts.push(`wins ${mtMinutes(h.wins)}, losses ${mtMinutes(h.losses)}`);
  return parts.join(" · ");
}

export const MY_TABLES = [
  { key: "setup", title: "By scanner setup at entry", head: "Setup" },
  { key: "timeOfDay", title: "By time of day (ET)", head: "Time" },
  { key: "dte", title: "By days to expiry at entry", head: "DTE" },
  { key: "delta", title: "By delta at entry", head: "Delta" },
  { key: "callPut", title: "Calls vs puts", head: "Side" },
  { key: "weekday", title: "By weekday", head: "Day" },
  { key: "ticker", title: "By ticker", head: "Ticker" },
];

const ALIASES = {
  setup: ["setup", "bySetup", "setups"], timeOfDay: ["timeOfDay", "byTimeOfDay", "time"], dte: ["dte", "byDte"],
  delta: ["delta", "byDelta"], callPut: ["callPut", "byCallPut", "side", "callsPuts"], weekday: ["weekday", "byWeekday"],
  ticker: ["ticker", "byTicker", "tickers"],
};

function groupRows(doc, key) {
  const pools = [doc && doc.breakdowns, doc && doc.by, doc];
  for (const pool of pools) {
    if (!pool) continue;
    for (const name of ALIASES[key] || [key]) {
      const v = pool[name];
      if (Array.isArray(v)) return v;
      if (v && typeof v === "object") return Object.entries(v).map(([label, stats]) => ({ label, ...stats }));
    }
  }
  return [];
}

/** One table, ready to draw. Rows with too few trades stay in, marked, so nothing is hidden. */
export function myTable(doc, key) {
  const rows = groupRows(doc, key).map((r) => {
    const n = tradeCount(r);
    const few = n < TOO_FEW;
    return {
      key: String(r.key ?? r.label ?? r.name ?? ""), label: String(r.label ?? r.name ?? r.key ?? "–"), trades: n, few,
      winRate: mtFraction(r.winRate), expectancy: mtMoney(r.expectancy), expectancyTone: few ? "" : mtTone(r.expectancy),
      profitFactor: mtFactor(r.profitFactor), pnl: mtMoney(r.pnl ?? r.totalPnl), pnlTone: mtTone(r.pnl ?? r.totalPnl),
      pnlValue: Number.isFinite(num(r.pnl ?? r.totalPnl)) ? num(r.pnl ?? r.totalPnl) : 0,
      note: few ? TOO_FEW_LINE : "",
    };
  });
  // Ticker and setup tables are ordered by how much they made or lost; the
  // ordered buckets (time, DTE, delta, weekday) keep the server's order.
  if (key === "ticker" || key === "setup") {
    rows.sort((a, b) => Math.abs(b.pnlValue) - Math.abs(a.pnlValue) || b.trades - a.trades);
  }
  return rows;
}

const HABIT_TEXT = {
  revenge: { title: "New trade soon after a loss", what: "A new entry within 15 minutes of closing a losing trade." },
  overtrading: { title: "Busier days than usual", what: "More trades in a day than 1.5x the 20-day median." },
  sizeUpAfterLoss: { title: "Bigger size after a loss", what: "The next trade cost more than 1.5x the losing one." },
  heldPast50: { title: "Held past −50%", what: "The option fell 50% or more below the entry before it was sold." },
  cutWinnersEarly: { title: "Sold before +100%", what: "Sold below +100% on a contract that reached +100% later that day." },
};

const habitList = (doc) => {
  const h = doc && doc.habits;
  if (Array.isArray(h)) return h;
  if (h && typeof h === "object") return Object.entries(h).filter(([, v]) => v && typeof v === "object").map(([key, v]) => ({ key, ...v }));
  return [];
};

/** "Habits that cost money": each card with what it means, the dollar cost and the trades behind it. Costliest first. */
export function myHabits(doc) {
  return habitList(doc).map((h) => {
    const key = String(h.key || h.id || "");
    const text = HABIT_TEXT[key] || { title: h.label || h.title || key, what: h.what || h.description || "" };
    const trades = (h.trades || h.examples || []).map((t) => ({
      id: String(t.id ?? t.tradeId ?? `${t.symbol || t.optionSymbol}-${t.entryAt || t.at}`),
      when: mtClock(t.entryAt || t.at || t.exitAt), contract: tradeContract(t), pnl: mtMoney(t.pnl), tone: mtTone(t.pnl),
      note: String(t.note || t.why || ""),
    }));
    const count = Number.isFinite(num(h.count)) ? num(h.count) : trades.length;
    const cost = num(h.cost ?? h.costDollars ?? h.pnl);
    // The server's cost is money lost on those trades, except for winners sold early,
    // where it is what the same contracts were worth more at their later +100%.
    const costWords = key === "cutWinnersEarly" ? "less than the later +100% price" : "lost on those trades";
    return {
      key, title: h.label || text.title, what: h.what || text.what, count, trades,
      cost: Number.isFinite(cost) ? mtCost(cost) : "–", costValue: Number.isFinite(cost) ? Math.abs(cost) : 0,
      approx: !!h.approx, note: String(h.note || ""), empty: count === 0,
      line: count === 0 ? "Did not happen in this period."
        : `${count} ${count === 1 ? "time" : "times"}` + (Number.isFinite(cost) && cost !== 0 ? `, ${mtCost(cost)} ${costWords}` : ""),
    };
  }).sort((a, b) => b.costValue - a.costValue || b.count - a.count);
}

/** "NVDA 11-14 190C". */
export function tradeContract(t) {
  if (!t) return "";
  const sym = t.underlying || t.symbol || "";
  const exp = String(t.expiry || t.expiration || "").slice(5, 10);
  const strike = num(t.strike);
  const cp = String(t.putCall || t.callPut || t.right || "").toUpperCase().startsWith("P") ? "P" : String(t.putCall || t.callPut || t.right || "") ? "C" : "";
  return [sym, exp, Number.isFinite(strike) ? `${strike}${cp}` : cp].filter(Boolean).join(" ") || String(t.optionSymbol || "");
}

/** Fills against the chain's mid price. */
export function myExecution(doc) {
  const e = (doc && doc.execution) || null;
  if (!e) return null;
  const n = num(e.trades ?? e.matched ?? e.fills);
  const priced = Number.isFinite(n) ? n : 0;
  const approx = num(e.approxCount ?? e.approximate) || 0;
  // spreadCost is signed: + = paid above mid, − = filled better than mid (saved). A saving is never shown as money paid.
  const total = num(e.spreadCost ?? e.totalSpreadCost);
  const perFill = num(e.avgSpreadDollars ?? e.avgSpread);
  const pct = num(e.avgSpreadPct);
  const saved = Number.isFinite(total) ? total < 0 : perFill < 0;
  const pctAbs = Number.isFinite(pct) ? Math.abs(pct) : NaN;
  // With no fill priced, the server's 0 totals are "nothing measured", not "$0 paid": show a dash and let the panel hide the tiles.
  const measured = priced > 0;
  const line = !measured ? "No saved option prices near these fills, so the spread cannot be measured."
    : `Measured on ${priced} ${priced === 1 ? "fill" : "fills"} with a saved chain price`
      + (approx ? `; ${approx} of them used a chain saved more than 5 minutes away (approximate)` : "") + ".";
  const note = String(e.note || "").trim();
  return {
    priced, measured, saved,
    spreadLabel: saved ? "Saved vs mid in total" : "Spread paid in total",
    spreadCost: measured ? mtCost(total) : "–",
    avgLabel: perFill < 0 ? "Saved per fill on average" : "Paid per fill on average",
    avgPerFill: measured ? mtCost(perFill) : "–",
    avgPct: measured ? mtFraction(pctAbs) : "–",
    avgPctLine: measured && Number.isFinite(pctAbs) ? `${mtFraction(pctAbs)} of the price${pct < 0 ? " below mid" : ""}` : "",
    vsMid: measured ? mtMoney(e.vsMid ?? e.fillVsMid) : "–",
    unpriced: num(e.unpriced ?? e.noSavedPrice) || 0,
    approx,
    // The server's note often says the same thing as the line (it words it slightly differently when approximate),
    // so it is printed only when it adds something.
    note: !note || note === line || (!measured && /cannot be measured/.test(note)) || (measured && /^Measured on /.test(note)) ? "" : note,
    line,
  };
}

function botList(doc) {
  const bots = (doc && (doc.paperBots || doc.versus || doc.bots)) || [];
  return Array.isArray(bots) ? bots : Object.entries(bots).map(([k, v]) => ({ account: k, ...v }));
}

/** You vs the paper bots over the same days. The owner's own row comes first. Each bot row uses the
 *  server's sameDays numbers (only the days the owner traded); the whole-period numbers are a fallback
 *  for an older doc without them, and myVersusBasis() then says so. */
export function myVersus(doc) {
  const you = summaryOf(doc);
  const row = (label, s, own) => ({
    label, own, trades: tradeCount(s), winRate: mtFraction(s.winRate), expectancy: mtMoney(s.expectancy),
    pnl: mtMoney(s.pnl ?? s.totalPnl), tone: mtTone(s.pnl ?? s.totalPnl), few: tradeCount(s) < TOO_FEW,
  });
  const list = botList(doc).map((b) => row(b.label || b.name || b.account || "Paper bot", b.sameDays || b, false));
  return list.length ? [row("You (real)", you, true), ...list] : [];
}

/** The basis line for the you-vs-bots card: the server's period text for the same-days numbers. */
export function myVersusBasis(doc) {
  const bots = botList(doc);
  if (!bots.length) return "";
  const same = bots.find((b) => b && b.sameDays);
  if (same) return String(same.sameDays.period || "the days you traded");
  const p = bots[0] && bots[0].period;
  return `${p ? `${p}, ` : ""}whole period (same-days numbers not available)`;
}

/** The upcoming-events strip. When no feed is available the reason is said, not hidden. */
export function myEvents(doc, now = new Date()) {
  const ev = (doc && (doc.events || doc.marketEvents)) || null;
  const items = Array.isArray(ev) ? ev : (ev && (ev.items || ev.events)) || [];
  const note = ev && !Array.isArray(ev) ? String(ev.note || ev.reason || "") : "";
  const today = etDay(now);
  const list = items.map((e) => {
    const at = String(e.at || e.date || e.time || "");
    return { key: `${at}-${e.title || e.name || e.symbol}`, day: at.slice(5, 10), time: at.length >= 16 ? at.slice(11, 16) : "",
      title: String(e.title || e.name || e.event || (e.symbol ? `${e.symbol} earnings` : "")), kind: String(e.kind || e.type || ""),
      today: at.slice(0, 10) === today, high: String(e.impact || "").toLowerCase() === "high" };
  }).filter((e) => e.title);
  return { items: list, note: list.length ? note : note || "No market events saved for the next 7 days." };
}

function etDay(now) {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  } catch { return now.toISOString().slice(0, 10); }
}

/** Where the Schwab read stands, in one line, and whether the manual refresh button may be used now. */
export function mySchwab(doc, now = new Date()) {
  const s = (doc && (doc.schwab || doc.source)) || {};
  const last = s.lastReadAt || s.lastRead || (doc && doc.lastReadAt);
  const stopped = !!(s.stoppedDay || s.stopped || s.signInNeeded || s.state === "signin_needed" || s.state === "stopped");
  // "stopped" without a sign-in cause (a bad reply, the day's budget) is not a login problem.
  const signIn = stopped && (s.signInNeeded === true || s.state === "signin_needed" || (s.signInNeeded === undefined && s.state !== "stopped"));
  const lastLine = last ? `Last read from Schwab at ${mtClock(last) || String(last)}` : "Not read from Schwab yet";
  const nextOk = s.nextManualAt || s.canRefreshAt || s.manualAllowedAt;
  const nextMs = nextOk ? Date.parse(nextOk) : NaN;
  const waitMin = Number.isFinite(nextMs) ? Math.ceil((nextMs - now.getTime()) / 60000) : 0;
  let canRefresh = !stopped && !(s.reading || s.state === "reading") && waitMin <= 0;
  if (s.canRefresh === false) canRefresh = false;
  const refreshNote = stopped ? "Reading is stopped for today"
    : s.reading || s.state === "reading" ? "Reading now"
      : waitMin > 0 ? `Refresh again in ${waitMin} min (once an hour)` : "";
  return {
    tone: stopped ? "bad" : last ? "ok" : "warn",
    title: signIn ? "Schwab sign-in needed" : stopped ? "Schwab reading stopped for today" : lastLine,
    line: signIn
      ? `${lastLine}. Schwab refused the sign-in${s.reason || s.stoppedReason ? ` (${s.reason || s.stoppedReason})` : ""}, so no more reads today. The Schwab trading login needs renewing in Settings.`
      : stopped ? `${lastLine}. The last read failed${s.reason || s.stoppedReason ? ` (${s.reason || s.stoppedReason})` : ""}, so no more reads today.`
        : "Read once a day after 16:30 ET. Read-only: this page cannot place, change or cancel an order.",
    reading: !!(s.reading || s.state === "reading"),
    canRefresh, refreshNote,
  };
}

/** What the refresh button says after a POST. */
export function refreshReply(status, body) {
  if (status === 200 || status === 202) return { tone: "ok", text: (body && (body.message || body.line)) || "Reading from Schwab. The page updates when it is done." };
  if (status === 429) return { tone: "warn", text: (body && (body.message || body.error)) || "Already read in the last hour." };
  if (status === 401 || status === 403) return { tone: "bad", text: "Owner only." };
  if (status === 409) {
    const text = (body && (body.message || body.error)) || "Schwab sign-in needed.";
    return { tone: /already reading/i.test(text) ? "warn" : "bad", text };
  }
  return { tone: "bad", text: (body && (body.message || body.error)) || "The refresh did not start." };
}

export function openPositions(doc) {
  const list = (doc && (doc.openPositions || doc.open)) || [];
  return (Array.isArray(list) ? list : []).map((p) => ({
    key: String(p.optionSymbol || tradeContract(p)), contract: tradeContract(p), qty: num(p.quantity ?? p.qty) || 0,
    cost: mtCost(p.cost ?? (num(p.price) * num(p.quantity) * 100)), since: mtClock(p.entryAt || p.openedAt),
  }));
}

/** The page has something to show (at least one trade or one open position). */
export function hasTrades(doc) {
  return tradeCount(summaryOf(doc)) > 0 || openPositions(doc).length > 0;
}

/** "account …1867" / "accounts …1867, …4410" from the Schwab status; "" when none were listed. */
export function accountsWords(list) {
  const names = (Array.isArray(list) ? list : []).map((a) => String(a || "")).filter(Boolean);
  return names.length ? `${names.length === 1 ? "account" : "accounts"} ${names.join(", ")}` : "";
}

/** What an empty page says. A read that worked and found nothing says so (with when and which
 *  accounts); "not read yet" is only for a Schwab read that never happened. */
export function emptyLine(doc) {
  if (doc && doc.available === false) {
    const s = doc.schwab || doc.source || {};
    const last = s.lastReadAt || s.lastRead || doc.lastReadAt;
    if (last) {
      const where = accountsWords(s.accounts);
      return `Read from Schwab at ${mtClock(last) || String(last)}: no option trades found in the last 90 days${where ? ` on ${where}` : ""}.`;
    }
    if (s.signInNeeded || s.state === "signin_needed") return "Schwab sign-in needed: the trade history could not be read today.";
    if (s.stopped || s.state === "stopped") return "The Schwab read failed today, so no trades have been read yet.";
    if (s.reading || s.state === "reading") return "Reading from Schwab now.";
    return "No trades read from Schwab yet. The first read happens after 16:30 ET.";
  }
  return `No closed trades in the ${periodLabel(doc)}.`;
}
