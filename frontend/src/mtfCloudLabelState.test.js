import test from "node:test";
import assert from "node:assert/strict";
import { mtfCloudLabelState } from "./mtfCloudLabelState.js";

const ramp = (from, step, count) => Array.from({ length: count }, (_, index) => from + step * index);

test("steady uptrend: bull top and bottom, both strengthening, no fresh cross", () => {
  const state = mtfCloudLabelState(ramp(100, 1, 40));
  assert.equal(state.tone, "bull");
  assert.equal(state.priceTone, "bull");
  assert.equal(state.trendStrong, true);
  assert.equal(state.freshCross, null);
});

test("downtrend that bounces: top stays bear while bottom turns bull (the split D chip)", () => {
  const closes = [...ramp(140, -1, 40), 103, 106];
  const state = mtfCloudLabelState(closes);
  assert.equal(state.tone, "bear");
  assert.equal(state.priceTone, "bull");
  assert.equal(state.trendStrong, false);
});

test("fresh cross is flagged only on the bar EMA 9 crosses EMA 20", () => {
  const down = ramp(140, -1, 40);
  let closes = [...down];
  let state = mtfCloudLabelState(closes);
  while (state.tone === "bear") {
    closes = [...closes, closes.at(-1) + 4];
    state = mtfCloudLabelState(closes);
  }
  assert.equal(state.freshCross, "bull");
  assert.equal(mtfCloudLabelState([...closes, closes.at(-1) + 4]).freshCross, null);

  closes = ramp(100, 1, 40);
  state = mtfCloudLabelState(closes);
  while (state.tone === "bull") {
    closes = [...closes, closes.at(-1) - 4];
    state = mtfCloudLabelState(closes);
  }
  assert.equal(state.freshCross, "bear");
});

test("an exact EMA tie is Bear, like the ThinkScript", () => {
  assert.equal(mtfCloudLabelState([50, 50, 50]).tone, "bear");
});

test("empty input gives no chip", () => {
  assert.equal(mtfCloudLabelState([]), null);
  assert.equal(mtfCloudLabelState(null), null);
});
