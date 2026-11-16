// BOT desk scorecard (2026-11-09): the words and geometry for the prop-firm style
// page that judges the paper accounts on REAL fills only
// (GET /api/momx-scanner/bot-desk, momx/bot_desk.py). Pure, so node:test can run it.

export const DESK_URL = "/api/momx-scanner/bot-desk?days=90";
export const DESK_VIEW_KEY = "bot.view";
export const DESK_ACCOUNT_KEY = "bot.deskAccount";

/** The Scorecard is the page a trader lands on; only an explicit choice opens Research. */
export function readDeskView(stored) {
  return stored === "research" || stored === "ask" ? stored : "desk";
}

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** "+$1,350" / "−$6,817" / "$0". A missing number is a dash, never a zero. */
export function deskMoney(v) {
  if (!isNum(v)) return "–";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return sign + "$" + Math.round(Math.abs(v)).toLocaleString("en-US");
}

/** A dollar amount with no sign, for limits and targets: "$1,000". */
export function deskDollars(v) {
  return isNum(v) ? "$" + Math.round(Math.abs(v)).toLocaleString("en-US") : "–";
}

export function deskPct(v, digits = 0) {
  return isNum(v) ? v.toFixed(digits) + "%" : "–";
}

export function deskTone(v) {
  return !isNum(v) || v === 0 ? "" : v > 0 ? "is-up" : "is-down";
}

/** "HH:MM ET" from the server's ET stamp ("2026-11-09T15:05:52-05:00"). */
export function deskClock(stamp) {
  const s = String(stamp || "");
  return s.length >= 16 ? `${s.slice(5, 10)} ${s.slice(11, 16)} ET` : "";
}

/** The one answer at the top of the page. */
export function deskVerdict(acct) {
  const v = acct && acct.verdict;
  if (!v || !acct.orders || !acct.orders.sent) {
    return { tone: "empty", title: "No paper trades yet", line: "The evaluation starts with the first real paper fill." };
  }
  if (v.ready) {
    return { tone: "ready", title: "Passed the paper evaluation",
      line: `All ${v.total} checks passed on real paper fills. Going live is still your decision; start with the smallest size.` };
  }
  return { tone: "notready", title: "Not ready for real money",
    line: `Passed ${v.passed} of ${v.total} checks on real paper fills. Every check must pass before real money.` };
}

const OBJECTIVE_TEXT = {
  profit: { label: "Profit target", help: "The account must be up by the target on real fills." },
  dailyLoss: { label: "Daily loss limit", help: "No single day may lose more than this. One bad day ends a prop-firm evaluation." },
  drawdown: { label: "Max drawdown", help: "The biggest fall from the account's highest point." },
  days: { label: "Trading days", help: "Enough days that one good week cannot pass on its own." },
  trades: { label: "Filled trades closed", help: "Enough really-filled trades to trust the averages." },
  profitFactor: { label: "Profit factor", help: "Dollars won for every dollar lost. Above 1.0 makes money." },
  luck: { label: "Not luck", help: "Still in profit after the best few trades are taken away." },
  fill: { label: "Orders filled", help: "Share of orders sent that really filled. A trade that never fills earns nothing." },
};

const clamp01 = (x) => Math.max(0, Math.min(1, x));

/** The evaluation checklist as the screen reads it: label, what it got, what it needs. */
export function deskObjectives(acct, limits = {}) {
  return ((acct && acct.objectives) || []).map((o) => {
    const text = OBJECTIVE_TEXT[o.key] || { label: o.key, help: "" };
    let actual = "–";
    let target = "";
    let progress = 0;
    const a = o.actual;
    if (o.key === "profit") {
      actual = deskMoney(a);
      target = `Needs ${deskMoney(o.target)}`;
      progress = isNum(a) ? clamp01(a / o.target) : 0;
    } else if (o.key === "dailyLoss") {
      actual = deskMoney(a);
      target = `Worst day · limit ${deskDollars(o.target)}` + (o.breaks ? ` · broken on ${o.breaks} ${o.breaks === 1 ? "day" : "days"}` : "");
      progress = isNum(a) ? (o.pass ? 1 : clamp01(Math.abs(o.target) / Math.abs(a))) : 0;
    } else if (o.key === "drawdown") {
      actual = isNum(a) ? `Fell ${deskDollars(a)}` : "–";
      target = `Limit ${deskDollars(o.target)}`;
      progress = isNum(a) ? (o.pass ? 1 : clamp01(o.target / a)) : 0;
    } else if (o.key === "days" || o.key === "trades") {
      actual = isNum(a) ? String(a) : "0";
      target = `Needs ${o.target}`;
      progress = isNum(a) ? clamp01(a / o.target) : 0;
    } else if (o.key === "profitFactor") {
      actual = isNum(a) ? a.toFixed(2) : "–";
      target = `Needs ${Number(o.target).toFixed(1)} or more`;
      progress = isNum(a) ? clamp01(a / o.target) : 0;
    } else if (o.key === "luck") {
      actual = deskMoney(a);
      target = `Without the best ${o.trades || limits.luckTrades || 3} trades · needs above $0`;
      progress = o.pass ? 1 : 0;
    } else if (o.key === "fill") {
      actual = deskPct(a);
      target = `Needs ${o.target}% or more`;
      progress = isNum(a) ? clamp01(a / o.target) : 0;
    }
    return { key: o.key, label: text.label, help: text.help, actual, target, pass: !!o.pass, progress: o.pass ? 1 : progress };
  });
}

/** Things that are wrong right now, loudest first. Empty = nothing to flag. */
export function deskAlerts(doc, acct) {
  const out = [];
  if (doc && doc.paused) out.push({ tone: "warn", text: "Paper bot is switched OFF" });
  const o = (acct && acct.orders) || {};
  if (o.stuck) {
    out.push({ tone: "bad", text: `${o.stuck} stuck ${o.stuck === 1 ? "order is" : "orders are"} holding a slot` + (o.stuckSymbols && o.stuckSymbols.length ? ` (${o.stuckSymbols.join(", ")})` : "") });
  }
  if (o.holdingOvernight) {
    out.push({ tone: "warn", text: `${o.holdingOvernight} ${o.holdingOvernight === 1 ? "position" : "positions"} from an earlier day still not sold` });
  }
  return out;
}

export const DESK_CONTROL_URL = "/api/momx-scanner/paper-control";

/** The paper bot's safety strip: is it buying, the Pause / Resume button, and
 * the daily loss stop. null when the server sent no control state. */
