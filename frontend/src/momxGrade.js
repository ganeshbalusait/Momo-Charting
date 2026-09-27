// Pure display helpers for the MomoX scanner grade / momentum / freshness
// columns and the chart's grade lookup. No DOM, no fetches - everything here
// takes the shapes the backend already emits (Tasks 2-7b) and turns them into
// strings/tones a component can render directly. See
// docs/superpowers/specs/2026-09-21-momx-setup-grade-design.md ("Momentum
// now" and "Scanner (Watchlist + Mag7)") for the source rules.
//
// Every function tolerates null/undefined/garbage input without throwing:
// grading (and its display) must never break a row.

export const MOMENTUM_LABEL = {
  building: "Building ↑",
  holding: "Holding",
  fading: "Fading ↓",
  extended: "Extended",
  quiet: "Quiet",
};

// The BEAR row's words (spec 2026-09-24): building is DOWN, fading is a
// bounce. The state codes are the worker's and shared with bull.
export const MOMENTUM_LABEL_BEAR = {
  building: "Building ↓",
  holding: "Holding",
  fading: "Fading ↑",
  extended: "Extended",
  quiet: "Quiet",
};

function isBear(row) {
  return Boolean(row && typeof row === "object" && row.direction === "bear");
}

// momx/momentum.py emits the 5m chart state as a snake_case code; these are
// the spec's words for it ("Momentum now" table).
const CHART_LABEL = {
  below: "Below trigger",
  breakout_provisional: "Breakout – provisional",
  breakout_confirmed: "Breakout confirmed",
  holding: "Holding",
  failed: "Failed",
};

const CHART_LABEL_BEAR = {
  above: "Above trigger",
  breakout_provisional: "Breakdown – provisional",
  breakout_confirmed: "Breakdown confirmed",
  holding: "Holding",
  failed: "Failed",
};

export function chartLabel(chart, direction = "bull") {
  if (typeof chart !== "string" || !chart) return "–";
  const table = direction === "bear" ? CHART_LABEL_BEAR : CHART_LABEL;
  return table[chart] || chart;
}

// H/L degree (checks.hlDegree) runs -1..+1, so it needs two decimals:
// rounding showed 0.79 as "1" and 0.3 as "0". Anything but a finite number
// is "–" (a missing reading never reads as a value).
export function hlDegreeText(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "–";
  const text = value.toFixed(2);
  return text === "-0.00" ? "0.00" : text;
}

export const PATTERN_ICON = {
  explosive: "💥", // was ⚡ until 2026-09-25: ⚡ now means "a setup fired" (MomoX)
  steady: "📈",
};

const MOMENTUM_RANK = {
  building: 4,
  holding: 3,
  extended: 2,
  fading: 1,
  quiet: 0,
};

const LETTER_RANK = {
  "A+": 3,
  A: 2,
  B: 1,
};

const LETTER_TONE = {
  "A+": "aplus",
  A: "a",
  B: "b",
};

// Red palette for the bear letters (index.css .is-aplus-bear / .is-a-bear).
const LETTER_TONE_BEAR = {
  "A+": "aplus-bear",
  A: "a-bear",
  B: "b",
};

export const GRADE_DISCLAIMER =
  "A+ means the defined conditions align. It is not a recommendation and does not guarantee profit.";

function letterOf(row) {
  const letter = row && row.grade && row.grade.letter;
  return typeof letter === "string" ? letter : null;
}

function m5Of(row) {
  return row && typeof row === "object" ? row.m5 : null;
}

// "<letter|-> <patternIcon>? · <MomentumLabel>? (prov.)?"
export function setupText(row) {
  const letter = letterOf(row);
  const m5 = m5Of(row) || {};
  const pattern = m5.pattern === "explosive" || m5.pattern === "steady" ? m5.pattern : null;
  const state = typeof m5.state === "string" ? m5.state : null;
  const labels = isBear(row) ? MOMENTUM_LABEL_BEAR : MOMENTUM_LABEL;
  const momentumLabel = state && labels[state] ? labels[state] : null;
  const isQuietOrAbsent = !state || state === "quiet";

  if (!letter && !pattern && isQuietOrAbsent) return "";

  let text = letter || "–";
  if (pattern) text += " " + PATTERN_ICON[pattern];
  if (momentumLabel) text += " · " + momentumLabel;
  if (m5.provisional) text += " (prov.)";
  return text;
}

