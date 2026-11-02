import { test } from "node:test";
import assert from "node:assert/strict";
import { matchingScannerRows, currentScannerRows } from "./scannerMatches.js";
test("scanner excludes failed, missing and malformed results, including saved nonmatches", () => {
  const match = {symbol:"WRBY", scanPass:true};
  assert.deepEqual(matchingScannerRows([null, match, {symbol:"IBM",scanPass:false}, {symbol:"REGN"}, {scanPass:"false"}, {scanPass:1}]), [match]);
});
test("no matches stays empty and a lost match disappears on the next update", () => {
  assert.deepEqual(matchingScannerRows(undefined), []);
  assert.equal(matchingScannerRows([{symbol:"WRBY",scanPass:true}]).length,1);
  assert.deepEqual(matchingScannerRows([{symbol:"WRBY",scanPass:false}]),[]);
});

test("unchecking shows the whole list; checking again restores scan matches", () => {
  const rows = [{symbol:"WRBY",scanPass:true}, {symbol:"IBM",scanPass:false}, {symbol:"REGN"}];
  assert.deepEqual(matchingScannerRows(rows, false), rows);
  assert.deepEqual(matchingScannerRows(rows, true), [rows[0]]);
  assert.deepEqual(matchingScannerRows(undefined, false), []);
});

test("Live hides prior-session dates but keeps today's older candles and overnight bars", () => {
  const now=Date.parse("2026-11-02T07:00:00Z");
  const today={symbol:"IBM",marketDataAt:"2026-11-02T05:40:00Z"};
  const overnight={symbol:"OVERNIGHT",marketDataAt:"2026-11-02T01:00:00Z"};
  const friday={symbol:"FRIDAY",marketDataAt:"2026-10-30T20:30:00Z"};
  const rows=[today,overnight,friday,{symbol:"UNKNOWN"},{marketDataAt:now+1}];
  assert.deepEqual(currentScannerRows(rows,now),[today,overnight]);
  assert.deepEqual(matchingScannerRows(currentScannerRows(rows,now),false),[today,overnight]);
});
test("session rolls at 20 ET, respects DST and source-study timestamps over quote times", () => {
  const now=Date.parse("2026-11-02T07:00:00Z");
  const old={marketDataAt:now,m5:{sqzMom:{barAt:"2026-10-30T20:30:00Z"}}};
  const before={marketDataAt:"2026-11-02T00:59:59Z"};
  const after={marketDataAt:Date.parse("2026-11-02T01:00:00Z")/1000};
  assert.deepEqual(currentScannerRows([old,before,after],now),[after]);
  assert.deepEqual(currentScannerRows([after],Date.parse("2026-11-03T01:00:00Z")),[]);
});