export function deskControl(acct, doc) {
  const c = acct && acct.control;
  if (!c) return null;
  const stop = c.dailyStop || {};
  const when = (stamp) => (deskClock(stamp) ? ` at ${deskClock(stamp)}` : "");
  // Three things can hold buys: the master off switch, this account's Pause,
  // and today's daily loss stop. The pill never says ON while any of them holds.
  const off = !!(doc && doc.paused);
  const hit = !!stop.hit;
  return {
    paused: !!c.paused,
    tone: off || hit ? "bad" : c.paused ? "warn" : "on",
    state: off ? "Paper bot is switched OFF · no buys"
      : c.paused ? `New buys PAUSED${when(c.pausedAt)}`
      : hit ? "New buys HELD by the daily loss stop"
      : "New buys are ON",
    action: c.paused ? "resume" : "pause",
    button: c.paused ? "Resume buying" : "Pause new buys",
    done: !c.paused ? "Paused. No new buys; positions already held still sell on their plan."
      : off ? "Pause lifted, but the paper bot is switched OFF, so it still places no buys."
      : hit ? "Pause lifted, but the daily loss stop still holds new buys until tomorrow."
      : "Buying is back on from the next signal.",
    stopHit: hit,
    stop: stop.on === false ? `No daily loss stop · today ${deskMoney(stop.todayPnl)}`
      : stop.hit
      ? `Daily loss stop HIT${when(stop.hitAt)} · no new buys until tomorrow`
      : `Daily loss stop ${deskDollars(stop.limit)} · today ${deskMoney(stop.todayPnl)}`,
  };
}

/** "CCJ   261106P00072000" -> "CCJ $72 put · expires 11-06". Unknown shapes pass through. */
export function deskContract(contract, symbol = "") {
  const m = /^([A-Z.]+)\s*\d{2}(\d{2})(\d{2})([CP])(\d{8})$/.exec(String(contract || "").trim());
  if (!m) return String(contract || symbol || "").trim();
  const strike = Number(m[5]) / 1000;
  return `${m[1]} $${Number.isInteger(strike) ? strike : strike.toFixed(2).replace(/0$/, "")} ${m[4] === "P" ? "put" : "call"} · expires ${m[2]}-${m[3]}`;
}

/** The stuck orders, one line each, for the Remove buttons. Rows the server
 * could not name (no key) are left out: a button that cannot work is worse than none. */
export function deskStuckRows(acct) {
  const rows = (acct && acct.orders && acct.orders.stuckRows) || [];
  return rows.filter((r) => r && typeof r.key === "string" && r.key).map((r) => ({
    key: r.key,
    symbol: String(r.symbol || ""),
    title: deskContract(r.contract, r.symbol),
    detail: (deskClock(r.buyAt) ? `sent ${deskClock(r.buyAt)}` : "sent earlier")
      + (/order not found|40410000/i.test(r.note || "") ? " · the broker has no record of it" : " · never confirmed by the broker"),
  }));
}

/** The fresh-evaluation line (2026-11-09: "start fresh scoreboard with 10k account size"):
 * when it started, what the paper bot trades, and the account balance now. */
export function deskEvaluation(doc, acct) {
  if (!doc || !doc.startDay) return "";
  const size = doc.limits && doc.limits.accountSize;
  const balance = acct && isNum(acct.balance) ? acct.balance : size;
  // The server says the paper bots still run the plan before the chart plan (doc.plan === "old":
  // the servers have not restarted since it was built). The new evaluation has not begun, and
  // this line must not say it trades the new plan.
  if (deskPlanWaiting(doc)) {
    return "The chart-plan evaluation has not started: the paper bots still trade the older plan"
      + (doc.oldStrategy ? ` (${doc.oldStrategy})` : "")
      + " until the servers are restarted. "
      // Only a restarted server splits the orders into an "earlier period" card. Before the restart the
      // payload has no plan field and the numbers on this page ARE the older plan's (2026-11-13 review).
      + (doc.plan === "old" ? "Orders sent until then are counted in the earlier period below"
        : "The numbers on this page are the older plan's")
      + ` · ${deskDollars(size)} account · balance now ${deskDollars(balance)}`;
  }
  return `Evaluation started ${doc.startDay.slice(5)}`
    + (doc.strategy ? ` · trades only ${doc.strategy}` : "")
    + ` · ${deskDollars(size)} account · balance now ${deskDollars(balance)}`;
}

/** The truth-check read for the claim card covers the same days as the evaluation
 * (1-30), so the BOT's claim and the real result are about the same trades. */
export function deskTruthDays(doc) {
  const start = Date.parse(String((doc && doc.startDay) || "") + "T00:00:00Z");
  const now = Date.parse(String((doc && doc.asOf) || "").slice(0, 10) + "T00:00:00Z");
  if (!Number.isFinite(start) || !Number.isFinite(now)) return 30;
  const n = Math.round((now - start) / 86400000) + 1;
  // Before the evaluation starts there is nothing to compare: 0 = do not read.
  return n < 1 ? 0 : Math.min(30, n);
}

/** The claim card's time label: "since 11-10" only while the read really covers the whole evaluation. */
export function deskTruthLabel(doc) {
  const start = Date.parse(String((doc && doc.startDay) || "") + "T00:00:00Z");
  const now = Date.parse(String((doc && doc.asOf) || "").slice(0, 10) + "T00:00:00Z");
  if (!Number.isFinite(start) || !Number.isFinite(now)) return "last 30 days";
  return Math.round((now - start) / 86400000) + 1 <= 30 ? `since ${doc.startDay.slice(5)}` : "last 30 days";
}

const STRATEGY_NAMES = { level1: "Level 1", level2: "Level 2", level3: "Level 3" };

/** "What the AI learned from the losing trades" (momx/loss_lessons.py), one row
 * per strategy and side. A study that has not run yet says so instead of showing nothing. */
