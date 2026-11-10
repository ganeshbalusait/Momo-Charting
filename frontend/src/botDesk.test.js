import assert from "node:assert/strict";
import test from "node:test";

import { deskAlerts, deskClock, deskCoverage, deskCurve, deskMoney, deskObjectives, deskPct, deskTone, deskVerdict, readDeskView } from "./botDesk.js";

const LIMITS = { profitTarget: 1000, dailyLossLimit: 500, maxDrawdown: 1000, luckTrades: 3 };

const PAPER3 = {
  orders: { sent: 375, filled: 132, missed: 238, holding: 3, stuck: 5, stuckSymbols: ["CCJ", "CEG"], holdingOvernight: 0, fillRatePct: 35.7 },
  verdict: { ready: false, passed: 1, total: 8 },
  from: "2026-10-19", through: "2026-11-09",
  objectives: [
    { key: "profit", target: 1000, actual: -6817, pass: false },
    { key: "dailyLoss", target: -500, actual: -1354, pass: false, breaks: 9 },
    { key: "drawdown", target: 1000, actual: 7281, pass: false },
    { key: "days", target: 20, actual: 16, pass: false },
    { key: "trades", target: 100, actual: 129, pass: true },
    { key: "profitFactor", target: 1.3, actual: 0.59, pass: false },
    { key: "luck", target: 0, actual: -9664, pass: false, trades: 3 },
    { key: "fill", target: 50, actual: 35.7, pass: false },
  ],
};

test("the Scorecard is the default view; only a saved Research choice opens Research", () => {
  assert.equal(readDeskView(null), "desk");
  assert.equal(readDeskView(""), "desk");
  assert.equal(readDeskView("anything"), "desk");
  assert.equal(readDeskView("research"), "research");
});

test("money shows its sign, and a missing number is a dash rather than a false zero", () => {
  assert.equal(deskMoney(1350), "+$1,350");
  assert.equal(deskMoney(-6817.4), "−$6,817");
  assert.equal(deskMoney(0), "$0");
  assert.equal(deskMoney(null), "–");
  assert.equal(deskMoney(undefined), "–");
  assert.equal(deskPct(35.7), "36%");
  assert.equal(deskPct(null), "–");
  assert.equal(deskTone(-1), "is-down");
  assert.equal(deskTone(2), "is-up");
  assert.equal(deskTone(null), "");
});

test("the verdict says not ready and how many checks passed", () => {
  assert.deepEqual(deskVerdict(PAPER3), { tone: "notready", title: "Not ready for real money",
    line: "Passed 1 of 8 checks on real paper fills. Every check must pass before real money." });
});

test("the verdict says passed only when every check passed, and empty when nothing traded", () => {
  const ready = deskVerdict({ orders: { sent: 300 }, verdict: { ready: true, passed: 8, total: 8 } });
  assert.equal(ready.tone, "ready");
  assert.equal(ready.title, "Passed the paper evaluation");
  assert.equal(deskVerdict(null).tone, "empty");
  assert.equal(deskVerdict({ orders: { sent: 0 }, verdict: { ready: false, passed: 0, total: 8 } }).title, "No paper trades yet");
});

test("each check reads as what it got next to what it needs", () => {
  const rows = Object.fromEntries(deskObjectives(PAPER3, LIMITS).map((o) => [o.key, o]));
  assert.deepEqual([rows.profit.actual, rows.profit.target, rows.profit.pass], ["−$6,817", "Needs +$1,000", false]);
  assert.equal(rows.profit.progress, 0);
  assert.equal(rows.dailyLoss.actual, "−$1,354");
  assert.equal(rows.dailyLoss.target, "Worst day · limit $500 · broken on 9 days");
  assert.equal(rows.drawdown.actual, "Fell $7,281");
  assert.deepEqual([rows.days.actual, rows.days.target, rows.days.progress], ["16", "Needs 20", 0.8]);
  assert.deepEqual([rows.trades.actual, rows.trades.pass, rows.trades.progress], ["129", true, 1]);
  assert.deepEqual([rows.profitFactor.actual, rows.profitFactor.target], ["0.59", "Needs 1.3 or more"]);
  assert.equal(rows.luck.target, "Without the best 3 trades · needs above $0");
  assert.deepEqual([rows.fill.actual, rows.fill.target], ["36%", "Needs 50% or more"]);
  assert.ok(rows.fill.help.length > 10);
});

test("a check with nothing to judge yet shows a dash and is not passed", () => {
  const rows = deskObjectives({ objectives: [{ key: "profit", target: 1000, actual: null, pass: false },
    { key: "profitFactor", target: 1.3, actual: null, pass: false }, { key: "dailyLoss", target: -500, actual: null, pass: false, breaks: 0 }] });
  assert.deepEqual(rows.map((r) => [r.actual, r.pass, r.progress]), [["–", false, 0], ["–", false, 0], ["–", false, 0]]);
  assert.equal(rows[2].target, "Worst day · limit $500");
});

test("alerts call out the off switch, stuck orders and positions left overnight", () => {
  assert.deepEqual(deskAlerts({ paused: false }, { orders: {} }), []);
  const alerts = deskAlerts({ paused: true }, { orders: { stuck: 2, stuckSymbols: ["CCJ", "CEG"], holdingOvernight: 1 } });
  assert.deepEqual(alerts.map((a) => a.text), ["Paper bot is switched OFF", "2 stuck orders are holding a slot (CCJ, CEG)",
    "1 position from an earlier day still not sold"]);
  assert.equal(deskAlerts({}, { orders: { stuck: 1, stuckSymbols: [] } })[0].text, "1 stuck order is holding a slot");
});

test("coverage line and clock", () => {
  assert.equal(deskCoverage(PAPER3), "2026-10-19 to 2026-11-09 · 375 orders sent · 132 filled · 238 never filled · 3 open now");
  assert.equal(deskCoverage(null), "");
  assert.equal(deskClock("2026-11-09T15:05:52-05:00"), "11-09 15:05 ET");
  assert.equal(deskClock(""), "");
});

test("the curve keeps zero, the target and the drawdown floor inside the frame", () => {
  const curve = deskCurve([{ day: "a", cum: 400, pnl: 400 }, { day: "b", cum: -300, pnl: -700 }, { day: "c", cum: -100, pnl: 200 }], LIMITS, 640, 200, 14);
  assert.equal(curve.points.length, 3);
  assert.equal(curve.points[0].x, 14);
  assert.equal(curve.points[2].x, 626);
  assert.equal(curve.targetY, 14);          // +1,000 is the top of the frame
  assert.equal(curve.floorY, 186);          // −1,000 is the bottom
  assert.equal(curve.zeroY, 100);
  assert.ok(curve.points[0].y < curve.zeroY && curve.points[1].y > curve.zeroY);
  assert.equal(curve.last.day, "c");
  assert.equal(curve.line.split(" ").length, 3);
});

test("a deep loss stretches the frame instead of drawing off the chart, and one day still draws", () => {
  const deep = deskCurve([{ day: "a", cum: -7000, pnl: -7000 }], LIMITS, 640, 200, 14);
  assert.equal(deep.points[0].y, 186);
  assert.equal(deep.points[0].x, 320);
  assert.ok(deep.floorY < 186 && deep.floorY > deep.zeroY);
  const none = deskCurve([], LIMITS);
  assert.deepEqual([none.points.length, none.line, none.last], [0, "", null]);
});
