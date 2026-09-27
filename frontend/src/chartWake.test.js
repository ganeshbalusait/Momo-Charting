import assert from "node:assert/strict";
import test from "node:test";

import { MAX_LIVE_BAR_GAP_SECONDS } from "./chartStreamBars.js";
import {
  CHART_STALE_AFTER_MS,
  chartStaleLabel,
  chartWakeDecision,
} from "./chartWake.js";

const NOW = 1_800_000_000_000; // ms
const barAt = (ageMs) => (NOW - ageMs) / 1000;

test("a candle inside the stale window needs nothing", () => {
  const decision = chartWakeDecision({ nowMs: NOW, latestBarTime: barAt(CHART_STALE_AFTER_MS - 1_000) });
  assert.equal(decision.stale, false);
  assert.equal(decision.mode, "none");
  assert.equal(decision.reconnectStream, false);
});

test("a candle just past the stale window gets a delta and a stream reconnect", () => {
  const decision = chartWakeDecision({ nowMs: NOW, latestBarTime: barAt(CHART_STALE_AFTER_MS + 1_000) });
  assert.equal(decision.stale, true);
  assert.equal(decision.mode, "delta");
  assert.equal(decision.reconnectStream, true);
});

test("a gap past the live tick gate needs the full tape (the overnight phone case)", () => {
  const justInside = chartWakeDecision({ nowMs: NOW, latestBarTime: barAt(MAX_LIVE_BAR_GAP_SECONDS * 1000 - 60_000) });
  assert.equal(justInside.mode, "delta");
  const sixHours = chartWakeDecision({ nowMs: NOW, latestBarTime: barAt(6 * 3_600_000) });
  assert.equal(sixHours.mode, "full");
});

test("no candle at all means the full tape", () => {
  assert.equal(chartWakeDecision({ nowMs: NOW, latestBarTime: 0 }).mode, "full");
  assert.equal(chartWakeDecision({ nowMs: NOW }).mode, "full");
});

test("only a request that outlived the suspension is abandoned", () => {
  const fresh = chartWakeDecision({ nowMs: NOW, latestBarTime: barAt(0), requestInFlight: true, requestStartedAt: NOW - 5_000 });
  assert.equal(fresh.abandonRequest, false);
  const dead = chartWakeDecision({ nowMs: NOW, latestBarTime: barAt(0), requestInFlight: true, requestStartedAt: NOW - 31_000 });
  assert.equal(dead.abandonRequest, true);
  const idle = chartWakeDecision({ nowMs: NOW, latestBarTime: barAt(0), requestInFlight: false, requestStartedAt: NOW - 31_000 });
  assert.equal(idle.abandonRequest, false);
});

test("stale label reads as minutes, then hours", () => {
  assert.equal(chartStaleLabel(60_000), null);
  assert.equal(chartStaleLabel(6 * 60_000), "STALE (6m)");
  assert.equal(chartStaleLabel(6 * 3_600_000), "STALE (6h)");
  assert.equal(chartStaleLabel(6 * 3_600_000 + 5 * 60_000), "STALE (6h 5m)");
  assert.equal(chartStaleLabel(Infinity), "STALE");
});
