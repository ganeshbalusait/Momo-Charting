import test from "node:test";
import assert from "node:assert/strict";
import {timingStart, timingMark} from "./chartLoadTiming.js";

test("a stalled timing report releases its connection after five seconds", async t => {
  t.mock.timers.enable({apis: ["setTimeout"]});
  let signal;
  t.mock.method(globalThis, "fetch", async (url, options) => {
    assert.equal(url, "/api/client-timing");
    assert.equal(options.priority, "low");
    signal = options.signal;
    return new Promise((resolve, reject) => signal.addEventListener("abort", () => reject(new Error("aborted"))));
  });
  timingMark(timingStart("SPY", "first"), "error", {error: "offline"});
  t.mock.timers.tick(4999);assert.equal(signal.aborted, false);
  t.mock.timers.tick(1);assert.equal(signal.aborted, true);
  await new Promise(resolve => setImmediate(resolve));
});

test("a completed timing report clears its deadline", async t => {
  t.mock.timers.enable({apis: ["setTimeout"]});
  let signal;
  t.mock.method(globalThis, "fetch", async (_url, options) => {signal=options.signal;return {ok:true};});
  timingMark(timingStart("AAPL", "first"), "noBars");
  await new Promise(resolve => setImmediate(resolve));
  t.mock.timers.tick(5001);assert.equal(signal.aborted, false);
});
