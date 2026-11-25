// Bot home page (2026-11-25): the words and numbers for the one-column Bot page a
// trader reads on a phone. Home · Journal · Trades · Learned · Ready, all from the
// Scorecard's own read (GET /api/momx-scanner/bot-desk, momx/bot_desk.py): real
// Alpaca paper fills only. Pure, so node:test can run it. No React here.
import { DESK_LOW_BUYING_POWER, deskDollars, deskMoney, deskShortMoney } from "./botDesk.js";

// "More" (2026-11-25, his "I see only this, where is research panel and they 2 panel?"): the older pages
// (Ticker trader, Scorecard, Research, Ask) were only at the bottom of Home, so from any other tab they
// looked gone. They now have their own tab, in reach from every screen. It is not remembered: the Bot
// reopens on the last real section (HOME_KEPT_SECTIONS).
export const HOME_SECTIONS = [["home", "Home"], ["journal", "Journal"], ["trades", "Trades"], ["learned", "Learned"], ["ready", "Ready"], ["more", "More"]];
export const HOME_KEPT_SECTIONS = ["home", "journal", "trades", "learned", "ready"];
export const HOME_SECTION_KEY = "bot.homeSection";
export const HOME_ACCOUNTS = ["paper3", "paper5"];
export const HOME_REFRESH_MS = 60 * 1000;

const isNum = (v) => typeof v === "number" && Number.isFinite(v);
const text = (v) => (v == null ? "" : String(v));
const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export function readHomeSection(stored) {
  return HOME_KEPT_SECTIONS.includes(stored) ? stored : "home";
}

/** The account to show: the stored one when the server has it, else the first it has. */
export function readHomeAccount(stored, doc) {
  const have = HOME_ACCOUNTS.filter((name) => doc && doc.accounts && doc.accounts[name]);
  if (have.includes(stored)) return stored;
  return have[0] || (HOME_ACCOUNTS.includes(stored) ? stored : HOME_ACCOUNTS[0]);
}

/** "is-up" / "is-down" / "": the colour of a result. A missing number has none. */
export function homeTone(v) {
  return !isNum(v) || v === 0 ? "" : v > 0 ? "is-up" : "is-down";
}

/** "+12%" / "−46%" / "0%". A missing number is a dash, never a zero. */
export function homePct(v) {
  if (!isNum(v)) return "–";
  const r = Math.round(Math.abs(v));
  return (r === 0 ? "" : v > 0 ? "+" : "−") + r + "%";
}

const dateOf = (stamp) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(text(stamp));
  if (!m) return null;
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
  return Number.isNaN(d.getTime()) || d.getUTCMonth() !== Number(m[2]) - 1 ? null : d;
};

/** "Mon, Nov 23" from a day or the server's ET stamp. "" when it is not a date. */
export function homeDay(stamp) {
  const d = dateOf(stamp);
  return d ? `${DOW[d.getUTCDay()]}, ${MONTHS[d.getUTCMonth()].slice(0, 3)} ${d.getUTCDate()}` : "";
}

/** "Nov 23". */
export function homeShortDay(stamp) {
  const d = dateOf(stamp);
  return d ? `${MONTHS[d.getUTCMonth()].slice(0, 3)} ${d.getUTCDate()}` : "";
}

/** "Mon". */
export function homeWeekday(stamp) {
  const d = dateOf(stamp);
  return d ? DOW[d.getUTCDay()] : "";
}

/** "1:53 PM" from the server's ET stamp (the clock is read as written, never shifted). */
export function homeTime(stamp) {
  const m = /^\d{4}-\d{2}-\d{2}T(\d{2}):(\d{2})/.exec(text(stamp));
  if (!m) return "";
  const h = Number(m[1]);
  return `${((h + 11) % 12) + 1}:${m[2]} ${h < 12 ? "AM" : "PM"}`;
}

/** "Nov 25, 1:19 AM" for the read time and the saved-copy line. */
export function homeStamp(stamp) {
  const day = homeShortDay(stamp);
  const time = homeTime(stamp);
  return day && time ? `${day}, ${time}` : day || time;
}

export function homeSide(side) {
  return side === "bear" ? "Put" : "Call";
}

/** "Level 2": the highest level the signal reached. Any other rule name passes through. */
export function homeLevel(rule, levels) {
  const list = Array.isArray(levels) && levels.length ? levels : [rule];
  const top = text(list[list.length - 1]);
  const m = /^level\s*(\d)$/i.exec(top);
  return m ? `Level ${m[1]}` : top;
}

