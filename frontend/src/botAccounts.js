// The Bot's paper accounts page (2026-11-27, his "See Webull paper trade page mobile I want same how reset button
// pnl", "I set money for app trade automatically", "Use this journal for all", "when I reset new money old journal
// save with money that I know how much time I reset with pnl"). One screen for four accounts: Paper 3, Paper 5,
// Research and Ticker. Each has its starting money, its own journal since its last reset, and its past runs.
// Pure logic only, so node --test can check it. The server (momx/bot_accounts.py) does every sum; this file only
// reads its answer and words it.
import { deskCalendar, deskKpis, deskMoney, deskMonthKey, deskTone } from "./botDesk.js";

export const ACCOUNTS_URL = "/api/momx-scanner/bot-accounts";
export const ACCOUNTS_RESET_URL = "/api/momx-scanner/bot-accounts/reset";
export const ACCOUNTS_MONEY_URL = "/api/momx-scanner/bot-accounts/money";
export const ACCOUNT_KEY = "bot.account";            // which account was showing
export const ACCOUNT_TAB_KEY = "bot.accountTab";     // which list was showing
export const ACCOUNT_ORDER = ["paper3", "paper5", "research", "ticker"];
export const ACCOUNT_TABS = [["positions", "Positions"], ["orders", "Orders"], ["journal", "Journal"], ["runs", "Past runs"]];
export const MONEY_PRESETS = [2000, 3000, 5000, 10000];
export const MONEY_MIN = 500;
export const MONEY_MAX = 1000000;
export const PER_TRADE_MIN = 50;

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** "$4,412.00": an account value, to the cent. A dash when the server has no number. */
export function accountValue(v) {
  if (!isNum(v)) return "–";
  return (v < 0 ? "−" : "") + "$" + Math.abs(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** "$5,000": whole dollars with no sign, for starting money and limits. */
export function wholeDollars(v) {
  return isNum(v) ? "$" + Math.round(Math.abs(v)).toLocaleString("en-US") : "–";
}

/** "+13.7%" / "−79.6%". */
export function signedPct(v) {
  if (!isNum(v)) return "–";
  return (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1) + "%";
}

/** "Nov 19" from an ISO stamp or a YYYY-MM-DD day. The date as written, never shifted by the browser's zone. */
export function shortDay(stamp) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(stamp || ""));
  if (!m) return "";
  return ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][Number(m[2]) - 1] + " " + Number(m[3]);
}

/** The account to show: the remembered one when the server lists it, else the first listed. */
export function pickAccount(doc, stored) {
  const order = accountOrder(doc);
  return order.includes(stored) ? stored : order[0] || "";
}

/** The accounts the server sent, in its order (ours when it sends none). */
export function accountOrder(doc) {
  const have = (doc && doc.accounts) || {};
  const order = Array.isArray(doc && doc.order) && doc.order.length ? doc.order : ACCOUNT_ORDER;
  return order.filter((key) => have[key]);
}

export function pickTab(stored) {
  return ACCOUNT_TABS.some(([key]) => key === stored) ? stored : "positions";
}

/** What the trader typed as money: "5k", "5,000", "$3000", "2.5k" -> a number of dollars. null when it is not money. */
export function parseMoney(text) {
  const s = String(text == null ? "" : text).trim().toLowerCase().replace(/[$,\s]/g, "");
  const m = /^(\d+(?:\.\d+)?)(k?)$/.exec(s);
  if (!m) return null;
  const v = Number(m[1]) * (m[2] ? 1000 : 1);
  return Number.isFinite(v) ? Math.round(v * 100) / 100 : null;
}

/** Is this starting money and money-per-trade something the server will take? `error` is a sentence for the screen. */
export function checkMoney(moneyText, perTradeText) {
  const money = parseMoney(moneyText);
  if (money === null) return { ok: false, error: "Type the starting money as a number, for example 5000 or 5k." };
  if (money < MONEY_MIN) return { ok: false, error: `Starting money has to be at least ${wholeDollars(MONEY_MIN)}.` };
  if (money > MONEY_MAX) return { ok: false, error: `Starting money can be at most ${wholeDollars(MONEY_MAX)}.` };
  const perTrade = parseMoney(perTradeText);
  if (perTrade === null) return { ok: false, error: "Type the money per trade as a number, for example 300." };
  if (perTrade < PER_TRADE_MIN) return { ok: false, error: `Money per trade has to be at least ${wholeDollars(PER_TRADE_MIN)}.` };
  if (perTrade > money) return { ok: false, error: "Money per trade cannot be more than the starting money." };
  return { ok: true, money, perTrade, error: "" };
}

