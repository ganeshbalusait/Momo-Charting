import test from 'node:test';
import assert from 'node:assert/strict';
import { SCANNER_SETUP_TYPES, SCANNER_SETUP_CHOICES, scannerSetupLabel, scannerSetupBadge } from './scannerSetupOptions.js';
import { DEFAULT_MOMX_FILTERS, coerceFilters, rowPasses, setupRulesPass, selectedSetupRules,
  gatesActive, describeFilters, describeMembership, whyNothingPasses, withSetupRules, filterRows, countPassing, visibleFilterRows, filtersAreActive } from './momxFilters.js';

const now = Date.parse('2026-10-30T10:00:00-04:00');
const config = (rules = [], mode = 'all') => ({ setupRules: rules, setupRulesMode: mode,
  rvol: { on: false }, sqz: { on: false }, skittles: { on: false } });
const row = () => ({ symbol: 'AMZN', direction: 'bull',
  grade: { letter: 'A' },
  m5: { pattern: 'explosive', sqzMom: { value: .4, prev: .3, color: 'cyan', barAt: now / 1000 } },
  adx: { '5m': { plus: 31, minus: 10, adx: 30 }, '30m': { plus: 32, minus: 12, adx: 30 } },
  skittles: { '2h': { bg: 'green' } }, gradeFresh: { timeline: [{ what: 'SKIT 2h bg green', at: new Date(now - 60000).toISOString() }] },
});

test('A+ selects the exact displayed grade, composes with tags, and persists without changing BOT types', () => {
  assert.equal(SCANNER_SETUP_TYPES.length, 23);
  assert.equal(SCANNER_SETUP_CHOICES.length, 24);
  assert.equal(scannerSetupBadge('aPlus').text, 'A+');
  for (const direction of ['bull', 'bear']) {
    const rows = [
      { ...row(), symbol: 'REGN', direction, grade: { letter: 'A+' }, m5: {} },
      { ...row(), symbol: 'BBWI', direction, grade: { letter: 'A+' } },
      { ...row(), symbol: 'ULTA', direction, grade: { letter: 'A' } },
      { ...row(), symbol: 'NONE', direction, grade: null },
    ];
    const saved = coerceFilters({ ...DEFAULT_MOMX_FILTERS, setupRules: ['aPlus'], setup: 'tagAi' });
    assert.deepEqual(saved.setupRules, ['aPlus']);
    assert.deepEqual(visibleFilterRows(rows, rows, saved, false, now).map(r => r.symbol), ['REGN', 'BBWI']);
    assert.deepEqual(filterRows(rows, config(['aPlus', 'tagExplosive']), now).map(r => r.symbol), ['BBWI']);
    assert.deepEqual(filterRows(rows, config(['aPlus', 'tagExplosive'], 'any'), now).map(r => r.symbol), ['REGN', 'BBWI', 'ULTA']);
    assert.match(describeMembership(saved), /A\+/);
  }
});

test('23 distinct choices have no preset selection; old settings survive persistence', () => {
  assert.equal(SCANNER_SETUP_TYPES.length, 23);
  assert.equal(new Set(SCANNER_SETUP_TYPES.map(([key]) => key)).size, 23);
  assert.deepEqual(DEFAULT_MOMX_FILTERS.setupRules, []);
  assert.equal(DEFAULT_MOMX_FILTERS.setupRulesMode, 'all');
  const saved = coerceFilters({ ...DEFAULT_MOMX_FILTERS, mode: 'all', setup: 'aPlus',
    setupRules: ['tagSkit', 'tagExplosive', 'tagSkit', 'unknown'], setupRulesMode: 'any' });
  assert.deepEqual(saved.setupRules, ['tagSkit', 'tagExplosive']);
  assert.equal(saved.setupRulesMode, 'any'); assert.equal(saved.setup, 'any'); assert.equal(saved.mode, 'all');
  assert.deepEqual(selectedSetupRules({ setupRules: 'tagSkit' }), []);
  assert.equal(coerceFilters({ ...DEFAULT_MOMX_FILTERS, setupRulesMode: 'invalid' }).setupRulesMode, 'all');
});

test('arbitrary selections require all checked rules on the same ticker, and support ANY', () => {
  const selected = config(['tagMomStrong', 'tagExplosive', 'tagSkit']);
  assert.equal(rowPasses(row(), selected, now), true);
  const noSkit = { ...row(), gradeFresh: { timeline: [] } };
  const onlySkit = { ...row(), symbol: 'GE', m5: {} };
  assert.equal(rowPasses(noSkit, selected, now), false);
  assert.equal(rowPasses(onlySkit, selected, now), false);
  assert.equal(rowPasses(noSkit, { ...selected, setupRulesMode: 'any' }, now), true);
  assert.equal(rowPasses(onlySkit, { ...selected, setupRulesMode: 'any' }, now), true);
  assert.equal(rowPasses(row(), config(['tagExplosive', 'tagSteady']), now), false);
  assert.equal(rowPasses(row(), config(['tagExplosive', 'tagSteady'], 'any'), now), true);
});