/** The saved exit sentence in three words for a row. The full sentence stays in the detail. */
export function homeExitWord(exit) {
  const s = text(exit).trim();
  if (!s) return "sold";
  if (/option down 50%/i.test(s)) return "sold at the −50% stop";
  if (/21 EMA/i.test(s)) return "trend turned";
  if (/worthless|expir/i.test(s)) return "expired worthless";
  if (/\b15:5\d\b|close$/i.test(s)) return "sold at the close";
  if (/\bT[123]\b/.test(s)) return "target reached";
  if (/stop/i.test(s)) return "sold at the stop";
  return s.charAt(0).toLowerCase() + s.slice(1);
}

/** A saved note as a trader may read it: no broker reply, no program text, no status code. */
export function homePlainNote(note, fallback = "") {
  let s = text(note).split("{")[0].replace(/[:\s]+$/, "").trim();
  if (!s || /traceback|exception\b|\bhttp\s*\d{3}\b|\bstatus\s*\d{3}\b|^error\b/i.test(s) || /[A-Za-z]Error\b/.test(s)) s = fallback;
  return s;
}

// ---------------------------------------------------------------- Home: the state, then the one money story

/** Is the bot buying right now, in plain words, and when it last placed an order.
 * Out of money comes first: a bot that cannot pay is stopped whatever the switches say.
 * The test is the Scorecard's own "Under $300 to spend" one (botDesk.js DESK_LOW_BUYING_POWER). */
export function homeState(doc, acct) {
  const b = acct && acct.broker;
  const c = (acct && acct.control) || {};
  const stop = c.dailyStop || {};
  const power = b && b.at ? (isNum(b.optionsBuyingPower) ? b.optionsBuyingPower : b.cash) : null;
  const off = !!(doc && doc.paused);
  const paused = off || !!c.paused;
  const held = !!stop.hit;
  const last = acct && homeDay(acct.lastActivity);
  const lastOrder = last ? `Last order ${last}` : "No order placed yet";
  const read = b && b.at && homeStamp(b.at) ? `Alpaca balance read ${homeStamp(b.at)} ET.` : "";
  if (!acct) {
    return { kind: "none", tone: "", title: "No paper account yet", line: "The bot has not placed a paper order.", lastOrder: "", read: "" };
  }
  if (isNum(power) && power < DESK_LOW_BUYING_POWER) {
    return {
      kind: "cannot", tone: "bad",
      title: `Cannot buy: the Alpaca paper account has ${deskDollars(power)} left`,
      line: "The bot has stopped buying. Reset or top up the paper account at Alpaca to start it again."
        + (paused ? " Buying is also paused." : ""),
      lastOrder, read,
    };
  }
  if (paused || held) {
    return {
      kind: "paused", tone: "warn", title: "Buying is paused",
      line: off ? "The paper bot is switched off. It places no new buys."
        : c.paused ? "No new buys until you resume it. Trades already open still sell on their plan."
        : "Today's loss limit was hit. No new buys until tomorrow.",
      lastOrder, read,
    };
  }
  return {
    kind: "on", tone: "on", title: "Buying is on",
    line: isNum(power) ? `The Alpaca paper account has ${deskDollars(power)} to spend.` : "The Alpaca balance has not been read yet.",
    lastOrder, read,
  };
}

/** "8 closed trades: too few to judge (needs 20)". "" when the sample is big enough or empty. */
export function homeFewNote(n, doc) {
  const need = doc && isNum(doc.smallSample) ? doc.smallSample : 20;
  if (!isNum(n) || n <= 0 || n >= need) return "";
  return `${n} closed ${n === 1 ? "trade" : "trades"}: too few to judge (needs ${need})`;
}

/** The one big number: the real result on real paper fills since the start day. */
export function homeResult(doc, acct) {
  const s = (acct && acct.stats) || {};
  const d = (acct && acct.daySummary) || {};
  const n = isNum(s.n) ? s.n : 0;
  const size = doc && doc.limits && isNum(doc.limits.accountSize) ? doc.limits.accountSize : null;
  const since = doc && homeShortDay(doc.startDay);
  const parts = [];
  if (size != null) parts.push(`from a ${deskDollars(size)} start`);
  parts.push(`${n} closed ${n === 1 ? "trade" : "trades"}`);
  if (since) parts.push(`since ${since}`);
  const days = isNum(d.tradingDays) ? d.tradingDays : 0;
  const green = isNum(d.greenDays) ? d.greenDays : 0;
  return {
    empty: !n,
    value: !n ? "$0" : deskMoney(s.netPnl),
    tone: n ? homeTone(s.netPnl) : "",
    line: parts.join(" · "),
    days: days ? `${green} winning ${green === 1 ? "day" : "days"} of ${days}` : "",
    few: homeFewNote(n, doc),
  };
}