/** The top card: value, what it started with, and the three P&L numbers. */
export function accountHead(acct) {
  const a = acct || {};
  const run = a.run || {};
  const pnl = (label, v) => ({ label, value: isNum(v) ? deskMoney(v) : "–", tone: isNum(v) ? deskTone(v) : "" });
  return {
    label: a.label || "", note: a.note || "", value: accountValue(a.value),
    started: isNum(run.money) ? `Started with ${wholeDollars(run.money)}${shortDay(run.startedAt) ? " on " + shortDay(run.startedAt) : ""} · run ${run.n || 1}` : "",
    pnls: [pnl("Day P&L", a.dayPnl), pnl("Open P&L", a.openPnl), pnl("Total P&L", a.totalPnl)],
  };
}

/** The money card. An account that was never reset has no limit yet, and the card says so. */
export function accountMoney(acct) {
  const a = acct || {};
  const run = a.run || {};
  const value = isNum(a.value) && a.value > 0 ? a.value : 0;
  const invested = isNum(a.invested) ? a.invested : 0;
  const share = value ? Math.max(0, Math.min(1, invested / value)) : 0;
  const skipped = a.skippedNoMoney || 0;
  return {
    // Research before its first reset has no money per trade: one contract for every Level signal.
    perTrade: isNum(run.perTrade) ? wholeDollars(run.perTrade) : "1 contract", free: isNum(a.free) ? wholeDollars(Math.max(0, a.free)) : "–",
    capped: !!run.capped, share,
    // The Ticker trader sizes its own trades (a tenth of its money), so there is nothing to change there.
    canChange: !!run.capped && a.kind !== "simulated",
    barLabel: `${wholeDollars(invested)} of ${wholeDollars(value)} is in open trades`,
    note: run.capped
      ? `${wholeDollars(invested)} is in open trades. The app stops buying when the free money runs out.`
        + (skipped ? ` ${skipped} ${skipped === 1 ? "trade was" : "trades were"} left out in this run because the free money had run out.` : "")
      : "No money limit yet: every trade counts, as before. Tap Reset and set your money to start a limit.",
  };
}

/** The journal tiles, the same five the Scorecard shows, from the server's tiles for a run. */
export function accountKpis(tiles) {
  const t = tiles || {};
  return deskKpis({
    stats: { n: t.closed || 0, netPnl: t.netPnl, winRatePct: t.winPct, wins: t.wins || 0, losses: t.losses || 0, profitFactor: t.profitFactor,
      avgWin: t.avgWin, avgLoss: t.avgLoss },
    daySummary: { tradingDays: (t.greenDays || 0) + (t.redDays || 0), greenDays: t.greenDays || 0, redDays: t.redDays || 0 },
  });
}

/** The journal month calendar, the Scorecard's own, from a run's daily P&L list. */
export function accountCalendar(days, monthKey, todayKey = "") {
  const day = (Array.isArray(days) ? days : []).filter((d) => d && d.day).map((d) => ({ key: d.day, pnl: d.pnl, trades: d.trades || 0 }));
  return deskCalendar({ journal: { periods: { day } } }, monthKey, todayKey);
}

/** The first and last month a run has anything in, so the calendar's arrows stop there. */
export function runMonths(run, days, thisMonth) {
  const keys = (Array.isArray(days) ? days : []).map((d) => deskMonthKey(d && d.day)).filter(Boolean).sort();
  const start = deskMonthKey(run && run.startedAt) || keys[0] || thisMonth;
  const end = deskMonthKey(run && run.endedAt) || thisMonth;
  const first = keys.length && keys[0] < start ? keys[0] : start;
  const last = keys.length && keys[keys.length - 1] > end ? keys[keys.length - 1] : end;
  return { first: first <= last ? first : last, last };
}

/** One open position as a row. */
export function positionRow(p, i) {
  const qty = p.qty || 0;
  const price = (v) => (isNum(v) ? v.toFixed(2) : "–");
  return { key: `${p.symbol}|${p.contract}|${p.at || i}`, symbol: p.symbol || "", contract: p.contract || "",
    detail: `${qty} ${qty === 1 ? "contract" : "contracts"} · bought ${price(p.entry)} · now ${price(p.mark)}`,
    pnl: isNum(p.pnl) ? deskMoney(p.pnl) : "–", pct: isNum(p.pct) ? signedPct(p.pct) : "", tone: isNum(p.pnl) ? deskTone(p.pnl) : "" };
}

/** One order as a row. */
export function orderRow(o, i) {
  const qty = o.qty || 0;
  return { key: `${o.symbol}|${o.contract}|${o.at || i}`, symbol: o.symbol || "", contract: o.contract || "", status: o.status || "",
    detail: `${qty} ${qty === 1 ? "contract" : "contracts"}${isNum(o.price) ? " · " + o.price.toFixed(2) : ""}${o.at ? " · " + shortDay(o.at) + (timeEt(o.at) ? " " + timeEt(o.at) : "") : ""}` };
}

