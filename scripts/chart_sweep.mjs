// Chart sweep: every timeframe x a ticker sample, through the REAL client
// pipeline, against LIVE API responses.
//
// Why this exists: on 2026-08-13 six separate chart defects were reported one
// at a time (silent infinite loading, blank 4H, stale tape, wiped study tape +
// shaking, disconnected live-quote spike, stale watchlist). Every one was the
// same ambiguity - an empty array meaning "none exists" vs "not included in
// this response" vs "failed to load" - and each fix taught one more call site
// how to guess. This sweep exercises the shared modules the chart actually
// uses, so the whole class shows up as one list instead of arriving one
// complaint at a time.
//
// Run:  node scripts/chart_sweep.mjs [SYM,SYM,...]
// Needs the backend on :3001. Read-only; it issues one request per ticker
// (plus one study request), never a burst.

import fs from "node:fs";
import {
  normalizeOiChartPayload,
  resolveHistorySeriesUpdate,
  chartHistorySeriesGuarded,
} from "../frontend/src/chartSeriesData.js";
import { buildChartDisplayBars, normalizeChartCandleBars } from "../frontend/src/chartAggregation.js";
import { shouldUseEquityTradeForChart } from "../frontend/src/chartStreamBars.js";
import {
  oiChartNeedsInitialStudySeed,
  oiChartHasInitialStudySeed,
} from "../frontend/src/oiChartInitialHistory.js";

const API = "http://127.0.0.1:3001";
const TIMEFRAMES = [
  { key: "3m", minutes: 3 }, { key: "5m", minutes: 5 }, { key: "10m", minutes: 10 },
  { key: "15m", minutes: 15 }, { key: "30m", minutes: 30 }, { key: "1h", minutes: 60 },
  { key: "2h", minutes: 120 }, { key: "4h", minutes: 240 },
  { key: "D", minutes: 1440 }, { key: "W", minutes: 10080 }, { key: "M", minutes: 43200 },
];
// A timeframe showing fewer candles than this is unusable for reading price
// action - it is the "4H shows 10 candles" failure, expressed as a number.
const MIN_CANDLES = 24;

// ...but only when the symbol's own history can actually supply that many.
// A flat 24 meant 2 YEARS of Monthly candles, so every recently-listed
// company failed M for having a correctly short history (34 false failures in
// the first full-watchlist sweep). Judge a timeframe against the tape it
// actually renders from: D/W/M draw on the daily seed, intraday on the study
// tape (or the one-minute tape when there is no study tape).
function expectedCandles(tf, { dailyBars, studyBars, liveBars }) {
  const source = tf.minutes >= 1440
    ? dailyBars
    : (studyBars.length ? studyBars : liveBars);
  if (source.length < 2) return 0;
  const spanMinutes = (Number(source.at(-1).time) - Number(source[0].time)) / 60;
  if (!Number.isFinite(spanMinutes) || spanMinutes <= 0) return 0;
  return Math.max(1, Math.floor(spanMinutes / tf.minutes));
}

// --all sweeps the real watchlist. It is deliberately SEQUENTIAL with a pause
// between tickers: a deep pull is 3-5MB and enqueues a CPU-heavy rebuild, and
// bulk-hitting this endpoint starves the app. Run it outside market hours.
const args = process.argv.slice(2);
const paceMs = Number((args.find((a) => a.startsWith("--pace=")) || "--pace=1500").split("=")[1]) || 1500;
const positional = args.find((a) => !a.startsWith("--"));
let symbols;
if (args.includes("--all")) {
  const raw = fs.readFileSync(new URL("../watchlist.txt", import.meta.url), "utf8");
  symbols = [...new Set(raw.split(/[,\s]+/).map((s) => s.trim().toUpperCase())
    .filter((s) => /^[A-Z][A-Z0-9.-]{0,9}$/.test(s)))];
  if (!symbols.length) {
    console.error("--all matched no tickers in watchlist.txt - refusing to report a vacuous PASS");
    process.exit(2);
  }
} else {
  symbols = (positional || "AAPL,NVDA,NFLX,SOFI,WBD").split(",").map((s) => s.trim().toUpperCase());
}

const get = async (qs) => {
  const res = await fetch(`${API}/api/oi-finder-chart?${qs}`);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
};

