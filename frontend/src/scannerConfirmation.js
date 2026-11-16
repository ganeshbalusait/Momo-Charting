import { FILTER_RVOL_TIMEFRAMES, FILTER_SKITTLES_TIMEFRAMES,
  rvolIsBullish, SQZ_STATE_BG, SKITTLES_STATE_BG, strategyTags, isBearRow } from './momxFilters.js';
import { scannerRowFreshness } from './scannerFreshness.js';
import { scannerSetupTypes } from './scannerSetupTypes.js';
import { etParts, isTradingDay } from './marketSession.js';

export const CONFIRM_SQZ_TIMEFRAMES = Object.freeze(['2h', '4h', 'D']);
const valid = value => typeof value === 'number' && Number.isFinite(value);
// Built on first use: momxFilters imports this module too, so nothing from it
// may be read while the modules are still loading.
// Bear (his "Yes" 2026-11-05) mirrors bull: magenta/red/plum boxes.
let boxSets = null;
const boxes = bear => (boxSets ||= {
  bull: new Set([SKITTLES_STATE_BG.cyan, SKITTLES_STATE_BG.green, SKITTLES_STATE_BG.dark_green]),
  bear: new Set([SKITTLES_STATE_BG.magenta, SKITTLES_STATE_BG.red, SKITTLES_STATE_BG.plum]),
})[bear ? 'bear' : 'bull'];
const blank = cell => cell?.bg === SQZ_STATE_BG.blank && typeof cell.value === 'string'
  && ['', '-', '–', '—'].includes(cell.value.trim());
