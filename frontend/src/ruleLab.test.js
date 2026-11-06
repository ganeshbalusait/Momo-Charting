import test from 'node:test';
import assert from 'node:assert/strict';
import { bestCheck, ruleLabRows, ruleLabSummary } from './ruleLab.js';

const split = (wt, wr, wa, ot) => ({ with: { trades: wt, winRate: wr, avgReturn: wa }, without: { trades: ot, winRate: 30, avgReturn: -9 } });
const status = {
  trials: 3,
  scorecard: { checks: { wallRoom3: { bull: 'Room to run', bear: 'Put wall room' }, emaTrend: { bull: 'EMA9 above EMA21', bear: 'x' } },
    rules: [
      { side: 'bull', rule: 'mom', trades: 300, days: 12, winRate: 41.2, avgReturn: -3.4, lastDay: { day: '2026-11-04', trades: 20, winRate: 50, avgReturn: 1.25 },
        checks: { wallRoom3: split(80, 60, 6.1, 90), emaTrend: split(150, 45, -1, 150) }, aiTesting: { startDay: '2026-11-05', checkMeaning: 'Room to run' } },
      { side: 'bull', rule: 'go', trades: 12, days: 4, winRate: 50, avgReturn: 2, checks: { wallRoom3: split(5, 60, 6, 7) }, aiImproved: { checkMeaning: 'Above R1' } },
      { side: 'bear', rule: 'mom', trades: 999, winRate: 1, avgReturn: 1, checks: {} },
    ] },
  experiments: [{ state: 'running' }, { state: 'improved' }, { state: 'rejected' }],
};

test('rows are one side, busiest rule first, with record, best check and AI state', () => {
  const rows = ruleLabRows(status, 'bull');
  assert.deepEqual(rows.map(r => r.rule), ['mom', 'go']);
  assert.equal(rows[0].win, '41%');
  assert.equal(rows[0].avg, '-3.4%');
  assert.equal(rows[0].today, '2026-11-04: 20 · 50% · +1.3%');
  assert.equal(rows[0].best, 'Room to run: 60% · +6.1% (80)');
  assert.equal(rows[0].ai, 'Testing since 2026-11-05: Room to run');
  assert.equal(rows[1].best, 'Not enough trades');
  assert.equal(rows[1].ai, 'Improved: take only when Above R1');
});

test('best check needs 20+ trades each side; summary counts every state', () => {
  assert.equal(bestCheck({ avgReturn: 0, checks: { wallRoom3: split(19, 90, 50, 30) } }), null);
  assert.equal(ruleLabSummary(status), '1 testing · 1 improved · 1 rejected · 3 tried in all');
  assert.deepEqual(ruleLabRows({}, 'bull'), []);
});