// setupText() split at its first " · ": the grade letter (+ pattern icon) and
// the momentum words. The cell renders them as two spans so the phone layout
// can stack them and keep the pinned Setup column narrow.
export function setupParts(text) {
  const value = typeof text === "string" ? text : "";
  const parts = value.split(" · ");
  return { letter: parts[0], momentum: parts.slice(1).join(" · ") };
}

export function setupTone(row) {
  const letter = letterOf(row);
  const tones = isBear(row) ? LETTER_TONE_BEAR : LETTER_TONE;
  return (letter && tones[letter]) || "none";
}

// "up" / "down" are COLOURS (green / red): a bear row building is red, a
// bear row fading (bouncing) is green.
export function momentumTone(row) {
  const m5 = m5Of(row) || {};
  const bear = isBear(row);
  if (m5.state === "building") return bear ? "down" : "up";
  if (m5.state === "fading") return bear ? "up" : "down";
  return "flat";
}

export function setupSortValue(row) {
  const letter = letterOf(row);
  const m5 = m5Of(row) || {};
  const mRank = (m5.state && MOMENTUM_RANK[m5.state]) || 0;

  // Lettered rows: LETTER_RANK * 10 + MOMENTUM_RANK (>= 10)
  if (letter) {
    const rank = LETTER_RANK[letter];
    if (rank) return rank * 10 + mRank;
  }

  // No letter but pattern exists: 1 + MOMENTUM_RANK (1..5, Building highest)
  const pattern = m5.pattern === "explosive" || m5.pattern === "steady" ? m5.pattern : null;
  if (pattern) return 1 + mRank;

  // No letter, no pattern, but the 5-minute momentum is moving (building /
  // holding / extended / fading): 0 - below every pattern row, above blanks.
  if (mRank > 0) return 0;

  // Nothing at all: -1
  return -1;
}

/** ET calendar date + clock time of an instant, or null when unreadable.
 *  { date: "2026-09-22", md: "09-22", time: "13:40" } */
function etParts(value) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  try {
    const picked = {};
    for (const part of new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York",
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).formatToParts(date)) {
      picked[part.type] = part.value;
    }
    const { year, month, day, hour, minute } = picked;
    if (!year || !month || !day || !hour || !minute) return null;
    return {
      date: `${year}-${month}-${day}`,
      md: `${month}-${day}`,
      time: `${hour}:${minute}`,
    };
  } catch {
    return null;
  }
}

function formatET(iso) {
  const parts = etParts(iso);
  return parts ? parts.time : null;
}

// The Fresh column's icon kinds, in the order momx/grade_log.py ICON_ORDER
// emits them. "ADX" (trend strength: a +DI/-DI cross) sits after SQZ.
export const FRESH_ICON_ORDER = ["SKIT", "RVOL", "SQZ", "ADX", "NEWS"];

function matchesIcon(what, icon) {
  if (typeof what !== "string") return false;
  if (icon === "NEWS") return what.toLowerCase().startsWith("news");
  // "ADX 5m bull cross (ADX 35)". The trailing space matters: without it any
  // future kind whose name merely starts with "ADX" would be swallowed here.
  if (icon === "ADX") return what.startsWith("ADX ");
  return what.startsWith(icon);
}

