// The MomoX "Five Pillars" as a checklist (2026-09-25, his ask "build the checklist";
// 2026-10-03 rebuilt to the published definitions he pasted, and for PUTS too):
//
//   01 Trend Direction  - the EMA ribbon (9/21/50) bull vs bear across timeframes, the
//                         higher-timeframe trend, and where price sits in today's range
//                         (auto-Fibonacci retracement). Direction comes first.
//   02 Price Action     - the squeeze: contraction (High = white, Mid = orange; our TOS
//                         feed has no separate Low level) vs expansion (fired) on
//                         30m / 1h / 2h / 4h / D / W.
//   03 Market Structure - the critical time of day, strength vs weakness (the stock's % today
//                         and its sector), and the option's delta + estimated ROI if the
//                         stock reaches 2x the expected move - his rule: at least +100%.
//   04 Sell Targets     - price levels in the trade's direction: OI walls, pivot points,
//                         moving averages, Fibonacci levels of today's range, prior-day
//                         high / low, premarket high, whole-number and 5% psych levels.
//                         (No Ichimoku / supply-demand zones: we do not compute them.)
//   05 Buy Signals      - the trigger, never the reason: 9x20 / 4x8 EMA and MACD crosses,
//                         chart arrows, squeeze fired, stochastic turning, RVOL - and how
//                         many are stacked.
//
// side: "bull" (call) or "bear" (put); default = the row's own direction. Pure and
// tested (momxPillars.test.js). Inputs are what the row and the card already carry:
// row.m5.pillars (worker momx/momentum.py), row.skittles, row.sqz / row.sqzFast,
// row.highLow, row.rvol, row.sectorRotation, the chain's expected move and its walls.

import { chartArrows, isBearRow, sqzFires } from "./momxFilters.js";

const SIGNAL_WINDOW_MS = 30 * 60 * 1000;
const FIB = [0.236, 0.382, 0.5, 0.618, 0.786];

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

function sideOf(row, side) {
  if (side === "bull" || side === "bear") return side;
  return isBearRow(row) ? "bear" : "bull";
}

const BULL_BG = ["cyan", "green", "lime", "dark_green"];
const BEAR_BG = ["magenta", "red", "light_red", "plum"];

function skittlesSide(cell) {
  const c = cell || {};
  const bg = c.bg && c.bg !== "black" ? c.bg : null;
  const paint = bg || c.fg;
  if (BULL_BG.includes(paint)) return "bull";
  if (BEAR_BG.includes(paint)) return "bear";
  return null;
}

// ---------------------------------------------------------------- 01 Trend
export function trendPillar(row, side) {
  const s = sideOf(row, side);
  const p = (row && row.m5 && row.m5.pillars) || {};
  const parts = [];
  let withIt = 0;
  let total = 0;
  let mainOk = null;
  for (const tf of ["5m", "30m"]) {
    const r = p["ribbon" + tf];
    if (!r || !r.trend) continue;
    total += 1;
    const ok = r.trend === s;
    if (ok) withIt += 1;
    parts.push("EMA 9/21/50 " + tf + " " + (r.trend === "bull" ? "bull" : r.trend === "bear" ? "bear" : "mixed") + (ok ? " ✓" : ""));
    mainOk = tf === "30m" || mainOk === null ? ok : mainOk;
  }
  const sk = (row && row.skittles) || {};
  const tfs = [];
  for (const tf of ["2h", "4h", "D", "Wk"]) {
    const d = skittlesSide(sk[tf]);
    if (!d) continue;
    total += 1;
    if (d === s) {
      withIt += 1;
      tfs.push(tf);
    }
  }
  if (total === 0) return { pass: null, text: "EMA ribbon not computed yet" };
  if (Object.keys(sk).length) parts.push("higher timeframes with the " + (s === "bull" ? "call" : "put") + ": " + (tfs.join(", ") || "none"));
  // auto-Fibonacci: where the price sits in today's range
  const hl = (row && row.highLow) || {};
  const hh = num(hl.hh);
  const ll = num(hl.ll);
  const last = num(row && row.last);
  if (Number.isFinite(hh) && Number.isFinite(ll) && hh > ll && Number.isFinite(last)) {
    const pulled = s === "bull" ? (hh - last) / (hh - ll) : (last - ll) / (hh - ll);
    const near = FIB.reduce((a, b) => (Math.abs(b - pulled) < Math.abs(a - pulled) ? b : a), FIB[0]);
    parts.push(pulled <= 0.1 ? "at the day's " + (s === "bull" ? "high" : "low")
      : "retraced " + Math.round(pulled * 100) + "% of today's range (near Fib " + (near * 100).toFixed(1) + "%)");
  }
  const pass = mainOk !== false && withIt * 2 >= total;
  return { pass, text: parts.join(" · ") + " - " + withIt + " of " + total + " with the " + (s === "bull" ? "call" : "put") };
}