/** The account line (days[].cum) as SVG geometry, starting from $0 on the start day. null with no days. */
export function homeSpark(days, width = 320, height = 64, pad = 5) {
  const list = (Array.isArray(days) ? days : []).filter((d) => d && isNum(d.cum));
  if (!list.length) return null;
  const pts = [0, ...list.map((d) => d.cum)];
  const hi = Math.max(...pts);
  const lo = Math.min(...pts);
  const x = (i) => Math.round(((i / (pts.length - 1)) * width) * 10) / 10;
  const y = (v) => Math.round((pad + (hi === lo ? 0.5 : (hi - v) / (hi - lo)) * (height - pad * 2)) * 10) / 10;
  const last = pts[pts.length - 1];
  return {
    width, height, zeroY: y(0), last, tone: homeTone(last),
    line: pts.map((v, i) => `${x(i)},${y(v)}`).join(" "),
    label: `Result by day, from $0 to ${deskMoney(last)}`,
  };
}

/** The verdict as the Home banner and the Ready heading say it. */
export function homeVerdict(acct) {
  const v = acct && acct.verdict;
  const sent = acct && acct.orders && acct.orders.sent;
  if (!v || !isNum(v.total) || sent === 0) {
    return { tone: "empty", title: "No paper trades yet", line: "The checks start with the first real paper fill." };
  }
  const passed = isNum(v.passed) ? v.passed : 0;
  return {
    tone: v.ready ? "ready" : "notready",
    title: v.ready ? "Ready for real money" : "Not ready for real money",
    line: `${passed} of ${v.total} checks passed`,
  };
}

/** Three tiles under the big number. Each says how many trades it rests on. */
export function homeTiles(acct) {
  const s = (acct && acct.stats) || {};
  const n = isNum(s.n) ? s.n : 0;
  const open = homeOpenRows(acct).length;
  const trades = `on ${n} ${n === 1 ? "trade" : "trades"}`;
  return [
    { key: "won", label: "Won", value: n ? `${isNum(s.wins) ? s.wins : 0} of ${n}` : "–", tone: "",
      sub: n && isNum(s.winRatePct) ? `${Math.round(s.winRatePct)}% of closed trades` : "no closed trades yet" },
    { key: "perTrade", label: "Per trade, on average", value: n && isNum(s.expectancy) ? deskMoney(s.expectancy) : "–",
      tone: n ? homeTone(s.expectancy) : "", sub: n ? trades : "no closed trades yet" },
    { key: "open", label: "Open now", value: String(open), tone: "", sub: open === 1 ? "trade" : "trades" },
  ];
}

/** The newest finding that carries a suggested change, for the Home teaser. null when there is none. */
export function homeTopLesson(doc) {
  const list = doc && doc.lessons && Array.isArray(doc.lessons.lessons) ? doc.lessons.lessons : [];
  const hit = list.find((l) => l && l.proposedChange && l.lesson) || list.find((l) => l && l.status !== "collecting" && l.lesson);
  return hit ? text(hit.lesson) : null;
}

/** The four rows at the bottom of Home. `name` is what onOpen is called with. */
export const HOME_MORE = [
  { name: "ticker", title: "Trade one ticker", sub: "Simulated in the app. No broker order" },
  { name: "scorecard", title: "Full scorecard", sub: "Every table behind these numbers" },
  { name: "research", title: "Research: every signal, simulated", sub: "Pretend fills, not real ones" },
  { name: "ask", title: "Ask the bot", sub: "Questions about a ticker or a trade" },
];

// ---------------------------------------------------------------- trades

function planOf(plan) {
  const p = plan && typeof plan === "object" ? plan : null;
  if (!p) return null;
  const hits = Array.isArray(p.hits) ? p.hits.filter((h) => h && h.target) : [];
  return {
    entryStock: isNum(p.entryStock) ? p.entryStock : null,
    stop: isNum(p.stop) ? p.stop : null,
    targets: [p.t1, p.t2, p.t3].filter(isNum),
    hits: hits.length,
  };
}

const price = (v) => (isNum(v) ? "$" + v.toFixed(2) : "–");
const stock = (v) => (isNum(v) ? v.toFixed(2) : "–");