export function deskLessons(doc, ruleLabel = (rule) => STRATEGY_NAMES[rule] || String(rule || "")) {
  const study = doc && doc.lessons;
  if (!study || !Array.isArray(study.lessons)) {
    return { ready: false, note: "The first study runs after the next market close (5:00 PM ET). It reads real losing paper trades only.", rows: [] };
  }
  const rows = study.lessons.map((x) => ({
    key: `${x.side}|${x.rule}`,
    title: `${ruleLabel(x.rule, x.side)} · ${x.side === "bear" ? "puts" : "calls"}`,
    counts: `${x.losses ?? 0} losing · ${x.wins ?? 0} winning · ${deskMoney(x.netPnl)}`,
    basis: x.basis ? `Checked against the counts: ${x.basis}.` : "",
    lesson: String(x.lesson || ""),
    change: String(x.proposedChange || ""),
    test: String(x.howToTest || ""),
    tag: x.status === "lesson" ? "AI lesson" : x.status === "numbers" ? "numbers only" : "collecting",
    tone: x.status === "lesson" ? "on" : "",
  }));
  return {
    ready: true, rows,
    note: rows.length ? `Studied ${deskClock(study.updatedAt)} · each signal counted once across both accounts · ${study.note || "Suggestions only."}`
      : `Studied ${deskClock(study.updatedAt)} · no closed paper trades in this evaluation yet.`,
  };
}

/** "Paper 3 · Rules bot": the account button says how that account chooses. */
export function deskAccountLabel(name, acct, base) {
  const mode = acct && acct.mode;
  return mode && mode.name ? `${base} · ${mode.name}` : base;
}

/** Rules bot against AI bot: the two accounts side by side and the one-line answer.
 * A leader is named only when both have enough closed real trades. */
export function deskVersus(doc, accountLabel = (a) => a) {
  const v = doc && doc.versus;
  if (!v || !Array.isArray(v.rows) || v.rows.length < 2) return null;
  const pct = (x) => (isNum(x) ? (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(1) + "%" : "–");
  const rows = v.rows.map((r) => ({
    key: r.account, name: r.name, account: accountLabel(r.account), how: r.how, leader: v.leader === r.account,
    closed: r.closed || 0, win: r.closed ? deskPct(r.winRatePct) : "–", pnl: r.closed ? deskMoney(r.pnl) : "–",
    roi: r.closed ? pct(r.roiPct) : "–", perTrade: r.closed ? deskMoney(r.perTrade) : "–",
    balance: deskDollars(r.balance), tone: r.closed ? deskTone(r.pnl) : "",
  }));
  const lead = rows.find((r) => r.leader);
  let answer;
  if (!rows.some((r) => r.closed)) answer = "No closed trades yet. Both bots start with the 9:30 AM ET buy window.";
  else if (lead) answer = `${lead.name} is ahead (${lead.roi} return, ${lead.pnl} on ${lead.closed} closed trades). That is the one to take to real money, once its account passes the checks.`;
  else if (v.allLosing) answer = "Neither is good so far: both bots have lost money on real fills.";
  else if (v.enough && !v.aiProven) answer = `Not a fair test yet: the AI chose only ${v.aiChosenClosed || 0} of the AI bot's ${v.aiClosed || 0} closed trades (it needs ${v.need}, and at least half). The rest used the rules order, so both bots traded almost the same way.`;
  else if (v.tooClose || v.ready) answer = "Too close to call: the gap between the two bots is smaller than one trade can move it.";
  else answer = `Too early to say. Each bot needs ${v.need} closed real trades; they have ${rows.map((r) => `${r.closed} (${r.name})`).join(" and ")}.`;
  return { rows, answer, tone: lead ? "on" : v.allLosing ? "bad" : "" };
}

/** One line for the AI bot: how many of its orders the AI really chose. "" for the rules bot. */
export function deskAiPicks(acct) {
  const s = acct && acct.aiPicks;
  if (!s) return "";
  const p = s.picked || {};
  const f = s.fallback || {};
  if (!p.sent && !f.sent) return "The AI has not chosen an order yet. When it has no answer in time, the rules order is used so a slot is never left waiting.";
  const part = (x, label) => `${x.sent || 0} ${label}` + (x.closed ? ` (${deskMoney(x.pnl)} on ${x.closed} closed)` : "");
  return `Orders chosen by the AI: ${part(p, "picked by the AI")} · ${part(f, "on the rules order because the AI had no answer in time")}.`;
}

const LEVEL_NAMES = { level1: "Level 1", level2: "Level 2", level3: "Level 3" };
const LEVEL_NEEDS = {
  // His rule 2026-11-10: 3+ Setup types, then any of RVOL / SQZ / Skittles; the level counts them.
  level1: "3+ Setup types and any 1 of RVOL / SQZ / Skittles",
  level2: "3+ Setup types and any 2 of RVOL / SQZ / Skittles",
  level3: "3+ Setup types and all 3: RVOL + SQZ + Skittles",
};

/** The three strategies side by side on real fills, and the one-line answer
 * to "which one is good". Never picks a winner on too few trades. */
export function deskStrategies(acct) {
  const s = acct && acct.strategies;
  if (!s || !Array.isArray(s.rows)) return null;
  const side = (x) => (x && x.closed ? `${deskMoney(x.pnl)} · ${deskPct(x.winRatePct)} of ${x.closed}` : "–");
  const rows = s.rows.map((r) => ({
    key: r.rule,
    name: LEVEL_NAMES[r.rule] || String(r.rule),
    needs: LEVEL_NEEDS[r.rule] || "",
    best: s.best === r.rule,
    tag: r.sameAs ? `same trades as ${LEVEL_NAMES[r.sameAs] || r.sameAs}` : s.best === r.rule ? "best so far" : r.closed && r.smallSample ? "too few" : "",
    closed: r.closed || 0,
    win: r.closed ? deskPct(r.winRatePct) : "–",
    pnl: r.closed ? deskMoney(r.pnl) : "–",
    pnlTone: r.closed ? deskTone(r.pnl) : "",
    roi: isNum(r.roiPct) ? (r.roiPct > 0 ? "+" : r.roiPct < 0 ? "−" : "") + Math.abs(r.roiPct).toFixed(1) + "%" : "–",
    perTrade: r.closed ? deskMoney(r.perTrade) : "–",
    calls: side(r.calls),
    puts: side(r.puts),
    callsTone: r.calls && r.calls.closed ? deskTone(r.calls.pnl) : "",
    putsTone: r.puts && r.puts.closed ? deskTone(r.puts.pnl) : "",
    filled: r.sent ? `${r.filled} of ${r.sent}` : "–",
  }));
  const total = rows.reduce((n, r) => Math.max(n, r.closed), 0);
  const bestRow = rows.find((r) => r.best);
  let answer;
  if (!total) answer = "No closed trades yet. The first Level orders go out in the 9:30 AM ET buy window.";
  else if (bestRow) answer = `Best so far: ${bestRow.name} (${bestRow.roi} return, ${bestRow.pnl} on ${bestRow.closed} closed trades).`;
  else if (s.allLosing) answer = "None is good so far: every strategy with enough trades has lost money on real fills.";
  else {
    const most = s.mostClosed || {};
    answer = `Too early to pick one. A strategy needs ${s.need} closed real trades; the most so far is ${LEVEL_NAMES[most.rule] || "one"} with ${most.closed || 0}.`;
  }
  return { rows, answer, tone: bestRow ? "on" : s.allLosing ? "bad" : "" };
}

/** One line under the title: what the numbers cover. */
export function deskCoverage(acct) {
  if (!acct || !acct.from) return "no paper orders yet in this evaluation";
  const o = acct.orders || {};
  const span = acct.from === acct.through ? acct.from : `${acct.from} to ${acct.through}`;
  return `${span} · ${o.sent} orders sent · ${o.filled} filled · ${o.missed} never filled` + (o.holding ? ` · ${o.holding} open now` : "");
}

/** The account curve as SVG geometry: running real P/L by day, with the target
 * and the drawdown floor always inside the frame so "how far off" is visible. */
export function deskCurve(days, limits = {}, width = 640, height = 200, pad = 14) {
  const list = Array.isArray(days) ? days.filter((d) => d && isNum(d.cum)) : [];
  const target = isNum(limits.profitTarget) ? limits.profitTarget : 0;
  const floor = isNum(limits.maxDrawdown) ? -limits.maxDrawdown : 0;
  const values = [0, target, floor, ...list.map((d) => d.cum)];
  const hi = Math.max(...values);
  const lo = Math.min(...values);
  const span = hi - lo || 1;
  const y = (v) => Math.round((pad + ((hi - v) / span) * (height - 2 * pad)) * 10) / 10;
  const step = list.length > 1 ? (width - 2 * pad) / (list.length - 1) : 0;
  const x = (i) => Math.round((pad + (list.length > 1 ? i * step : (width - 2 * pad) / 2)) * 10) / 10;
  const points = list.map((d, i) => ({ x: x(i), y: y(d.cum), day: d.day, cum: d.cum, pnl: d.pnl }));
  return {
    width, height, points,
    line: points.map((p) => `${p.x},${p.y}`).join(" "),
    zeroY: y(0), targetY: y(target), floorY: y(floor),
    last: points.length ? points[points.length - 1] : null,
  };
}

// ---- the Scorecard as a journal (2026-11-10: "how I see the d/w/m pnl with which strategy is good for real money")
export const JOURNAL_MODES = [["day", "Day"], ["week", "Week"], ["month", "Month"]];
export const JOURNAL_MODE_KEY = "bot.journalMode";
const MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function readJournalMode(stored) {
  return JOURNAL_MODES.some(([key]) => key === stored) ? stored : "day";
}

/** "Tue 11-10" for a day, "Week of 11-09" for a week (its Monday), "Nov 2026" for a month. */
export function journalLabel(mode, key) {
  const k = String(key || "");
  if (mode === "month") {
    const m = Number(k.slice(5, 7));
    return m >= 1 && m <= 12 ? `${MONTH_NAMES[m - 1]} ${k.slice(0, 4)}` : k;
  }
  if (mode === "week") return k.length >= 10 ? `Week of ${k.slice(5, 10)}` : k;
  const at = new Date(k + "T12:00:00Z");
  const name = Number.isNaN(at.getTime()) ? "" : ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][at.getUTCDay()];
  return k.length >= 10 ? `${name} ${k.slice(5, 10)}`.trim() : k;
}