// ---------------------------------------------------------- 02 Price action
const SQZ_WORD = { white: "high squeeze", orange: "mid squeeze", cyan: "fired ↑", magenta: "fired ↓" };

export function squeezePillar(row, side) {
  const s = sideOf(row, side);
  const cells = { ...((row && row.sqzFast) || {}), ...((row && row.sqz) || {}) };
  const contraction = [];
  const expansion = [];
  let any = false;
  for (const tf of ["30m", "1h", "2h", "4h", "D", "Wk"]) {
    const bg = cells[tf] && cells[tf].bg;
    if (!bg) continue;
    any = true;
    if (bg === "white" || bg === "orange") contraction.push(tf + " " + SQZ_WORD[bg]);
    if (bg === "cyan" || bg === "magenta") expansion.push(tf + " " + SQZ_WORD[bg]);
  }
  if (!any) return { pass: null, text: "no squeeze reading" };
  const withFire = expansion.some((e) => e.endsWith(s === "bull" ? "↑" : "↓"));
  const against = expansion.length > 0 && !withFire;
  const text = [contraction.length ? "contraction: " + contraction.join(", ") : "",
    expansion.length ? "expansion: " + expansion.join(", ") : ""].filter(Boolean).join(" · ");
  const pass = (contraction.length > 0 || withFire) && !(against && !contraction.length);
  return { pass, text: (text || "no squeeze on any timeframe") + (pass ? "" : " - no contraction to release") };
}

// ------------------------------------------------------- 03 Market structure
export function criticalTime(nowMs) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "numeric", hourCycle: "h23" })
    .formatToParts(new Date(nowMs));
  const h = Number(parts.find((p) => p.type === "hour").value);
  const m = Number(parts.find((p) => p.type === "minute").value);
  const t = h * 60 + m;
  if (t < 570) return "premarket";
  if (t < 600) return "the open (retail)";
  if (t < 690) return "after the first 30 min (smart money)";
  if (t < 780) return "first-2h retracement / lunch";
  if (t < 900) return "afternoon";
  if (t < 960) return "power hour";
  return "after hours";
}