/** When it was bought, for a row: the time, with the weekday when that was an earlier day. */
function boughtWhen(boughtAt, day) {
  const time = homeTime(boughtAt);
  if (!time) return "";
  const other = day && text(boughtAt).slice(0, 10) !== text(day).slice(0, 10);
  return other ? `bought ${homeWeekday(boughtAt)} ${time}` : time;
}

/** The closed trades of the journal, newest first, as rows and their detail. */
export function homeClosedTrades(acct) {
  const list = acct && acct.journal && Array.isArray(acct.journal.trades) ? acct.journal.trades : [];
  return list.filter((t) => t && typeof t === "object").map((t, i) => {
    const day = text(t.day || t.closedAt).slice(0, 10);
    const roi = isNum(t.roiPct) ? t.roiPct : null;
    const exit = text(t.exit).trim();
    const when = boughtWhen(t.boughtAt, day);
    const otherDay = /^bought /.test(when);
    const plan = planOf(t.plan);
    return {
      id: `c|${t.closedAt || day}|${t.symbol}|${i}`, kind: "closed", day,
      title: `${text(t.symbol)} · ${homeSide(t.side)}`,
      sub: [when, homeExitWord(exit)].filter(Boolean).join(" · "),
      pnl: isNum(t.pnl) ? t.pnl : null, value: deskMoney(t.pnl), tone: homeTone(t.pnl), roi: homePct(roi),
      detail: [
        ["Signal", homeLevel(t.rule, t.levels) || "–"],
        ["Bought", [isNum(t.qty) ? `${t.qty} at ${price(t.buy)}` : price(t.buy), otherDay ? `${homeWeekday(t.boughtAt)} ${homeTime(t.boughtAt)}` : homeTime(t.boughtAt)].filter(Boolean).join(" · ")],
        ["Sold", homeTime(t.closedAt) ? (otherDay ? `${homeWeekday(t.closedAt)} ` : "") + homeTime(t.closedAt) : "–"],
        ["Stock when bought", plan ? stock(plan.entryStock) : "–"],
        ["Stop (stock price)", plan ? stock(plan.stop) : "–"],
        ["Targets", plan && plan.targets.length ? plan.targets.map(stock).join(" · ") : "–"],
        ["Targets reached", plan && plan.targets.length ? (plan.hits ? `${Math.min(plan.hits, plan.targets.length)} of ${plan.targets.length}` : "none") : "–"],
      ],
      sentence: exit,
      // A −50% stop is a promise; when the sale filled worse than −55% the trader is told so.
      stopNote: /option down 50%/i.test(exit) && roi != null && roi < -55
        ? `The stop was set at −50% but the sale filled at ${homePct(roi)}.` : "",
    };
  });
}

const logRows = (acct) => (acct && acct.tradeLog && Array.isArray(acct.tradeLog.rows) ? acct.tradeLog.rows.filter((r) => r && typeof r === "object") : []);

/** Trades the account holds now (and orders still waiting for a fill). */
export function homeOpenRows(acct) {
  return logRows(acct).filter((r) => r.status === "open" || r.status === "waiting").map((r, i) => {
    const at = r.boughtAt || r.at;
    const waiting = r.status === "waiting";
    const plan = planOf(r.plan);
    const note = homePlainNote(r.note, waiting ? "waiting for a fill" : "holding");
    const marked = homeStamp(r.markAt);
    return {
      id: `o|${r.key || at}|${r.symbol}|${i}`, kind: "open", day: text(at).slice(0, 10),
      title: `${text(r.symbol)} · ${homeSide(r.side)}`,
      sub: [homeStamp(at), waiting ? "waiting for a fill" : "holding"].filter(Boolean).join(" · "),
      pnl: isNum(r.pnl) ? r.pnl : null, value: deskMoney(r.pnl), tone: homeTone(r.pnl),
      roi: isNum(r.pnl) ? `${homePct(r.roiPct)} so far` : "",
      detail: [
        ["Signal", homeLevel(r.rule, r.levels) || "–"],
        [waiting ? "Order" : "Bought", [isNum(r.qty) ? `${r.qty} at ${price(isNum(r.buy) ? r.buy : r.limit)}` : price(r.buy), homeTime(at)].filter(Boolean).join(" · ")],
        ["Stock when bought", plan ? stock(plan.entryStock) : "–"],
        ["Stop (stock price)", plan ? stock(plan.stop) : "–"],
        ["Targets", plan && plan.targets.length ? plan.targets.map(stock).join(" · ") : "–"],
        ["Targets reached", plan && plan.targets.length ? (plan.hits ? `${Math.min(plan.hits, plan.targets.length)} of ${plan.targets.length}` : "none") : "–"],
      ],
      sentence: (note && note !== "holding" ? note.charAt(0).toUpperCase() + note.slice(1) + ". " : "")
        + (isNum(r.pnl) ? `Not sold yet. The result is an estimate${marked ? ` from the price at ${marked}` : ""}.` : "Not sold yet."),
      stopNote: "",
    };
  });
}

