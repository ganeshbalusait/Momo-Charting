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
    stop: stop.hit
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
  level1: "RVOL rising on 3+ timeframes, plus 3+ Setup types",
  level2: "Level 1 plus a 2h / 4h / Daily squeeze",
  level3: "Level 2 plus a Skittles box",
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
