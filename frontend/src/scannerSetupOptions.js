// The 23 existing Setup-column filter types, shared by the scanner and BOT.
export const SCANNER_SETUP_TYPES = Object.freeze([
  ['tagBolt','Setup fired today'],['go','GO'],['goRvolMacd','GO+'],['mxaplus','MX A+'],
  ['tagStar1','Star 1'],['tagSector','SECTOR'],['dayLine','LINE'],['dayLine100','$LINE'],
  ['dayReclaim','RECLAIM'],['tagZs','ZS'],['tagAbvx','ABVX'],['tagMomStrong','Strong MOM'],
  ['tagFlip','FLIP'],['tagRvol','RVOL'],['tagSkit','SKIT'],['tagSqz','SQZ fire'],
  ['tagChartSqz','Chart squeeze'],['tagAdx','ADX'],['chart','CALL2H / CALL4H'],
  ['chartShort','C2H / C4H'],['tagWarn','Warning'],['tagExplosive','Explosive'],['tagSteady','Steady'],
]);

// Confirm L1/L2/L3 (his ask 2026-11-05): filter choices only. Like A+, they
// are NOT Setup types, so a level never counts toward its own 3+ types.
export const CONFIRM_LEVEL_CHOICES = Object.freeze([['confirmL1', 'Confirm L1+'], ['confirmL2', 'Confirm L2+'], ['confirmL3', 'Confirm L3']]);
export const confirmChoiceLevel = key => ({ confirmL1: 1, confirmL2: 2, confirmL3: 3 })[key] || 0;

// Grade selection is a display filter, not an additional BOT Setup type.
export const SCANNER_SETUP_CHOICES = Object.freeze([['aPlus', 'A+'], ...CONFIRM_LEVEL_CHOICES, ...SCANNER_SETUP_TYPES]);

export function scannerSetupLabel(key, direction = 'bull') {
  if (key === 'aPlus') return 'A+';
  if (confirmChoiceLevel(key)) return CONFIRM_LEVEL_CHOICES.find(([value]) => value === key)[1] + (direction === 'bear' ? ' (bull only)' : '');
  const arrow = direction === 'bear' ? '↓' : '↑';
  const labels = { tagBolt: '⚡ Setup fired today', tagStar1: '⭐1', tagSector: '🔥 / 🔸 SECTOR',
    dayLine: 'LINE' + arrow, dayLine100: '$LINE' + arrow, dayReclaim: 'RECLAIM' + arrow,
    tagZs: 'ZS' + arrow, tagAbvx: 'ABVX' + arrow, tagMomStrong: 'MOM' + arrow,
    tagFlip: 'FLIP' + arrow, tagRvol: 'RVOL' + arrow, tagSkit: 'SKIT' + arrow,
    tagChartSqz: '🔥 Chart squeeze', tagWarn: '⚠ Warning', tagExplosive: '💥 Explosive', tagSteady: '📈 Steady',
    tagAdx: direction === 'bear' ? 'ADX magenta' : 'ADX cyan' };
  if (direction === 'bear' && key === 'chart') return 'PUT2H / PUT4H';
  if (direction === 'bear' && key === 'chartShort') return 'P2H / P4H';
  if (direction === 'bear' && key === 'tagSector') return 'SECTOR (bull only)';
  if (labels[key]) return labels[key];
  return SCANNER_SETUP_TYPES.find(([value]) => value === key)?.[1] || key;
}

export function scannerSetupBadge(key, direction = 'bull') {
  const bear = direction === 'bear';
  const level = confirmChoiceLevel(key);
  if (level) return { text: scannerSetupLabel(key, direction), tag: 'confirmation',
    className: `momx-strategy-tag is-confirmation is-level-${level}` };
  const tags = { aPlus: 'grade-aplus', tagBolt: 'bolt', go: 'go', goRvolMacd: 'go-rvol-macd', mxaplus: 'mxaplus',
    tagStar1: 'rank', tagSector: 'sector', dayLine: 'dayline', dayLine100: 'dayline100', dayReclaim: 'dayreclaim',
    tagZs: 'zsrule', tagAbvx: 'abvx', tagMomStrong: bear ? 'sqzmom-bear' : 'sqzmom',
    tagFlip: bear ? 'momflip-bear' : 'momflip', tagRvol: 'rvol', tagSkit: 'skit', tagSqz: 'sqzfire',
    tagChartSqz: 'chartsqz', tagAdx: 'adx', chart: 'chart', chartShort: 'chart', tagWarn: 'warn',
    tagExplosive: 'explosive', tagSteady: 'steady' };
  const tag = tags[key];
  return { text: scannerSetupLabel(key, direction), tag,
    className: 'momx-strategy-tag is-' + tag + (key === 'tagMomStrong' ? ' is-strong' : '') + (bear ? ' is-bear' : '') };
}