/** Orders the bot sent that the broker never filled, with the saved reason. */
export function homeMissedRows(acct) {
  return logRows(acct).filter((r) => r.status === "missed").map((r, i) => {
    const plan = planOf(r.plan);
    return {
      id: `m|${r.key || r.at}|${r.symbol}|${i}`, kind: "missed", day: text(r.at).slice(0, 10),
      title: `${text(r.symbol)} · ${homeSide(r.side)}`,
      sub: [homeStamp(r.at), "not bought"].filter(Boolean).join(" · "),
      pnl: null, value: "Not bought", tone: "", roi: "",
      detail: [
        ["Signal", homeLevel(r.rule, r.levels) || "–"],
        ["Order", [isNum(r.qty) ? `${r.qty} at ${price(isNum(r.limit) ? r.limit : r.buy)}` : price(r.limit), homeTime(r.at)].filter(Boolean).join(" · ")],
        ["Stock at the signal", plan ? stock(plan.entryStock) : "–"],
        ["Targets", plan && plan.targets.length ? plan.targets.map(stock).join(" · ") : "–"],
      ],
      sentence: homePlainNote(r.note, "The order was sent and never filled."),
      stopNote: "",
    };
  });
}

export const HOME_TRADE_FILTERS = [["all", "All"], ["open", "Open"], ["won", "Won"], ["lost", "Lost"], ["missed", "Not bought"]];

export function readHomeFilter(value) {
  return HOME_TRADE_FILTERS.some(([key]) => key === value) ? value : "all";
}

/** Closed rows grouped by the day they closed, newest day first, each with its total. */
export function homeDayGroups(rows) {
  const by = new Map();
  for (const r of Array.isArray(rows) ? rows : []) {
    if (!r || !r.day) continue;
    if (!by.has(r.day)) by.set(r.day, []);
    by.get(r.day).push(r);
  }
  return [...by.keys()].sort().reverse().map((day) => {
    const list = by.get(day);
    const pnl = list.reduce((sum, r) => sum + (isNum(r.pnl) ? r.pnl : 0), 0);
    return { day, label: homeDay(day), pnl, value: deskMoney(pnl), tone: homeTone(pnl), rows: list };
  });
}

/** The Trades screen for one filter: the counted buttons, the open rows, the day groups, the not-bought rows. */
export function homeTrades(acct, filter = "all") {
  const chosen = readHomeFilter(filter);
  const closed = homeClosedTrades(acct);
  const open = homeOpenRows(acct);
  const missed = homeMissedRows(acct);
  const won = closed.filter((t) => isNum(t.pnl) && t.pnl > 0);
  const lost = closed.filter((t) => isNum(t.pnl) && t.pnl < 0);
  // The journal keeps the newest closed trades only; the count on the button is the real total.
  const total = acct && acct.journal && isNum(acct.journal.tradeCount) ? acct.journal.tradeCount : closed.length;
  const count = { all: Math.max(total, closed.length) + open.length, open: open.length, won: won.length, lost: lost.length, missed: missed.length };
  const pick = chosen === "won" ? won : chosen === "lost" ? lost : chosen === "all" ? closed : [];
  const showOpen = chosen === "all" || chosen === "open" ? open : [];
  const showMissed = chosen === "missed" ? missed : [];
  const names = { all: "No trades yet", open: "No open trades", won: "No winning trades yet", lost: "No losing trades yet", missed: "Every order was bought" };
  const lines = {
    all: "Each trade shows here once the paper account buys it.",
    open: "The bot is holding nothing right now.",
    won: "A trade shows here when it closes with a profit.",
    lost: "A trade shows here when it closes with a loss.",
    missed: "No order was refused or left unfilled.",
  };
  const nothing = !pick.length && !showOpen.length && !showMissed.length;
  return {
    filter: chosen,
    filters: HOME_TRADE_FILTERS.map(([key, label]) => ({ key, label, count: count[key], on: key === chosen })),
    open: showOpen, groups: homeDayGroups(pick), missed: showMissed,
    capped: chosen === "all" && closed.length < total ? `Showing the newest ${closed.length} of ${total} closed trades.` : "",
    empty: nothing ? { title: names[chosen], line: lines[chosen] } : null,
  };
}

