import { rowPasses, strategyTags, setupRulesPass } from './momxFilters.js';
import { SCANNER_SETUP_TYPES } from './scannerSetupOptions.js';
export { SCANNER_SETUP_TYPES } from './scannerSetupOptions.js';

// The bullish Setup-column filter choices: one type per family, not timeframe.
export function scannerSetupTypes(row, at, tags = strategyTags(row, at)) {
  const keys = new Set(tags.map(t => t.key));
  return SCANNER_SETUP_TYPES.filter(([setup]) => setup === 'tagBolt' ? keys.has('bolt')
    : setup === 'tagStar1' ? tags.some(t => t.key === 'rank' && t.text === '⭐1')
    : setup === 'tagWarn' ? keys.has('warn')
    : setup === 'tagExplosive' || setup === 'tagSteady' ? setupRulesPass(row, { setupRules: [setup] }, at)
    : rowPasses(row, { setup }, at))
    .map(([key, text]) => ({ key, text }));
}