// Latest timeline item per icon kind, rendered "<what> HH:MM" (ET), joined by
// " · ", then " · <ageMinutes>m" when known.
export function freshText(row) {
  const fresh = row && typeof row === "object" ? row.gradeFresh : null;
  if (!fresh || typeof fresh !== "object") return "";

  const icons = Array.isArray(fresh.icons) ? fresh.icons : [];
  const timeline = Array.isArray(fresh.timeline) ? fresh.timeline : [];

  const parts = [];
  for (const icon of icons) {
    let latest = null;
    let latestMs = -Infinity;
    for (const item of timeline) {
      if (!item || typeof item !== "object") continue;
      if (!matchesIcon(item.what, icon)) continue;
      const ms = Date.parse(item.at);
      if (Number.isNaN(ms)) continue;
      if (ms > latestMs) {
        latestMs = ms;
        latest = item;
      }
    }
    if (latest) {
      const time = formatET(latest.at);
      if (time) parts.push(`${latest.what} ${time}`);
    }
  }

  let text = parts.join(" · ");
  // The age here is "how long since the newest change on this row", NOT the
  // age of the grade. Until 2026-09-22 this printed gradeFresh.ageMinutes
  // (minutes since the row first reached its letter), so MDB read
  // "SKIT 4D bg green 13:47 · 243m" four minutes after that colour changed -
  // the trader reasonably read it as a four-hour-old change. The grade's own
  // age now lives in the Setup cell's hover (gradeAgeText).
  const newest = newestTimelineMs(timeline);
  if (newest !== null) {
    const minutes = Math.max(0, Math.round((nowMs() - newest) / 60000));
    text = text ? `${text} · ${minutes}m` : `${minutes}m`;
  }
  return text;
}

/** Epoch ms of the newest readable timeline entry, or null. */
function newestTimelineMs(timeline) {
  let newest = null;
  for (const item of timeline) {
    if (!item || typeof item !== "object") continue;
    const ms = Date.parse(item.at);
    if (Number.isNaN(ms)) continue;
    if (newest === null || ms > newest) newest = ms;
  }
  return newest;
}

/** Overridable clock so tests do not depend on the wall clock. */
let clock = () => Date.now();
function nowMs() {
  return clock();
}
export function setFreshClockForTests(fn) {
  clock = typeof fn === "function" ? fn : () => Date.now();
}

/** "A+ since 09:43 ET (243m)" for the Setup cell's hover, or "" when there is
 *  no letter yet today. The Fresh column deliberately no longer shows this. */
export function gradeAgeText(row) {
  const fresh = row && typeof row === "object" ? row.gradeFresh : null;
  const letter = row && row.grade && typeof row.grade === "object" ? row.grade.letter : null;
  if (!fresh || typeof fresh !== "object" || !letter) return "";
  const first = (fresh.firstToday || {})[letter];
  if (!first || typeof first !== "object") return "";
  const time = formatET(first.at);
  if (!time) return "";
  const age = typeof fresh.ageMinutes === "number" && Number.isFinite(fresh.ageMinutes)
    ? ` (${fresh.ageMinutes}m)`
    : "";
  return `${letter} since ${time} ET${age}`;
}

export function freshSortValue(row) {
  // Newest change first, to match what the column now prints.
  const fresh = row && typeof row === "object" ? row.gradeFresh : null;
  const timeline = fresh && Array.isArray(fresh.timeline) ? fresh.timeline : [];
  const newest = newestTimelineMs(timeline);
  return newest === null ? -Infinity : newest;
}

// ---------------------------------------------------------------------------
// Timeline detail: WHEN THE BAR IS vs WHEN THE SCANNER SAW IT
//
// A gradeFresh.timeline entry's `at` is the moment a SCAN observed the change
// (accurate to one scan, 15-35s). The bar behind it can be far older: TMC
// logged "RVOL 5m 2.1" at 13:53 for a 5m bucket that started 13:40, a SQZ
// release on 4h/D/W can be hours back, and a 4D Skittles bar may have opened
// 14 hours ago even though its colour changed four minutes ago. The "why"
// panel therefore prints both times. When the bar time is unknown we print
// only the seen time - never an invented one.
//
// The bar time per icon (all epoch SECONDS on the row the board already sent):
//   RVOL <tf> ...      -> row.rvol[tf].barAt          (volume midpoint of the bucket)
//   SKIT <tf> bg ...   -> row.skittles[tf].barAt      (that bar's open)
//   SQZ  <tf> released -> row.sqzRaw[tf].lastReleaseAt (the release bar; may be null)
//   news               -> row.news.at (ISO), printed as "published"
//   ADX <tf> ... cross -> NO bar time ON PURPOSE. row.adx carries the study's
//                         values, not the bar they belong to, and an invented
//                         bar time is worse than none.
//   5m breakout confirmed, anything else -> no bar time
const TIMELINE_BAR_SOURCE = {
  RVOL: { section: "rvol", field: "barAt", label: "bar" },
  SKIT: { section: "skittles", field: "barAt", label: "bar" },
  SQZ: { section: "sqzRaw", field: "lastReleaseAt", label: "release bar" },
};

