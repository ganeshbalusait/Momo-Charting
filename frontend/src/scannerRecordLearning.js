import { scannerConfirmation } from './scannerConfirmation.js';
import { scannerSetupTypes } from './scannerSetupTypes.js';
import { scannerRowFreshness } from './scannerFreshness.js';

// Current rule confirmations are descriptive inputs, never probability labels.
export function scannerRecordLearning(row, now = Date.now()) {
  const stale = scannerRowFreshness(row, now);
  const types = scannerSetupTypes(row, now);
  const confirmation = scannerConfirmation(row, now, undefined, types);
  const counts = row?.recordLearning;
  const forecast = row?.recordProbability;
  const predicted = Date.parse(forecast?.scoredAt || ''), validatedAt = Date.parse(forecast?.validatedAt || '');
  const through = Date.parse((forecast?.validationThrough || '') + 'T00:00:00Z');
  const day = at => new Date(at).toLocaleDateString('en-US', {timeZone:'America/New_York'});
  const keys = list => Array.isArray(list) ? list.map(t=>t.key).sort().join('|') : '';
  const ready = !stale && counts?.probabilityValidated === true && counts?.state === 'validated'
    && forecast?.validated === true && forecast?.phase === 'before_entry'
    && forecast?.targetVersion === 'virtual_strategy_exit_gross_chart_v3' && typeof forecast.modelId === 'string'
    && forecast.modelId.length >= 12 && typeof forecast.contract === 'string' && forecast.contract.length > 0
    && typeof forecast.probability === 'number' && Number.isFinite(forecast.probability) && forecast.probability >= 0 && forecast.probability <= 1
    && Number.isFinite(predicted) && predicted <= now && day(predicted) === day(now)
    && Number.isFinite(validatedAt) && validatedAt <= predicted && now-validatedAt <= 14*86400000
    && Number.isFinite(through) && through <= now && now-through <= 15*86400000
    && forecast.validationTrades >= 100 && forecast.validationDays >= 10
    && forecast.symbol === row.symbol && forecast.side === (row.direction === 'bear' ? 'bear' : 'bull')
    && confirmation.level > 0 && confirmation.level === forecast.scannerInputs?.highestLevel
    && types.length >= 3 && keys(types) === keys(forecast.scannerInputs?.setupTypes);
  return { ready, text: ready ? `P(win) ${Math.round(forecast.probability*100)}%` : 'Learning', lines: [
    ready ? `Validated win probability recorded at entry ${forecast.scoredAt}, contract ${forecast.contract}. This is an entry forecast, not a continuously repriced option or a guaranteed outcome.`
      : 'Before-fee win probability is learning: no validated entry forecast matches this Level + Setup snapshot yet.',
    stale ? 'Saved study data; current Level cannot be confirmed.'
      : `Current confirmation: ${confirmation.level ? 'Level ' + confirmation.level : 'no confirmed level'}. ${types.length} distinct Setup types: ${types.map(t => t.text).join(', ') || 'none'}.`,
    'Learning inputs: Level 1–3, all 23 Setup flags, distinct-rule count and entry-time scanner values, and completed 5m chart levels (VWAP, EMA 9/21/50/200, previous-session and premarket highs/lows, classical support/resistance pivots). Missing or stale chart inputs remain unknown. A+ is a separate grade; MOM is optional.',
    'Target: one virtual contract, recorded Strategy-exit midpoint, before fees; fees are excluded. Fills and slippage are not guaranteed. This is not a real-money forecast.',
    counts ? `Quote-checked frozen-input dataset: ${counts.trainingTrades || 0} training / ${counts.calibrationTrades || 0} calibration / ${counts.testTrades || 0} held-out trades. State: ${counts.state || 'collecting'}.` : 'Frozen-input training coverage is not available in this snapshot.',
    `Readiness requires 200 training, 100 calibration, 100 held-out trades across at least 5 test days, then 100 prospective outcomes across 10 entry days. Future paper validation: ${counts?.forwardTrades || 0}/100 trades across ${counts?.forwardDays || 0}/10 days. Missing or late quotes do not count.`,
    'The neural-network challenger trains on recorded entry inputs and completed outcomes. OpenClaw reviewer, critic and specialists propose experiments; agent agreement does not establish a probability.',
    'Missing old Level/Setup snapshots stay unknown. No reconstructed levels, generated wins or hindsight best-price labels.',
  ] };
}
