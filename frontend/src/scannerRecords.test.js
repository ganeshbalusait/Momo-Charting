import test from 'node:test';
import assert from 'node:assert/strict';
import {aiTags,jevRecordTag,filterRows} from './momxFilters.js';
import {scannerFreshness,scannerRowFreshness} from './scannerFreshness.js';
const now=Date.parse('2026-10-29T11:00:00-04:00'),at='2026-10-29T10:00:00-04:00';
const row={symbol:'AAPL',direction:'bull',aiScore:{p:.62,rule:'go',symbol:'AAPL',side:'bull',at}};
test('AI mark belongs to this ticker, side, day and historical observation time',()=>{
 assert.equal(aiTags(row,now)[0].text,'AI 62%');
 assert.match(aiTags(row,now)[0].title,/earlier paper trade/);
 for(const patch of [{symbol:'TSLA'},{side:'bear'},{at:'2026-10-28T10:00:00-04:00'},{at:'2026-10-29T12:00:00-04:00'},{scoredAt:'2026-10-29T12:00:00-04:00'},{p:1.1},{at:null}])
  assert.deepEqual(aiTags({...row,aiScore:{...row.aiScore,...patch}},now),[]);
 assert.deepEqual(aiTags({...row,aiScore:{...row.aiScore,p:.5496}},now),[]);
});
test('JEV mark requires the recorded TAKE and matching actual entry time',()=>{
 const r={symbol:'AAPL',dayLines:{jev:at},jevPick:{at,p:.42,rule:'go'}};
 assert.equal(jevRecordTag(r,now).text,'JEV TAKE 42%');
 for(const patch of [{p:.2},{p:2},{at:'2026-10-29T09:50:00-04:00'},{rule:null}])
  assert.equal(jevRecordTag({...r,jevPick:{...r.jevPick,...patch}},now),null);
 assert.equal(jevRecordTag(r,Date.parse('2026-10-29T09:59:00-04:00')),null);
 assert.equal(jevRecordTag(r,Date.parse('2026-10-30T09:59:00-04:00')),null);
});

test('AI and JEV Setup filters agree with their actual marks at the observation time',()=>{
 assert.equal(filterRows([row],{setup:'tagAi'},now).length,1);
 assert.equal(filterRows([row],{setup:'tagAi'},Date.parse('2026-10-29T09:59:00-04:00')).length,0);
 assert.equal(filterRows([{symbol:'AAPL',dayLines:{jev:at}}],{setup:'tagJev'},now).length,0);
 const accepted={symbol:'AAPL',dayLines:{jev:at},jevPick:{at,p:.42,rule:'go'}};
 assert.equal(filterRows([accepted],{setup:'tagJev'},now).length,1);
});

test('old row study windows are visible even when a live quote is newer',()=>{
 const atMs=Date.parse('2026-10-30T07:30:00-04:00');
 const row={marketDataAt:atMs/1000,m5:{sqzMom:{barAt:atMs/1000}}};
 const f=scannerRowFreshness(row,Date.parse('2026-10-30T08:09:00-04:00'));
 assert.equal(f.text,'Data 07:30');
 assert.match(f.title,/newer live quote does not refresh/);
 assert.equal(scannerRowFreshness(row,atMs+300009),null);
 assert.equal(scannerRowFreshness({},atMs),null);
 assert.equal(scannerRowFreshness({marketDataAt:0},atMs),null);
 const yesterday=Date.parse('2026-10-29T16:00:00-04:00');
 assert.equal(scannerRowFreshness({marketDataAt:yesterday/1000},atMs).text,'Data 10/29 16:00');
 assert.match(scannerRowFreshness({marketDataAt:yesterday/1000},atMs).title,/10\/29\/2026 16:00 ET/);
});
test('freshness distinguishes newest and oldest source bars from scan, API and browser times',()=>{
 const b={generatedAt:at,tapeAsOf:at,freshness:{newestDataAt:at,oldestDataAt:'2026-10-29T09:45:00-04:00',servedAt:'2026-10-29T10:59:50-04:00',providerNote:'Rate-paced TOS'}};
 const f=scannerFreshness(b,now,'2026-10-29T10:59:51-04:00');
 assert.match(f.text,/60m old.*oldest start 75m/);assert.match(f.title,/API served:.*10:59:50.*Browser received:.*10:59:51/);
 assert.equal(scannerFreshness({},now),null);
});

test('07:25 is displayed as a five-minute window ending at 07:30, not five minutes of feed delay',()=>{
 const board={freshness:{newestDataAt:'2026-10-30T07:25:00-04:00',barTimestampRole:'start',barIntervalSeconds:300}};
 const f=scannerFreshness(board,Date.parse('2026-10-30T07:30:09-04:00'),Date.parse('2026-10-30T07:30:11-04:00'));
 assert.equal(f.text,'5m bar 07:25–07:30 ET');
 assert.match(f.title,/not when the feed arrived/);
 assert.match(f.title,/not proof/);
 assert.match(f.title,/window ends.*07:30:00/);
 assert.match(f.title,/Browser received:.*07:30:11/);
 assert.match(scannerFreshness(board,Date.parse('2026-10-30T07:50:00-04:00')).text,/window ended 20m ago/);
});

test('forming, midnight and stale row windows retain their real times',()=>{
 const b={freshness:{newestDataAt:'2026-10-30T23:55:00-04:00',oldestDataAt:'2026-10-30T23:40:00-04:00',barTimestampRole:'start',barIntervalSeconds:300}};
 const f=scannerFreshness(b,Date.parse('2026-10-30T23:57:00-04:00'));
 assert.match(f.text,/23:55–00:00 ET/);assert.match(f.text,/oldest start 17m ago/);
 assert.doesNotMatch(f.text,/window ended/);
});


test('compact Record can show a50percent score while the AI filter retains55percent cutoff',()=>{
 const lower={...row,aiScore:{...row.aiScore,p:.5}};
 assert.deepEqual(aiTags(lower,now),[]);
 assert.equal(aiTags(lower,now,0)[0].text,'AI 50%');
 assert.deepEqual(aiTags(lower,Date.parse('2026-10-30T11:00:00-04:00'),0),[]);
});