/** "10:42 AM" from the server's ET stamp (the clock time as written in the stamp). */
export function timeEt(stamp) {
  const m = /T(\d{2}):(\d{2})/.exec(String(stamp || ""));
  if (!m) return "";
  const h = Number(m[1]);
  return `${h % 12 || 12}:${m[2]} ${h < 12 ? "AM" : "PM"}`;
}

/** The run that is running now, as a Past runs row, so the list reads newest first with "now" on top. */
export function runRows(acct) {
  const a = acct || {};
  const run = a.run || {};
  const closed = (a.journal && a.journal.tiles && a.journal.tiles.closed) || 0;
  const trades = (n) => `${n} closed ${n === 1 ? "trade" : "trades"}`;
  const now = { key: "now", n: run.n || 1, live: true, title: `Run ${run.n || 1} · started with ${wholeDollars(run.money)}`,
    dates: `${shortDay(run.startedAt) || "Start"} to today · ${trades(closed)}`,
    pnl: isNum(a.totalPnl) ? deskMoney(a.totalPnl) : "–", tone: isNum(a.totalPnl) ? deskTone(a.totalPnl) : "", end: `now ${wholeDollars(a.value)}` };
  const past = (Array.isArray(a.pastRuns) ? a.pastRuns : []).map((r) => ({ key: "run-" + r.n, n: r.n, live: false,
    title: `Run ${r.n} · started with ${wholeDollars(r.money)}`, dates: `${shortDay(r.startedAt)} to ${shortDay(r.endedAt)} · ${trades(r.trades || 0)}`,
    pnl: `${deskMoney(r.pnl)}${isNum(r.pnlPct) ? " · " + signedPct(r.pnlPct) : ""}`, tone: deskTone(r.pnl), end: `ended ${wholeDollars(r.endValue)}` }));
  return [now, ...past];
}

/** The line over the Past runs list: how many runs, how much money was put in, and what all of them made. */
export function runsSummary(acct) {
  const a = acct || {};
  const past = Array.isArray(a.pastRuns) ? a.pastRuns : [];
  const money = past.reduce((sum, r) => sum + (isNum(r.money) ? r.money : 0), isNum(a.run && a.run.money) ? a.run.money : 0);
  const pnl = past.reduce((sum, r) => sum + (isNum(r.pnl) ? r.pnl : 0), isNum(a.totalPnl) ? a.totalPnl : 0);
  const resets = isNum(a.resets) ? a.resets : past.length;
  return { runs: past.length + 1, resets, resetsText: resets === 1 ? "reset 1 time" : `reset ${resets} times`, money: wholeDollars(money),
    pnl: deskMoney(pnl), tone: deskTone(pnl) };
}

/** What the Reset sheet says is ending. */
export function endingRun(acct) {
  const a = acct || {};
  const run = a.run || {};
  const closed = (a.journal && a.journal.tiles && a.journal.tiles.closed) || 0;
  const pct = isNum(a.totalPnl) && isNum(run.money) && run.money ? (a.totalPnl / run.money) * 100 : null;
  return [["Run", String(run.n || 1), ""], ["Started with", wholeDollars(run.money), ""], ["Dates", `${shortDay(run.startedAt) || "Start"} to today`, ""],
    ["Closed trades", String(closed), ""], ["Ends at", wholeDollars(a.value), ""],
    ["P&L", `${isNum(a.totalPnl) ? deskMoney(a.totalPnl) : "–"}${pct === null ? "" : " · " + signedPct(pct)}`, isNum(a.totalPnl) ? deskTone(a.totalPnl) : ""]];
}

/** What Reset does, in his words, for the account it is about. */
export function resetNotes(acct, money) {
  const a = acct || {};
  const n = (a.run && a.run.n) || 1;
  const lines = [`The journal, tiles and calendar start again from ${wholeDollars(money)}, counting only trades from now on.`,
    `Run ${n} moves to Past runs with its money and its P&L. Nothing is deleted.`];
  if (a.kind === "paper") lines.push("The Alpaca paper account itself is not changed. The app counts from the new amount, and buys only what fits in it.");
  if (a.kind === "virtual") lines.push("From now on Research takes only the signals this money can pay for, so its totals will be smaller than the full list.");
  if (a.kind === "simulated") lines.push("The Ticker trader's tickers are cleared and it starts again with this money.");
  return lines;
}

/** Send a reset or a money change. Resolves { ok, account, error }: error is a sentence for the screen. */
export async function accountWrite(url, body, fetcher = fetch) {
  try {
    const r = await fetcher(url, { method: "POST", credentials: "include", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let doc = null;
    try { doc = await r.json(); } catch { doc = null; }
    if (r.ok && doc && doc.ok) return { ok: true, account: doc.account || null, error: "" };
    return { ok: false, account: null, error: (doc && doc.error) || `The server said no (${r.status}). Nothing was changed.` };
  } catch {
    return { ok: false, account: null, error: "The app server did not answer. Nothing was changed." };
  }
}
