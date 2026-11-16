import test from 'node:test';
import assert from 'node:assert/strict';
import { createRvolObservationTracker, scannerConfirmation as confirmRaw, scannerConfirmationShown, observeConfirmationBoard, observeConfirmationHistory, confirmationDescription } from './scannerConfirmation.js';

// Most tests are about RVOL/SQZ/Skittles; they hand in 3 Setup types so the
// 3+ Setup-type gate (his rule 2026-11-05) is met. The gate has its own tests.
// His rule 2026-11-10: the level COUNTS the confirms - RVOL OR SQZ OR Skittles,
// any one = L1, two = L2, all three = L3. RVOL colour decides, rising does not.
const THREE=[{key:'tagMomStrong',text:'MOM'},{key:'tagRvol',text:'RVOL'},{key:'tagSkit',text:'SKIT'}];
const scannerConfirmation=(r,at,tags,types=THREE)=>confirmRaw(r,at,tags,types);

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
// SQZ off: every 2h-D cell still squeezed (orange) and nothing fired.
function noSqz(r) { for(const tf of ['2h','4h','D'])r.sqz[tf]={value:5,bg:'orange'};r.gradeFresh.timeline=[];return r; }

test('the same calendar day number in a later month cannot reuse old RVOL history',()=>{
  const tracker=createRvolObservationTracker();
  tracker.observe(row(),T,'live');
  const next=Date.parse('2026-11-30T15:00:00Z');
  const r={...row([3,3,3]),marketDataAt:new Date(next).toISOString()};
  const observation=tracker.observe(r,next,'live');
  assert.equal(observation.day,'2026-11-30');
  assert.deepEqual(observation.before,{});
  const c=scannerConfirmation({...r,_rvolObservation:observation},next);
  assert.deepEqual(c.increasing,[],'no rise without same-day history');
  assert.equal(c.rvol,true,'his rule 2026-11-10: the colour alone counts');
});

test('RVOL colour counts on the first observation; rising is reported, not required',()=>{
  const tracker=createRvolObservationTracker();
  const first=scannerConfirmation(attach(tracker,row(),T),T);
  assert.equal(first.level,3);assert.deepEqual(first.increasing,[]);
  const r=attach(tracker,row([1.1,1.2,1.3]),T+15000);
  assert.equal(scannerConfirmation(r,T+15000).level,3);
  assert.deepEqual(scannerConfirmation(r,T+15000).increasing,['5m','15m','30m']);
  // His "Last change" rule (2026-11-06) still drives `increasing`: a repeat is
  // not a new reading, so the rise vs 1/1/1 still stands...
  assert.deepEqual(scannerConfirmation(attach(tracker,row([1.1,1.2,1.3]),T+30000),T+30000).increasing,['5m','15m','30m']);
  // ...until a number actually falls - and the level does not care.
  const fell=scannerConfirmation(attach(tracker,row([1.0,1.2,1.3]),T+45000),T+45000);
  assert.deepEqual(fell.increasing,['15m','30m']);
  assert.equal(fell.level,3);assert.ok(fell.confirms.includes('rvol'));
});

test('RVOL green on 3 timeframes but NOT rising still counts (his "rvol is green or cyan is matter")',()=>{
  const t=createRvolObservationTracker();t.observe(row([2,2,2]),T,'live');
  const r=noSqz(attach(t,row([1,1,1]),T+15000));r.skittles={};
  const c=scannerConfirmation(r,T+15000);
  assert.deepEqual(c.increasing,[]);
  assert.deepEqual(c.rvolTimeframes,['5m','15m','30m']);
  assert.deepEqual(c.confirms,['rvol']);
  assert.equal(c.level,1);
});

test('repeated scans of the same bar keep RVOL rising (live 2026-11-06: 90% of scans repeat the number)',()=>{
  const t=createRvolObservationTracker();
  t.observe(row([1,1,1]),T,'live');
  for (let i=1;i<=4;i++) {
    const r=attach(t,row([1.4,1.4,1.4]),T+i*80000);
    assert.deepEqual(scannerConfirmation(r,T+i*80000).increasing,['5m','15m','30m'],`scan ${i}`);
  }
  const up=attach(t,row([1.6,1.4,1.4]),T+400000);
  assert.deepEqual(scannerConfirmation(up,T+400000).increasing,['5m','15m','30m']);
  // One timeframe falling drops it from `increasing`; RVOL stays on by colour.
  const down=attach(t,row([1.6,1.2,1.4]),T+480000);
  assert.deepEqual(scannerConfirmation(down,T+480000).increasing,['5m','30m']);
  assert.equal(scannerConfirmation(down,T+480000).rvol,true);
});

test('equal or falling values do not remove the RVOL confirm',()=>{
  for (const values of [[2,2,1],[2,1,2],[1,2,2],[1,1,1],[.9,.8,.7]]) {
    const tracker=createRvolObservationTracker();tracker.observe(row(),T,'live');
    const c=scannerConfirmation(attach(tracker,row(values),T+15000),T+15000);
    assert.equal(c.rvol,true,String(values));assert.equal(c.level,3);
  }
});

