import { FILTER_RVOL_TIMEFRAMES, FILTER_SQZ_TIMEFRAMES, FILTER_SKITTLES_TIMEFRAMES,
  rvolIsBullish, SQZ_STATE_BG, SKITTLES_STATE_BG, strategyTags } from './momxFilters.js';
import { scannerRowFreshness } from './scannerFreshness.js';
import { etParts } from './marketSession.js';

const valid = value => typeof value === 'number' && Number.isFinite(value);
const greenBoxes = new Set([SKITTLES_STATE_BG.cyan, SKITTLES_STATE_BG.green, SKITTLES_STATE_BG.dark_green]);
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

export function scannerConfirmation(row, nowMs = Date.now(), tags = undefined) {
  const observation = row?._rvolObservation;
  const result = { level: 0, increasing: [], sqz: false, fire: false, skittles: false };
  if (!observation || observation.at > nowMs || observation.day !== sessionDay(nowMs)
      || scannerRowFreshness(row, nowMs)) return result;
  const source = time(row?.m5?.sqzMom?.barAt ?? row?.marketDataAt);
  if (Number.isFinite(source) && source > nowMs) return result;
  result.increasing = FILTER_RVOL_TIMEFRAMES.filter(tf => observation.increasing.includes(tf)
    && valid(row.rvol?.[tf]?.value) && row.rvol[tf].value === observation.current[tf]
    && rvolIsBullish(row.rvol[tf]) === true);
  if (result.increasing.length < 3) return result;
  const cells = FILTER_SQZ_TIMEFRAMES.map(tf => row.sqz?.[tf]);
  result.sqz = cells.some(cell => cell?.bg === SQZ_STATE_BG.cyan) || cells.every(blank);
  result.fire = (tags ?? strategyTags(row, nowMs)).some(tag => tag.key.startsWith('sqzfire-')
    && !tag.gone && Number.isFinite(tag.atMs) && tag.atMs <= nowMs && sessionDay(tag.atMs) === sessionDay(nowMs));
  result.skittles = FILTER_SKITTLES_TIMEFRAMES.some(tf => greenBoxes.has(row.skittles?.[tf]?.bg));
  if (result.increasing.length >= 3) result.level = result.sqz && result.fire ? (result.skittles ? 3 : 2) : 1;
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
  return `Rule confirmation L${result.level}; not a win probability. RVOL color + strict increase: ${result.increasing.join(', ') || 'none'} `
    + `(${result.increasing.length}/3 required). SQZ cyan or ALL cells blank: ${result.sqz ? 'yes' : 'no'}. `
    + `Active Setup SQZ fire: ${result.fire ? 'yes' : 'no'}. Green/cyan Skittles box: ${result.skittles ? 'yes' : 'no'}. `
    + 'Compared with the preceding valid scanner publication for each ticker/timeframe; first observation does not qualify. History uses loaded snapshots only.';
}