/** "13:40", or "09-21 00:00" when the instant falls on an earlier ET date.
 *  Midnight on today's date is tagged "(today)": a bare "00:00" reads like a
 *  time whose date went missing. */
function etStamp(ms) {
  const parts = etParts(ms);
  if (!parts) return null;
  const today = etParts(nowMs());
  if (today && parts.date !== today.date) return `${parts.md} ${parts.time}`;
  return parts.time === "00:00" ? "00:00 (today)" : parts.time;
}

/** Epoch ms of the bar behind a timeline `what`, with its label, or null. */
function timelineBar(row, what) {
  const [kind, tf] = what.split(/\s+/);
  const source = TIMELINE_BAR_SOURCE[kind];
  if (!source || !tf) return null;
  const group = row && typeof row === "object" ? row[source.section] : null;
  const cell = group && typeof group === "object" ? group[tf] : null;
  if (!cell || typeof cell !== "object") return null;
  const seconds = cell[source.field];
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds <= 0) return null;
  return { label: source.label, ms: seconds * 1000 };
}

function parsedMs(value) {
  return typeof value === "string" && value ? Date.parse(value) : NaN;
}

/** One timeline line for the grade "why" panel, e.g.
 *    "ADX 5m bull cross (ADX 35) · seen 13:45"
 *    "RVOL 5m 2.1 · bar 13:40 · seen 13:53"
 *    "SKIT 4D bg green · bar 00:00 (today) · seen 13:47"
 *    "SQZ 4h released · release bar 09:00 · seen 13:12"
 *    "news · seen 13:20 · published 13:05"
 *    "5m breakout confirmed · seen 10:02"
 *  Unreadable input yields "" rather than throwing. */
export function timelineDetail(row, item) {
  const entry = item && typeof item === "object" ? item : null;
  const what = entry && typeof entry.what === "string" ? entry.what.trim() : "";
  if (!what) return "";

  const parts = [what];
  const seen = formatET(parsedMs(entry.at));

  if (what.toLowerCase().startsWith("news")) {
    if (seen) parts.push("seen " + seen);
    const news = row && typeof row === "object" ? row.news : null;
    const publishedMs = parsedMs(news && typeof news === "object" ? news.at : null);
    const published = Number.isNaN(publishedMs) ? null : etStamp(publishedMs);
    if (published) parts.push("published " + published);
    return parts.join(" · ");
  }

  const bar = timelineBar(row, what);
  const stamp = bar ? etStamp(bar.ms) : null;
  if (bar && stamp) parts.push(bar.label + " " + stamp);
  if (seen) parts.push("seen " + seen);
  return parts.join(" · ");
}

// ---------------------------------------------------------------------------
// Trend strength (ADX) - A RECORDED FACT, NOT PART OF THE LETTER
//
// row.adx[tf] is momx/columns.py adx_cell: the chart's own ADX(10, WILDERS)
// study read on the last bar of that tape. Nothing here grades anything; the
// panel shows it so the trader can see what the chart was showing when an
// event was recorded (INOD 2026-09-22: +DI 48.6 vs -DI 13.0, ADX 12 -> 35 at
// 13:45, eleven minutes before an 11.9% run the letter only graded B).

/** "+DI 48.6 · −DI 13.0 · ADX 34.6 ↑" (the arrow only while ADX is rising),
 *  or "" when the cell holds no reading. Never throws. */
export function adxText(cell) {
  const source = cell && typeof cell === "object" ? cell : null;
  if (!source) return "";
  const plus = finiteNumber(source.plus);
  const minus = finiteNumber(source.minus);
  const adx = finiteNumber(source.adx);
  if (plus === null && minus === null && adx === null) return "";
  const arrow = adx !== null && source.rising === true ? " ↑" : "";
  return (
    `+DI ${oneDecimal(plus)} · −DI ${oneDecimal(minus)} · ADX ${oneDecimal(adx)}${arrow}`
  );
}

/** The chart's session-line windows (App.jsx:10755-10765), as the trader reads
 *  them off the chart. momx/grade_log.py session_window() emits these keys. */