test('every checkbox reuses its existing Setup predicate in both directions', () => {
  for (const candidate of [row(), { symbol: 'REGN' }, { ...row(), direction: 'bear' }]) {
    for (const [key] of SCANNER_SETUP_TYPES) {
      assert.equal(setupRulesPass(candidate, config([key]), now), rowPasses(candidate, { setup: key }, now), key);
    }
  }
  assert.match(scannerSetupLabel('chart', 'bear'), /PUT/);
  assert.match(scannerSetupLabel('tagSector', 'bear'), /bull only/);
});

test('selected column rules replace stale single strategies and put other groups on standby', () => {
  const noSkit = { ...row(), gradeFresh: { timeline: [] } };
  assert.equal(rowPasses(noSkit, { ...config(['tagSkit']), setup: 'tagExplosive', mode: 'any' }, now), false);
  assert.equal(rowPasses(row(), { ...config(['tagSkit']), setup: 'tagExplosive' }, now), true);
  assert.equal(rowPasses(row(), { ...config(), setup: 'aPlus' }, now), false);
  assert.equal(rowPasses(row(), config(), now), true);
  assert.equal(gatesActive(config(['tagSkit'])), true);
  assert.match(describeFilters(config(['tagSkit'])), /SKIT/);
  assert.match(describeMembership({ ...config(['tagSkit']), setup: 'tagExplosive' }), /Setup rules ALL.*only/);
  assert.match(whyNothingPasses(config(['tagSkit']), { total: 10, passing: 0 }), /selected Setup column rules/);
  const previous = { ...DEFAULT_MOMX_FILTERS, setup: 'tagAi' };
  const next = withSetupRules(previous, ['tagExplosive']);
  assert.equal(next.setup, 'any');
  assert.equal(next.rvol, previous.rvol); // preserved, on standby
  assert.equal(rowPasses(row(), next, now), true); // no AI score or RVOL gate needed
  const migrated = coerceFilters({ ...previous, setupRules: ['tagExplosive'] });
  assert.equal(migrated.setup, 'any');
  assert.equal(rowPasses(row(), migrated, now), true);
});

test('Explosive alone shows exactly the two matching ticker rows and the same count', () => {
  const rows = [row(), { ...row(), symbol: 'ULTA' }, { symbol: 'GE', m5: { pattern: 'steady' } },
    { ...row(), symbol: 'HIDDEN', grade: { letter: null } }];
  const selected = withSetupRules({ ...DEFAULT_MOMX_FILTERS, setup: 'tagAi' }, ['tagExplosive']);
  assert.deepEqual(filterRows(rows, selected, now).map(r => r.symbol), ['AMZN', 'ULTA']);
  assert.equal(countPassing(rows, selected), 2);
  assert.deepEqual(selectedSetupRules(withSetupRules(selected, [])), []);
});

test('checked Setup choices filter the visible board even when the old FILTERS switch is OFF', () => {
  const rows = [{ ...row(), symbol: 'BBWI' }, { ...row(), symbol: 'ULTA' },
    { ...row(), symbol: 'PENN', grade: { letter: null } }, { symbol: 'GE', m5: { pattern: 'steady' } }];
  const saved = { ...DEFAULT_MOMX_FILTERS, setup: 'tagAi', setupRules: ['tagExplosive'] };
  assert.equal(filtersAreActive(saved, false), true);
  assert.deepEqual(visibleFilterRows(rows, rows, saved, false, now).map(r => r.symbol), ['BBWI', 'ULTA']);
  assert.equal(visibleFilterRows(rows, rows, saved, false, now).length, countPassing(rows, saved));
  const cleared = withSetupRules(saved, []);
  assert.equal(filtersAreActive(cleared, false), false);
  assert.equal(visibleFilterRows(rows, rows, cleared, false, now), rows);
});

test('MOM follows the visible strong tag and existing candle timing', () => {
  const selected = config(['tagMomStrong']);
  assert.equal(rowPasses(row(), selected, now), true);
  assert.equal(rowPasses({ ...row(), adx: {} }, selected, now), false);
  const old = row(); old.m5.sqzMom.barAt -= 86400;
  assert.equal(rowPasses(old, selected, now), false);
});

test('selector labels carry Setup symbols and direction-specific badge identities without invented values', () => {
  assert.equal(scannerSetupBadge('tagExplosive').text, '💥 Explosive');
  assert.equal(scannerSetupBadge('tagStar1').text, '⭐1');
  assert.equal(scannerSetupBadge('tagAdx').text, 'ADX cyan');
  assert.equal(scannerSetupBadge('tagAdx', 'bear').text, 'ADX magenta');
  assert.match(scannerSetupBadge('tagMomStrong').className, /is-sqzmom is-strong/);
  assert.match(scannerSetupBadge('tagMomStrong', 'bear').className, /is-sqzmom-bear/);
  for (const [key] of SCANNER_SETUP_TYPES) assert.ok(scannerSetupBadge(key).tag, key);
});