/** The journal for one account: the periods of the chosen size with each strategy's P/L,
 * a total line, the one-line answer for that size, and the trade list. */
export function deskJournal(acct, mode = "day") {
  const j = acct && acct.journal;
  if (!j || !j.periods) return null;
  const size = readJournalMode(mode);
  const cell = (x) => (x && x.trades ? deskMoney(x.pnl) : "–");
  const rows = (j.periods[size] || []).map((p) => {
    const by = Object.fromEntries((p.strategies || []).map((s) => [s.rule, s]));
    return {
      key: p.key, label: journalLabel(size, p.key), trades: p.trades || 0, win: p.trades ? deskPct(p.winRatePct) : "–",
      pnl: deskMoney(p.pnl), tone: deskTone(p.pnl),
      level1: cell(by.level1), level2: cell(by.level2), level3: cell(by.level3),
      tone1: by.level1 && by.level1.trades ? deskTone(by.level1.pnl) : "", tone2: by.level2 && by.level2.trades ? deskTone(by.level2.pnl) : "",
      tone3: by.level3 && by.level3.trades ? deskTone(by.level3.pnl) : "",
      calls: deskMoney(p.calls), puts: deskMoney(p.puts), callsTone: deskTone(p.calls), putsTone: deskTone(p.puts),
      best: p.best ? LEVEL_NAMES[p.best] || p.best : "–",
    };
  });
  const green = (j.periods[size] || []).filter((p) => p.pnl > 0).length;
  const total = (j.periods[size] || []).length;
  const word = size === "day" ? "days" : size === "week" ? "weeks" : "months";
  // Which strategy was best most often: one count per period it led and made money.
  const led = {};
  for (const p of j.periods[size] || []) if (p.best) led[p.best] = (led[p.best] || 0) + 1;
  const top = Object.entries(led).sort((a, b) => b[1] - a[1])[0];
  const answer = !total ? "No closed trades yet. The journal fills in as real paper trades close."
    : `${green} of ${total} ${word} in profit.` + (top ? ` ${LEVEL_NAMES[top[0]] || top[0]} was the best strategy on ${top[1]} of them.` : " No strategy has made money in any of them yet.");
  const trades = (j.trades || []).map((t, i) => ({
    key: `${t.closedAt || t.day}|${t.symbol}|${i}`,
    when: deskClock(t.closedAt) || String(t.day || ""),
    symbol: String(t.symbol || ""), side: t.side === "bear" ? "Put" : "Call",
    strategy: (Array.isArray(t.levels) && t.levels.length ? t.levels : [t.rule]).map((r) => LEVEL_NAMES[r] || String(r || "")).slice(-1)[0],
    qty: t.qty, buy: isNum(t.buy) ? "$" + t.buy.toFixed(2) : "–", pnl: deskMoney(t.pnl), tone: deskTone(t.pnl),
    roi: isNum(t.roiPct) ? (t.roiPct > 0 ? "+" : t.roiPct < 0 ? "−" : "") + Math.abs(t.roiPct).toFixed(0) + "%" : "–",
    exit: String(t.exit || "–"), aiPicked: !!t.aiPicked, plan: deskPlan(t.plan),
  }));
  return { mode: size, rows, answer, trades, tradeCount: j.tradeCount || 0, shown: trades.length };
}

