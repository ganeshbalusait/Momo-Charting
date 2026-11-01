// The ticker card's "today 4:00-20:00 ET" mini chart (his ask 2026-10-03: "I want same as
// momox"). Pure geometry, tested in momxDayChart.test.js; MomxTickerCard draws the SVG.
//
// x = time of day 04:00 -> 20:00 ET (premarket and after-hours shaded), y = price.
// Lines: the day's 5m closes (cyan rising / magenta falling, the board palette), the
// High-OI walls from OUR OI page (calls cyan, puts magenta, dashed, "245C 3.2k"), and the
// ±EM2 edges from the same expected move the walls box prints (top red, bottom green).

const START_MIN = 4 * 60;
const END_MIN = 20 * 60;
const OPEN_MIN = 9 * 60 + 30;
const CLOSE_MIN = 16 * 60;

function etMinute(epochSec) {
  const parts = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "numeric", hourCycle: "h23" })
    .formatToParts(new Date(epochSec * 1000));
  return Number(parts.find((p) => p.type === "hour").value) * 60 + Number(parts.find((p) => p.type === "minute").value);
}

function compact(n) {
  const v = Number(n);
  if (!Number.isFinite(v)) return "";
  return v >= 1000 ? (v / 1000).toFixed(v >= 10000 ? 0 : 1).replace(/\.0$/, "") + "k" : String(Math.round(v));
}

/**
 * bars: [[epochSec, close], ...]; walls: {calls:[{strike, openInterest}], puts, spot, expectedMove}.
 * Returns null when there are fewer than 2 bars. Coordinates are in a width x height box.
 */
export function dayChartModel(bars, walls, { width = 360, height = 120, markMs = null } = {}) {
  const pts = (Array.isArray(bars) ? bars : [])
    .map(([t, c]) => [etMinute(Number(t)), Number(c)])
    .filter(([m, c]) => m >= START_MIN && m < END_MIN && Number.isFinite(c) && c > 0);
  if (pts.length < 2) return null;
  const closes = pts.map(([, c]) => c);
  const low = Math.min(...closes);
  const high = Math.max(...closes);
  const spot = Number(walls && walls.spot) || closes[closes.length - 1];
  const em = Number(walls && walls.expectedMove) || 0;
  const band = em > 0 ? { top: spot + 2 * em, bottom: spot - 2 * em } : null;
  const levels = [];
  for (const [side, list] of [["call", walls && walls.calls], ["put", walls && walls.puts]]) {
    for (const w of Array.isArray(list) ? list : []) {
      const strike = Number(w && w.strike);
      if (Number.isFinite(strike)) levels.push({ side, price: strike, label: strike + (side === "call" ? "C" : "P") + " " + compact(w.openInterest) });
    }
  }
  // y-range: the day's prices, and the walls / band edges within half the day's range of
  // them - a far wall (Saturday: next week's expiry) must not squash the price line flat.
  const span = Math.max(high - low, high * 0.01);
  const near = (p) => p >= low - 0.5 * span && p <= high + 0.5 * span;
  const ys = [low, high, ...levels.map((l) => l.price).filter(near)];
  if (band) ys.push(...[band.top, band.bottom].filter(near));
  const yMin = Math.min(...ys);
  const yMax = Math.max(...ys);
  const pad = (yMax - yMin) * 0.06 || 1;
  const lo = yMin - pad;
  const hi = yMax + pad;
  const x = (m) => ((m - START_MIN) / (END_MIN - START_MIN)) * width;
  const y = (p) => height - ((p - lo) / (hi - lo)) * height;
  const segments = [];
  for (let i = 1; i < pts.length; i += 1) {
    // colour by the 15-minute direction (close vs 3 bars back), not bar-to-bar flicker
    const back = pts[Math.max(0, i - 3)][1];
    segments.push({ x1: x(pts[i - 1][0]), y1: y(pts[i - 1][1]), x2: x(pts[i][0]), y2: y(pts[i][1]), up: pts[i][1] >= back });
  }
  return {
    width,
    height,
    low,
    high,
    segments,
    shade: [{ x: 0, w: x(OPEN_MIN) }, { x: x(CLOSE_MIN), w: width - x(CLOSE_MIN) }],
    walls: levels.filter((l) => near(l.price)).map((l) => ({ ...l, y: y(l.price) })),
    // the nearest wall OFF the chart on each side, labelled at the edge - a thin name's only
    // walls can sit far away (ARQQ 35C at $24.80) and must not silently vanish
    offAbove: levels.filter((l) => !near(l.price) && l.price > high).sort((a, b) => a.price - b.price)[0] || null,
    offBelow: levels.filter((l) => !near(l.price) && l.price < low).sort((a, b) => b.price - a.price)[0] || null,
    // a History snapshot's moment, when it falls inside 04:00-20:00 of the drawn day
    mark: (() => {
      if (!Number.isFinite(markMs)) return null;
      const dayOf = (ms) => new Date(ms).toLocaleDateString("en-CA", { timeZone: "America/New_York" });
      const m = etMinute(markMs / 1000);
      return dayOf(markMs) === dayOf(Number(bars[0][0]) * 1000) && m >= START_MIN && m < END_MIN ? x(m) : null;
    })(),
    band: band ? { top: near(band.top) ? y(band.top) : null, bottom: near(band.bottom) ? y(band.bottom) : null } : null,
  };
}
