// BOT desk scorecard (2026-11-09): the words and geometry for the prop-firm style
// page that judges the paper accounts on REAL fills only
// (GET /api/momx-scanner/bot-desk, momx/bot_desk.py). Pure, so node:test can run it.

export const DESK_URL = "/api/momx-scanner/bot-desk?days=90";
export const DESK_VIEW_KEY = "bot.view";
export const DESK_ACCOUNT_KEY = "bot.deskAccount";

/** The Scorecard is the page a trader lands on; only an explicit choice opens Research. */
export function readDeskView(stored) {
  return stored === "research" ? "research" : "desk";
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

/** One line under the title: what the numbers cover. */
export function deskCoverage(acct) {
  if (!acct || !acct.from) return "";
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