// ---- the last Scorecard is kept on this device and shown at once (2026-11-10: "Taking too much time")
export const DESK_SAVED_KEY = "bot.deskSaved";

/** The Scorecard saved on this device at the last visit, or null. Anything unreadable is ignored. */
export function readSavedDesk(text) {
  try {
    const doc = JSON.parse(text);
    return doc && typeof doc === "object" && typeof doc.asOf === "string" && doc.accounts && typeof doc.accounts === "object" ? doc : null;
  } catch {
    return null;
  }
}

// ---- journal dashboard (2026-11-10: "Scoreboard look like not a tradezella"): the tiles, the daily bars,
// the month calendar and the recent trades a trading journal opens on. All from the same real-fill numbers.
const clampShare = (x) => (isNum(x) ? Math.max(0, Math.min(1, x)) : null);

/** "+$1.2k" / "−$340" / "$0": money short enough for a calendar cell. */
export function deskShortMoney(v) {
  if (!isNum(v)) return "–";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  const a = Math.abs(v);
  if (a >= 10000) return `${sign}$${Math.round(a / 1000)}k`;
  if (a >= 1000) return `${sign}$${(a / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return `${sign}$${Math.round(a)}`;
}

/** The five tiles on top: net P&L, trade win %, profit factor, day win %, average win against average loss.
 * `share` (0-1) fills the tile's gauge; null = no gauge yet. Nothing is shown as a number before a trade closes. */
export function deskKpis(acct) {
  const s = (acct && acct.stats) || {};
  const d = (acct && acct.daySummary) || {};
  const n = s.n || 0;
  const days = d.tradingDays || 0;
  const avgLoss = isNum(s.avgLoss) ? Math.abs(s.avgLoss) : null;
  const ratio = isNum(s.avgWin) && avgLoss ? s.avgWin / avgLoss : null;
  return [
    { key: "net", label: "Net P&L", value: n ? deskMoney(s.netPnl) : "$0", tone: n ? deskTone(s.netPnl) : "", share: null,
      sub: `${n} closed ${n === 1 ? "trade" : "trades"}` },
    { key: "win", label: "Trade win %", value: n ? deskPct(s.winRatePct) : "–", tone: "", share: n ? clampShare((s.wins || 0) / n) : null,
      sub: `${s.wins || 0} won · ${s.losses || 0} lost` },
    { key: "factor", label: "Profit factor", value: isNum(s.profitFactor) ? s.profitFactor.toFixed(2) : "–", tone: "",
      share: isNum(s.profitFactor) ? clampShare(s.profitFactor / (1 + s.profitFactor)) : null, sub: "dollars won per dollar lost" },
    { key: "dayWin", label: "Day win %", value: days ? deskPct(((d.greenDays || 0) / days) * 100) : "–", tone: "",
      share: days ? clampShare((d.greenDays || 0) / days) : null, sub: `${d.greenDays || 0} green · ${d.redDays || 0} red` },
    { key: "avg", label: "Avg win / loss", value: ratio == null ? "–" : ratio.toFixed(2), tone: "",
      share: ratio == null ? null : clampShare(s.avgWin / (s.avgWin + avgLoss)),
      sub: `${isNum(s.avgWin) ? deskMoney(s.avgWin) : "–"} · ${isNum(s.avgLoss) ? deskMoney(s.avgLoss) : "–"}` },
  ];
}

const MONTH_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
const iso = (d) => d.toISOString().slice(0, 10);

/** "2026-11" from the server's ET stamp. */
export function deskMonthKey(stamp) {
  const s = String(stamp || "");
  return /^\d{4}-\d{2}/.test(s) ? s.slice(0, 7) : "";
}

/** The month before (-1) or after (+1) a "YYYY-MM" key. */
export function shiftMonth(key, delta) {
  const m = /^(\d{4})-(\d{2})$/.exec(String(key || ""));
  if (!m) return "";
  const d = new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1 + delta, 1));
  return d.toISOString().slice(0, 7);
}

/** One month as a journal calendar: Monday-to-Friday rows, each day with its real P&L and trade count,
 * a total for each week and for the month. Days outside the month are blank cells. */
export function deskCalendar(acct, monthKey, todayKey = "") {
  const m = /^(\d{4})-(\d{2})$/.exec(String(monthKey || ""));
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]) - 1;
  const byDay = {};
  for (const p of ((acct && acct.journal && acct.journal.periods && acct.journal.periods.day) || [])) byDay[p.key] = p;
  const first = new Date(Date.UTC(year, month, 1));
  const last = new Date(Date.UTC(year, month + 1, 0));
  const monday = new Date(first);
  monday.setUTCDate(first.getUTCDate() - ((first.getUTCDay() + 6) % 7));       // the Monday on or before the 1st
  const weeks = [];
  const total = { pnl: 0, days: 0, trades: 0 };
  for (let start = new Date(monday); start <= last; start.setUTCDate(start.getUTCDate() + 7)) {
    const cells = [];
    const week = { pnl: 0, days: 0, trades: 0 };
    for (let i = 0; i < 5; i += 1) {
      const d = new Date(start);
      d.setUTCDate(start.getUTCDate() + i);
      const key = iso(d);
      const inMonth = d.getUTCMonth() === month;
      const hit = inMonth ? byDay[key] : null;
      if (hit && hit.trades) {
        week.pnl += hit.pnl; week.days += 1; week.trades += hit.trades;
      }
      cells.push({ key, num: d.getUTCDate(), inMonth, isToday: inMonth && key === todayKey, trades: hit ? hit.trades || 0 : 0,
        pnl: hit && hit.trades ? deskShortMoney(hit.pnl) : "", tone: hit && hit.trades ? deskTone(hit.pnl) : "",
        title: hit && hit.trades ? `${key}: ${deskMoney(hit.pnl)} on ${hit.trades} ${hit.trades === 1 ? "trade" : "trades"}` : key });
    }
    if (!cells.some((c) => c.inMonth)) continue;                                 // a row with no weekday of this month
    total.pnl += week.pnl; total.days += week.days; total.trades += week.trades;
    weeks.push({ key: cells[0].key, cells, pnl: week.days ? deskShortMoney(week.pnl) : "–", tone: week.days ? deskTone(week.pnl) : "",
      days: week.days, trades: week.trades });
  }
  return { key: monthKey, label: `${MONTH_LONG[month]} ${year}`, prev: shiftMonth(monthKey, -1), next: shiftMonth(monthKey, 1), weeks,
    pnl: total.days ? deskMoney(total.pnl) : "$0", tone: total.days ? deskTone(total.pnl) : "", days: total.days, trades: total.trades };
}

/** Net daily P&L as bars around a zero line (SVG geometry). */
export function deskDailyBars(days, width = 640, height = 200, pad = 14) {
  const list = (Array.isArray(days) ? days : []).filter((d) => d && isNum(d.pnl) && d.closed);
  const hi = Math.max(0, ...list.map((d) => d.pnl));
  const lo = Math.min(0, ...list.map((d) => d.pnl));
  const span = hi - lo || 1;
  const y = (v) => Math.round((pad + ((hi - v) / span) * (height - 2 * pad)) * 10) / 10;
  const slot = list.length ? (width - 2 * pad) / list.length : 0;
  const w = Math.max(2, Math.min(34, slot * 0.7));
  const bars = list.map((d, i) => {
    const top = y(Math.max(d.pnl, 0));
    const bottom = y(Math.min(d.pnl, 0));
    return { day: d.day, pnl: d.pnl, tone: deskTone(d.pnl), x: Math.round((pad + i * slot + (slot - w) / 2) * 10) / 10, y: top,
      w: Math.round(w * 10) / 10, h: Math.max(1, Math.round((bottom - top) * 10) / 10) };
  });
  return { width, height, zeroY: y(0), bars };
}

// ---- The chart plan (2026-11-12, his "we have in chart 'entry, exit and target' use that entry the
// position and target 1,2,3 ... and stop loss"): the same Entry / Stop / T1 / T2 / T3 the chart draws,
// on every paper order and every Bot trade (momx/chart_plan.py, a tested port of the chart's formulas).

/** The plan in his words, for the rules text on the Bot page. One sentence per line.
 * (2026-11-13: the first line said "No new buys from 9:30 to 10:00"; he took that rule back the
 * same morning: "Remove 10 am rule on bot also if you see the setup then trade it".) */
export const DESK_PLAN_RULES = [
  "Buys go in from 9:30 to 11:00 AM and from 1:00 to 3:30 PM ET, as soon as the setup is there. From 9:30 to 9:45 the ticker only has to be in the top of its board; from 9:45 it has to stay there for 5 minutes. A signal with no chart plan (no stop and no targets could be worked out) is not bought.",
  "Entry: a limit order at the option's ask when the signal comes (the 0.20-delta contract, 3 or more contracts).",
  "Stop: the stock price where the option is down 50% from the entry, from the chart's own formula. The moment the stock trades through it, everything is sold. As a backstop, the option itself down 50% on a fresh quote also sells everything on the first tick.",
  "Targets on the stock: T1 is half the expected move, T2 is the full expected move, T3 is the biggest open-interest strike past the move (1.5 times the move when there is none).",
  "With 3 contracts: sell 1 at T1 and move the stop to break-even (the stock price at entry); sell 1 at T2 and move the stop to T1; sell the last at T3. With 2 contracts: T1 and T2. With 1: T2.",
  "Also sold: when a 5-minute candle closes past the 21 EMA, and whatever is left at the 3:50 PM close.",
];

/** Entry / Stop / T1 / T2 / T3 of one trade as the tables show them. `plan` is the Scorecard's shape
 * ({entryAsk, entryStock, stop, stopNow, stopKind, t1, t2, t3, hits, ready, status, pct, reason});
 * a trade from before the chart plan has none and shows dashes. A reached target carries a check
 * mark and the time; a stop that moved shows where it stands now. */
export function deskPlan(plan) {
  if (!plan || typeof plan !== "object") {
    return { has: false, entry: "–", stock: "–", stop: "–", t1: "–", t2: "–", t3: "–", hit: [], result: "" };
  }
  const hits = {};
  for (const h of Array.isArray(plan.hits) ? plan.hits : []) if (h && h.target) hits[h.target] = String(h.at || "").slice(11, 16);
  const target = (name, v) => (isNum(v) ? v.toFixed(2) + (name in hits ? " ✓" + (hits[name] ? " " + hits[name] : "") : "") : "–");
  const moved = isNum(plan.stop) && isNum(plan.stopNow) && plan.stopNow !== plan.stop;
  const stop = isNum(plan.stop)
    ? plan.stop.toFixed(2) + (moved ? ` → ${plan.stopNow.toFixed(2)} (${plan.stopKind === "T1" ? "T1" : "break-even"})` : "")
    : plan.ready === false ? "waiting for the chain" : "–";
  const pct = isNum(plan.pct) ? (plan.pct > 0 ? "+" : plan.pct < 0 ? "−" : "") + Math.abs(plan.pct).toFixed(0) + "%" : "";
  return {
    has: true,
    entry: isNum(plan.entryAsk) ? "$" + plan.entryAsk.toFixed(2) : "–",
    stock: isNum(plan.entryStock) ? plan.entryStock.toFixed(2) : "–",
    stop,
    t1: target("T1", plan.t1), t2: target("T2", plan.t2), t3: target("T3", plan.t3),
    hit: Object.keys(hits),
    // Only a Bot signal no order was sent for carries a result here: what three contracts would have done.
    result: plan.status === "closed" && pct ? `on the plan ${pct}${plan.reason ? " (" + plan.reason + ")" : ""}`
      : plan.status === "open" || plan.status === "closing" ? "on the plan: still running" : "",
  };
}

/** A paper order as the paper bot saves it (momx/paper_mirror.py) -> the plan shape above.
 * Null for an order from before the chart plan. */
export function paperRowPlan(r) {
  if (!r || (r.exitPlan !== "chart" && !r.plan)) return null;
  const p = r.plan || {};
  return { entryAsk: r.entryAsk, entryStock: r.entryStock, stop: r.stopStock0, stopNow: r.stopStock, stopKind: r.stopKind,
    t1: p.t1, t2: p.t2, t3: p.t3, hits: r.hits || [], ready: !!r.plan };
}

/** A Bot tracked trade (momx/option_track.py) -> the plan shape above. Null when it has no plan
 * (a signal outside the buy windows keeps only its note). */
export function botTradePlan(t) {
  const p = t && t.plan;
  if (!p || typeof p !== "object") return null;
  const s = (t.planState && typeof t.planState === "object") ? t.planState : {};
  return { entryAsk: p.entry, entryStock: p.entrySpot, stop: p.stopStock, stopNow: s.stopStock, stopKind: s.stopKind,
    t1: p.t1, t2: p.t2, t3: p.t3, hits: s.hits || [], ready: true, status: s.status, pct: s.pct, reason: s.reason };
}

/** The Bot trade's plan in one line for the Research table: the numbers, then what happened. */
export function botTradePlanLine(t) {
  const plan = botTradePlan(t);
  if (!plan) return t && t.planNote ? String(t.planNote) : "–";
  const cells = deskPlan(plan);
  const line = `Entry ${cells.entry} · Stop ${cells.stop} · T1 ${cells.t1} · T2 ${cells.t2} · T3 ${cells.t3}`;
  return cells.result ? `${line} · ${cells.result}` : line;
}

/** How the bots trade while the paper bots still run the plan BEFORE the chart plan. */
export const DESK_OLD_PLAN_RULES = [
  "Buys go in from 9:30 to 11:00 AM and from 1:00 to 3:30 PM ET: a limit order at the middle of the bid and ask, sent again at the ask when it does not fill.",
  "At +100% on the option, or when the stock reaches the high-OI level ahead of it, 2 of 3 contracts are sold and the stop on the rest moves to break-even; at +200% the stop moves to +100%.",
  "Stop: the option down 50% (seen on two checks in a row). All sales are limit orders at the middle.",
  "Also sold: when a 5-minute candle closes past the 21 EMA, and whatever is left at the 3:50 PM close.",
];

/** True when the SERVER says the running paper bots trade the chart plan (momx/bot_desk.py
 * "plan": "chart", from the marker each bot writes when it starts). A payload without the field
 * comes from a server that has not restarted since the chart plan was built: not active. */
export function deskPlanActive(doc) {
  return !!doc && doc.plan === "chart";
}

/** True when a Scorecard is on screen and the server does NOT say the chart plan runs. */
export function deskPlanWaiting(doc) {
  return !!doc && !!doc.startDay && !deskPlanActive(doc);
}

/** The "How the bot trades" paragraph: the chart plan only when the server says it is running;
 * otherwise the older plan, with a plain note that the new one waits for a restart. */
export function deskPlanText(doc) {
  if (deskPlanActive(doc)) {
    const since = String(doc.planSince || doc.startDay || "").slice(5, 10);
    return { active: true,
      title: `How the bot trades${since ? ` since ${since}` : ""} (the chart's Entry / Target / Stop plan):`,
      rules: DESK_PLAN_RULES, note: "" };
  }
  return { active: false,
    title: "How the bot trades right now (the older plan is still running):",
    rules: DESK_OLD_PLAN_RULES,
    note: "The chart's Entry / Target / Stop plan (buy at the ask, stock-price stop, T1 / T2 / T3) is built but not running yet. It starts when the servers are restarted. Orders sent until then follow the older plan above and are never counted in the chart plan's evaluation." };
}

