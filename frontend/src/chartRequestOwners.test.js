import test from "node:test";
import assert from "node:assert/strict";
import {createChartRequestOwners} from "./chartRequestOwners.js";
test("switching the last pane cancels its archive body read", () => {
  const owners=createChartRequestOwners(),leave=owners.retain("TSLA"),controller=new AbortController();
  owners.trackArchive("TSLA",controller);
  let bodyCanceled=false;controller.signal.addEventListener("abort",()=>bodyCanceled=true);
  leave();assert.equal(bodyCanceled,true);leave();assert.equal(controller.signal.aborted,true);
});
test("one linked pane leaving cannot abort another pane's shared archive", () => {
  const owners=createChartRequestOwners(),first=owners.retain("spy"),second=owners.retain("SPY");
  const controller=new AbortController();owners.trackArchive("SPY",controller);
  first();assert.equal(controller.signal.aborted,false);
  second();assert.equal(controller.signal.aborted,true);
});
test("settled requests and other tickers are independent of a later switch", () => {
  const owners=createChartRequestOwners(),leave=owners.retain("AAPL"),leaveSpy=owners.retain("SPY");
  const done=new AbortController(),spy=new AbortController();
  owners.trackArchive("AAPL",done)();owners.trackArchive("SPY",spy);
  leave();assert.equal(done.signal.aborted,false);assert.equal(spy.signal.aborted,false);
  leaveSpy();assert.equal(spy.signal.aborted,true);
});
test("a delayed archive cannot start after its ticker's pane left", () => {
  const owners=createChartRequestOwners(),leave=owners.retain("NVDA");leave();
  const controller=new AbortController();owners.trackArchive("NVDA",controller);
  assert.equal(controller.signal.aborted,true);
});
test("switching cancels ordinary snapshots and history-status reads as well as archives", () => {
  const owners=createChartRequestOwners(),leave=owners.retain("META");
  const snapshot=new AbortController(),status=new AbortController();
  owners.trackRequest("META",snapshot);owners.trackRequest("META",status);
  leave();assert.equal(snapshot.signal.aborted,true);assert.equal(status.signal.aborted,true);
});
test("an ownerless bounded warm-up survives until its adopting pane leaves", () => {
  const owners=createChartRequestOwners(),warm=new AbortController();
  owners.trackRequest("NVDA",warm);assert.equal(warm.signal.aborted,false);
  const leave=owners.retain("NVDA");leave();assert.equal(warm.signal.aborted,true);
});
test("one linked pane leaving keeps ordinary shared reads alive", () => {
  const owners=createChartRequestOwners(),first=owners.retain("SPY"),second=owners.retain("SPY");
  const request=new AbortController();owners.trackRequest("SPY",request);
  first();assert.equal(request.signal.aborted,false);second();assert.equal(request.signal.aborted,true);
});