test('fewer than three coloured RVOL cells is no RVOL confirm',()=>{
  const r=noSqz(risen());r.skittles={};
  r.rvol['30m']={value:2,bg:'black',fg:'black'};
  const c=scannerConfirmation(r,T+15000);
  assert.equal(c.rvol,false);assert.equal(c.level,0);assert.deepEqual(c.confirms,[]);
});

test('missing history does not block the level; invalid readings retain only the last valid baseline',()=>{
  const tracker=createRvolObservationTracker();
  const first=row();delete first.rvol['30m'];tracker.observe(first,T,'live');
  const c=scannerConfirmation(attach(tracker,row([2,2,2]),T+15000),T+15000);
  assert.deepEqual(c.increasing,['5m','15m']);assert.equal(c.level,3);
  tracker.observe(row([null,NaN,Infinity]),T+30000,'live');
  assert.deepEqual(scannerConfirmation(attach(tracker,row([3,3,3]),T+45000),T+45000).increasing,['5m','15m','30m']);
});

test('repeated IDs and renders never become a new previous observation',()=>{
  const t=createRvolObservationTracker();t.observe(row(),T,'live');
  for(let i=0;i<5;i++)assert.equal(scannerConfirmation(attach(t,row([2,2,2]),T+15000),T+15000+i).increasing.length,3);
  // A corrected same publication compares to 1, not the earlier correction 2.
  assert.equal(scannerConfirmation(attach(t,row([1.5,1.5,1.5]),T+15000),T+15000).increasing.length,3);
  assert.equal(t.observe(row([8,8,8]),T-15000,'live'),null);
  assert.equal(scannerConfirmation(attach(t,row([1.5,1.5,1.5]),T+30000),T+30000).increasing.length,3);
  assert.deepEqual(scannerConfirmation(attach(t,row([1.4,1.4,1.4]),T+45000),T+45000).increasing,[]);
});

test('ticker, timeframe, list/direction scopes and sessions remain isolated',()=>{
  const t=createRvolObservationTracker();t.observe(row(),T,'live');
  assert.deepEqual(scannerConfirmation(attach(t,row([2,2,2],'GE'),T+15000),T+15000).increasing,[]);
  assert.deepEqual(scannerConfirmation(attach(t,row([2,2,2]),T+15000,'other-list'),T+15000).increasing,[]);
  const r=row([2,2,2]);r.rvol['D']={value:4,fg:'green'};
  assert.equal(scannerConfirmation(attach(t,r,T+15000),T+15000).increasing.length,3);
  const nextDay={...row([4,4,4]),marketDataAt:new Date(T+86400000).toISOString()};
  assert.equal(t.observe(nextDay,T+86400000,'live').increasing.length,0);
});

test('timeframe bounds and rounded values reuse source color semantics',()=>{
  const t=createRvolObservationTracker();const a=row([.4,.4,.4]);a.rvol.D={value:.4,fg:'green'};t.observe(a,T,'live');
  const b=row([.5,.5,.4]);b.rvol['5m'].fg='dark_green';b.rvol.D={value:.5,fg:'green'};
  b.rvol['1m']={value:100,fg:'cyan'};b.rvol.M={value:100,fg:'green'};
  const cb=scannerConfirmation(attach(t,b,T+15000),T+15000);
  assert.deepEqual(cb.increasing,['5m','15m','D']);
  assert.deepEqual(cb.rvolTimeframes,['5m','15m','30m','D'],'1m and M are outside 5m-D');
  const c=risen();c.rvol['5m']={value:2,bg:'red',fg:'green'};
  const cc=scannerConfirmation(c,T+15000);
  assert.equal(cc.rvol,false,'a red background beats a green number');
  assert.deepEqual(cc.confirms,['sqz','skittles']);assert.equal(cc.level,2);
});

test('levels are counted: RVOL, SQZ (cyan, blank or fired) and Skittles 2h-M each add one',()=>{
  let r=risen();assert.equal(scannerConfirmation(r,T+15000).level,3);
  r.skittles={M:{value:52,bg:'black',fg:'green'}};
  assert.equal(scannerConfirmation(r,T+15000).level,2);
  r.skittles={'1h':{bg:'green'},'2h':{bg:'dark_green'}};
  assert.equal(scannerConfirmation(r,T+15000).level,3);
  r.gradeFresh.timeline=[];r.sqz['D']={value:5,bg:'orange'};
  assert.equal(scannerConfirmation(r,T+15000).level,3,'one blank cell among orange is enough');
  for(const tf of ['2h','4h','D'])r.sqz[tf]={value:5,bg:'orange'};
  const two=scannerConfirmation(r,T+15000);
  assert.equal(two.level,2,'all orange 2h-D and no fire: RVOL + Skittles');
  assert.deepEqual(two.confirms,['rvol','skittles']);
  r.sqz['4h']={value:2,bg:'cyan'};
  assert.equal(scannerConfirmation(r,T+15000).level,3,'one cyan is sufficient');
  r.sqz['4h']={value:2,bg:'orange'};
  r.gradeFresh.timeline=[{what:'SQZ 2h released',at:new Date(T-60000).toISOString()}];
  const fired=scannerConfirmation(r,T+15000);
  assert.equal(fired.level,3,'a 2h fire alone is the SQZ confirm');
  assert.equal(fired.sqz,false);assert.equal(fired.fire,true);
});