/** The evaluations that ended (the plan before the chart plan): kept visible, never added to the
 * current one. One card each: when, what it traded, why it ended, its own totals and closed trades. */
export function deskEarlier(acct) {
  const list = acct && Array.isArray(acct.earlier) ? acct.earlier : [];
  return list.filter((p) => p && p.startDay && p.endDay).map((p) => {
    const closed = p.trades || 0;
    const parts = [`${closed} closed trade${closed === 1 ? "" : "s"}`];
    if (closed) {
      parts.push(`${p.wins || 0} win${p.wins === 1 ? "" : "s"}${isNum(p.winRatePct) ? ` (${p.winRatePct.toFixed(0)}%)` : ""}`);
      parts.push(`net ${deskMoney(p.netPnl)}`);
      if (isNum(p.worstTrade)) parts.push(`worst trade ${deskMoney(p.worstTrade)}`);
      if (isNum(p.bestTrade)) parts.push(`best trade ${deskMoney(p.bestTrade)}`);
    }
    if (p.stillOpen) parts.push(`${p.stillOpen} still open on the old exit plan`);
    // Orders the bot sent under the older plan after the period's last day (it trades the older
    // plan until it is restarted): counted here, never in the chart plan's evaluation.
    if (p.afterSwitch > 0) {
      parts.push(`includes ${p.afterSwitch} older-plan order${p.afterSwitch === 1 ? "" : "s"} sent after ${p.endDay.slice(5)}, before the bot was restarted`);
    }
    return {
      key: p.startDay,
      title: `Earlier period: ${p.startDay.slice(5)} to ${p.endDay.slice(5)}`,
      strategy: String(p.strategy || ""), why: String(p.why || ""),
      summary: parts.join(" · "), tone: closed ? deskTone(p.netPnl) : "",
      trades: (Array.isArray(p.rows) ? p.rows : []).map((t, i) => ({
        key: `${t.closedAt || t.day}|${t.symbol}|${i}`,
        when: deskClock(t.closedAt) || String(t.day || ""),
        symbol: String(t.symbol || ""), side: t.side === "bear" ? "Put" : "Call",
        qty: t.qty, buy: isNum(t.buy) ? "$" + t.buy.toFixed(2) : "–", pnl: deskMoney(t.pnl), tone: deskTone(t.pnl),
        roi: isNum(t.roiPct) ? (t.roiPct > 0 ? "+" : t.roiPct < 0 ? "−" : "") + Math.abs(t.roiPct).toFixed(0) + "%" : "–",
        exit: String(t.exit || "–"),
      })),
    };
  });
}

