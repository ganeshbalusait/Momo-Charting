import test from 'node:test';
import assert from 'node:assert/strict';
import { scannerRecordLearning } from './scannerRecordLearning.js';
import { scannerSetupTypes } from './scannerSetupTypes.js';

test('Record does not turn a shadow score or claimed AI percentage into a win probability', () => {
  const now = Date.parse('2026-10-30T14:00:00Z');
  const row = {symbol:'AMZN', aiScore:{p:.99}, recordLearning:{probability:.99,validated:true}};
  const view = scannerRecordLearning(row, now);
  assert.equal(view.text, 'Learning');
  assert.match(view.lines.join(' '), /no validated entry forecast matches/);
  assert.match(view.lines.join(' '), /23 Setup flags/);
});

test('Record explains changing confirmations and stale inputs without manufacturing probabilities', () => {
  const now = Date.parse('2026-10-30T14:00:00Z');
  const row = {symbol:'REGN',marketDataAt:new Date(now).toISOString(),
    rvol:Object.fromEntries(['5m','15m','30m'].map(tf=>[tf,{value:2,bg:'cyan',fg:'black'}])),
    _rvolObservation:{at:now,day:'2026-10-30',current:{'5m':2,'15m':2,'30m':2},increasing:['5m','15m','30m']}};
  // Only 1 Setup type (RVOL): every level needs 3+ since 2026-11-05.
  assert.match(scannerRecordLearning(row,now).lines[1], /Current confirmation: no confirmed level\. 1 distinct Setup types: RVOL\./);
  assert.match(scannerRecordLearning({...row,marketDataAt:new Date(now-3600000).toISOString()},now).lines.join(' '), /Saved study data/);
});

test('only a validated matching entry forecast can display a percentage; changed rules and future proof withdraw it', () => {
  const now=Date.parse('2026-10-30T14:00:00Z');
  const row={symbol:'AMZN',direction:'bull',recordLearning:{probabilityValidated:true,state:'validated'},marketDataAt:new Date(now).toISOString(),grade:{letter:'A+'},
    m5:{pattern:'explosive'},
    rvol:Object.fromEntries(['5m','15m','30m'].map(tf=>[tf,{value:3,bg:'cyan',fg:'black',barAt:now/1000}])),
    sqz:Object.fromEntries(['2h','4h','D','Wk'].map(tf=>[tf,{value:'-',bg:'black'}])),
    skittles:{M:{value:50,bg:'green'}},
    gradeFresh:{timeline:[{what:'SQZ 4h released',at:new Date(now-60000).toISOString()}]},
    _rvolObservation:{at:now,day:'2026-10-30',current:{'5m':3,'15m':3,'30m':3},increasing:['5m','15m','30m']}};
  const forecast={available:true,validated:true,modelId:'validated-neural-model',targetVersion:'virtual_strategy_exit_gross_chart_v3',
    phase:'before_entry',symbol:'AMZN',side:'bull',contract:'AMZN_CALL',probability:.62,
    scoredAt:new Date(now-1000).toISOString(),validatedAt:new Date(now-86400000).toISOString(),
    validationThrough:'2026-10-28',validationTrades:100,validationDays:10,
    scannerInputs:{highestLevel:3,setupTypes:scannerSetupTypes(row,now)}};
  assert.equal(scannerRecordLearning({...row,recordProbability:forecast},now).text,'P(win) 62%');
  assert.equal(scannerRecordLearning({...row,recordLearning:{state:'withdrawn',probabilityValidated:false},recordProbability:forecast},now).text,'Learning');
  for (const change of [{validated:false},{validationTrades:99},{validationDays:9},
    {probability:1.1},{symbol:'GE'},{validatedAt:new Date(now+1000).toISOString()},
    {validationThrough:'2026-09-01'},{scannerInputs:{highestLevel:1,setupTypes:[]}}]) {
    assert.equal(scannerRecordLearning({...row,recordProbability:{...forecast,...change}},now).text,'Learning');
  }
});
