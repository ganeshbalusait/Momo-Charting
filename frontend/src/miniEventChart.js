// The "Technical analysis" card's data (2026-11-11): the Short / Mid / Long-Term verdicts, and the small read-only
// candle chart's bars, event markers and support / resistance lines. Pure, so node:test can run it. Every price,
// verdict and event is the server's; this file only checks them and works out where each marker sits.

const num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
const str = (v, max = 200) => (typeof v === "string" ? v.trim().slice(0, max) : "");
const VWAP = /\bvwap\b/i;   // his rule: "Don't use vwap"

export const TERMS = [
  { key: "short", label: "Short-Term" },
  { key: "mid", label: "Mid-Term" },
  { key: "long", label: "Long-Term" },
];

const TONES = { bullish: "up", bearish: "down", neutral: "flat" };
const DEFAULT_WORD = { bullish: "Bullish signal", bearish: "Bearish signal", neutral: "Neutral" };

/** One term's verdict: tone up / down / flat, the word to show, strength 0-4 and the one reason line. null = none. */
export function termVerdict(raw) {
  if (!raw || typeof raw !== "object") return null;
  const verdict = String(raw.verdict || raw.signal || "").toLowerCase();
  const key = verdict.startsWith("bull") ? "bullish" : verdict.startsWith("bear") ? "bearish" : verdict.startsWith("neut") ? "neutral" : "";
  if (!key) return null;
  const s = num(raw.strength);
  const reason = str(raw.reason, 240);
  return { verdict: key, tone: TONES[key], word: str(raw.label, 40) || DEFAULT_WORD[key], strength: s === null ? 0 : Math.max(0, Math.min(4, Math.round(s))), reason: VWAP.test(reason) ? "" : reason };
}

/** A bar's day as "YYYY-MM-DD" (what the chart library takes for daily and weekly bars). "" = unusable. */
export function barDay(time) {
  if (typeof time === "string" && /^\d{4}-\d{2}-\d{2}/.test(time)) return time.slice(0, 10);
  const n = num(time);
  if (n === null) return "";
  const ms = n > 1e12 ? n : n * 1000;
  const d = new Date(ms);
  return Number.isNaN(d.getTime()) ? "" : d.toISOString().slice(0, 10);
}

/** Bars oldest first, one per day, with sane prices. */
export function cleanBars(raw, max = 400) {
  const byDay = new Map();
  for (const b of Array.isArray(raw) ? raw : []) {
    const time = barDay(b?.time ?? b?.t ?? b?.date);
    const open = num(b?.open ?? b?.o); const high = num(b?.high ?? b?.h); const low = num(b?.low ?? b?.l); const close = num(b?.close ?? b?.c);
    if (!time || [open, high, low, close].some((v) => v === null || v <= 0) || high < low) continue;
    byDay.set(time, { time, open, high, low, close, volume: Math.max(0, num(b?.volume ?? b?.v) || 0) });
  }
  return [...byDay.values()].sort((a, b) => a.time.localeCompare(b.time)).slice(-max);
}

export const MARKER_LOOK = {
  bullish: { shape: "dot", color: "#22c55e", side: "below", title: "Bullish" },
  bearish: { shape: "square", color: "#ef4444", side: "above", title: "Bearish" },
  neutral: { shape: "diamond", color: "#9ca3af", side: "above", title: "Neutral" },
  earnings: { shape: "earnings", color: "#e5e7eb", side: "bottom", title: "Earnings" },
};

const MARKER_NAMES = 4;   // names listed on a marker that stands for several events

const kindOf = (raw) => {
  const k = String(raw || "").toLowerCase();
  return k.startsWith("bull") ? "bullish" : k.startsWith("bear") ? "bearish" : k.startsWith("earn") ? "earnings" : k.startsWith("neut") ? "neutral" : "";
};

/**
 * Where each event sits: on the last bar dated on or before it (a weekly bar holds its whole week), above the
 * high for bearish and neutral, below the low for bullish, and on the bottom edge for earnings. Events on the
 * same bar and side stack outwards (stack 0, 1, 2...). Events before the first bar, or more than one bar-gap
 * after the last, are left out. Returns markers in bar order.
 *
 * Events of the SAME kind on one bar share ONE marker that names them all (2026-11-19, NVDA: 24 bearish events
 * stacked into tall red blocks that covered the chart and the text above it). So a bar holds at most one marker
 * per kind, and a stack is never deeper than the kinds that share a side (two).
 */
