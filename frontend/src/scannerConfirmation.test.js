import test from 'node:test';
import assert from 'node:assert/strict';
import { createRvolObservationTracker, scannerConfirmation, observeConfirmationBoard, observeConfirmationHistory } from './scannerConfirmation.js';

const T=Date.parse('2026-10-30T14:00:00Z');
function row(values=[1,1,1],symbol='AMZN') {
  return {symbol,marketDataAt:new Date(T).toISOString(),
    rvol:Object.fromEntries(['5m','15m','30m'].map((tf,i)=>[tf,{value:values[i],bg:'black',fg:i===0?'cyan':i===1?'green':'dark_green'}])),
    sqz:Object.fromEntries(['2h','4h','D','Wk'].map(tf=>[tf,{value:'-',bg:'black',fg:'black'}])),
    skittles:{M:{value:52,bg:'green',fg:'black'}},
    gradeFresh:{timeline:[{what:'SQZ 4h released',at:new Date(T-60000).toISOString()}]}};
}
function attach(tracker,r,at,scope='live') { return {...r,_rvolObservation:tracker.observe(r,at,scope)}; }
function risen(r=row([2,2,2])) {
  const t=createRvolObservationTracker();t.observe(row(),T,'live');return attach(t,r,T+15000);
}

test('the same calendar day number in a later month cannot reuse old RVOL history',()=>{
  const tracker=createRvolObservationTracker();
  tracker.observe(row(),T,'live');
  const next=Date.parse('2026-11-30T15:00:00Z');
  const r={...row([3,3,3]),marketDataAt:new Date(next).toISOString()};
  const observation=tracker.observe(r,next,'live');
  assert.equal(observation.day,'2026-11-30');
  assert.deepEqual(observation.before,{});
  assert.equal(scannerConfirmation({...r,_rvolObservation:observation},next).level,0);
});

test('three mixed bullish cells must each rise vs previous same-timeframe values',()=>{
  const tracker=createRvolObservationTracker();
  assert.equal(scannerConfirmation(attach(tracker,row(),T),T).level,0);
  const r=attach(tracker,row([1.1,1.2,1.3]),T+15000);
  assert.equal(scannerConfirmation(r,T+15000).level,3);
  assert.deepEqual(scannerConfirmation(r,T+15000).increasing,['5m','15m','30m']);
  // Across-column ascending values alone do not establish a time increase.
  assert.equal(scannerConfirmation(attach(tracker,row([1.1,1.2,1.3]),T+30000),T+30000).level,0);
});

test('equal/decreasing values and fewer than three rising cells remove the level',()=>{
  for (const values of [[2,2,1],[2,1,2],[1,2,2],[1,1,1],[.9,.8,.7]]) {
    const tracker=createRvolObservationTracker();tracker.observe(row(),T,'live');
    assert.equal(scannerConfirmation(attach(tracker,row(values),T+15000),T+15000).level,0);
  }
});

test('missing history does not qualify; invalid readings retain only the last valid baseline',()=>{
  const tracker=createRvolObservationTracker();
  const first=row();delete first.rvol['30m'];tracker.observe(first,T,'live');
  assert.equal(scannerConfirmation(attach(tracker,row([2,2,2]),T+15000),T+15000).level,0);
  tracker.observe(row([null,NaN,Infinity]),T+30000,'live');
  assert.equal(scannerConfirmation(attach(tracker,row([3,3,3]),T+45000),T+45000).level,3);
});

test('repeated IDs and renders never become a new previous observation',()=>{
  const t=createRvolObservationTracker();t.observe(row(),T,'live');
  for(let i=0;i<5;i++)assert.equal(scannerConfirmation(attach(t,row([2,2,2]),T+15000),T+15000+i).level,3);
  // A corrected same publication compares to 1, not the earlier correction 2.
  assert.equal(scannerConfirmation(attach(t,row([1.5,1.5,1.5]),T+15000),T+15000).level,3);
  assert.equal(t.observe(row([8,8,8]),T-15000,'live'),null);
  assert.equal(scannerConfirmation(attach(t,row([1.5,1.5,1.5]),T+30000),T+30000).level,0);
});

test('ticker, timeframe, list/direction scopes and sessions remain isolated',()=>{
  const t=createRvolObservationTracker();t.observe(row(),T,'live');
  assert.equal(scannerConfirmation(attach(t,row([2,2,2],'GE'),T+15000),T+15000).level,0);
  assert.equal(scannerConfirmation(attach(t,row([2,2,2]),T+15000,'other-list'),T+15000).level,0);
  const r=row([2,2,2]);r.rvol['D']={value:4,fg:'green'};
  assert.equal(scannerConfirmation(attach(t,r,T+15000),T+15000).increasing.length,3);
  const nextDay={...row([4,4,4]),marketDataAt:new Date(T+86400000).toISOString()};
  assert.equal(t.observe(nextDay,T+86400000,'live').increasing.length,0);
});

