import { FILTER_RVOL_TIMEFRAMES, FILTER_SKITTLES_TIMEFRAMES,
  rvolIsBullish, SQZ_STATE_BG, SKITTLES_STATE_BG, strategyTags } from './momxFilters.js';
import { scannerRowFreshness } from './scannerFreshness.js';
import { scannerSetupTypes } from './scannerSetupTypes.js';
import { etParts } from './marketSession.js';

export const CONFIRM_SQZ_TIMEFRAMES = Object.freeze(['2h', '4h', 'D']);
const valid = value => typeof value === 'number' && Number.isFinite(value);
// Built on first use: momxFilters imports this module too, so nothing from it
// may be read while the modules are still loading.
let greenBoxSet = null;
const greenBoxes = () => greenBoxSet ||= new Set([SKITTLES_STATE_BG.cyan, SKITTLES_STATE_BG.green, SKITTLES_STATE_BG.dark_green]);
const blank = cell => cell?.bg === SQZ_STATE_BG.blank && typeof cell.value === 'string'
  && ['', '-', '–', '—'].includes(cell.value.trim());
const time = value => typeof value === 'number' ? (value < 1e11 ? value * 1000 : value) : Date.parse(value || '');
const sessionDay = value => {
  const {year,month,day} = etParts(value);
  return `${year}-${String(month).padStart(2,'0')}-${String(day).padStart(2,'0')}`;
};

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
      const current = Object.fromEntries(FILTER_RVOL_TIMEFRAMES.map(tf => [tf, valid(row.rvol?.[tf]?.value) ? row.rvol[tf].value : null]));
      let before = {};
      if (previous?.day === day) {
        before = { ...previous.before };
        if (at > previous.at) {
          for (const tf of FILTER_RVOL_TIMEFRAMES) if (valid(previous.current[tf])) before[tf] = previous.current[tf];
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

/** Levels (his rules 2026-11-05). Every level needs 3+ distinct Setup types.
 * L1 = 3+ cyan/green RVOL cells (5m-D) rising since the last publication.
 * L2 = L1 + one of 2h/4h/D SQZ cyan, blank or fired today.
 * L3 = L2 + one or more cyan/green Skittles boxes 2h-M.
 * `setupTypes` may be passed when the caller already counted them; it is only
 * counted here once RVOL passes, because the count runs every Setup rule. */
export function scannerConfirmation(row, nowMs = Date.now(), tags = undefined, setupTypes = undefined) {
  const observation = row?._rvolObservation;
  const result = { level: 0, increasing: [], sqz: false, fire: false, skittles: false, setupTypes: [], setupCount: 0 };
  if (!observation || observation.at > nowMs || observation.day !== sessionDay(nowMs)
      || scannerRowFreshness(row, nowMs)) return result;
  const source = time(row?.m5?.sqzMom?.barAt ?? row?.marketDataAt);
  if (Number.isFinite(source) && source > nowMs) return result;
  result.increasing = FILTER_RVOL_TIMEFRAMES.filter(tf => observation.increasing.includes(tf)
    && valid(row.rvol?.[tf]?.value) && row.rvol[tf].value === observation.current[tf]
    && rvolIsBullish(row.rvol[tf]) === true);
  if (result.increasing.length < 3) return result;
  // His rule 2026-11-05: L2 = L1 + ANY ONE of 2h/4h/D whose SQZ cell is cyan,
  // blank, or fired today. Weekly is not part of the check.
  const cells = CONFIRM_SQZ_TIMEFRAMES.map(tf => row.sqz?.[tf]);
  result.sqz = cells.some(cell => cell?.bg === SQZ_STATE_BG.cyan || blank(cell));
  result.fire = (tags ?? strategyTags(row, nowMs)).some(tag => CONFIRM_SQZ_TIMEFRAMES.some(tf => tag.key === `sqzfire-${tf}`)
    && !tag.gone && Number.isFinite(tag.atMs) && tag.atMs <= nowMs && sessionDay(tag.atMs) === sessionDay(nowMs));
  result.skittles = FILTER_SKITTLES_TIMEFRAMES.some(tf => greenBoxes().has(row.skittles?.[tf]?.bg));
  result.setupTypes = Array.isArray(setupTypes) ? setupTypes : scannerSetupTypes(row, nowMs);
  result.setupCount = result.setupTypes.length;
  if (result.setupCount < 3) return result;
  result.level = result.sqz || result.fire ? (result.skittles ? 3 : 2) : 1;
  return result;
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

export function confirmationDescription(result) {
  return `Rule confirmation L${result.level}; not a win probability. Setup types ${result.setupCount}/3 required. RVOL color + strict increase: ${result.increasing.join(', ') || 'none'} `
    + `(${result.increasing.length}/3 required). L2 needs ONE of 2h/4h/D SQZ cyan, blank or fired: `
    + `cyan/blank ${result.sqz ? 'yes' : 'no'}, fired today ${result.fire ? 'yes' : 'no'}. L3 adds a green/cyan Skittles box 2h-M: ${result.skittles ? 'yes' : 'no'}. `
    + 'Compared with the preceding valid scanner publication for each ticker/timeframe; first observation does not qualify. History uses loaded snapshots only.';
}
