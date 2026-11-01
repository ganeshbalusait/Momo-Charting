import test from "node:test";
import assert from "node:assert/strict";
import { createScannerRequestScope } from "./scannerRequestScope.js";

test("leaving scanner cancels every read, including a body whose headers already arrived", async () => {
  const calls=[];
  const scope=createScannerRequestScope(async (url,options)=>{calls.push({url,options});return {json:()=>new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(options.signal.reason),{once:true}))};});
  const response=await scope.fetch('/records');
  const body=response.json();
  await scope.fetch('/lists');
  scope.abortReads();
  await assert.rejects(body,e=>e.name==='AbortError');
  assert.equal(calls.every(c=>c.options.signal.aborted),true);
  await assert.rejects(scope.fetch('/late-poll'),e=>e.name==='AbortError');
  assert.equal(calls.length,2);
});

test("caller deadline cancels its own read without canceling the other scanner reads", async () => {
  const signals=[];const scope=createScannerRequestScope(async (url,options)=>signals.push(options.signal));
  const deadline=new AbortController();
  await scope.fetch('/board',{signal:deadline.signal});await scope.fetch('/momentum');
  deadline.abort();
  assert.equal(signals[0].aborted,true);assert.equal(signals[1].aborted,false);
  scope.abortReads();assert.equal(signals[1].aborted,true);
});

test("writes keep their options and are not canceled by read cleanup", async () => {
  const options={method:'POST',body:'fixture'};let received;
  const scope=createScannerRequestScope(async (url,init)=>received=init);
  await scope.fetch('/save',options);scope.abortReads();
  assert.strictEqual(received,options);assert.equal(received.signal,undefined);
});

test("a remounted scanner receives a fresh scope without reviving canceled reads", async () => {
  const signals=[];const scope=createScannerRequestScope(async (url,options)=>signals.push(options.signal));
  await scope.fetch('/first');scope.abortReads();scope.resume();await scope.fetch('/second');
  assert.equal(signals[0].aborted,true);assert.equal(signals[1].aborted,false);
  scope.abortReads();assert.equal(signals[1].aborted,true);
});

test("fallback signal composition works where AbortSignal.any is unavailable", async () => {
  const original=AbortSignal.any;AbortSignal.any=undefined;
  try {
    const caller=new AbortController();let signal;
    const scope=createScannerRequestScope(async (url,options)=>signal=options.signal);
    await scope.fetch('/board',{signal:caller.signal});scope.abortReads();assert.equal(signal.aborted,true);
  } finally {AbortSignal.any=original;}
});
