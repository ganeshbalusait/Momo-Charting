// ROI side on Auto (2026-11-26, Ganesh: "I have to choose call or puts or both, can we do automatic based on
// trend or scanner?" -> "Build it now"). Above today's open = Call, below = Put.
//
// Why this rule: of ten trend rules tried on the Mag7 back-test, it was the only one that pointed the right way in
// every sample (target reached 12.4% vs 10.5% Jul-Sep, 17.9% vs 14.7% Sep-Nov, 24.6% vs 16.6% on real option
// prices Nov 10-25). A small lean, not a proven edge. VWAP, the 9/21 EMA cross and the last 30 minutes netted to
// nothing; following the daily trend was worse than going against it.

// Inside this distance of the open the side is kept, so a price sitting on the open does not flip the lines.
export const AUTO_SIDE_BAND = 0.0005;

const OPEN_MINUTES = 9 * 60 + 30;

const EASTERN = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

function easternParts(date) {
  const parts = Object.fromEntries(EASTERN.formatToParts(date).map((part) => [part.type, part.value]));
  return { day: `${parts.year}-${parts.month}-${parts.day}`, minutes: Number(parts.hour) * 60 + Number(parts.minute) };
}

function sideOf(side) {
  return String(side || "").toUpperCase() === "PUT" ? "PUT" : "CALL";
}

// Today's regular-session open, or 0 when there is none yet (before 9:30 AM ET, a weekend, a holiday). An intraday
// chart gives it as the open of the candle that starts at 9:30 (up to 9:59 for a chart whose first candle starts
// late); a daily chart gives it as today's daily candle.
export function roiSessionOpen({ dailyBars = [], chartBars = [], now = new Date() } = {}) {
  const today = easternParts(now);
  if (today.minutes < OPEN_MINUTES) return 0;
  // Today's 9:30 as a clock time, worked out once, so the walk below compares plain numbers (this runs on every
  // price tick of every chart).
  const openAt = Math.floor(now.getTime() / 60000) * 60 - (today.minutes - OPEN_MINUTES) * 60;
  const intraday = Array.isArray(chartBars) ? chartBars : [];
  let first = null;
  for (let index = intraday.length - 1; index >= 0; index -= 1) {
    const bar = intraday[index];
    const seconds = Number(bar?.time);
    if (!Number.isFinite(seconds)) continue;
    if (seconds < openAt) break;
    // The earliest candle in the 9:30-9:59 window is the open.
    if (seconds < openAt + 30 * 60 && Number(bar.open) > 0) first = bar;
  }
  if (first) return Number(first.open);
  const daily = Array.isArray(dailyBars) ? dailyBars : [];
  for (let index = daily.length - 1; index >= 0; index -= 1) {
    const bar = daily[index];
    const seconds = Number(bar?.time);
    // A daily candle's time is midnight UTC of its date, so its date is read in UTC.
    const day = bar?.date ? String(bar.date).slice(0, 10)
      : Number.isFinite(seconds) ? new Date(seconds * 1000 + 12 * 3600 * 1000).toISOString().slice(0, 10) : "";
    if (!day) continue;
    if (day < today.day) break;
    if (day === today.day && Number(bar.open) > 0) return Number(bar.open);
  }
  return 0;
}

// The side Auto wants, or null when it cannot say (no open yet, no price).
export function roiAutoSide({ spot = 0, open = 0, current = "" } = {}) {
  const price = Number(spot);
  const start = Number(open);
  if (!(price > 0) || !(start > 0)) return null;
  const edge = Math.round(start * AUTO_SIDE_BAND * 1e6) / 1e6;
  if (price - start >= edge - 1e-9) return "CALL";
  if (start - price >= edge - 1e-9) return "PUT";
  return current ? sideOf(current) : null;
}

export function roiAutoNote({ ticker = "", spot = 0, open = 0, side = "CALL" } = {}) {
  const name = sideOf(side) === "PUT" ? "Put" : "Call";
  const price = Number(spot);
  const start = Number(open);
  if (!(start > 0) || !(price > 0)) return `Auto: waiting for today's open (9:30 AM ET) · ${name} kept`;
  const wanted = roiAutoSide({ spot: price, open: start });
  if (!wanted) return `Auto: ${name} · ${ticker} ${price.toFixed(2)} is right at today's open ${start.toFixed(2)}, side kept`;
  return `Auto: ${name} · ${ticker} ${price.toFixed(2)} is ${wanted === "CALL" ? "above" : "below"} today's open ${start.toFixed(2)}`;
}

// Which toolbar button is lit for a plan.
export function roiViewOf(plan) {
  if (!plan) return "";
  if (plan.auto === true) return "AUTO";
  return plan.both === true ? "BOTH" : sideOf(plan.side);
}

// The phone's one button steps Call -> Put -> Both -> Auto.
export function roiNextView(view) {
  return view === "CALL" ? "PUT" : view === "PUT" ? "BOTH" : view === "BOTH" ? "AUTO" : "CALL";
}
