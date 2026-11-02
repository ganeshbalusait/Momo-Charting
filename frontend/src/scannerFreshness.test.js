import {test} from "node:test";
import assert from "node:assert/strict";
import {scannerRowFreshness,setupSourceTime} from "./scannerFreshness.js";
test("Time column uses clock only today while preserving the separate data warning",()=>{
 const value=scannerRowFreshness({marketDataAt:"2026-11-02T13:10:00Z"},Date.parse("2026-11-02T13:30:00Z"));
 assert.equal(value.timeText,"08:10");assert.equal(value.text,"Data 08:10");assert.match(value.title,/older/);
});
test("previous ET date includes month, day and time",()=>{
 const value=scannerRowFreshness({marketDataAt:"2026-10-30T20:30:00Z"},Date.parse("2026-11-02T13:30:00Z"));
 assert.equal(value.timeText,"10/30 16:30");
});
test("date boundary follows New York instead of UTC",()=>{
 const value=scannerRowFreshness({marketDataAt:"2026-11-02T04:30:00Z"},Date.parse("2026-11-02T05:10:00Z"));
 assert.equal(value.timeText,"11/01 23:30");
});
test("Setup omits a duplicate Time timestamp and retains a different or hidden source time without Data",()=>{
 const warning=scannerRowFreshness({marketDataAt:"2026-11-02T13:10:00Z"},Date.parse("2026-11-02T13:30:00Z"));
 assert.equal(setupSourceTime(warning,"08:10"),null);
 assert.equal(setupSourceTime(warning,"08:15"),"08:10");
 assert.equal(setupSourceTime(warning),"08:10");
 assert.equal(setupSourceTime(null,"08:10"),null);
 const old=scannerRowFreshness({marketDataAt:"2026-10-30T12:10:00Z"},Date.parse("2026-11-02T13:30:00Z"));
 assert.equal(setupSourceTime(old,"08:10"),"10/30 08:10");
});
