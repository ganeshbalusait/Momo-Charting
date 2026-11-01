import test from "node:test";
import assert from "node:assert/strict";
import { createLiveRowProjector, liveQuote } from "./scannerLiveRows.js";
import { sortRows, highLowSortValue } from "./momxCells.js";

const now = Date.parse("2026-10-30T10:00:00-04:00");
const quote = (last, at = now) => ({ last, lastAt: new Date(at).toISOString(), tradeTime: at });
const row = (symbol = "SPY", barAt = now / 1000 - 3600) => ({ symbol, prevClose: 105,
  highLow: { hh: 110, ll: 100, value: 0.2, bg: "green", barAt } });

test("H/L retains a new high after retreat and updates its sort position", () => {
  const project = createLiveRowProjector(), source = row();
  assert.equal(project(source, quote(114), now, true).highLow.value, 1);
  const retreat = project(source, quote(110, now + 1000), now + 1000, true);
  assert.equal(retreat.highLow.hh, 114);
  assert.equal(retreat.highLow.value, 0.43); // used to incorrectly show +1
  const sorted = sortRows([{symbol:"SPY", hl:retreat.highLow.value}, {symbol:"AAPL", hl:0.6}], "hl", "desc");
  assert.deepEqual(sorted.map(r => r.symbol), ["AAPL", "SPY"]);
  assert.equal(source.highLow.hh, 110); // no mutation of stored board/history
});

test("AXP/CBOE-style values displayed as 1.00 still sort by the actual formula", () => {
  const axp = {symbol:"AXP", highLow:{value:1,degree:(325.68 - (325.69 + 320) / 2) / ((325.69 - 320) / 2)}};
  const cboe = {symbol:"CBOE", highLow:{value:1,degree:1}};
  const index = [axp,cboe].map(r => ({symbol:r.symbol,hl:highLowSortValue(r.highLow)}));
  assert.deepEqual(sortRows(index,"hl","desc").map(r => r.symbol), ["CBOE","AXP"]);
  const bear = [{symbol:"AXP",hl:-0.996},{symbol:"CBOE",hl:-1}];
  assert.equal(sortRows(bear,"hl","asc")[0].symbol,"CBOE");
  assert.equal(highLowSortValue({value:null}),null);
  assert.equal(highLowSortValue({value:0.3}),0.3);
});

test("H/L retains a new low after a bounce, without crossing tickers", () => {
  const project = createLiveRowProjector(), source = row();
  project(source, quote(96), now, true);
  assert.equal(project(source, quote(100, now + 1000), now + 1000, true).highLow.value, -0.43);
  assert.equal(project(row("AAPL"), quote(110), now, true).highLow.value, 1);
});

test("same-window rebuild keeps extrema, new 2h window discards them", () => {
  const project = createLiveRowProjector(), source = row();
  project(source, quote(114), now, true);
  assert.equal(project({...source}, quote(110, now + 1000), now + 1000, true).highLow.hh, 114);
  const rolled = row("SPY", now / 1000);
  assert.equal(project(rolled, quote(110, now + 2000), now + 2000, true).highLow.hh, 110);
});

test("stale, missing, future and older-than-board quotes keep the board value", () => {
  const project = createLiveRowProjector(), source = row();
  for (const q of [null, {last:110}, quote(110, now - 91000), quote(110, now + 6000)]) {
    assert.equal(project(source, q, now, true), source);
  }
  const freshBoard = {...source, marketDataAt:new Date(now + 2000).toISOString()};
  assert.equal(project(freshBoard, quote(110), now, true), freshBoard);
  assert.equal(project({...source, marketDataAt:(now + 2000) / 1000}, quote(110), now, true).highLow.live, undefined);
  assert.equal(liveQuote({...quote(110), tradeTime:now - 91000}, now), null);
  assert.ok(liveQuote({...quote(110), tradeTime:now - 91000, lastSource:"chart"}, now));
});

test("out-of-order quote cannot reverse a newer price; unchanged rows reuse identity", () => {
  const project = createLiveRowProjector(), source = row();
  const output = project(source, quote(114, now + 1000), now + 1000, true);
  assert.equal(project(source, quote(106), now + 1000, true), output);
  assert.equal(project(source, quote(114, now + 2000), now + 2000, true), output);
  assert.equal(project(source, quote(106, now + 1500), now + 2000, true), output);
});

test("flat range opens and retracts correctly; legacy cells reset on rebuild", () => {
  const project = createLiveRowProjector();
  const flat = {...row(), highLow:{hh:100,ll:100,value:0,bg:"gray",barAt:1}};
  assert.equal(project(flat, quote(110), now, true).highLow.value, 1);
  assert.equal(project(flat, quote(105, now + 1000), now + 1000, true).highLow.value, 0);
  const legacy = {...row(), highLow:{hh:110,ll:100}};
  project(legacy, quote(114), now, true);
  assert.equal(project({...legacy}, quote(110, now + 1000), now + 1000, true).highLow.hh, 110);
});
