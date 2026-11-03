import {test} from "node:test";
import assert from "node:assert/strict";
import {readScannerSort,writeScannerSort} from "./scannerSortPreferences.js";
const keys=["highLow","matchedSince","symbol","pctChange","rvol_5m"];
const fallback={key:"highLow",direction:"desc"};
function store(){const values=new Map();return {getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v)};}
test("untouched scanner defaults H/L; every column and direction survives remount",()=>{
 const storage=store();assert.equal(readScannerSort("live:bull",keys,fallback,storage),fallback);
 for(const key of keys) for(const direction of ["asc","desc"]){
  writeScannerSort("live:bull",{key,direction},storage);
  assert.deepEqual(readScannerSort("live:bull",keys,fallback,storage),{key,direction});
 }
});
test("bull, bear, news and history sorts remain independent",()=>{
 const storage=store();
 writeScannerSort("live:bull",{key:"matchedSince",direction:"desc"},storage);
 writeScannerSort("live:bear",{key:"symbol",direction:"asc"},storage);
 writeScannerSort("history",{key:"pctChange",direction:"asc"},storage);
 assert.equal(readScannerSort("live:bull",keys,fallback,storage).key,"matchedSince");
 assert.equal(readScannerSort("live:bear",keys,fallback,storage).key,"symbol");
 assert.equal(readScannerSort("news",keys,fallback,storage),fallback);
 assert.equal(readScannerSort("history",keys,fallback,storage).key,"pctChange");
});
test("explicit no-sort survives while invalid preferences fall back safely",()=>{
 const storage=store();
 writeScannerSort("live:bull",{key:null,direction:"none"},storage);
 assert.deepEqual(readScannerSort("live:bull",keys,fallback,storage),{key:null,direction:"none"});
 for(const value of [null,{key:"removed",direction:"desc"},{key:"symbol",direction:"wrong"}]){
  writeScannerSort("live:bull",value,storage);assert.equal(readScannerSort("live:bull",keys,fallback,storage),fallback);
 }
 const blocked={getItem(){throw Error("blocked")},setItem(){throw Error("blocked")}};
 assert.equal(readScannerSort("live:bull",keys,fallback,blocked),fallback);
 assert.doesNotThrow(()=>writeScannerSort("live:bull",fallback,blocked));
});
