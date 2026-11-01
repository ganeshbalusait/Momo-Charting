import test from "node:test";
import assert from "node:assert/strict";
import { readScannerBoard, SCANNER_REQUEST_TIMEOUT_MS } from "./scannerBoardRead.js";

const reply = data => ({ok:true,status:200,json:async()=>data});
const aborted = () => new DOMException("Fetch is aborted", "AbortError");
const hang = signal => new Promise((_,reject)=>signal.addEventListener("abort",()=>reject(aborted()),{once:true}));

test("mobile read deadline outlives the poll interval and JSON is read before acceptance",async()=>{
 assert.ok(SCANNER_REQUEST_TIMEOUT_MS>15000);
 const result=await readScannerBoard('/board',{fetchImpl:async(url,init)=>{
  assert.equal(init.priority,'high');assert.equal(init.cache,'no-store');return reply({rows:['AXP']});
 }});
 assert.deepEqual(result.payload,{rows:['AXP']});assert.equal(result.response.status,200);
});
test("a deadline cancels the first body, then a fresh signal recovers the rows",async()=>{
 let calls=0;const signals=[],messages=[];
 const result=await readScannerBoard('/board',{timeoutMs:10,retryDelayMs:0,onRetry:m=>messages.push(m),fetchImpl:async(u,init)=>{
  signals.push(init.signal);calls++;return calls===1?{ok:true,json:()=>hang(init.signal)}:reply({rows:['CBOE']});
 }});
 assert.equal(calls,2);assert.equal(signals[0].aborted,true);assert.equal(signals[1].aborted,false);
 assert.deepEqual(result.payload.rows,['CBOE']);assert.equal(messages.length,1);
});
test("an unexpected Safari abort retries once instead of surfacing browser error text",async()=>{
 let calls=0;const result=await readScannerBoard('/board',{retryDelayMs:0,fetchImpl:async()=>{if(++calls===1)throw aborted();return reply({rows:[1]});}});
 assert.equal(calls,2);assert.deepEqual(result.payload.rows,[1]);
});
test("repeated interruption has an honest message and no raw Fetch is aborted text",async()=>{
 let calls=0;
 await assert.rejects(readScannerBoard('/board',{retryDelayMs:0,fetchImpl:async()=>{calls++;throw aborted();}}),e=>e.name==='ScannerReadTimeout'&&!e.message.includes('Fetch is aborted')&&e.message.includes('retry automatically'));
 assert.equal(calls,2);
});
test("leaving the view cancels its body without issuing another read",async()=>{
 const parent=new AbortController();let calls=0,notify;const started=new Promise(r=>notify=r);
 const result=readScannerBoard('/board',{signal:parent.signal,fetchImpl:async(u,init)=>{calls++;notify();return {ok:true,json:()=>hang(init.signal)};}});
 await started;await Promise.resolve();parent.abort();
 await assert.rejects(result,e=>e.name==='AbortError');assert.equal(calls,1);
});
test("HTTP errors are returned without parsing their body or retrying",async()=>{
 let calls=0;const result=await readScannerBoard('/board',{fetchImpl:async()=>{calls++;return {ok:false,status:404,json:()=>{throw Error('must not parse');}};}});
 assert.equal(result.response.status,404);assert.equal(result.payload,null);assert.equal(calls,1);
});