// ---------------------------------------------------------------- journal

/** The six tiles on top of the journal. */
export function homeJournalTiles(doc, acct) {
  const s = (acct && acct.stats) || {};
  const d = (acct && acct.daySummary) || {};
  const n = isNum(s.n) ? s.n : 0;
  const need = doc && doc.limits && isNum(doc.limits.minProfitFactor) ? doc.limits.minProfitFactor : null;
  return [
    { key: "result", label: "Result", value: n ? deskMoney(s.netPnl) : "$0", tone: n ? homeTone(s.netPnl) : "",
      sub: `${n} closed ${n === 1 ? "trade" : "trades"}` },
    { key: "won", label: "Won", value: n && isNum(s.winRatePct) ? `${Math.round(s.winRatePct)}%` : "–", tone: "",
      sub: `${isNum(s.wins) ? s.wins : 0} wins · ${isNum(s.losses) ? s.losses : 0} losses` },
    { key: "avgWin", label: "Average win", value: isNum(s.avgWin) ? deskMoney(s.avgWin) : "–", tone: isNum(s.avgWin) ? "is-up" : "",
      sub: isNum(s.bestTrade) && n ? `best ${deskMoney(s.bestTrade)}` : "no win yet" },
    { key: "avgLoss", label: "Average loss", value: isNum(s.avgLoss) ? deskMoney(s.avgLoss) : "–", tone: isNum(s.avgLoss) ? "is-down" : "",
      sub: isNum(s.worstTrade) && n ? `worst ${deskMoney(s.worstTrade)}` : "no loss yet" },
    { key: "perDollar", label: "Made per $1 lost", value: n && isNum(s.profitFactor) ? "$" + s.profitFactor.toFixed(2) : "–", tone: "",
      sub: need != null ? `needs $${need.toFixed(2)}` : "" },
    { key: "drop", label: "Biggest drop", value: n && isNum(d.maxDrawdown) ? (d.maxDrawdown > 0 ? deskMoney(-d.maxDrawdown) : "$0") : "–",
      tone: n && isNum(d.maxDrawdown) && d.maxDrawdown > 0 ? "is-down" : "", sub: "from the highest point" },
  ];
}

const dayPeriods = (acct) => (acct && acct.journal && acct.journal.periods && Array.isArray(acct.journal.periods.day)
  ? acct.journal.periods.day.filter((p) => p && /^\d{4}-\d{2}-\d{2}$/.test(text(p.key))) : []);

/** The months that have a closed trade, oldest first ("2026-11"). */
export function homeMonths(acct) {
  return [...new Set(dayPeriods(acct).map((p) => p.key.slice(0, 7)))].sort();
}

/** The newest day with a closed trade, or "". */
export function homeNewestDay(acct, month = "") {
  const keys = dayPeriods(acct).map((p) => p.key).filter((k) => !month || k.startsWith(month)).sort();
  return keys.length ? keys[keys.length - 1] : "";
}

/** One month as a Monday-to-Friday grid of real days. A cell is null (before the 1st's weekday),
 * a plain day, or a day with trades (`has`) the trader can tap. prev / next step among the months
 * that have trades; "" when there is none that way. */
export function homeCalendar(acct, monthKey, selected = "") {
  const months = homeMonths(acct);
  const m = /^(\d{4})-(\d{2})$/.exec(text(monthKey));
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]) - 1;
  if (month < 0 || month > 11) return null;
  const byDay = {};
  for (const p of dayPeriods(acct)) byDay[p.key] = p;
  const cells = [];
  for (let d = new Date(Date.UTC(year, month, 1)); d.getUTCMonth() === month; d.setUTCDate(d.getUTCDate() + 1)) {
    const wd = d.getUTCDay();
    if (wd === 0 || wd === 6) continue;
    if (!cells.length) for (let k = 1; k < wd; k += 1) cells.push(null);
    const key = d.toISOString().slice(0, 10);
    const p = byDay[key];
    const has = !!(p && isNum(p.pnl));
    cells.push({
      key, num: d.getUTCDate(), has, selected: has && key === selected,
      // A five-figure day would not fit a phone cell: "−$12k" there, the exact figure in the heading under the calendar.
      value: has ? (Math.abs(p.pnl) >= 10000 ? deskShortMoney(p.pnl) : deskMoney(p.pnl)) : "", tone: has ? homeTone(p.pnl) : "",
      label: has ? `${homeDay(key)}: ${deskMoney(p.pnl)} on ${isNum(p.trades) ? p.trades : 0} ${p.trades === 1 ? "trade" : "trades"}` : homeDay(key),
    });
  }
  const at = months.indexOf(monthKey);
  return {
    key: monthKey, label: `${MONTHS[month]} ${year}`, cells,
    prev: at > 0 ? months[at - 1] : "", next: at >= 0 && at < months.length - 1 ? months[at + 1] : "",
  };
}