test('a SQZ fire and a cyan cell together are still ONE confirm',()=>{
  const r=risen();r.sqz['2h']={value:2,bg:'cyan'};r.skittles={};
  const c=scannerConfirmation(r,T+15000);
  assert.equal(c.sqz,true);assert.equal(c.fire,true);
  assert.deepEqual(c.confirms,['rvol','sqz']);assert.equal(c.level,2);
});

test('Weekly SQZ is not part of the 2h-D check',()=>{
  const r=noSqz(risen());
  r.sqz.Wk={value:3,bg:'cyan'};
  r.gradeFresh.timeline=[{what:'SQZ Wk released',at:new Date(T-60000).toISOString()}];
  const c=scannerConfirmation(r,T+15000);
  assert.equal(c.level,2);assert.deepEqual(c.confirms,['rvol','skittles']);
});

test('CMCSA 2026-11-10 11:23: all RVOL black, Skittles 2h cyan + 4h green, SQZ white/orange -> L1 via Skittles',()=>{
  const at=Date.parse('2026-11-10T16:23:00Z');  // 11:23 ET
  const cmcsa={symbol:'CMCSA',marketDataAt:new Date(at).toISOString(),
    rvol:Object.fromEntries(['5m','15m','30m','1h','2h','4h','D'].map(tf=>[tf,{value:.4,bg:'black',fg:'black'}])),
    sqz:{'2h':{value:5,bg:'white',fg:'black'},'4h':{value:5,bg:'orange',fg:'black'},D:{value:5,bg:'orange',fg:'black'}},
    skittles:{'2h':{value:60,bg:'cyan',fg:'black'},'4h':{value:58,bg:'green',fg:'black'}},
    gradeFresh:{timeline:[]}};
  const t=createRvolObservationTracker();
  const c=scannerConfirmation(attach(t,cmcsa,at),at,undefined,THREE);
  assert.equal(c.setupCount,3);
  assert.equal(c.rvol,false,'black RVOL is no side');
  assert.equal(c.sqz||c.fire,false);
  assert.equal(c.skittles,true);
  assert.deepEqual(c.confirms,['skittles']);
  assert.equal(c.level,1);
});

test('every level needs 3+ Setup types; fewer is L0 even with all three confirms',()=>{
  const r=risen();
  const two=confirmRaw(r,T+15000,undefined,THREE.slice(0,2));
  assert.equal(two.level,0,'2 types = no level');
  assert.deepEqual(two.confirms,['rvol','sqz','skittles'],'the confirms are still reported');
  assert.equal(confirmRaw(r,T+15000,undefined,[]).level,0);
  const ok=confirmRaw(r,T+15000,undefined,THREE);
  assert.equal(ok.level,3);assert.equal(ok.setupCount,3);
  noSqz(r);
  assert.equal(confirmRaw(r,T+15000,undefined,THREE.slice(0,2)).level,0,'L2 also needs 3 types');
  assert.equal(confirmRaw(r,T+15000,undefined,THREE).level,2);
  assert.match(confirmationDescription(ok),/Needs 3\+ Setup types \(has 3\)/);
  assert.match(confirmationDescription(ok),/RVOL OR SQZ OR Skittles/);
  assert.match(confirmationDescription(ok),/On: rvol \+ sqz \+ skittles/);
});

test('without passed types the real Setup counter runs (fixture has fewer than 3)',()=>{
  assert.equal(confirmRaw(risen(),T+15000).level,0);
});

test('no confirm on: the Setup count is skipped (it runs every Setup rule)',()=>{
  const r=noSqz(risen());r.skittles={};
  for(const tf of ['5m','15m','30m'])r.rvol[tf]={value:2,bg:'black',fg:'black'};
  const c=confirmRaw(r,T+15000);
  assert.equal(c.level,0);assert.deepEqual(c.confirms,[]);assert.equal(c.setupCount,0);
});