// ---- "All trades" (2026-11-10: "Scoreboard I don't see all trade"): every signal and what became of it.
export const TRADE_FILTER_KEY = "bot.deskTradeFilter";
export const TRADE_FILTERS = [["all", "All"], ["open", "Open"], ["closed", "Closed"], ["missed", "Not filled"], ["signal", "Not ordered"], ["skipped", "Skipped"]];
const TRADE_STATUS = { open: "Open", closed: "Closed", missed: "Not filled", waiting: "Waiting", skipped: "Skipped", signal: "Not ordered" };

export function readTradeFilter(value) {
  return TRADE_FILTERS.some(([key]) => key === value) ? value : "all";
}

/** The trade list for one filter. "Open" also holds orders still waiting for a fill. Before the server sends
 * the full list (an older server), the closed trades of the journal are shown so the card is never blank. */
export function deskTradeLog(acct, filter = "all", todayKey = "") {
  if (!acct) return null;
  const chosen = readTradeFilter(filter);
  const log = acct.tradeLog;
  const source = log && Array.isArray(log.rows) ? log.rows
    : ((acct.journal && acct.journal.trades) || []).map((t) => ({ ...t, status: "closed", at: t.closedAt || t.day, note: t.exit, live: false }));
  const counts = log && log.counts ? log.counts : { open: 0, closed: source.length, missed: 0, waiting: 0, skipped: 0 };
  const total = log && isNum(log.total) ? log.total : source.length;
  const inFilter = (status) => chosen === "all" || status === chosen || (chosen === "open" && status === "waiting");
  const rows = source.filter((t) => inFilter(t.status)).map((t, i) => {
    const hasPnl = isNum(t.pnl);
    return {
      key: `${t.key || t.at}|${t.symbol}|${i}`,
      when: deskClock(t.at) || String(t.at || ""),
      symbol: String(t.symbol || ""), side: t.side === "bear" ? "Put" : "Call",
      strategy: (Array.isArray(t.levels) && t.levels.length ? t.levels : [t.rule]).map((r) => LEVEL_NAMES[r] || String(r || "")).slice(-1)[0],
      status: t.status, statusLabel: TRADE_STATUS[t.status] || String(t.status || ""),
      qty: isNum(t.qty) ? t.qty : "–", buy: isNum(t.buy) ? "$" + t.buy.toFixed(2) : "–",
      // An open trade's estimate says how fresh its price is: "now" today, else the time of the last price.
      pnl: hasPnl ? deskMoney(t.pnl) + (!t.live ? "" : !t.markAt || !todayKey || String(t.markAt).slice(0, 10) === todayKey ? " now" : ` at ${deskClock(t.markAt)}`) : "–",
      tone: hasPnl ? deskTone(t.pnl) : "",
      roi: isNum(t.roiPct) ? (t.roiPct > 0 ? "+" : t.roiPct < 0 ? "−" : "") + Math.abs(t.roiPct).toFixed(0) + "%" : "–",
      note: String(t.note || "–"), aiPicked: !!t.aiPicked, live: !!t.live,
      fill: FILL_LABELS[t.fillChance] || "", fillKey: FILL_LABELS[t.fillChance] ? t.fillChance : "",
      plan: deskPlan(t.plan),
    };
  });
  const count = { all: total, open: (counts.open || 0) + (counts.waiting || 0), closed: counts.closed || 0, missed: counts.missed || 0,
    signal: counts.signal || 0, skipped: counts.skipped || 0 };
  // The bot no longer writes skipped rows; the Skipped button only shows while there is one (or it is chosen).
  // "Not ordered" (Bot signals this bot sent no order for) shows once the server sends them; Skipped only while there is one.
  const filters = TRADE_FILTERS.filter(([key]) => (key !== "skipped" || count.skipped || chosen === "skipped")
    && (key !== "signal" || count.signal || chosen === "signal"))
    .map(([key, label]) => ({ key, label: `${label} ${count[key]}`, on: key === chosen }));
  const empty = !total ? "No trades yet. Every Level signal shows here the moment the bot acts on it."
    : !rows.length ? `Nothing under ${TRADE_FILTERS.find(([key]) => key === chosen)[1]} right now.` : "";
  // Only the oldest not-filled / skipped rows are ever left out; say so against the count on the button.
  const shownOfAll = rows.length < count[chosen] ? `newest ${rows.length} of ${count[chosen]}` : "";
  return { filter: chosen, filters, rows, total, empty, capped: shownOfAll, full: !!(log && Array.isArray(log.rows)) };
}

