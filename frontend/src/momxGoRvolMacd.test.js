import test from 'node:test';
import assert from 'node:assert/strict';
import { goRvolMacd, coerceFilters, rowPasses, DEFAULT_MOMX_FILTERS, strategyTags, describeMembership } from './momxFilters.js';
const now = Date.parse('2026-09-21T10:05:20-04:00');
const fixture = () => ({ symbol: 'META', grade: {letter:'A'},
  rvol: {'15m':{bg:'cyan',value:0.2}},
  m5:{gapGo:{goAt: now/1000-1220},goConfirmation:{barAt:now/1000-320,macdUp:true,buyers30:true}} });
test('built-in conjunction, persistence, badge and live removal', () => {
  const config=coerceFilters({...DEFAULT_MOMX_FILTERS,setup:'goRvolMacd'});
  assert.equal(config.setup,'goRvolMacd');
  const r=fixture();
  assert.equal(rowPasses(r,config,now),true);
  assert.ok(strategyTags(r,now).some(t=>t.key==='go-rvol-macd'));
  r.m5.goConfirmation.macdUp=false;
  assert.equal(rowPasses(r,config,now),false);
  assert.match(describeMembership(config),/built-in RVOL/);
});
test('each short cyan background passes; text, green and long frames do not',()=>{
  for(const tf of ['5m','15m','30m']){const r=fixture();r.rvol={[tf]:{bg:'cyan'}};assert.ok(goRvolMacd(r,now));}
  for(const rv of [{'5m':{fg:'cyan'}},{'5m':{bg:'green'}},{'2h':{bg:'cyan'}},{}]){
    const r=fixture();r.rvol=rv;assert.equal(goRvolMacd(r,now),null);
  }
});
test('missing, bearish, stale, future, outside hours and lower grades fail closed',()=>{
  for(const change of [r=>r.direction='bear',r=>r.grade.letter='B',r=>delete r.m5.goConfirmation,
    r=>r.m5.goConfirmation.buyers30=false,r=>r.m5.goConfirmation.barAt-=600,
    r=>r.m5.goConfirmation.barAt+=300,r=>r.m5.gapGo.goAt+=86400]){
    const r=fixture();change(r);assert.equal(goRvolMacd(r,now),null);
  }
  assert.equal(goRvolMacd(fixture(),Date.parse('2026-09-22T10:05:20-04:00')),null);
  assert.equal(goRvolMacd(fixture(),Date.parse('2026-09-21T16:05:20-04:00')),null);
});
