// Unit (2026-11-15, feature chart-time-machine): what the chart's Odds card
// shows from a /api/time-machine answer.
import assert from "node:assert/strict";
import test from "node:test";

import { roiAtPrice, timeMachineFan, timeMachinePlan, timeMachineUrl, timeMachineView } from "./chartTimeMachine.js";

const ANSWER = {
  ok: true, symbol: "NVDA", side: "CALL", spot: 182.4, target: 185.6, stop: 180.8, mode: "today",
  startLabel: "10:15", flatLabel: "3:55 PM", steps: 3, paths: 10000, sessions: 4,
  odds: { target: 34.04, stop: 29.0, neither: 36.96 },
  bands: { 10: [181, 180.5, 180], 25: [181.8, 181.5, 181.2], 50: [182.4, 182.5, 182.6], 75: [183, 183.4, 183.8], 90: [183.6, 184.4, 185] },
  sample: [{ outcome: "target", closes: [183, 184.5, 185.7] }, { outcome: "stop", closes: [181.2, 180.7] }],
  history: [{ day: "2026-11-10", outcome: "target" }, { day: "2026-11-11", outcome: "stop" }, { day: "2026-11-12", outcome: "neither" }, { day: "2026-11-13", outcome: "target" }],
  historyCounts: { target: 2, stop: 1, neither: 1 },
  neitherMedianClose: 182.9,
  verdict: "Slight edge to the target, not a sure thing",
};

test("the request carries the plan, and nothing is asked without a full plan", () => {
  assert.equal(timeMachineUrl({ symbol: "nvda", side: "call", spot: 182.4, target: 185.6, stop: 180.8 }),
    "/api/time-machine?symbol=NVDA&side=CALL&spot=182.40&target=185.60&stop=180.80");
  assert.equal(timeMachineUrl({ symbol: "SPY", side: "put", spot: 600, target: 595, stop: 603 }).includes("side=PUT"), true);
  assert.equal(timeMachineUrl({ symbol: "NVDA", spot: 182.4, target: 185.6 }), "");
  assert.deepEqual(timeMachinePlan(null), { url: "", reason: "Waiting for the option chain." });
  assert.match(timeMachinePlan({ ready: true, side: "CALL", spot: 1, target: 2, stopStock: null }).reason, /no stop/);
  assert.match(timeMachinePlan({ ready: true, side: "CALL", spot: 182.4, target: 185.6, stopStock: 180.8 }, "NVDA").url, /stop=180\.80/);
});

test("the card shows three odds rows, the history and the average option outcome", () => {
  const view = timeMachineView(ANSWER, { roiAtTarget: 57, roiAtStop: -50, roiAtNeither: 4 });
  assert.equal(view.ready, true);
  assert.deepEqual(view.rows.map((row) => [row.key, row.label, row.pct]), [
    ["target", "Target hit first", 34], ["stop", "Stop hit first", 29], ["neither", "Neither by 3:55 PM", 37],
  ]);
  assert.equal(view.averageRoi, Math.round((34 * 57 + 29 * -50 + 37 * 4) / 10) / 10);
  assert.equal(view.historyTotal, 4);
  assert.deepEqual(view.historyCounts, { target: 2, stop: 1, neither: 1 });
  assert.equal(view.subtitle, "10,000 replays · from 10:15 ET to 3:55 PM");
  assert.match(view.historyNote, /last 4 sessions \(11-10 – 11-13\)/);
  assert.equal(view.verdict, ANSWER.verdict);
});

test("without an option price the average is left out; a closed market says so; a refusal keeps its reason", () => {
  assert.equal(timeMachineView(ANSWER, { roiAtTarget: null, roiAtStop: -50 }).averageRoi, null);
  assert.match(timeMachineView({ ...ANSWER, mode: "next-session", startLabel: "9:30" }).when, /market closed/);
  assert.deepEqual(timeMachineView({ ok: false, reason: "Only 3 full sessions" }), { ready: false, reason: "Only 3 full sessions" });
  assert.equal(timeMachineView(null).ready, false);
});

test("the option % at a price uses the ROI card's contract model", () => {
  const result = { side: "CALL", spot: 182.4, entry: 1.85, livePrice: 1.85, strike: 185, contract: { delta: 0.32, gamma: 0.04 } };
  const atTarget = roiAtPrice(result, 185.6);
  assert.ok(atTarget > 40 && atTarget < 80, String(atTarget));
  assert.ok(roiAtPrice(result, 181) < 0);
  assert.equal(roiAtPrice({ ...result, contract: null }, 185), null);
});

test("the fan keeps every line inside the drawing and colours paths by outcome", () => {
  const fan = timeMachineFan(ANSWER, { width: 300, height: 150 });
  assert.equal(fan.paths.length, 2);
  assert.deepEqual(fan.paths.map((path) => path.outcome), ["target", "stop"]);
  for (const level of fan.levels) assert.ok(level.y >= 0 && level.y <= 150, level.key);
  const numbers = [fan.outer, fan.inner, fan.median, ...fan.paths.map((path) => path.d)].join(" ").match(/-?\d+\.\d/g).map(Number);
  assert.ok(numbers.every((value) => value >= 0 && value <= 300));
  assert.ok(fan.levels.find((level) => level.key === "target").y < fan.levels.find((level) => level.key === "stop").y);
  assert.equal(timeMachineFan({ ok: false }), null);
});