export const SESSION_LABEL = {
  premarket: "Premarket",
  first30m: "First 30m (Smart)",
  morning: "Morning",
  midday: "Midday",
  second30m: "Second 30m (Smart)",
  powerHour: "Power Hour",
  after: "After hours",
};

export function sessionLabel(value) {
  return typeof value === "string" && SESSION_LABEL[value] ? SESSION_LABEL[value] : null;
}

function finiteNumber(value) {
  // Number(null) === 0, so the type check has to come first or a missing
  // reading prints a confident "0.0".
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function oneDecimal(value) {
  return value === null ? "–" : value.toFixed(1);
}

function formatSignedPct(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "–";
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

function formatPct(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "–";
  return `${Math.round(value)}%`;
}

// e.g. "A+ · 325 signals · 44% closed higher · avg +0.10% (back-test from History archive (13 days))"
export function trackRecordLine(record, letter) {
  if (!record || typeof record !== "object" || !letter) return "track record unavailable";
  const letters = record.letters && typeof record.letters === "object" ? record.letters : {};
  const stats = letters[letter];
  if (!stats || typeof stats !== "object" || typeof stats.count !== "number") {
    return "track record unavailable";
  }
  if (stats.count === 0) return `${letter} · no signals yet`;

  const pct = formatPct(stats.pctHigherClose);
  const avg = formatSignedPct(stats.avgToClose);
  const source = record.source;
  const sourceLabel = source === "recorded" ? "recorded" : typeof source === "string" && source ? source : null;
  const suffix = sourceLabel ? ` (${sourceLabel})` : "";
  // A bear record is scored in the trade's favour: "closed lower" is the win.
  const word = record.direction === "bear" ? "closed lower" : "closed higher";

  return `${letter} · ${stats.count} signals · ${pct} ${word} · avg ${avg}${suffix}`;
}

// Latest tape entry with t <= time and t >= time - 300s, else null.
export function gradeAtTime(entries, epochSeconds) {
  if (!Array.isArray(entries) || typeof epochSeconds !== "number" || !Number.isFinite(epochSeconds)) {
    return null;
  }
  let best = null;
  let bestSec = -Infinity;
  for (const entry of entries) {
    if (!entry || typeof entry !== "object" || typeof entry.t !== "string") continue;
    const ms = Date.parse(entry.t);
    if (Number.isNaN(ms)) continue;
    const sec = ms / 1000;
    if (sec <= epochSeconds && sec >= epochSeconds - 300 && sec > bestSec) {
      bestSec = sec;
      best = entry;
    }
  }
  return best;
}

function entrySec(entry) {
  if (typeof entry._sec === "number" && Number.isFinite(entry._sec)) return entry._sec;
  if (typeof entry.t !== "string") return null;
  const ms = Date.parse(entry.t);
  if (Number.isNaN(ms)) return null;
  return ms / 1000;
}

// How far BEFORE a candle's open a grade may be carried forward onto it.
//
// The scanner does not sample on the chart's grid: it records a symbol once
// per board build, and the Watchlist build is slow. Measured over 2026-09-22
// (57 samples/symbol): median gap 8.1 min, p90 13.1 min, p99 28.1 min. A 5m
// candle is 5 minutes wide, so demanding a sample INSIDE the candle leaves
// most 5m candles ungraded - which is what put A+ circles on only a few of
// META's CALL bubbles while the scanner had graded it A+ all morning.
//
// 15 minutes covers the p90 gap, so nearly every candle finds the grade that
// was live when its signal fired. Beyond that the reading is too old to
// honestly label a 5-minute candle, and the bubble stays bare instead.
export const GRADE_CARRY_BACK_SECONDS = 15 * 60;

// Latest tape entry at or before the end of the label's OWN candle window,
// reaching back at most GRADE_CARRY_BACK_SECONDS before the candle opened:
//   candleOpenSec - carryBack <= t <= min(candleOpenSec + candleSeconds, nowSec)
// A sample inside the candle always wins, because the newest match wins and
// an inside-the-candle sample is newer than any carried-back one. A grade
// from "later than now" (future) never leaks in, and a grade is never carried
// BACKWARDS onto a candle that closed before it was recorded.
export function gradeWithinCandle(entries, candleOpenSec, candleSeconds, nowSec = Date.now() / 1000,
                                  carryBackSec = GRADE_CARRY_BACK_SECONDS) {
  if (!Array.isArray(entries)) return null;
  if (typeof candleOpenSec !== "number" || !Number.isFinite(candleOpenSec)) return null;
  if (typeof candleSeconds !== "number" || !Number.isFinite(candleSeconds) || candleSeconds <= 0) return null;
  if (typeof nowSec !== "number" || !Number.isFinite(nowSec)) return null;
  const carryBack = typeof carryBackSec === "number" && Number.isFinite(carryBackSec) && carryBackSec >= 0
    ? carryBackSec
    : 0;

  const windowEnd = Math.min(candleOpenSec + candleSeconds, nowSec);
  const windowStart = candleOpenSec - carryBack;
  let best = null;
  let bestSec = -Infinity;
  for (const entry of entries) {
    if (!entry || typeof entry !== "object") continue;
    const sec = entrySec(entry);
    if (sec === null) continue;
    if (sec >= windowStart && sec <= windowEnd && sec > bestSec) {
      bestSec = sec;
      best = entry;
    }
  }
  return best;
}

/**
 * Is a graded signal currently UNDERWATER - is price now below where it fired?
 *
 * Asked for 2026-09-22 off GOOGL: its 09:35 A+ was correct when stamped (7 of
 * 8 timeframes bullish, price climbing 357 -> 363) and then gave the whole day
 * back, closing -2.4%. The circle is a photograph of that moment and must not
 * be rewritten - but a dead signal should stop LOOKING live.
 *
 * The measure is the LATEST bar's close against the signal's own entry price,
 * and nothing else. Two rules were measured and thrown away first, on today's
 * 4,026 graded signals:
 *
 *   closed below the entry's `trigger` (the 30-minute high)   94.5% fired
 *   two closes below the trigger in a row                     89.9% fired
 *   price is now below the entry price                        43.5% fired
 *
 * The first two are what a trigger-based rule actually does: price dips back
 * under the 30-minute high at some point on almost any day, so 19 circles in
 * 20 would be struck through and the mark would carry no information at all.
 * The surviving rule needs no invented threshold and says exactly one thing.
 *
 * Only bars STRICTLY AFTER the signal's own candle count - judging a signal by
 * its own forming bar is hindsight of the worst kind. Returns the epoch
 * seconds of that latest bar, or null.
 *
 * `entryPrice` missing / not positive -> null. No entry price means no
 * verdict, and "no verdict" must render exactly like a live signal, never
 * like a failure.
 */
export function gradeUnderwaterNow(entryPrice, bars, candleOpenSec, direction = "bull") {
  const entry = Number(entryPrice);
  if (!Number.isFinite(entry) || entry <= 0) return null;
  if (!Array.isArray(bars) || !bars.length) return null;
  const after = Number(candleOpenSec);
  if (!Number.isFinite(after)) return null;
  let latestTime = null;
  let latestClose = null;
  for (const bar of bars) {
    if (!bar || typeof bar !== "object") continue;
    const time = Number(bar.time);
    if (!Number.isFinite(time) || time <= after) continue;
    const close = Number(bar.close);
    if (!Number.isFinite(close)) continue;
    if (latestTime === null || time > latestTime) {
      latestTime = time;
      latestClose = close;
    }
  }
  if (latestTime === null) return null;
  // A bear entry (spec 2026-09-24) is underwater when price closed ABOVE it.
  if (direction === "bear") return latestClose > entry ? latestTime : null;
  return latestClose < entry ? latestTime : null;
}

export function isBullishCallLabel(text, direction) {
  if (direction === "bullish") return true;
  if (typeof text !== "string") return false;
  const upper = text.toUpperCase();
  if (upper.startsWith("CALL")) return true;
  if (/^C\d/.test(upper)) return true;
  return false;
}

/** The bear twin: a PUT-direction label (PUT2H / PUT4H / P2H ...), for the
 * chart's red circles from the bear grade tape (spec 2026-09-24). */
export function isBearishPutLabel(text, direction) {
  if (direction === "PUT" || direction === "bearish") return true;
  if (typeof text !== "string") return false;
  const upper = text.toUpperCase();
  if (upper.startsWith("PUT")) return true;
  if (/^P\d/.test(upper)) return true;
  return false;
}
