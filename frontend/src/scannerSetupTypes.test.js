import test from 'node:test';
import assert from 'node:assert/strict';
import { SCANNER_SETUP_TYPES, scannerSetupTypes } from './scannerSetupTypes.js';
test('Distinct 23 Setup types include patterns and informational choices without timeframe inflation',()=>{
  assert.equal(SCANNER_SETUP_TYPES.length,23);assert.equal(new Set(SCANNER_SETUP_TYPES.map(t=>t[0])).size,23);
  const at=Date.parse('2026-10-30T14:00:00Z');
  const r={symbol:'X',grade:{letter:'A'},m5:{pattern:'explosive'},rvol:Object.fromEntries(['5m','15m','30m'].map(tf=>[tf,{value:3,bg:'cyan',barAt:at/1000}]))};
  const tags=scannerSetupTypes(r,at);
  assert.equal(tags.filter(t=>t.key==='tagRvol').length,1);
  assert.ok(tags.some(t=>t.key==='tagExplosive'));
  assert.equal(tags.some(t=>t.key==='tagMomStrong'),false);
  assert.equal(scannerSetupTypes({...r,grade:null},at).some(t=>t.key==='tagExplosive'),false);
});