const time = value => typeof value === 'number' ? (value < 1e11 ? value * 1000 : value) : Date.parse(value || '');
const sessionDay = value => {
  const {year,month,day} = etParts(value);
  return `${year}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
};
const rvolReadings = row => Object.fromEntries(FILTER_RVOL_TIMEFRAMES.map(tf => [tf, valid(row.rvol?.[tf]?.value) ? row.rvol[tf].value : null]));

/** One state per panel; observations are completed scanner publications, not
 * browser polls/quotes/renders. Repeat IDs never advance history. Corrections
 * to the same ID compare to its original preceding publication, not itself. */
export function createRvolObservationTracker({ maxTickers = 3000 } = {}) {
  const held = new Map();
  return {
    observe(row, observedAt, scope = '') {
      const at = time(observedAt);
      const symbol = typeof row?.symbol === 'string' ? row.symbol.trim().toUpperCase() : '';
      if (!symbol || !Number.isFinite(at)) return null;
      const source = time(row?.m5?.sqzMom?.barAt ?? row?.marketDataAt);
      // Invalid provider times must not become a baseline for a later good row.
      if (scannerRowFreshness(row, at) || (Number.isFinite(source) && source > at)) return null;
      const day = sessionDay(at);
      const key = `${scope}|${symbol}`;
      const previous = held.get(key);
      if (previous && at < previous.at) return null;
      const current = rvolReadings(row);
      let before = {};
      if (previous?.day === day) {
        before = { ...previous.before };
        // His choice 2026-11-06 ("Last change"): RVOL only moves when a bar
        // closes but the scanner republishes every ~80 s, so a repeated number
        // is not a new reading. Each timeframe keeps comparing with its last
        // DIFFERENT value; it stays rising until the number actually falls.
        if (at > previous.at) {
          for (const tf of FILTER_RVOL_TIMEFRAMES) {
            if (valid(previous.current[tf]) && previous.current[tf] !== current[tf]) before[tf] = previous.current[tf];
          }
        }
      }
      const increasing = FILTER_RVOL_TIMEFRAMES.filter(tf => valid(current[tf]) && valid(before[tf]) && current[tf] > before[tf]);
      const result = { at, day, current, before, increasing };
      held.delete(key); held.set(key, result);
      while (held.size > maxTickers) held.delete(held.keys().next().value);
      return result;
    },
  };
}

/** Levels (his rule 2026-11-10, "Setup3 with Rvol or sqz or skittles"):
 * three separate confirms, ANY one is enough, and the level is how many are on.
 *   rvol     = 3+ RVOL cells (5m-D) coloured cyan/green. Only the colour matters
 *              ("rvol is green or cyan is matter"); rising is NOT required any
 *              more. `increasing` is still reported as information.
 *   sqz      = one of 2h/4h/D SQZ cyan, blank, or fired today (Weekly ignored).
 *   skittles = one or more cyan/green Skittles boxes 2h-M.
 * L1 = any one, L2 = any two, L3 = all three - and only with 3+ distinct Setup
 * types; fewer is L0 whatever the confirms say. Until 2026-11-10 the levels
 * were stacked (L2 = L1 + SQZ, L3 = L2 + Skittles) and RVOL had to rise.
 * BEAR rows mirror every colour: magenta/red RVOL, magenta SQZ, magenta/red/plum Skittles.
 * `confirms` names the ones that are on, e.g. ['rvol', 'skittles'].
 * `setupTypes` may be passed when the caller already counted them; otherwise
 * they are only counted when at least one confirm is on, because the count runs
 * every Setup rule and with no confirm the level is 0 anyway. */
export function scannerConfirmation(row, nowMs = Date.now(), tags = undefined, setupTypes = undefined) {
  const observation = row?._rvolObservation;
  const result = { level: 0, confirms: [], rvol: false, rvolTimeframes: [], increasing: [],
    sqz: false, fire: false, skittles: false, setupTypes: [], setupCount: 0 };
  // The observation is still required: it is how stale, future or previous-day
  // scanner data is kept out, even though rising no longer decides the level.
  if (!observation || observation.at > nowMs || observation.day !== sessionDay(nowMs)
      || scannerRowFreshness(row, nowMs)) return result;
  const source = time(row?.m5?.sqzMom?.barAt ?? row?.marketDataAt);
  if (Number.isFinite(source) && source > nowMs) return result;
  const bear = isBearRow(row);
  // His rule 2026-11-10: the RVOL cell's colour is what counts. Black cells
  // (side unknown) count for neither direction.
  result.rvolTimeframes = FILTER_RVOL_TIMEFRAMES.filter(tf => valid(row.rvol?.[tf]?.value)
    && rvolIsBullish(row.rvol[tf]) === !bear);
  result.rvol = result.rvolTimeframes.length >= 3;
  result.increasing = result.rvolTimeframes.filter(tf => observation.increasing.includes(tf)
    && row.rvol[tf].value === observation.current[tf]);
  // ANY ONE of 2h/4h/D whose SQZ cell is cyan, blank, or fired today.
  const cells = CONFIRM_SQZ_TIMEFRAMES.map(tf => row.sqz?.[tf]);
  result.sqz = cells.some(cell => cell?.bg === (bear ? SQZ_STATE_BG.magenta : SQZ_STATE_BG.cyan) || blank(cell));
  result.fire = (tags ?? strategyTags(row, nowMs)).some(tag => CONFIRM_SQZ_TIMEFRAMES.some(tf => tag.key === `sqzfire-${tf}`)
    && !tag.gone && Number.isFinite(tag.atMs) && tag.atMs <= nowMs && sessionDay(tag.atMs) === sessionDay(nowMs));
  result.skittles = FILTER_SKITTLES_TIMEFRAMES.some(tf => boxes(bear).has(row.skittles?.[tf]?.bg));
  // A fire and a cyan/blank cell are the same SQZ confirm: they count once.
  result.confirms = [result.rvol && 'rvol', (result.sqz || result.fire) && 'sqz', result.skittles && 'skittles'].filter(Boolean);
  if (!result.confirms.length && !Array.isArray(setupTypes)) return result;
  result.setupTypes = Array.isArray(setupTypes) ? setupTypes : scannerSetupTypes(row, nowMs);
  result.setupCount = result.setupTypes.length;
  if (result.setupCount < 3) return result;
  result.level = result.confirms.length;
  return result;
}

// A last candle older than this many calendar days is a dead ticker, not a
// closed market: the longest normal gap is a long weekend.
const SHOWN_MAX_CANDLE_AGE_DAYS = 4;
const etDayNumber = value => {
  const {year,month,day} = etParts(value);
  return Date.UTC(year, month - 1, day) / 86400000;
};
// The row stays "not stale" until this long after its candle closes
// (scannerRowFreshness).
const SHOWN_FRESH_WINDOW_MS = 600000;
const REGULAR_OPEN_MINUTE = 9 * 60 + 30;
const REGULAR_CLOSE_MINUTE = 16 * 60;
// No holiday calendar in this repo: a weekday holiday counts as in session, so
// it stays strict, which is the safe way to be wrong.
const inRegularSession = value => {
  const {weekday,hour,minute} = etParts(value);
  const minuteOfDay = hour * 60 + minute;
  return isTradingDay(weekday) && minuteOfDay >= REGULAR_OPEN_MINUTE && minuteOfDay < REGULAR_CLOSE_MINUTE;
};
// SKIT and SQZ-fire events are stamped when the scanner saw them, usually a few
// minutes after the close. Judging at the bare close dropped them (QS 11-10:
// History L3, Live L0), so judge at the newest event seen while this candle was
// still the live one.
const shownAnchor = (row, asOf) => {
  let anchor = asOf;
  const timeline = Array.isArray(row.gradeFresh?.timeline) ? row.gradeFresh.timeline : [];
  for (const event of timeline) {
    const at = Date.parse(event?.at || '');
    if (at > anchor && at < asOf + SHOWN_FRESH_WINDOW_MS) anchor = at;
  }
  return anchor;
};
// Keyed on the row's RVOL object, not the row: the panel copies a row for every
// live price and every star rank, but the copies share the scan's own objects,
// so one judgement per scan serves every re-render and the filter.
const shownByScan = new WeakMap();

/** DISPLAY ONLY - what the Live board's Setup column and its Confirm filter
 * show. His ask 2026-11-10: after hours every row is stale, so the strict level
 * below was 0 on the whole board. A row that is not stale is exactly
 * scannerConfirmation, and so is every row during the regular session
 * (09:30-16:00 ET), when a stale row really is behind and must not pass a
 * Confirm filter. Otherwise a stale row is judged as of its OWN last candle
 * (overnight candles included), like History judges a snapshot, and carries
 * asOfData (when that candle ended).
 * Bots, alerts and the record keep calling scannerConfirmation: stale data must
 * never feed a decision. The star rank is left out because the panel stamps it
 * at the current time on displayed rows only, so it is neither as-of the candle
 * nor visible to the filter. */
export function scannerConfirmationShown(row, nowMs = Date.now(), tags = undefined) {
  // Not stale covers no usable time and a future time too; the strict rule
  // decides those. Being stale guarantees a real, past candle time below.
  if (!scannerRowFreshness(row, nowMs)) {
    // A fresh row drawn from the saved board copy never went through the
    // observer, so it has no observation at all (undefined; null means the
    // observer turned it down). Its own candle time has just proved it is fresh,
    // so it shows its level instead of losing it until the next poll.
    const neverObserved = row && row._rvolObservation === undefined
      && Number.isFinite(time(row.m5?.sqzMom?.barAt ?? row.marketDataAt));
    return scannerConfirmation(neverObserved
      ? { ...row, _rvolObservation: { at: nowMs, day: sessionDay(nowMs), current: rvolReadings(row), before: {}, increasing: [] } }
      : row, nowMs, tags);
  }
  if (inRegularSession(nowMs)) return scannerConfirmation(row, nowMs, tags);
  const asOf = time(row.m5?.sqzMom?.barAt ?? row.marketDataAt) + 300000;
  if (etDayNumber(nowMs) - etDayNumber(asOf) > SHOWN_MAX_CANDLE_AGE_DAYS) return scannerConfirmation(row, nowMs, tags);
  const anchor = shownAnchor(row, asOf);
  const scan = row.rvol && typeof row.rvol === 'object' ? row.rvol : row;
  const held = shownByScan.get(scan);
  if (held?.asOfData === asOf && held.judgedAt === anchor) return held;
  const atCandle = { ...row, bestRank: undefined,
    _rvolObservation: { at: anchor, day: sessionDay(anchor), current: rvolReadings(row), before: {}, increasing: [] } };
  const shown = { ...scannerConfirmation(atCandle, anchor, strategyTags(atCandle, anchor)), asOfData: asOf, judgedAt: anchor };
  shownByScan.set(scan, shown);
  return shown;
}

export function observeConfirmationBoard(payload, tracker, scope, nowMs = Date.now()) {
  const at = payload?.scanCompletedAt || payload?.generatedAt;
  const observe = row => {
    if (!row || scannerRowFreshness(row, nowMs) || time(at) > nowMs) return { ...row, _rvolObservation: null };
    return { ...row, _rvolObservation: tracker.observe(row, at, scope) };
  };
  return { ...payload, rows: (payload?.rows || []).map(observe), rest: (payload?.rest || []).map(observe) };
}

/** Replay the FULL loaded page before search/sorting; no live state or future
 * snapshot is ever used as history. First observations correctly don't match. */
export function observeConfirmationHistory(entries) {
  const tracker = createRvolObservationTracker();
  const results = new Map();
  for (const entry of [...entries].sort((a,b) => a.atMs - b.atMs)) {
    const row = entry.row;
    results.set(entry, { ...entry, row: { ...row,
      _rvolObservation: scannerRowFreshness(row, entry.atMs) ? null : tracker.observe(row, entry.atMs, 'history') } });
  }
  return entries.map(entry => results.get(entry));
}

// His rule 2026-11-10 in plain words: 3+ Setup types, then RVOL OR SQZ OR
// Skittles; the level counts how many of the three are on.
export function confirmationDescription(result) {
  const yes = on => on ? 'yes' : 'no';
  const confirms = result.confirms || [];
  const rvolTfs = result.rvolTimeframes || [];
  let lastCandle = '';
  if (result.asOfData) {
    const {hour,minute,month,day} = etParts(result.asOfData);
    lastCandle = `Not live: from the last candle at ${String(hour).padStart(2,'0')}:${String(minute).padStart(2,'0')} ET on ${month}/${day}. `
      + `Setup types at that candle: ${(result.setupTypes || []).map(type => type.text).join(', ') || 'none'}. `
      + 'Time-limited tags on the row may have expired since. '
      + 'It shows where this ticker stood on that candle; the bots and alerts do not use it. ';
  }
  return `${lastCandle}Rule confirmation L${result.level}; not a win probability. Needs 3+ Setup types (has ${result.setupCount}), `
    + 'then any of three confirms - RVOL OR SQZ OR Skittles. L1 = one confirm, L2 = two, L3 = all three. '
    + `RVOL: ${yes(result.rvol)} (cyan/green on 3+ of 5m-D; colour only, rising not needed): ${rvolTfs.join(', ') || 'none'}; `
    + `rising now: ${result.increasing.join(', ') || 'none'}. `
    + `SQZ: ${yes(result.sqz || result.fire)} (one of 2h/4h/D cyan or blank ${yes(result.sqz)}, fired today ${yes(result.fire)}). `
    + `Skittles: ${yes(result.skittles)} (a green/cyan box 2h-M). `
    + `On: ${confirms.join(' + ') || 'none'}. Bear rows use the mirror colours (magenta/red). History uses loaded snapshots only.`;
}