export function structurePillar(row, chain, side, nowMs = Date.now()) {
  const s = sideOf(row, side);
  const parts = ["time: " + criticalTime(nowMs)];
  const chg = num(row && row.pctChange);
  let strong = null;
  if (Number.isFinite(chg)) {
    strong = s === "bull" ? chg > 0 : chg < 0;      // the stock's own strength / weakness agrees with the trade
    parts.push((chg >= 0 ? "strength" : "weakness") + " " + fmtPct(chg) + " today"
      + (strong ? " (with the " + (s === "bull" ? "call" : "put") + ")" : " (against the " + (s === "bull" ? "call" : "put") + ")"));
  }
  const sec = (row && row.sectorRotation) || {};
  if (Number.isFinite(num(sec.up)) && Number.isFinite(num(sec.total)) && num(sec.total) > 0) {
    parts.push("sector " + num(sec.up) + "/" + num(sec.total) + " up");
  }
  if (!chain) return { pass: strong === false ? false : null, text: parts.join(" · ") + " · reading the option chain…" };
  // His rule 2026-10-03 ("fewer losses - exp move 2x and ROI at least 100"): the contract the
  // BOT buys must be worth >= 2x its cost if the stock reaches 2x the 1-day expected move
  // from here - the same check as momx/option_track.py risk_check and the Bot page's ✓ 2×EM.
  const spot = num(chain.underlyingPrice);
  const expiries = Array.isArray(chain.expiries) ? chain.expiries : [];
  const atmEm = num(chain.currentAtm && chain.currentAtm.expectedMove);
  const em = Number.isFinite(atmEm) && atmEm > 0 ? atmEm : num((chain.expiryExpectedMoves || {})[expiries[0]]);
  const last = Number.isFinite(num(row && row.last)) ? num(row.last) : spot;
  if (!Number.isFinite(last) || !Number.isFinite(em) || em <= 0) {
    return { pass: strong === false ? false : null, text: parts.join(" · ") + " · expected move unavailable" };
  }
  const edge = s === "bull" ? last + RISK_EM_MULTIPLE * em : last - RISK_EM_MULTIPLE * em;
  const emText = "EM ±" + em.toFixed(2) + " · 2×EM " + (s === "bull" ? "top" : "bottom") + " $" + edge.toFixed(2);
  const roi = estimatedRoi(chain, s, last, edge);
  if (!roi) return { pass: strong === false ? false : null, text: parts.join(" · ") + " · " + emText + " · no priced contract" };
  const ok = roi.roi >= RISK_MIN_ROI_PCT;
  return { pass: ok && strong !== false, text: parts.join(" · ") + " · " + emText + " · " + roi.text
    + (ok ? "" : " - under your +100% rule") };
}

const RISK_EM_MULTIPLE = 2;
const RISK_MIN_ROI_PCT = 100;

function midPrice(r) {
  const bid = num(r.bid);
  const ask = num(r.ask);
  if (bid > 0 && ask > 0) return (bid + ask) / 2;
  const mark = num(r.mark);
  return mark > 0 ? mark : num(r.last);
}

/** Delta + estimated ROI (MomoX "Expected Move / ROI"), on the SAME contract the BOT buys
 *  (momx/option_track.py pick_contract): the OTM strike nearest the price on the first
 *  expiry after today, at the mid. Valued at its intrinsic value at ``edge``. A rough
 *  guide - it ignores time value left and the bid/ask exit. */
export function estimatedRoi(chain, side, last, edge) {
  const rows = (chain && chain.selectedExpiryChainRows) || [];
  const want = side === "bull" ? "CALL" : "PUT";
  const legs = rows.filter((r) => r && String(r.side).toUpperCase() === want && r.expiry
    && num(r.days_to_expiration) >= 1 && Number.isFinite(num(r.strike)) && midPrice(r) > 0
    && (side === "bull" ? num(r.strike) > last : num(r.strike) < last));
  if (!legs.length) return null;
  const expiry = legs.map((r) => String(r.expiry).slice(0, 10)).sort()[0];
  const best = legs.filter((r) => String(r.expiry).slice(0, 10) === expiry)
    .reduce((a, b) => (Math.abs(num(b.strike) - last) < Math.abs(num(a.strike) - last) ? b : a));
  const strike = num(best.strike);
  const price = midPrice(best);
  const intrinsic = Math.max(0, side === "bull" ? edge - strike : strike - edge);
  const roi = ((intrinsic - price) / price) * 100;
  const d = Math.abs(num(best.delta));
  return {
    roi,
    text: (Number.isFinite(d) ? "Δ" + d.toFixed(2) + " " : "") + strike + (side === "bull" ? "C" : "P") + " $" + price.toFixed(2)
      + " → " + (roi >= 0 ? "+" : "") + Math.round(roi) + "% at 2×EM",
  };
}

// ---------------------------------------------------------- 04 Sell targets
function psychLevels(price, s) {
  // round numbers (MomoX: "psych levels - 5% or whole numbers"). The step grows with the
  // price: the next whole DOLLAR on a $150 stock is +0.7% away and would fail the 1%-room
  // check on almost every ticker, so $25+ stocks use $5, $250+ $10, $1000+ $50.
  const step = price < 25 ? 1 : price < 250 ? 5 : price < 1000 ? 10 : 50;
  const next = s === "bull" ? Math.floor(price / step) * step + step : Math.ceil(price / step) * step - step;
  return [{ price: next, name: "psych level" }];
}