const etaMinutes = (bars) => {
  const last = Number(bars.at(-1)?.time || 0);
  return last > 0 ? Math.round((Date.now() / 1000 - last) / 60) : Infinity;
};

// Regular session in ET, weekdays only.
const nowEt = new Date(new Date().toLocaleString("en-US", { timeZone: "America/New_York" }));
const etMinutes = nowEt.getHours() * 60 + nowEt.getMinutes();
const marketOpen = nowEt.getDay() >= 1 && nowEt.getDay() <= 5 && etMinutes >= 570 && etMinutes <= 960;

// Session-date comparison, done on YYYY-MM-DD strings so there is no epoch or
// DST arithmetic to get wrong.
const etDate = (d) => new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
}).format(d);
const etWeekday = (d) => new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", weekday: "short",
}).format(d);

// The most recent session that has ALREADY BEGUN. Walks back over weekends,
// and back a day when today's 09:30 has not arrived yet.
function mostRecentSessionDate(now) {
  let probe = new Date(now);
  for (let i = 0; i < 8; i += 1) {
    const weekend = ["Sat", "Sun"].includes(etWeekday(probe));
    const isToday = etDate(probe) === etDate(now);
    const hasStarted = !isToday || etMinutes >= 570;
    if (!weekend && hasStarted) return etDate(probe);
    probe = new Date(probe.getTime() - 24 * 3600 * 1000);
  }
  return etDate(now);
}
const SESSION_DATE = mostRecentSessionDate(new Date());

const failures = [];
const notes = [];
const rows = [];