/** The heading over one day's trades: "Mon, Nov 23 · −$1,026 · 4 trades". */
export function homeDayHead(acct, day) {
  const p = dayPeriods(acct).find((x) => x.key === day);
  if (!p) return "";
  const n = isNum(p.trades) ? p.trades : 0;
  return `${homeDay(day)} · ${deskMoney(p.pnl)} · ${n} ${n === 1 ? "trade" : "trades"}`;
}

export const HOME_PERIODS = [["day", "Day"], ["week", "Week"], ["month", "Month"]];

/** The Day / Week / Month table: label, trades, won, result. */
export function homePeriodRows(acct, mode = "day") {
  const size = HOME_PERIODS.some(([key]) => key === mode) ? mode : "day";
  const list = acct && acct.journal && acct.journal.periods && Array.isArray(acct.journal.periods[size]) ? acct.journal.periods[size] : [];
  return list.filter((p) => p && p.key).map((p) => {
    const k = text(p.key);
    const mo = Number(k.slice(5, 7));
    const label = size === "month" ? (mo >= 1 && mo <= 12 ? `${MONTHS[mo - 1]} ${k.slice(0, 4)}` : k)
      : size === "week" ? (homeShortDay(k) ? `Week of ${homeShortDay(k)}` : k)
      : homeDay(k) || k;
    const n = isNum(p.trades) ? p.trades : 0;
    return { key: k, label, trades: n, won: n && isNum(p.winRatePct) ? `${Math.round(p.winRatePct)}%` : "–", value: deskMoney(p.pnl), tone: homeTone(p.pnl) };
  });
}

/** Calls against puts over the months on record. null before the first closed trade. */
export function homeCallsPuts(acct) {
  const months = acct && acct.journal && acct.journal.periods && Array.isArray(acct.journal.periods.month) ? acct.journal.periods.month : [];
  if (!months.some((p) => p && isNum(p.trades) && p.trades > 0)) return null;
  const sum = (k) => months.reduce((t, p) => t + (p && isNum(p[k]) ? p[k] : 0), 0);
  const calls = sum("calls");
  const puts = sum("puts");
  return [
    { key: "calls", label: "Calls", value: deskMoney(calls), tone: homeTone(calls) },
    { key: "puts", label: "Puts", value: deskMoney(puts), tone: homeTone(puts) },
  ];
}

// ---------------------------------------------------------------- learned

/** The review's findings in the trader's words. Nothing here is applied to the bot, and the page says so. */
export function homeLessons(doc) {
  const study = doc && doc.lessons;
  const applied = "Today nothing here changes what the bot buys.";
  if (!study || !Array.isArray(study.lessons)) {
    return { ready: false, title: "No review yet", ran: "The review runs after the market closes, on losing paper trades only.", applied, rows: [] };
  }
  const need = isNum(study.minLosses) ? study.minLosses : 10;
  const when = homeStamp(study.updatedAt);
  const rows = study.lessons.filter((l) => l && typeof l === "object").map((l, i) => {
    const lost = isNum(l.losses) ? l.losses : 0;
    const won = isNum(l.wins) ? l.wins : 0;
    const collecting = l.status === "collecting";
    return {
      key: `${l.side}|${l.rule}|${i}`,
      evidence: [`${homeSide(l.side)}s`, homeLevel(l.rule), `${lost} lost, ${won} won`, isNum(l.netPnl) ? deskMoney(l.netPnl) : ""].filter(Boolean).join(" · "),
      sentence: collecting ? `Not enough losing trades yet: ${lost} of the ${need} the review needs.` : homePlainNote(l.lesson, "The review found no clear pattern."),
      change: collecting ? "" : text(l.proposedChange).trim(),
      status: collecting ? "Still collecting trades" : "Suggestion only · not applied",
      collecting,
    };
  });
  return {
    ready: true, rows,
    title: "What the review found in the losing trades",
    ran: when ? `The review last ran ${homeWeekday(study.updatedAt)}, ${when} ET.` : "The review has run.",
    applied,
    empty: rows.length ? "" : `No findings yet. The review needs at least ${need} losing trades on a level.`,
  };
}