export function targetPillar(row, walls, side) {
  const s = sideOf(row, side);
  const last = num(row && row.last);
  if (!Number.isFinite(last) || last <= 0) return { pass: null, text: "no price" };
  const p = (row && row.m5 && row.m5.pillars) || {};
  const dl = (row && row.m5 && row.m5.dayLine) || {};
  const levels = [];
  const wallList = walls ? (s === "bull" ? walls.calls : walls.puts) : null;
  for (const c of Array.isArray(wallList) ? wallList : []) {
    const strike = num(c && c.strike);
    if (Number.isFinite(strike)) levels.push({ price: strike, name: strike + (s === "bull" ? "C" : "P") + " wall" });
  }
  if (s === "bull") {
    if (Number.isFinite(num(p.prevHigh))) levels.push({ price: num(p.prevHigh), name: "prior-day high" });
    if (Number.isFinite(num(p.premarketHigh))) levels.push({ price: num(p.premarketHigh), name: "premarket high" });
  } else if (Number.isFinite(num(dl.prevLow))) {
    levels.push({ price: num(dl.prevLow), name: "prior-day low" });
  }
  // pivot points (classic floor pivots from the prior session's high / low / close)
  const zs = p.zs || {};
  const ph = Number.isFinite(num(p.prevHigh)) ? num(p.prevHigh) : num(zs.prevHigh);
  const pl = Number.isFinite(num(zs.prevLow)) ? num(zs.prevLow) : num(dl.prevLow);
  const pc = num(row && row.prevClose);
  if ([ph, pl, pc].every(Number.isFinite) && ph > pl) {
    const pp = (ph + pl + pc) / 3;
    levels.push({ price: pp, name: "pivot PP" }, { price: 2 * pp - pl, name: "pivot R1" }, { price: pp + (ph - pl), name: "pivot R2" },
      { price: 2 * pp - ph, name: "pivot S1" }, { price: pp - (ph - pl), name: "pivot S2" });
  }
  // moving averages (the ribbons' 21 / 50 EMA on 5m and 30m)
  for (const tf of ["5m", "30m"]) {
    const r = p["ribbon" + tf] || {};
    for (const k of ["ema21", "ema50"]) {
      if (Number.isFinite(num(r[k]))) levels.push({ price: num(r[k]), name: tf + " " + k.replace("ema", "EMA ") });
    }
  }
  // Fibonacci levels of today's range (and the day's high / low itself)
  const hl = (row && row.highLow) || {};
  const hh = num(hl.hh);
  const ll = num(hl.ll);
  if (Number.isFinite(hh) && Number.isFinite(ll) && hh > ll) {
    for (const f of FIB) levels.push({ price: s === "bull" ? hh - (hh - ll) * f : ll + (hh - ll) * f, name: "Fib " + (f * 100).toFixed(1) + "%" });
    levels.push(s === "bull" ? { price: hh, name: "day high" } : { price: ll, name: "day low" });
  }
  levels.push(...psychLevels(last, s));
  const prevClose = num(row && row.prevClose);
  if (Number.isFinite(prevClose) && prevClose > 0) {
    for (let k = 1; k <= 4; k += 1) {
      const lvl = prevClose * (1 + (s === "bull" ? 0.05 : -0.05) * k);
      levels.push({ price: lvl, name: (s === "bull" ? "+" : "-") + 5 * k + "% level" });
    }
  }
  const ahead = levels
    .filter((l) => (s === "bull" ? l.price > last * 1.001 : l.price < last * 0.999))
    .sort((a, b) => (s === "bull" ? a.price - b.price : b.price - a.price));
  if (!ahead.length) return { pass: false, text: "no level " + (s === "bull" ? "above" : "below") + " - price discovery" };
  const t = ahead[0];
  const room = Math.abs(pct(last, t.price));
  const text = t.name + " $" + t.price.toFixed(2) + " (" + (s === "bull" ? "+" : "-") + room.toFixed(1) + "%)"
    + (ahead[1] ? " · then " + ahead[1].name + " $" + ahead[1].price.toFixed(2) : "");
  return room >= 1.0 ? { pass: true, text } : { pass: false, text: text + " - too close" };
}

