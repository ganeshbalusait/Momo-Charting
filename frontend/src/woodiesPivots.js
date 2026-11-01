// WoodiesPivots (Charles Schwab TOS study, pasted by Ganesh 2026-10-08;
// his settings: time frame 4 DAYS, show only today Yes, Daily chart only).
//
//   H = high(period)[1]  L = low(period)[1]   - the PREVIOUS period
//   O = open(period)                          - the CURRENT period
//   PP = (H + L + 2 * O) / 4
//   R1 = 2 * PP - L          S1 = 2 * PP - H
//   R2 = PP + R1 - S1        S2 = PP + S1 - R1
//   R3 = R2 + R1 - PP        S3 = S2 + S1 - PP
//   showOnlyToday hides every bar whose period has a NEXT period
//   (`!IsNaN(close(period)[-1])`), i.e. only the latest period is drawn.
//
// TOS 2/3/4-DAY periods are chunks of WEEKDAYS (holidays counted) at a
// measured phase: floor((weekdays since 1970-01-01 + 6) / N) - the rule
// momx/buckets.py tos_multiday_group_key pinned against thinkorswim.

import { dailyBarTradingDay, dedupeDailyBars } from "./dailyBars.js";

export const WOODIES_TIMEFRAMES = Object.freeze([
  { key: "DAY", label: "Day", days: 1 },
  { key: "2 DAYS", label: "2 days", days: 2 },
  { key: "3 DAYS", label: "3 days", days: 3 },
  { key: "4 DAYS", label: "4 days", days: 4 },
  { key: "WEEK", label: "Week" },
  { key: "MONTH", label: "Month" },
]);
export const WOODIES_LEVELS = Object.freeze(["R3", "R2", "R1", "PP", "S1", "S2", "S3"]);
export const WOODIES_DEFAULTS = Object.freeze({
  timeFrame: "4 DAYS",
  showOnlyToday: true,
  // R measured from his TOS colour tab (GetColor(5) = 255, 95, 95). PP
  // (GetColor 0) and S (GetColor 6) not measured yet - editable.
  resistanceColor: "#ff5f5f",
  pivotColor: "#ff5fff",
  supportColor: "#5fff5f",
});

const TOS_WEEKDAY_PHASE = 6;
const WEEKDAYS_IN_EPOCH_REMAINDER = [0, 1, 2, 2, 2, 3, 4]; // 1970-01-01 was a Thursday

export function weekdayIndex(dateIso) {
  const ms = Date.parse(`${String(dateIso).slice(0, 10)}T00:00:00Z`);
  if (!Number.isFinite(ms)) return null;
  const days = Math.round(ms / 86_400_000);
  const weeks = Math.floor(days / 7);
  return weeks * 5 + WEEKDAYS_IN_EPOCH_REMAINDER[days - weeks * 7];
}

export function woodiesPeriodKey(dateIso, timeFrame) {
  const date = String(dateIso || "").slice(0, 10);
  if (!date) return null;
  const spec = WOODIES_TIMEFRAMES.find(({ key }) => key === timeFrame) || WOODIES_TIMEFRAMES[3];
  if (spec.key === "MONTH") return date.slice(0, 7);
  if (spec.key === "WEEK") {
    const day = new Date(`${date}T00:00:00Z`);
    day.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7));
    return `W${day.toISOString().slice(0, 10)}`;
  }
  const index = weekdayIndex(date);
  if (index === null) return null;
  return `${spec.days}D-${Math.floor((index + TOS_WEEKDAY_PHASE) / spec.days)}`;
}

/** {R3..S3: [{time, value}]} on the chart's own candles. */
export function buildWoodiesPivots(chartBars, dailyBars, options = {}) {
  const timeFrame = options.woodiesPivotsTimeFrame || WOODIES_DEFAULTS.timeFrame;
  const showOnlyToday = options.woodiesPivotsShowOnlyToday !== false;
  const result = Object.fromEntries(WOODIES_LEVELS.map((level) => [level, []]));
  const periods = new Map();
  const order = [];
  dedupeDailyBars(dailyBars).forEach((bar) => {
    const day = dailyBarTradingDay(bar?.time);
    const key = day && woodiesPeriodKey(day, timeFrame);
    const open = Number(bar?.open);
    const high = Number(bar?.high);
    const low = Number(bar?.low);
    if (!key || ![open, high, low].every(Number.isFinite)) return;
    const held = periods.get(key);
    if (!held) {
      periods.set(key, { open, high, low });
      order.push(key);
    } else {
      held.high = Math.max(held.high, high);
      held.low = Math.min(held.low, low);
    }
  });
  const levelsFor = new Map();
  order.forEach((key, index) => {
    const previous = index > 0 ? periods.get(order[index - 1]) : null;
    if (!previous) return;
    const { high, low } = previous;
    const pp = (high + low + 2 * periods.get(key).open) / 4;
    const r1 = 2 * pp - low;
    const s1 = 2 * pp - high;
    const r2 = pp + r1 - s1;
    const s2 = pp + s1 - r1;
    levelsFor.set(key, { PP: pp, R1: r1, S1: s1, R2: r2, S2: s2, R3: r2 + r1 - pp, S3: s2 + s1 - pp });
  });
  const latestKey = order.at(-1);
  (Array.isArray(chartBars) ? chartBars : []).forEach((bar) => {
    const day = dailyBarTradingDay(bar?.time);
    const key = day && woodiesPeriodKey(day, timeFrame);
    if (!key || (showOnlyToday && key !== latestKey)) return;
    const levels = levelsFor.get(key);
    if (!levels) return;
    WOODIES_LEVELS.forEach((level) => result[level].push({ time: Number(bar.time), value: levels[level] }));
  });
  return result;
}
