import test from "node:test";
import assert from "node:assert/strict";
import {nextChartGapPoll} from "./chartGapRecovery.js";

test("a refresh acknowledgement with old history keeps a short polling cadence without more quotes", () => {
  let state = nextChartGapPoll(null, 1800, 1200);
  assert.equal(state.delayMs, 1000);
  // The live quote stops. A second read still has the old delivered watermark.
  state = nextChartGapPoll(state, 1800, 1200);
  assert.equal(state.delayMs, 1000);
  assert.equal(nextChartGapPoll(state, 1800, 1740), null);
});
test("a stalled source or genuine no-trade interval backs off instead of polling every second forever", () => {
  let state = null;
  for (let i=0;i<9;i++) state = nextChartGapPoll(state, 1800, 1200);
  assert.equal(state.delayMs, 10000);
  // Further ticks alone never reset the backoff.
  state = nextChartGapPoll(state, 1860, 1200);
  assert.equal(state.delayMs, 10000);
  // Delivered history advancing permits a fresh short burst.
  state = nextChartGapPoll(state, 1860, 1560);
  assert.equal(state.delayMs, 1000);
});
test("unknown or already adjacent history does not create a poll loop", () => {
  for (const [live,history] of [[1800,0],[NaN,1200],[1800,NaN],[1800,1800],[1800,1740],[1800,1710],[1200,1800]])
    assert.equal(nextChartGapPoll(null,live,history),null);
});
