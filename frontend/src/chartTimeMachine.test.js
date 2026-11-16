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

test("the odds go on the Target, Stop and Entry lines of the same plan only", async () => {
  const { withOddsOnLines } = await import("./chartTimeMachine.js");
  const lines = [
    { key: "entry", title: "Entry 749.60  744P $0.99" },
    { key: "t1", title: "T1 745.99  +92%" },
    { key: "target", title: "T2 742.38  +226%" },
    { key: "stop", title: "Stop 753.14  −50%" },
  ];
  const result = { side: "PUT", target: 742.38, stopStock: 753.14 };
  const answer = { ok: true, side: "PUT", target: 742.38, stop: 753.14, odds: { target: 13.2, stop: 40.4, neither: 46.6 } };
  assert.deepEqual(withOddsOnLines(lines, answer, result).map((line) => line.title), [
    "Entry 749.60  744P $0.99 · 47% neither", "T1 745.99  +92%", "T2 742.38  +226% · 13%", "Stop 753.14  −50% · 40%",
  ]);
  // A moved target (or the other side) is a different plan: no odds shown.
  assert.equal(withOddsOnLines(lines, answer, { ...result, target: 741 }), lines);
  assert.equal(withOddsOnLines(lines, { ...answer, side: "CALL" }, result), lines);
  assert.equal(withOddsOnLines(lines, null, result), lines);
  // With Both on, only the planned side's lines get the odds.
  const both = [{ key: "entry", title: "Entry" }, { key: "c-target", title: "C" }, { key: "p-target", title: "P" }, { key: "p-stop", title: "PS" }];
  assert.deepEqual(withOddsOnLines(both, answer, result).map((line) => line.title), ["Entry · 47% neither", "C", "P · 13%", "PS · 40%"]);
});

test("the chart switch: ROI boxes, only the three odds lines, or both", async () => {
  const { chartPlanLines, oddsOnlyLines } = await import("./chartTimeMachine.js");
  const result = { ready: true, side: "CALL", spot: 355.4, entrySpot: 355.4, target: 359.55, stopStock: 353.82 };
  const answer = { ok: true, side: "CALL", target: 359.55, stop: 353.82, odds: { target: 33.2, stop: 60.9, neither: 5.9 } };
  const roi = [{ key: "entry", title: "Entry 355.40  362.5C $0.22" }, { key: "t1", title: "T1" }, { key: "target", title: "T2 359.55" }, { key: "stop", title: "Stop 353.82" }];
  assert.equal(chartPlanLines("roi", roi, result, answer), roi);
  assert.deepEqual(chartPlanLines("odds", roi, result, answer).map((line) => [line.key, line.price, line.title]), [
    ["entry", 355.4, "Entry 355.40 · 6% neither"], ["target", 359.55, "Target 359.55 · 33%"], ["stop", 353.82, "Stop 353.82 · 61%"],
  ]);
  assert.deepEqual(chartPlanLines("both", roi, result, answer).map((line) => line.title), [
    "Entry 355.40  362.5C $0.22 · 6% neither", "T1", "T2 359.55 · 33%", "Stop 353.82 · 61%",
  ]);
  // Still asking, or an answer for another target: "odds…" instead of a number.
  assert.deepEqual(oddsOnlyLines(result, null).map((line) => line.title), ["Entry 355.40 · odds…", "Target 359.55 · odds…", "Stop 353.82 · odds…"]);
  assert.match(oddsOnlyLines({ ...result, target: 360 }, answer)[1].title, /odds…$/);
  assert.deepEqual(oddsOnlyLines({ ...result, stopStock: null }, answer), []);
});