test('timeframe bounds and rounded values reuse source color semantics',()=>{
  const t=createRvolObservationTracker();const a=row([.4,.4,.4]);a.rvol.D={value:.4,fg:'green'};t.observe(a,T,'live');
  const b=row([.5,.5,.4]);b.rvol['5m'].fg='dark_green';b.rvol.D={value:.5,fg:'green'};
  b.rvol['1m']={value:100,fg:'cyan'};b.rvol.M={value:100,fg:'green'};
  assert.deepEqual(scannerConfirmation(attach(t,b,T+15000),T+15000).increasing,['5m','15m','D']);
  const c=risen();c.rvol['5m']={value:2,bg:'red',fg:'green'};
  assert.equal(scannerConfirmation(c,T+15000).level,0);
});

test('highest precedence: SQZ cyan OR all blank, AND active fire, AND Skittles box',()=>{
  let r=risen();assert.equal(scannerConfirmation(r,T+15000).level,3);
  r.skittles={M:{value:52,bg:'black',fg:'green'}};
  assert.equal(scannerConfirmation(r,T+15000).level,2);
  r.skittles={'1h':{bg:'green'},'2h':{bg:'dark_green'}};
  assert.equal(scannerConfirmation(r,T+15000).level,3);
  r.sqz['D']={value:5,bg:'orange'};
  assert.equal(scannerConfirmation(r,T+15000).level,1,'one blank among populated non-cyan is insufficient');
  r.sqz['4h']={value:2,bg:'cyan'};
  assert.equal(scannerConfirmation(r,T+15000).level,3,'one cyan is sufficient');
  r.gradeFresh.timeline=[];assert.equal(scannerConfirmation(r,T+15000).level,1);
});

test('missing/null SQZ is unknown, not blank; fire must be active at evaluation time',()=>{
  for(const cell of [undefined,{value:null,bg:'black'},{value:'-',bg:null}]) {
    const r=risen();r.sqz.Wk=cell;assert.equal(scannerConfirmation(r,T+15000).level,1);
  }
  for(const at of [T+30000,T-86400000]) {
    const r=risen();r.gradeFresh.timeline[0].at=new Date(at).toISOString();
    assert.equal(scannerConfirmation(r,T+15000).level,1);
  }
});

test('board cadence ignores poll receipt times; stale/future data cannot seed history',()=>{
  const t=createRvolObservationTracker();
  const board=(r,stamp)=>({rows:[r],rest:[],scanCompletedAt:new Date(stamp).toISOString()});
  observeConfirmationBoard(board(row(),T),t,'list',T);
  let x=observeConfirmationBoard(board(row([2,2,2]),T+15000),t,'list',T+15000);
  assert.equal(scannerConfirmation(x.rows[0],T+15000).level,3);
  x=observeConfirmationBoard({...board(row([2,2,2]),T+15000),servedAt:new Date(T+16000).toISOString()},t,'list',T+16000);
  assert.equal(scannerConfirmation(x.rows[0],T+16000).level,3);
  assert.equal(scannerConfirmation(x.rows[0],T+1200000).level,0,'stale studies age out');
  assert.equal(observeConfirmationBoard(board(row([9,9,9]),T+999999),t,'list',T+16000).rows[0]._rvolObservation,null);
  assert.equal(scannerConfirmation(attach(t,row([3,3,3]),T+30000,'list'),T+30000).level,3);
});

test('history replay is chronological, pure, isolated, and does not change under sorting',()=>{
  const a={atMs:T,row:row()},b={atMs:T+15000,row:row([2,2,2])};
  const input=[b,a];const out=observeConfirmationHistory(input);
  assert.equal(scannerConfirmation(out[0].row,b.atMs).level,3);
  assert.equal(scannerConfirmation(out[1].row,a.atMs).level,0);
  assert.equal(input[0].row._rvolObservation,undefined);
  assert.equal(scannerConfirmation(observeConfirmationHistory([b])[0].row,b.atMs).level,0,'no hidden live/unloaded baseline');
});

test('stale or future source data never becomes the next valid baseline',()=>{
  const t=createRvolObservationTracker();t.observe(row(),T,'live');
  assert.equal(t.observe({...row([9,9,9]),marketDataAt:new Date(T+60000).toISOString()},T+15000,'live'),null);
  assert.equal(t.observe({...row([9,9,9]),marketDataAt:new Date(T-1200000).toISOString()},T+15000,'live'),null);
  assert.equal(scannerConfirmation(attach(t,row([2,2,2]),T+30000),T+30000).level,3);
});