/** The earliest month the calendar may show: the first month with a result, never later than the current one. */
export function deskFirstMonth(acct, thisMonth) {
  const keys = ((acct && acct.journal && acct.journal.periods && acct.journal.periods.day) || []).map((p) => String(p.key || "").slice(0, 7)).filter(Boolean);
  return [thisMonth, ...keys].filter(Boolean).sort()[0] || "";
}

// ---- the real money in the Alpaca paper account (2026-11-10: "scoreboard should show aplaca buying power")
/** A typical Level trade costs about this much; less buying power than this means most buys are refused. */
export const DESK_LOW_BUYING_POWER = 300;

export function deskBroker(acct, accountSize = 10000, today = "") {
  const b = acct && acct.broker;
  if (!b || !b.at) return null;
  const bp = isNum(b.optionsBuyingPower) ? b.optionsBuyingPower : b.cash;
  const change = isNum(b.equity) && isNum(b.lastEquity) ? b.equity - b.lastEquity : null;
  const old = today && String(b.at).slice(0, 10) !== today;
  const low = isNum(bp) && bp < DESK_LOW_BUYING_POWER;
  const short = isNum(b.equity) && isNum(accountSize) && b.equity < accountSize * 0.5;
  return {
    buyingPower: deskDollars(bp), cash: deskDollars(b.cash), equity: deskDollars(b.equity),
    change: change == null ? "" : deskMoney(change), changeTone: change == null ? "" : deskTone(change),
    asOf: deskClock(b.at), old: !!old, tone: low ? "bad" : short ? "warn" : "on",
    warning: low ? `Under ${deskDollars(DESK_LOW_BUYING_POWER)} to spend: Alpaca will refuse most buys until the paper account is topped up.`
      : short ? `The Scorecard counts a ${deskDollars(accountSize)} account; the paper account at Alpaca holds ${deskDollars(b.equity)}.`
      : "",
    error: b.error ? "Last read failed; showing the read before it." : "",
  };
}

// ---- fill likelihood, like Robinhood's order screen (2026-11-10: "yes same like robinhood")
const FILL_LABELS = { high: "High fill", medium: "Medium fill", low: "Low fill" };

/** How each label really did, for the line under All trades. null when no labelled order has finished. */
export function deskFillChance(acct) {
  const m = acct && acct.fillChance;
  if (!m) return null;
  const parts = ["high", "medium", "low"].map((key) => {
    const s = m[key] || {};
    const n = s.orders || 0;
    return { key, label: FILL_LABELS[key], orders: n,
      text: n ? `${FILL_LABELS[key]}: ${s.filledAtMid} of ${n} filled at the mid (${s.midPct}%)${s.enough ? "" : " · too few yet"}` : `${FILL_LABELS[key]}: no orders yet` };
  });
  if (!parts.some((p) => p.orders)) return null;
  return { parts };
}
