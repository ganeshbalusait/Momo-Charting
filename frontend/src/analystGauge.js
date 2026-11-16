// The Analysts card's numbers (2026-11-11): the half-circle split by Strong Buy / Buy / Hold / Sell / Strong Sell,
// the consensus word, the price-target bar and the month-by-month mix. Pure, so node:test can run it. Every count
// and price is the server's (GET /api/long-term/analyst); this file only places them on the drawing.

export const RATINGS = [
  { key: "strongSell", label: "Strong Sell", color: "#ef4444" },
  { key: "sell", label: "Sell", color: "#f97316" },
  { key: "hold", label: "Hold", color: "#9ca3af" },
  { key: "buy", label: "Buy", color: "#4ade80" },
  { key: "strongBuy", label: "Strong Buy", color: "#16a34a" },
];
const WORDS = RATINGS.map((r) => r.label);

const count = (v) => (typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.round(v) : 0);
const price = (v) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null);

/** Whole-number percents that add up to exactly 100 (largest remainder). */
export function wholePercents(values) {
  const total = values.reduce((a, b) => a + b, 0);
  if (total <= 0) return values.map(() => 0);
  const raw = values.map((v) => (v * 100) / total);
  const out = raw.map(Math.floor);
  let left = 100 - out.reduce((a, b) => a + b, 0);
  raw.map((r, i) => [r - Math.floor(r), i]).sort((a, b) => b[0] - a[0] || a[1] - b[1]).forEach(([, i]) => { if (left > 0 && values[i] > 0) { out[i] += 1; left -= 1; } });
  return out;
}

/** The consensus from the counts: the average rating (Strong Sell 1 ... Strong Buy 5), rounded to its word. */
export function consensusWord(counts) {
  const n = RATINGS.reduce((a, r) => a + count(counts?.[r.key]), 0);
  if (!n) return "";
  const score = RATINGS.reduce((a, r, i) => a + count(counts?.[r.key]) * (i + 1), 0) / n;
  return WORDS[Math.min(4, Math.max(0, Math.round(score) - 1))];
}

/**
 * The gauge: segments left (Strong Sell, 180°) to right (Strong Buy, 0°), each as wide as its share,
 * plus the needle at the average rating. Angles in degrees, 180 = left end, 0 = right end.
 * null = no analysts.
 */
export function analystGauge(counts) {
  const values = RATINGS.map((r) => count(counts?.[r.key]));
  const total = values.reduce((a, b) => a + b, 0);
  if (!total) return null;
  const pct = wholePercents(values);
  let at = 180;
  const segments = RATINGS.map((r, i) => {
    const sweep = (values[i] / total) * 180;
    const seg = { ...r, count: values[i], pct: pct[i], from: at, to: at - sweep };
    at -= sweep;
    return seg;
  });
  const score = values.reduce((a, v, i) => a + v * (i + 1), 0) / total;
  const given = typeof counts?.consensus === "string" ? WORDS.find((w) => w.toLowerCase() === counts.consensus.trim().toLowerCase()) : "";
  return { total, segments, needle: 180 - ((score - 1) / 4) * 180, score, consensus: given || consensusWord(counts) };
}

/** A point on the half circle (centre cx, cy, radius r) at `deg` degrees; y grows downwards as on screen. */
export function arcPoint(cx, cy, r, deg) {
  const rad = (deg * Math.PI) / 180;
  return [cx + r * Math.cos(rad), cy - r * Math.sin(rad)];
}

/** SVG path for one segment's band, from `from` to `to` degrees (from > to). "" for an empty segment. */
export function arcPath(cx, cy, r, width, from, to) {
  if (!(from > to)) return "";
  const [x1, y1] = arcPoint(cx, cy, r, from);
  const [x2, y2] = arcPoint(cx, cy, r, to);
  const [x3, y3] = arcPoint(cx, cy, r - width, to);
  const [x4, y4] = arcPoint(cx, cy, r - width, from);
  const f = (n) => n.toFixed(2);
  return `M${f(x1)} ${f(y1)} A${r} ${r} 0 0 1 ${f(x2)} ${f(y2)} L${f(x3)} ${f(y3)} A${r - width} ${r - width} 0 0 0 ${f(x4)} ${f(y4)} Z`;
}

/**
 * The price-target bar: low / average / high against today's price, each as a 0-100 position on one bar
 * and its % from today's price. null = not enough to draw (no price, or no low and high).
 */