export function placeMarkers(bars, events) {
  const list = Array.isArray(bars) ? bars : [];
  if (!list.length) return [];
  const gap = list.length > 1 ? daysBetween(list[list.length - 2].time, list[list.length - 1].time) : 1;
  const stacks = new Map();
  const merged = new Map();
  const out = [];
  for (const e of Array.isArray(events) ? events : []) {
    const kind = kindOf(e?.kind ?? e?.type);
    const day = barDay(e?.time ?? e?.date);
    if (!kind || !day) continue;
    let index = -1;
    for (let i = list.length - 1; i >= 0; i -= 1) { if (list[i].time <= day) { index = i; break; } }
    if (index < 0) continue;
    if (index === list.length - 1 && daysBetween(list[index].time, day) >= Math.max(1, gap)) continue;
    const look = MARKER_LOOK[kind];
    const bar = list[index];
    const name = str(e?.name ?? e?.label, 120) || look.title;
    const same = merged.get(`${index}|${kind}`);
    if (same) {
      same.count += 1;
      if (!same.names.includes(name)) same.names.push(name);
      continue;
    }
    const slot = `${index}|${look.side}`;
    const stack = stacks.get(slot) || 0;
    stacks.set(slot, stack + 1);
    const marker = {
      index, barTime: bar.time, kind, shape: look.shape, color: look.color, side: look.side, stack,
      price: look.side === "below" ? bar.low : look.side === "above" ? bar.high : null,
      name, when: str(typeof e?.time === "string" ? e.time : "", 40) || day, count: 1, names: [name],
    };
    merged.set(`${index}|${kind}`, marker);
    out.push(marker);
  }
  for (const m of out) {
    if (m.count > 1) {
      const shown = m.names.slice(0, MARKER_NAMES);
      const rest = m.names.length - shown.length;
      m.name = `${m.count} ${MARKER_LOOK[m.kind].title.toLowerCase()} events: ${shown.join("; ")}${rest > 0 ? `; and ${rest} more` : ""}`;
      m.when = m.barTime;
    }
    delete m.names;
  }
  return out.sort((a, b) => a.index - b.index || a.stack - b.stack);
}

function daysBetween(a, b) {
  return Math.round((Date.parse(`${b}T00:00:00Z`) - Date.parse(`${a}T00:00:00Z`)) / 86400000);
}

/** Support / resistance as {price, label}; a line that is VWAP is never drawn. */
export function level(raw, fallback) {
  const price = num(typeof raw === "number" ? raw : raw?.price);
  if (price === null || price <= 0) return null;
  const label = str(raw?.label, 40) || fallback;
  return VWAP.test(label) ? null : { price, label };
}

/** One timeframe's chart, or null when it has no bars. */
export function chartFrame(raw) {
  const bars = cleanBars(raw?.bars);
  if (!bars.length) return null;
  return { bars, markers: placeMarkers(bars, raw?.events), support: level(raw?.support, "Support"), resistance: level(raw?.resistance, "Resistance") };
}

/** The whole card from the server's `technical` object, or null to hide it. */
export function technicalCard(raw) {
  if (!raw || typeof raw !== "object") return null;
  const terms = raw.terms && typeof raw.terms === "object" ? raw.terms : raw;
  const pills = TERMS.map((t) => ({ ...t, verdict: termVerdict(terms[t.key]) })).filter((t) => t.verdict);
  const charts = raw.charts && typeof raw.charts === "object" ? raw.charts : raw;
  const daily = chartFrame(charts.daily);
  const weekly = chartFrame(charts.weekly);
  if (!pills.length && !daily && !weekly) return null;
  return { pills, daily, weekly, asOf: str(raw.asOf, 40), source: str(raw.source, 60), symbol: str(raw.symbol, 12).toUpperCase() };
}

/** "$123.45" for the chart's line labels. */
export const priceText = (v) => `$${v >= 1000 ? v.toFixed(0) : v.toFixed(2)}`;