test('missing/null SQZ is unknown, not blank; fire must be active at evaluation time',()=>{
  for(const cell of [undefined,{value:null,bg:'black'},{value:'-',bg:null}]) {
    const r=risen();r.gradeFresh.timeline=[];r.sqz={'2h':cell,'4h':cell,D:cell};
    const c=scannerConfirmation(r,T+15000);
    assert.equal(c.sqz,false);assert.equal(c.level,2);
  }
  for(const at of [T+30000,T-86400000]) {
    const r=noSqz(risen());
    r.gradeFresh.timeline=[{what:'SQZ 4h released',at:new Date(at).toISOString()}];
    const c=scannerConfirmation(r,T+15000);
    assert.equal(c.fire,false);assert.equal(c.level,2);
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

test('rows without an observation (stale, future, unobserved) never get a level',()=>{
  const r=row([2,2,2]);
  assert.equal(scannerConfirmation(r,T).level,0);
  assert.equal(scannerConfirmation({...r,_rvolObservation:null},T).level,0);
});

test('history replay is chronological, pure, isolated, and does not change under sorting',()=>{
  const a={atMs:T,row:row()},b={atMs:T+15000,row:row([2,2,2])};
  const input=[b,a];const out=observeConfirmationHistory(input);
  assert.deepEqual(scannerConfirmation(out[0].row,b.atMs).increasing,['5m','15m','30m']);
  assert.deepEqual(scannerConfirmation(out[1].row,a.atMs).increasing,[]);
  assert.equal(scannerConfirmation(out[1].row,a.atMs).level,3,'colour counts on the first snapshot');
  assert.equal(input[0].row._rvolObservation,undefined);
  assert.deepEqual(scannerConfirmation(observeConfirmationHistory([b])[0].row,b.atMs).increasing,[],'no hidden live/unloaded baseline');
});

test('stale or future source data never becomes the next valid baseline',()=>{
  const t=createRvolObservationTracker();t.observe(row(),T,'live');
  assert.equal(t.observe({...row([9,9,9]),marketDataAt:new Date(T+60000).toISOString()},T+15000,'live'),null);
  assert.equal(t.observe({...row([9,9,9]),marketDataAt:new Date(T-1200000).toISOString()},T+15000,'live'),null);
  assert.deepEqual(scannerConfirmation(attach(t,row([2,2,2]),T+30000),T+30000).increasing,['5m','15m','30m']);
});

test('Setup filter has Confirm L1+/L2+/L3; they filter by level and never count as Setup types',async()=>{
  const {SCANNER_SETUP_CHOICES,SCANNER_SETUP_TYPES,scannerSetupBadge}=await import('./scannerSetupOptions.js');
  const {setupRulesPass}=await import('./momxFilters.js');
  const {scannerSetupTypes}=await import('./scannerSetupTypes.js');
  const keys=SCANNER_SETUP_CHOICES.map(([k])=>k);
  for(const k of ['confirmL1','confirmL2','confirmL3']){
    assert.ok(keys.includes(k));assert.ok(!SCANNER_SETUP_TYPES.some(([t])=>t===k));
  }
  assert.equal(scannerSetupBadge('confirmL2').className,'momx-strategy-tag is-confirmation is-level-2');
  assert.equal(scannerSetupBadge('confirmL1','bear').text,'Confirm L1+','bear has its own levels since 2026-11-05');
  // A real row with 3 real Setup types (RVOL, Explosive, Star 1).
  const r=risen({...row([2,2,2]),bestRank:1,grade:{letter:'A'},m5:{pattern:'explosive'}});
  assert.ok(scannerSetupTypes(r,T+15000).length>=3);
  const pass=k=>setupRulesPass(r,{setupRules:[k]},T+15000);
  assert.deepEqual([pass('confirmL1'),pass('confirmL2'),pass('confirmL3')],[true,true,true]);
  r.skittles={};
  assert.deepEqual([pass('confirmL1'),pass('confirmL2'),pass('confirmL3')],[true,true,false],'two confirms: L1+ and L2+ tick, L3 does not');
  const flat=risen();
  assert.equal(setupRulesPass(flat,{setupRules:['confirmL1']},T+15000),false,'under 3 Setup types never passes');
});

// Bear mirror (his "Yes" 2026-11-05): magenta/red RVOL, one of 2h/4h/D SQZ
// magenta, blank or fired, and a magenta/red/plum Skittles box 2h-M.
function bearRow(values=[1,1,1]) {
  const r=row(values);
  r.direction='bear';
  r.rvol=Object.fromEntries(['5m','15m','30m'].map((tf,i)=>[tf,{value:values[i],bg:'black',fg:i===0?'magenta':i===1?'red':'dark_red'}]));
  for(const tf of ['2h','4h','D'])r.sqz[tf]={value:5,bg:'orange'};
  r.skittles={M:{value:40,bg:'red',fg:'black'}};
  r.gradeFresh.timeline=[];
  return r;
}
function bearRisen(r) {
  const t=createRvolObservationTracker();t.observe(bearRow(),T,'live');return attach(t,r,T+15000);
}

test('bear rows count mirrored confirms',()=>{
  let r=bearRisen(bearRow([2,2,2]));
  assert.deepEqual(scannerConfirmation(r,T+15000).confirms,['rvol','skittles'],'magenta/red RVOL + red box, no SQZ');
  assert.equal(scannerConfirmation(r,T+15000).level,2);
  r.sqz['4h']={value:2,bg:'magenta'};
  assert.equal(scannerConfirmation(r,T+15000).level,3,'magenta SQZ + red Skittles box');
  r.skittles={M:{value:40,bg:'green'}};
  assert.equal(scannerConfirmation(r,T+15000).level,2,'a green box does not count for bear');
  r.skittles={D:{bg:'plum'}};
  assert.equal(scannerConfirmation(r,T+15000).level,3,'plum mirrors dark green');
  r.sqz['4h']={value:2,bg:'cyan'};
  assert.equal(scannerConfirmation(r,T+15000).level,2,'cyan SQZ is the bull color');
  r.sqz['D']={value:'-',bg:'black'};
  assert.equal(scannerConfirmation(r,T+15000).level,3,'blank counts on both sides');
});

test('bear: magenta RVOL that is falling still counts; only the colour matters',()=>{
  const t=createRvolObservationTracker();t.observe(bearRow([3,3,3]),T,'live');
  const r=attach(t,bearRow([1,1,1]),T+15000);r.skittles={};
  const c=scannerConfirmation(r,T+15000);
  assert.deepEqual(c.increasing,[]);assert.deepEqual(c.confirms,['rvol']);assert.equal(c.level,1);
});

test('bull-colored RVOL never confirms a bear row, and bear RVOL never confirms a bull row',()=>{
  const r=bearRisen({...row([2,2,2]),direction:'bear'});
  const c=scannerConfirmation(r,T+15000);
  assert.equal(c.rvol,false);assert.equal(c.skittles,false,'green box on a bear row');
  assert.deepEqual(c.confirms,['sqz'],'only the blank SQZ cell, which counts both ways');
  const b=risen({...bearRow([2,2,2]),direction:'bull'});
  assert.equal(scannerConfirmation(b,T+15000).rvol,false);
  assert.equal(scannerConfirmation(b,T+15000).level,0);
});

// What the Live board shows (his report 2026-11-10: "in setup column i dont see
// the Confirm L1,L2,L3 in Live"): after hours every row is stale, so the strict
// level was 0 on the whole board. A stale row is judged as of its own last candle.
const BAR=Date.parse('2026-11-10T20:55:00Z');  // 15:55 ET, a Tuesday
const CLOSE=BAR+300000;
const EVENING=Date.parse('2026-11-11T03:15:00Z');  // 22:15 ET the same day
// Three Setup types that do not depend on the clock: Explosive, MX A+, FLIP.
function candleRow(barMs=BAR,base=row([2,2,2])) {
  const barAt=barMs/1000;
  return {...base,marketDataAt:new Date(barMs).toISOString(),grade:{letter:'A'},momoxAPlus:{at:new Date(barMs-600000).toISOString()},
    m5:{pattern:'explosive',sqzMom:{barAt,value:base.direction==='bear'?1:2,prev:base.direction==='bear'?2:1,color:'black'}},
    adx:{'5m':{closedCross:base.direction==='bear'?'bear':'bull',closedCrossAt:barAt}},
    gradeFresh:{timeline:[{what:'SQZ 4h released',at:new Date(barMs-60000).toISOString()}]}};
}
const unobserved=r=>({...r,_rvolObservation:null});

test('a stale row gets its level as of its last candle, and the strict level stays 0',()=>{
  const r=unobserved(candleRow());
  const c=scannerConfirmationShown(r,EVENING);
  assert.equal(c.level,3);assert.equal(c.asOfData,CLOSE);
  assert.deepEqual(c.confirms,['rvol','sqz','skittles']);
  assert.ok(c.setupCount>=3);
  assert.deepEqual(c.increasing,[],'nothing can be called rising from one candle');
  assert.equal(confirmRaw(r,EVENING).level,0,'bots and alerts stay strict');
  assert.equal(confirmRaw(r,EVENING).asOfData,undefined);
});

test('barAt in seconds or milliseconds, or only marketDataAt, finds the same candle',()=>{
  const ms=unobserved(candleRow());ms.m5={...ms.m5,sqzMom:{...ms.m5.sqzMom,barAt:BAR}};
  assert.equal(scannerConfirmationShown(ms,EVENING).asOfData,CLOSE);
  const iso=unobserved(candleRow());iso.m5={pattern:'explosive'};
  assert.equal(scannerConfirmationShown(iso,EVENING).asOfData,CLOSE);
});

test('a row that is not stale is exactly scannerConfirmation',()=>{
  const r=risen({...candleRow(T),adx:{},m5:{pattern:'explosive'}});
  const tags=[{key:'sqzfire-4h',atMs:T-60000}];
  assert.deepEqual(scannerConfirmationShown(r,T+15000,tags),confirmRaw(r,T+15000,tags));
  assert.equal(scannerConfirmationShown(r,T+15000,tags).asOfData,undefined);
  // Fresh but never observed stays 0, as it always has.
  assert.equal(scannerConfirmationShown(unobserved(row([2,2,2])),T).level,0);
});

test('a fresh row from the saved board copy (never observed) still shows its level',()=>{
  const base=candleRow(T);
  const {_rvolObservation:dropped,...cached}={...base,adx:{},m5:{pattern:'explosive'}};
  assert.equal(cached._rvolObservation,undefined);
  const c=scannerConfirmationShown(cached,T+15000);
  assert.equal(c.level,3,'the row is fresh, so the level shows');
  assert.equal(c.asOfData,undefined,'and it is not marked as old');
  assert.deepEqual(c.increasing,[],'nothing is called rising without an earlier reading');
  assert.equal(confirmRaw(cached,T+15000).level,0,'bots and alerts stay strict');
  // The observer's own "no" still holds.
  assert.equal(scannerConfirmationShown({...cached,_rvolObservation:null},T+15000).level,0);
  // With no usable candle time there is nothing proving the row is fresh.
  const {marketDataAt:gone,...noTime}=cached;
  assert.equal(scannerConfirmationShown(noTime,T+15000).level,0);
  // A candle from the future is refused.
  const future={...cached,marketDataAt:new Date(T+3600000).toISOString()};
  assert.equal(scannerConfirmationShown(future,T+15000).level,0);
});

test('stale row: tags come from the candle time, not the ones computed now',()=>{
  const r=unobserved(candleRow());
  for(const tf of ['2h','4h','D'])r.sqz[tf]={value:5,bg:'orange'};
  const c=scannerConfirmationShown(r,EVENING,[]);
  assert.equal(c.fire,true,'the fire before the candle counts even with empty tags handed in');
  assert.ok(c.confirms.includes('sqz'));
  const later=unobserved(candleRow());
  for(const tf of ['2h','4h','D'])later.sqz[tf]={value:5,bg:'orange'};
  // Ten minutes after the close the row already counted as stale, so an event
  // from then on was not seen while this candle was the live one.
  later.gradeFresh.timeline=[{what:'SQZ 4h released',at:new Date(CLOSE+600000).toISOString()}];
  assert.equal(scannerConfirmationShown(later,EVENING).fire,false,'a fire once the row is stale is not known at the candle');
  assert.equal(scannerConfirmationShown(later,EVENING).level,2,'and the row is still judged at the close, on RVOL + Skittles');
});

// The real QS row, Watchlist 2026-11-10 17:02 ET (trimmed to what the level reads):
// candle 16:45-16:50, and the scanner saw its 4h Skittles cross at 16:55:52.
const QS_BAR=1794347100;
const QS_CLOSE=QS_BAR*1000+300000;
const QS_SKIT_AT='2026-11-10T16:55:52.980032-05:00';
const QS_EVENING=Date.parse('2026-11-11T04:40:00Z');  // 23:40 ET the same day
const box=(value,bg,fg)=>({value,bg,fg});
function qsRow(skitAt=QS_SKIT_AT) {
  return {symbol:'QS',direction:'bull',marketDataAt:QS_BAR,_rvolObservation:null,
    m5:{chart:'breakout_confirmed',state:'extended',pattern:'steady',direction:'bull',provisional:false,breakoutAt:QS_BAR,trigger:4.4998,
      sqzMom:{value:0.0785,prev:0.0781,color:'cyan',barAt:QS_BAR}},
    adx:{'5m':{plus:28.53,minus:10.9},'30m':{plus:24.91,minus:16.08}},momoxAPlus:null,
    chartSignals:[{label:'CALL4H',family:'4x8',timeframe:'4H',at:'2026-11-10T13:00:00-05:00',seenAt:'2026-11-10T16:56:11-05:00'}],dayLines:{zs:'2026-11-10T15:19:46-05:00'},
    grade:{letter:null,direction:'bull',checks:{sqzOK:false,sqzFired:[],sqzCoiling:['D'],push:true,pushRvol:['2h'],skit:3,skitTrend:1,skitCross:2,skitBullish:['2h','4h','D'],aboveMid:true,hlDegree:0.41},reasons:[]},
    gradeFresh:{icons:['SKIT'],timeline:[{at:skitAt,what:'SKIT 4h bg lime'}]},
    rvol:{'5m':{...box(-0.7,'black','black'),pace:null,barAt:1794347100},'15m':{...box(-0.9,'black','black'),pace:-0.8,barAt:1794347100},'30m':{...box(0.2,'black','black'),pace:1.2,barAt:1794346200},'1h':{...box(1.6,'black','cyan'),pace:2.6,barAt:1794344400},'2h':{...box(2.2,'green','black'),pace:2.7,barAt:1794344100},'4h':{...box(1.7,'black','cyan'),pace:1.8,barAt:1794343500},D:{...box(0.9,'black','dark_green'),pace:null,barAt:1794331800}},
    sqz:{'2h':box('-','black','black'),'4h':box('-','black','black'),D:box('*2','orange','black'),Wk:box('-','black','black')},
    skittles:{'2h':box(35,'black','cyan'),'4h':box(57,'lime','black'),D:box(66,'cyan','black'),'2D':box(58,'black','magenta'),'3D':box(39,'black','magenta'),'4D':box(30,'black','magenta'),Wk:box(22,'black','magenta'),M:box(9,'black','plum')}};
}
const typeKeys=c=>c.setupTypes.map(t=>t.key);

test('an event the scanner saw a few minutes after the close still counts (QS: History L3, not L0)',()=>{
  const c=scannerConfirmationShown(qsRow(),QS_EVENING);
  assert.ok(typeKeys(c).includes('tagSkit'),'the Skit seen at 16:55:52 is one of the three Setup types');
  assert.equal(c.setupCount,3);assert.equal(c.level,3);
  assert.equal(c.asOfData,QS_CLOSE,'the hover still names the candle close');
  // The same row as History judged it, at the 16:59:56 publication.
  const seen=Date.parse('2026-11-10T16:59:56-05:00');
  const history=confirmRaw({...qsRow(),_rvolObservation:createRvolObservationTracker().observe(qsRow(),seen,'history')},seen);
  assert.equal(c.level,history.level);assert.deepEqual(typeKeys(c),typeKeys(history));
});

test('only the ten minutes the row still counted as live can move the judgement',()=>{
  const iso=ms=>new Date(ms).toISOString();
  const just=scannerConfirmationShown(qsRow(iso(QS_CLOSE+599000)),QS_EVENING);
  assert.ok(typeKeys(just).includes('tagSkit'),'one second before the row went stale');
  for(const [name,at] of [['at the stale line',QS_CLOSE+600000],['long after',QS_CLOSE+3600000]]){
    const c=scannerConfirmationShown(qsRow(iso(at)),QS_EVENING);
    assert.ok(!typeKeys(c).includes('tagSkit'),name);assert.equal(c.level,0,name);
  }
});

test('a timeline that arrives for the same candle and scan objects is judged again',()=>{
  const before=qsRow('2026-11-10T17:30:00-05:00');
  assert.equal(scannerConfirmationShown(before,QS_EVENING).level,0);
  const after={...before,gradeFresh:{timeline:[{at:QS_SKIT_AT,what:'SKIT 4h bg lime'}]}};
  assert.equal(scannerConfirmationShown(after,QS_EVENING).level,3);
});

const ET=(day,clock)=>Date.parse(`2026-11-${day}T${clock}:00-05:00`);
test('during the regular session a stale row stays strict, in the column and the filter (TTWO 10:00)',async()=>{
  const {setupRulesPass}=await import('./momxFilters.js');
  const r=unobserved(candleRow(ET(10,'09:30')));
  for(const [name,now] of [['same morning',ET(10,'10:00')],['next day mid-session',ET(11,'11:00')],['last minute',ET(11,'15:59')],['the open',ET(11,'09:30')]]){
    const c=scannerConfirmationShown(r,now);
    assert.equal(c.level,0,name);assert.equal(c.asOfData,undefined,name);
    assert.equal(setupRulesPass(r,{setupRules:['confirmL1']},now),false,name);
    assert.equal(setupRulesPass(r,{setupRules:['confirmL3']},now),false,name);
  }
});

test('outside the regular session a stale row keeps its candle level',async()=>{
  const {setupRulesPass}=await import('./momxFilters.js');
  const r=unobserved(candleRow(ET(10,'09:30')));
  for(const [name,now] of [['before the open',ET(11,'09:29')],['the close',ET(10,'16:00')],['evening',ET(10,'22:15')],['Saturday midday',ET(14,'11:00')]]){
    const c=scannerConfirmationShown(r,now);
    assert.equal(c.level,3,name);assert.equal(c.asOfData,ET(10,'09:35'),name);
    assert.equal(setupRulesPass(r,{setupRules:['confirmL3']},now),true,name);
  }
});

test('the session gate does not serve a held evening answer the next morning',()=>{
  const r=unobserved(candleRow());
  assert.equal(scannerConfirmationShown(r,EVENING).level,3);
  assert.equal(scannerConfirmationShown(r,ET(11,'10:00')).level,0);
  assert.equal(scannerConfirmationShown(r,ET(11,'16:30')).level,3);
});

test('the explanation names the Setup types the level was judged on, even once they have expired',async()=>{
  const {strategyTags}=await import('./momxFilters.js');
  const c=scannerConfirmationShown(qsRow(),QS_EVENING);
  assert.ok(!strategyTags(qsRow(),QS_EVENING).some(tag=>tag.key==='skit'),'the Skit tag has expired by now');
  const names=c.setupTypes.map(t=>t.text);
  assert.ok(names.length>=3);
  assert.ok(confirmationDescription(c).includes(`Setup types at that candle: ${names.join(', ')}.`));
  assert.doesNotMatch(confirmationDescription(confirmRaw(risen(),T+15000,undefined,THREE)),/at that candle/);
});

test('stale bear row is judged on mirrored colours',()=>{
  const r=unobserved(candleRow(BAR,bearRow([2,2,2])));
  const c=scannerConfirmationShown(r,EVENING);
  assert.equal(c.asOfData,CLOSE);
  assert.ok(c.confirms.includes('rvol'));assert.ok(c.confirms.includes('skittles'));
  assert.ok(c.level>=2);
  const green=unobserved(candleRow(BAR,{...row([2,2,2]),direction:'bear'}));
  assert.equal(scannerConfirmationShown(green,EVENING).rvol,false,'bull colours never confirm a bear row');
});

test('after midnight ET the next day, and over a weekend, the level still shows',()=>{
  const midnight=Date.parse('2026-11-11T05:30:00Z');  // 00:30 ET Wednesday
  assert.equal(scannerConfirmationShown(unobserved(candleRow()),midnight).level,3);
  const friday=Date.parse('2026-11-06T20:55:00Z');
  const sunday=Date.parse('2026-11-08T18:00:00Z');
  const c=scannerConfirmationShown(unobserved(candleRow(friday)),sunday);
  assert.equal(c.level,3);assert.equal(c.asOfData,friday+300000);
});

test('a last candle older than 4 calendar days is a dead ticker: no level',()=>{
  const four=Date.parse('2026-11-15T04:30:00Z');  // 23:30 ET Saturday 11-14, 4 days after 11-10
  assert.equal(scannerConfirmationShown(unobserved(candleRow()),four).level,3);
  const five=Date.parse('2026-11-15T05:30:00Z');  // 00:30 ET Sunday 11-15, 5 days
  const c=scannerConfirmationShown(unobserved(candleRow()),five);
  assert.equal(c.level,0);assert.equal(c.asOfData,undefined);
});

test('no usable candle time or a future one never gets a candle level',()=>{
  const none=unobserved(candleRow());delete none.marketDataAt;none.m5={pattern:'explosive'};
  const c=scannerConfirmationShown(none,EVENING);
  assert.equal(c.level,0);assert.equal(c.asOfData,undefined);
  const junk=unobserved(candleRow());junk.marketDataAt='not a time';junk.m5={pattern:'explosive'};
  assert.equal(scannerConfirmationShown(junk,EVENING).level,0);
  const future=candleRow(EVENING+3600000);
  future._rvolObservation={at:EVENING-1000,day:'2026-11-10',current:{},before:{},increasing:[]};
  const f=scannerConfirmationShown(future,EVENING);
  assert.equal(f.level,0);assert.equal(f.asOfData,undefined);
  assert.equal(scannerConfirmationShown(undefined,EVENING).level,0);
});

test('the Confirm filter keeps exactly what the column shows on stale rows',async()=>{
  const {setupRulesPass}=await import('./momxFilters.js');
  const two=unobserved(candleRow());two.skittles={};
  const none=unobserved(candleRow());none.skittles={};for(const tf of ['5m','15m','30m'])none.rvol[tf]={value:2,bg:'black',fg:'black'};
  for(const tf of ['2h','4h','D'])none.sqz[tf]={value:5,bg:'orange'};none.gradeFresh.timeline=[];
  const dead=unobserved(candleRow(Date.parse('2026-11-02T20:55:00Z')));
  for(const [r,level] of [[unobserved(candleRow()),3],[two,2],[none,0],[dead,0]]){
    assert.equal(scannerConfirmationShown(r,EVENING).level,level);
    for(const [key,need] of [['confirmL1',1],['confirmL2',2],['confirmL3',3]])
      assert.equal(setupRulesPass(r,{setupRules:[key]},EVENING),level>=need,`${key} at L${level}`);
  }
});

test('the star rank the panel stamps on displayed rows cannot change a stale level',()=>{
  const only2=unobserved(candleRow());delete only2.momoxAPlus;only2.gradeFresh.timeline=[];
  assert.equal(scannerConfirmationShown(only2,EVENING).setupCount,2);
  assert.equal(scannerConfirmationShown({...only2,rvol:{...only2.rvol},bestRank:1},EVENING).level,0,
    'the filter never sees bestRank, so the column must not count it either');
});

test('re-renders and the panel copies of a row reuse one judgement; a new scan does not',()=>{
  const r=unobserved(candleRow());
  const first=scannerConfirmationShown(r,EVENING);
  assert.equal(scannerConfirmationShown(r,EVENING+15000),first);
  assert.equal(scannerConfirmationShown({...r,pctChange:9,bestRank:2},EVENING+30000),first,'live-price and rank copies share the scan');
  const next=unobserved(candleRow(BAR+300000));
  const c=scannerConfirmationShown(next,EVENING);
  assert.notEqual(c,first);assert.equal(c.asOfData,CLOSE+300000);
  const sameScan={...r,marketDataAt:new Date(BAR+300000).toISOString(),m5:{pattern:'explosive'}};
  assert.equal(scannerConfirmationShown(sameScan,EVENING).asOfData,CLOSE+300000,'a different candle is never served an old answer');
});

test('the explanation says last candle, in ET, and not live; strict text is unchanged',()=>{
  const shown=scannerConfirmationShown(unobserved(candleRow()),EVENING);
  assert.match(confirmationDescription(shown),/^Not live: from the last candle at 16:00 ET on 11\/10\./);
  const fresh=confirmRaw(risen(),T+15000,undefined,THREE);
  assert.doesNotMatch(confirmationDescription(fresh),/last candle/);
  assert.match(confirmationDescription(fresh),/^Rule confirmation L3;/);
});