// ---------------------------------------------------------------- ready

const count = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** One check as a plain pass / fail line: what must be true, and the real figure. Every sentence
 * is true for its key (momx/bot_desk.py objectives); a check this page has no words for is left out. */
const CHECK_TEXT = {
  profit: (o) => [`Account is up ${deskDollars(o.target)}`, isNum(o.actual) ? `now ${deskMoney(o.actual)}` : "no result yet"],
  dailyLoss: (o) => [`No day loses more than ${deskDollars(o.target)}`,
    (isNum(o.actual) ? (o.actual < 0 ? `worst day ${deskMoney(o.actual)}` : "no losing day so far") : "no trading day yet")
      + (isNum(o.breaks) && o.breaks > 0 ? ` · broken on ${count(o.breaks, "day", "days")}` : "")],
  drawdown: (o) => [`Biggest drop stays under ${deskDollars(o.target)}`,
    isNum(o.actual) ? (o.actual > 0 ? `dropped ${deskDollars(o.actual)} from the top` : "no drop so far") : "no result yet"],
  days: (o) => [`${o.target} trading days`, `${isNum(o.actual) ? o.actual : 0} so far`],
  trades: (o) => [`${o.target} closed trades`, `${isNum(o.actual) ? o.actual : 0} so far`],
  profitFactor: (o) => [`Makes $${Number(o.target).toFixed(2)} for each $1 lost`, isNum(o.actual) ? `makes $${o.actual.toFixed(2)}` : "not enough trades yet"],
  luck: (o, limits) => [`Still up without the ${isNum(o.trades) ? o.trades : isNum(limits.luckTrades) ? limits.luckTrades : 3} best trades`,
    isNum(o.actual) ? `${deskMoney(o.actual)} without them` : "not enough trades yet"],
  fill: (o) => [`At least ${o.target}% of orders get filled`, isNum(o.actual) ? `${Math.round(o.actual)}% filled` : "no order sent yet"],
};

export function homeChecks(doc, acct) {
  const limits = (doc && doc.limits) || {};
  return ((acct && Array.isArray(acct.objectives) && acct.objectives) || [])
    .filter((o) => o && CHECK_TEXT[o.key] && isNum(o.target))
    .map((o) => {
      const [title, figure] = CHECK_TEXT[o.key](o, limits);
      return { key: o.key, pass: !!o.pass, mark: o.pass ? "Passed" : "Not passed", title, figure };
    });
}

// ---------------------------------------------------------------- the page's status line

/** What the small line under the title says about how fresh the numbers are. */
export function homeFreshness(state) {
  const doc = state && state.doc;
  const when = doc && homeStamp(doc.asOf);
  if (!doc) return state && state.failed ? { tone: "bad", text: "Could not load the bot's numbers. Trying again shortly." } : { tone: "", text: "Loading the bot's numbers…" };
  if (state.failed) return { tone: "bad", text: "Could not refresh. Showing the last numbers." + (when ? ` Read ${when} ET.` : "") };
  if (state.saved) return { tone: "warn", text: `Saved copy from ${when || "your last visit"}${when ? " ET" : ""} · updating…` };
  return { tone: "", text: when ? `Read ${when} ET` : "" };
}

// The mount (BotPanel.jsx, 2026-11-25, his "sample page is really good build it"). The new home is the
// page the Bot opens on. An older page (Scorecard / Research / Ask) remembered from before the home page
// existed must not hide it, so the first visit after it shipped lands on the home page once; after that
// a remembered older page is honoured (the older specs and the Ask dock open those pages that way).
export const HOME_SEEN_KEY = "bot.homeSeen";
export const HOME_OLD_VIEWS = ["desk", "research", "ask"];

export function botLanding(storedView, seen) {
  return HOME_OLD_VIEWS.includes(storedView) && seen === "1" ? storedView : "home";
}

/** A "More" row on the home page -> the older page it opens, and whether that is the Ticker trader. */
export function botMoreTarget(name) {
  if (name === "scorecard") return { view: "desk", ticker: null };
  if (name === "ask") return { view: "ask", ticker: null };
  if (name === "ticker") return { view: "research", ticker: true };
  if (name === "research") return { view: "research", ticker: false };
  return null;
}