export function targetBar(target, today) {
  const now = price(today);
  const low = price(target?.low);
  const high = price(target?.high);
  const avg = price(target?.average ?? target?.mean ?? target?.avg);
  if (!now || !low || !high || high < low) return null;
  const min = Math.min(low, now);
  const max = Math.max(high, now);
  const span = max - min || 1;
  const at = (v) => Math.round(((v - min) / span) * 1000) / 10;
  const vs = (v) => Math.round(((v / now) - 1) * 1000) / 10;
  const mark = (key, label, v) => (v ? { key, label, price: v, pos: at(v), pct: vs(v) } : null);
  return { today: { price: now, pos: at(now) }, marks: [mark("low", "Low", low), mark("average", "Average", avg), mark("high", "High", high)].filter(Boolean) };
}

/** "+12.3%" / "-4.0%" */
export const signedPct = (v) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toFixed(1)}%`;

/** The month-by-month mix, oldest first, at most 6 months; each month's shares add up to 100. */
export function monthMix(months) {
  return (Array.isArray(months) ? months : []).map((m) => {
    const values = RATINGS.map((r) => count(m?.[r.key]));
    const total = values.reduce((a, b) => a + b, 0);
    const when = String(m?.period || m?.month || "").slice(0, 10);
    if (!total || !/^\d{4}-\d{2}/.test(when)) return null;
    const pct = wholePercents(values);
    return { period: when.slice(0, 7), total, parts: RATINGS.map((r, i) => ({ key: r.key, color: r.color, count: values[i], pct: pct[i] })) };
  }).filter(Boolean).sort((a, b) => a.period.localeCompare(b.period)).slice(-6);
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export const monthLabel = (period) => {
  const m = Number(String(period).slice(5, 7));
  return m >= 1 && m <= 12 ? MONTHS[m - 1] : String(period);
};

/**
 * GET /api/long-term/analyst answers momx/analyst.payload(): {symbol, status, label, views: {grades: {strongBuy, buy,
 * hold, sell, strongSell, consensus}, targets: {targetHigh, targetLow, targetConsensus, targetMedian},
 * finnhub: {months}, day, fetchedAt}, lines}. This puts it in the card's flat shape. The two sources count analysts
 * differently, so they are never added together: FMP's grades when there are any, otherwise Finnhub's newest month.
 */
export function fromAnalystRoute(raw) {
  const views = raw?.views;
  if (!views || typeof views !== "object" || Array.isArray(views)) return raw;
  const grades = views.grades && typeof views.grades === "object" ? views.grades : null;
  const months = Array.isArray(views.finnhub?.months) ? views.finnhub.months : [];
  const newest = [...months].filter((m) => m && typeof m.period === "string").sort((a, b) => b.period.localeCompare(a.period))[0] || null;
  const useGrades = grades && analystGauge(grades);
  const counts = useGrades ? grades : newest ? { ...newest, consensus: undefined } : null;
  const t = views.targets && typeof views.targets === "object" ? views.targets : null;
  return {
    symbol: raw.symbol || views.symbol || "",
    counts: counts || {},
    updated: String(views.day || views.fetchedAt || "").slice(0, 10),
    source: useGrades ? "FMP grades" : newest ? `Finnhub, ${String(newest.period).slice(0, 7)}` : "",
    target: t ? { low: t.targetLow, average: t.targetConsensus ?? t.targetMedian, high: t.targetHigh, source: "FMP" } : null,
    price: raw.price,
    months,
  };
}

/**
 * The whole card's data from the route's reply, or null to hide the card. Counts may sit at the top or under `counts`.
 * The route sends no price, so the page passes the price it already shows (`todayPrice`) for the target bar.
 */
export function analystCard(input, todayPrice = null) {
  if (!input || typeof input !== "object" || input.error) return null;
  const route = fromAnalystRoute(input);
  const raw = route.price == null && todayPrice != null ? { ...route, price: todayPrice } : route;
  const counts = raw.counts && typeof raw.counts === "object" ? { ...raw.counts, consensus: raw.consensus ?? raw.counts.consensus } : raw;
  const gauge = analystGauge(counts);
  if (!gauge) return null;
  const today = price(raw.price ?? raw.target?.price ?? raw.target?.current);
  return {
    symbol: String(raw.symbol || "").toUpperCase().slice(0, 12),
    gauge,
    updated: String(raw.updated || raw.period || "").slice(0, 10),
    source: String(raw.source || "").trim().slice(0, 60),
    target: targetBar(raw.target, today),
    targetSource: String(raw.target?.source || raw.targetSource || "").trim().slice(0, 60),
    months: monthMix(raw.months || raw.trend),
  };
}