let processed = 0;
for (const symbol of symbols) {
  processed += 1;
  if (processed > 1) await new Promise((r) => setTimeout(r, paceMs));
  if (processed % 25 === 0) console.error(`  ...${processed}/${symbols.length} swept (${failures.length} failures so far)`);
  let slim;
  let withStudy;
  let deep;
  try {
    slim = await get(`symbol=${symbol}&initial=true`);
    withStudy = await get(`symbol=${symbol}&initial=true&initialStudy=true`);
    // The real chart pulls the deep tape right after first paint; D/W/M draw
    // from its multi-year daily seed. Without it a sweep sees only the 320-row
    // slim seed and reports a Monthly pane that the trader never actually has.
    deep = await get(`symbol=${symbol}&deep=true`);
  } catch (error) {
    failures.push(`${symbol}: request failed - ${error.message}`);
    continue;
  }

  const slimPayload = normalizeOiChartPayload(slim);
  const seedPayload = normalizeOiChartPayload(withStudy);
  const deepPayload = normalizeOiChartPayload(deep);

  // Replay what the chart actually does: the slim first paint lands, then the
  // study seed arrives, then routine slim polls keep arriving forever. The
  // last step is the one that used to wipe the study tape and shake the pane.
  let studyBars = resolveHistorySeriesUpdate([], slimPayload.studyBars, chartHistorySeriesGuarded(slim));
  studyBars = resolveHistorySeriesUpdate(studyBars, seedPayload.studyBars, chartHistorySeriesGuarded(withStudy));
  const afterSeed = studyBars.length;
  for (let poll = 0; poll < 3; poll += 1) {
    studyBars = resolveHistorySeriesUpdate(studyBars, slimPayload.studyBars, chartHistorySeriesGuarded(slim));
  }
  if (afterSeed > 0 && studyBars.length === 0) {
    failures.push(`${symbol}: routine slim polls WIPED the study tape (${afterSeed} -> 0) - 4H/D/W/M lose history and the pane refits`);
  }

  let dailyBars = resolveHistorySeriesUpdate([], seedPayload.dailyBars, chartHistorySeriesGuarded(withStudy));
  dailyBars = resolveHistorySeriesUpdate(dailyBars, deepPayload.dailyBars, chartHistorySeriesGuarded(deep));
  studyBars = resolveHistorySeriesUpdate(studyBars, deepPayload.studyBars, chartHistorySeriesGuarded(deep));
  for (let poll = 0; poll < 3; poll += 1) {
    dailyBars = resolveHistorySeriesUpdate(dailyBars, slimPayload.dailyBars, chartHistorySeriesGuarded(slim));
  }

  const liveBars = normalizeChartCandleBars(deepPayload.bars.length ? deepPayload.bars : seedPayload.bars);
  const behind = etaMinutes(liveBars);
  if (!liveBars.length) {
    failures.push(`${symbol}: no live bars at all`);
  } else {
    // The check that matters, and the one the first version got wrong: did
    // this ticker print during the most recent session at all? The previous
    // logic only treated staleness as a failure while the market was OPEN, so
    // running the sweep after the close waved through 52 tickers whose newest
    // bar was ~22h old - a full session missed - each labelled "market closed,
    // no post-session prints", which was fiction. Session date, not clock.
    const newestDate = etDate(new Date(Number(liveBars.at(-1).time) * 1000));
    if (newestDate < SESSION_DATE) {
      failures.push(`${symbol}: newest bar is from ${newestDate}, but the ${SESSION_DATE} session has already run - MISSED A FULL SESSION`);
    } else if (marketOpen && behind > 20) {
      failures.push(`${symbol}: newest bar is ${behind} min old during RTH - stale tape`);
    } else if (behind > 20) {
      notes.push(`${symbol}: newest bar ${behind} min old, but it is from the current session (${newestDate}) - stopped printing before the close`);
    }
  }

  // The live-quote guard: a quote at "now" must be admitted when the tape is
  // current, and refused when it is hours ahead (that drew the gap spike).
  const newest = Number(liveBars.at(-1)?.time || 0);
  const admitsNow = shouldUseEquityTradeForChart({ equityTime: Math.floor(Date.now() / 1000), latestBarTime: newest });
  if (newest > 0 && behind <= 20 && marketOpen && !admitsNow) {
    failures.push(`${symbol}: live quote REFUSED against a current tape - forming candle will not tick`);
  }

  // The per-timeframe check above scales to the tape, so a truncated daily
  // seed would otherwise pass silently: dailyBars=3 "supports" 1 monthly
  // candle. That truncation is exactly the mid-rebuild signature (MSFT and
  // GOOGL both showed dailyBars=3 and recovered minutes later). A single run
  // cannot distinguish it from a genuinely new listing, so say so plainly and
  // let a second run settle it - a young symbol stays short, a rebuilding one
  // fills in.
  if (dailyBars.length > 0 && dailyBars.length < 250) {
    notes.push(`${symbol}: daily seed is only ${dailyBars.length} rows - either a young listing or a rebuild still in flight; re-run to tell them apart`);
  }

  if (oiChartNeedsInitialStudySeed(240) && !oiChartHasInitialStudySeed(withStudy)) {
    failures.push(`${symbol}: study seed request returned a payload that does not satisfy the seed contract - 4H will retry forever`);
  }

  for (const tf of TIMEFRAMES) {
    const display = buildChartDisplayBars({
      studyBars,
      fineStudyBars: seedPayload.fineStudyBars,
      liveBars,
      dailyBars,
      aggregationMinutes: tf.minutes,
    });
    const count = display.length;
    const supported = expectedCandles(tf, { dailyBars, studyBars, liveBars });
    // Never demand more than the symbol's history can produce.
    const required = Math.min(MIN_CANDLES, supported);
    const ok = required === 0 ? count > 0 : count >= required;
    rows.push(`${symbol.padEnd(6)} ${tf.key.padEnd(4)} candles=${String(count).padStart(5)} ${ok ? "ok" : `** ${count} < ${required} **`}`);
    if (!ok) {
      failures.push(`${symbol} ${tf.key}: ${count} candles but its tape supports ~${supported} - the aggregation is losing history`);
    }
  }
}

console.log(rows.join("\n"));
console.log("");
if (notes.length) { console.log("NOTES:"); notes.forEach((n) => console.log(`  - ${n}`)); console.log(""); }
console.log(`market session: ${marketOpen ? "OPEN" : "CLOSED"} (ET ${nowEt.getHours()}:${String(nowEt.getMinutes()).padStart(2,"0")})`);
if (failures.length === 0) {
  console.log(`PASS - ${symbols.length} tickers x ${TIMEFRAMES.length} timeframes, no defects`);
} else {
  console.log(`FAILURES (${failures.length}):`);
  failures.forEach((f) => console.log(`  - ${f}`));
  process.exitCode = 1;
}
