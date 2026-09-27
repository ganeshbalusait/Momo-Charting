// The MomoX "Five Pillars" as a checklist on the ticker card (2026-09-25, his
// ask "build the checklist"): 01 Trend -> 02 Price action -> 03 Market
// structure -> 04 Sell target -> 05 Buy signal. "The trigger, never the
// reason" - a signal (05) only counts when 01-04 already agree.
//
// Inputs are what the row and the card already carry: row.m5.pillars (EMA
// 9/21/50 ribbon, MACD / stochastic, prior-day and premarket highs, computed
// by the worker in momx/momentum.py), row.sqz (the TOS Squeeze410 cells:
// white = high squeeze, orange = mid squeeze, cyan = fired up, magenta =
// fired down, black = none), the option chain's expected move, and its call
// walls. Pure: every function here is tested in momxPillars.test.js.

import { chartArrows, sqzFires } from "./momxFilters.js";

const SIGNAL_WINDOW_MS = 30 * 60 * 1000;

function num(value) {
  if (value === null || value === undefined || value === "") return NaN;
  const n = Number(value);
  return Number.isFinite(n) ? n : NaN;
}

function pct(from, to) {
  return Number.isFinite(from) && Number.isFinite(to) && from > 0 ? ((to - from) / from) * 100 : NaN;
}

function fmtPct(value) {
  return (value >= 0 ? "+" : "") + value.toFixed(1) + "%";
}

export function trendPillar(row) {
  const p = row && row.m5 && row.m5.pillars;
  const r30 = p && p.ribbon30m;
  const r5 = p && p.ribbon5m;
  if (!r30 && !r5) return { pass: null, text: "EMA ribbon not computed yet" };
  const main = r30 || r5;
  const tf = r30 ? "30m" : "5m";
  const other = r30 && r5 ? " · 5m " + r5.trend : "";
  if (main.trend === "bull") return { pass: true, text: "9 > 21 > 50 on " + tf + " (bull)" + other };
  if (main.trend === "bear") return { pass: false, text: "9 < 21 < 50 on " + tf + " (bear)" + other };
  return { pass: false, text: "ribbon mixed on " + tf + other };
}

const SQZ_WORD = { white: "high squeeze", orange: "mid squeeze", cyan: "fired ↑", magenta: "fired ↓", black: "none" };

export function squeezePillar(row) {
  const cells = (row && row.sqz) || {};
  const parts = [];
  let pass = false;
  let any = false;
  for (const tf of ["2h", "4h"]) {
    const bg = cells[tf] && cells[tf].bg;
    if (!bg) continue;
    any = true;
    parts.push(tf + " " + (SQZ_WORD[bg] || bg));
    if (bg === "white" || bg === "orange" || bg === "cyan") pass = true;
  }
  if (!any) return { pass: null, text: "no squeeze reading" };
  return { pass, text: parts.join(" · ") + (pass ? "" : " - no contraction to release") };
}

// The day's playing field: how much of today's expected move is left above
// the price. EM from the option chain's nearest expiry; the base is the prior
// close (price - today's change).
export function structurePillar(row, chain) {
  if (!chain) return { pass: null, text: "reading the option chain…" };
  const spot = num(chain.underlyingPrice);
  const change = num(chain.todayChange);
  const expiries = Array.isArray(chain.expiries) ? chain.expiries : [];
  const em = num((chain.expiryExpectedMoves || {})[expiries[0]]);
  const last = Number.isFinite(num(row && row.last)) ? num(row.last) : spot;
  if (!Number.isFinite(spot) || !Number.isFinite(change) || !Number.isFinite(em) || em <= 0) {
    return { pass: null, text: "expected move unavailable" };
  }
  const prevClose = spot - change;
  const top = prevClose + em;
  const room = pct(last, top);
  if (room >= 0.5) return { pass: true, text: "±EM " + em.toFixed(2) + " · top $" + top.toFixed(2) + " · room " + fmtPct(room) };
  return { pass: false, text: "expected move used up (top $" + top.toFixed(2) + ", " + fmtPct(room) + ")" };
}

function psychLevel(price) {
  const step = price < 20 ? 1 : price < 100 ? 1 : price < 500 ? 5 : 10;
  return Math.floor(price / step) * step + step;
}

// Where a move stops: the nearest level ABOVE the price - a call wall, the
// prior-day high, today's premarket high, or the next psych level.
export function targetPillar(row, walls) {
  const last = num(row && row.last);
  if (!Number.isFinite(last) || last <= 0) return { pass: null, text: "no price" };
  const p = (row && row.m5 && row.m5.pillars) || {};
  const levels = [];
  for (const c of (walls && Array.isArray(walls.calls) ? walls.calls : [])) {
    const strike = num(c && c.strike);
    if (Number.isFinite(strike)) levels.push({ price: strike, name: strike + "C wall" });
  }
  if (Number.isFinite(num(p.prevHigh))) levels.push({ price: num(p.prevHigh), name: "prior-day high" });
  if (Number.isFinite(num(p.premarketHigh))) levels.push({ price: num(p.premarketHigh), name: "premarket high" });
  levels.push({ price: psychLevel(last), name: "psych level" });
  const above = levels.filter((l) => l.price > last * 1.001).sort((a, b) => a.price - b.price);
  if (!above.length) return { pass: false, text: "no level above - price discovery" };
  const t = above[0];
  const room = pct(last, t.price);
  const text = t.name + " $" + t.price.toFixed(2) + " (" + fmtPct(room) + ")";
  return room >= 1.0 ? { pass: true, text } : { pass: false, text: text + " - too close" };
}

// The trigger: a cross or a fire in the last 30 minutes.
export function signalPillar(row, nowMs = Date.now()) {
  const hits = [];
  const recent = (ms) => Number.isFinite(ms) && nowMs - ms >= 0 && nowMs - ms <= SIGNAL_WINDOW_MS;
  const m5 = (row && row.m5) || {};
  const p = m5.pillars || {};
  if (recent(num(m5.crossUpAt) * 1000)) hits.push("9x20 cross 5m");
  if (recent(num(p.macdCrossUpAt) * 1000)) hits.push("MACD cross 5m");
  for (const a of chartArrows(row, nowMs)) {
    if (recent(Date.parse(a.seenAt || a.at || ""))) hits.push(a.label + " " + a.clock);
  }
  for (const f of sqzFires(row, nowMs)) {
    if (recent(f.at)) hits.push("🔥 SQZ " + f.tf + " fired");
  }
  const st = p.stoch;
  if (st && num(st.k) <= 25 && num(st.k) > num(st.d)) hits.push("stoch oversold turning up");
  if (st && num(st.k) >= 80) hits.push("stoch overbought " + Math.round(num(st.k)));
  const buys = hits.filter((h) => !h.startsWith("stoch overbought"));
  if (buys.length) return { pass: true, text: hits.join(" · ") };
  return { pass: false, text: hits.length ? hits.join(" · ") : "no cross or fire in the last 30 min" };
}

export const PILLAR_NAMES = ["Trend", "Price action", "Market structure", "Sell target", "Buy signal"];

/** The five rows + a verdict: READY only when all five pass. */
export function fivePillars(row, { chain = null, walls = null, nowMs = Date.now() } = {}) {
  const rows = [
    trendPillar(row),
    squeezePillar(row),
    structurePillar(row, chain),
    targetPillar(row, walls),
    signalPillar(row, nowMs),
  ].map((r, i) => ({ ...r, name: PILLAR_NAMES[i], n: i + 1 }));
  const passed = rows.filter((r) => r.pass === true).length;
  return { rows, passed, ready: passed === 5 };
}