// ----------------------------------------------------------- 05 Buy signals
export function signalPillar(row, nowMs = Date.now(), side) {
  const s = sideOf(row, side);
  const hits = [];
  const recent = (ms) => Number.isFinite(ms) && nowMs - ms >= 0 && nowMs - ms <= SIGNAL_WINDOW_MS;
  const m5 = (row && row.m5) || {};
  const p = m5.pillars || {};
  if (recent(num(s === "bull" ? m5.crossUpAt : m5.crossDownAt) * 1000)) hits.push("9x20 cross " + (s === "bull" ? "up" : "down") + " 5m");
  if (recent(num(s === "bull" ? p.ema48CrossUpAt : p.ema48CrossDownAt) * 1000)) hits.push("4x8 cross " + (s === "bull" ? "up" : "down") + " 5m (early trend)");
  if (recent(num(s === "bull" ? p.macdCrossUpAt : p.macdCrossDownAt) * 1000)) hits.push("MACD cross " + (s === "bull" ? "up" : "down") + " 5m");
  const wantCall = s === "bull";
  for (const a of chartArrows(row, nowMs)) {
    const label = String(a.label || "");
    const isCall = /^C/i.test(label);
    if (!a.goneAt && isCall === wantCall && recent(Date.parse(a.seenAt || a.at || ""))) hits.push(label + " " + (a.clock || ""));
  }
  for (const f of sqzFires(row, nowMs)) {
    if (recent(f.at)) hits.push("🔥 SQZ " + f.tf + " fired");
  }
  const st = p.stoch;
  if (st && s === "bull" && num(st.k) <= 25 && num(st.k) > num(st.d)) hits.push("stoch oversold turning up");
  if (st && s === "bear" && num(st.k) >= 75 && num(st.k) < num(st.d)) hits.push("stoch overbought turning down");
  if (st && s === "bull" && num(st.k) >= 80) hits.push("stoch overbought " + Math.round(num(st.k)));
  if (st && s === "bear" && num(st.k) <= 20) hits.push("stoch oversold " + Math.round(num(st.k)));
  const rv = (row && row.rvol) || {};
  const lit = ["5m", "15m", "30m"].filter((tf) => rv[tf] && rv[tf].bg && rv[tf].bg !== "black");
  if (lit.length) hits.push("RVOL " + lit.join("/"));
  const triggers = hits.filter((h) => !h.startsWith("stoch overbought") && !h.startsWith("stoch oversold ") && !h.startsWith("RVOL"));
  const stacked = hits.filter((h) => !h.startsWith("stoch overbought") && !h.startsWith("stoch oversold ")).length;
  if (triggers.length) return { pass: true, text: stacked + " stacked: " + hits.join(" · "), stacked };
  return { pass: false, text: hits.length ? hits.join(" · ") + " - no cross or fire in the last 30 min" : "no cross or fire in the last 30 min", stacked };
}

export const PILLAR_NAMES = ["Trend", "Price action", "Market structure", "Sell target", "Buy signal"];

/** The five rows + a verdict: READY only when all five pass. side defaults to the row's direction. */
export function fivePillars(row, { chain = null, walls = null, nowMs = Date.now(), side } = {}) {
  const s = sideOf(row, side);
  const rows = [
    trendPillar(row, s),
    squeezePillar(row, s),
    structurePillar(row, chain, s, nowMs),
    targetPillar(row, walls, s),
    signalPillar(row, nowMs, s),
  ].map((r, i) => ({ ...r, name: PILLAR_NAMES[i], n: i + 1 }));
  const passed = rows.filter((r) => r.pass === true).length;
  return { rows, passed, ready: passed === 5, side: s };
}
