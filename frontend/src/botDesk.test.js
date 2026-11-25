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
  assert.equal(deskCoverage(null), "no paper orders yet in this evaluation");
  assert.equal(deskCoverage({ from: null, orders: { sent: 0 } }), "no paper orders yet in this evaluation");
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

// ---- safety controls (2026-11-09): Pause, the daily loss stop, stuck orders
const { deskContract, deskControl, deskStuckRows } = await import("./botDesk.js");

test("the control strip says buying is on, offers Pause, and shows the daily loss stop with today's result", () => {
  const c = deskControl({ control: { paused: false, pausedAt: null, dailyStop: { limit: 500, hit: false, hitAt: null, todayPnl: -120 } } });
  assert.equal(c.state, "New buys are ON");
  assert.deepEqual([c.action, c.button, c.paused, c.stopHit, c.tone], ["pause", "Pause new buys", false, false, "on"]);
  assert.equal(c.stop, "Daily loss stop $500 · today −$120");
});

test("a paused account offers Resume and says since when; a tripped stop says no buys until tomorrow", () => {
  const c = deskControl({ control: { paused: true, pausedAt: "2026-11-09T11:00:00-05:00",
    dailyStop: { limit: 500, hit: true, hitAt: "2026-11-09T10:42:00-05:00", todayPnl: -560 } } });
  assert.equal(c.state, "New buys PAUSED at 11-09 11:00 ET");
  assert.deepEqual([c.action, c.button, c.paused, c.stopHit], ["resume", "Resume buying", true, true]);
  assert.equal(c.stop, "Daily loss stop HIT at 11-09 10:42 ET · no new buys until tomorrow");
});

test("no control state from the server means no control strip, and a missing P/L is a dash", () => {
  assert.equal(deskControl({ orders: {} }), null);
  assert.equal(deskControl(null), null);
  assert.equal(deskControl({ control: { paused: false } }).stop, "Daily loss stop – · today –");
});

test("an option contract reads as ticker, strike, side and expiry", () => {
  assert.equal(deskContract("CCJ   261106P00072000"), "CCJ $72 put · expires 11-06");
  assert.equal(deskContract("META  261106C00632500"), "META $632.5 call · expires 11-06");
  assert.equal(deskContract("RUM261113C00005500"), "RUM $5.5 call · expires 11-13");
  assert.equal(deskContract("", "NVDA"), "NVDA");
  assert.equal(deskContract("something else"), "something else");
});

test("each stuck order gets one line and its key; a row with no key gets no button", () => {
  const rows = deskStuckRows({ orders: { stuckRows: [
    { key: "bear|call2h|CCJ|x", symbol: "CCJ", contract: "CCJ   261106P00072000", buyAt: "2026-11-04T09:33:19-05:00",
      note: 'Awaiting broker reconciliation: {"code":40410000,"message":"order not found for mxp-b0-d9"}' },
    { key: "bull|rvol|CRM|y", symbol: "CRM", contract: "CRM   261106C00225000", buyAt: null, note: "Awaiting broker reconciliation: timeout" },
    { symbol: "NOKEY", contract: "X" }, null,
  ] } });
  assert.deepEqual(rows, [
    { key: "bear|call2h|CCJ|x", symbol: "CCJ", title: "CCJ $72 put · expires 11-06", detail: "sent 11-04 09:33 ET · the broker has no record of it" },
    { key: "bull|rvol|CRM|y", symbol: "CRM", title: "CRM $225 call · expires 11-06", detail: "sent earlier · never confirmed by the broker" },
  ]);
  assert.deepEqual(deskStuckRows({ orders: {} }), []);
  assert.deepEqual(deskStuckRows(null), []);
});

test("the pill never says ON while the daily loss stop or the off switch holds buys", () => {
  const stopped = { control: { paused: false, dailyStop: { limit: 500, hit: true, hitAt: "2026-11-09T10:42:00-05:00", todayPnl: -560 } } };
  const c = deskControl(stopped, { paused: false });
  assert.deepEqual([c.state, c.tone, c.button], ["New buys HELD by the daily loss stop", "bad", "Pause new buys"]);
  const off = deskControl({ control: { paused: false, dailyStop: { limit: 500, hit: false } } }, { paused: true });
  assert.deepEqual([off.state, off.tone], ["Paper bot is switched OFF · no buys", "bad"]);
  assert.equal(deskControl({ control: { paused: true, dailyStop: {} } }, {}).tone, "warn");
});

test("Resume does not promise buying when something else still holds it", () => {
  const paused = (stop) => ({ control: { paused: true, pausedAt: null, dailyStop: stop } });
  assert.equal(deskControl(paused({ hit: false }), {}).done, "Buying is back on from the next signal.");
  assert.equal(deskControl(paused({ hit: true }), {}).done, "Pause lifted, but the daily loss stop still holds new buys until tomorrow.");
  assert.equal(deskControl(paused({ hit: false }), { paused: true }).done, "Pause lifted, but the paper bot is switched OFF, so it still places no buys.");
});

// ---- fresh evaluation + loss lessons (2026-11-09)
const { deskEvaluation, deskLessons, deskTruthDays } = await import("./botDesk.js");

test("the evaluation line says when it started, what is traded and the balance", () => {
  // plan: "chart" = the server says the running paper bots trade the plan being judged.
  const doc = { startDay: "2026-11-10", plan: "chart", strategy: "Level 1 / 2 / 3 on the 0.20-delta contract", limits: { accountSize: 10000 } };
  assert.equal(deskEvaluation(doc, { balance: 10120 }),
    "Evaluation started 11-10 · trades only Level 1 / 2 / 3 on the 0.20-delta contract · $10,000 account · balance now $10,120");
  assert.match(deskEvaluation(doc, null), /balance now \$10,000$/);          // nothing traded yet: the starting balance
  assert.match(deskEvaluation(doc, { balance: 9400.4 }), /balance now \$9,400$/);
  assert.equal(deskEvaluation({ limits: { accountSize: 10000 } }, {}), "");   // no start day sent: no line
});

test("the BOT claim is read for the days the evaluation covers, between 1 and 30", () => {
  assert.equal(deskTruthDays({ startDay: "2026-11-10", asOf: "2026-11-10T09:45:00-05:00" }), 1);
  assert.equal(deskTruthDays({ startDay: "2026-11-10", asOf: "2026-11-14T16:00:00-05:00" }), 5);
  assert.equal(deskTruthDays({ startDay: "2026-11-10", asOf: "2026-11-09T23:00:00-05:00" }), 0);    // before the start: nothing to read
  assert.equal(deskTruthDays({ startDay: "2026-11-10", asOf: "2026-12-25T12:00:00-05:00" }), 30);
  assert.equal(deskTruthDays({ startDay: "2026-01-01", asOf: "2026-11-10T09:45:00-05:00" }), 30);
  assert.equal(deskTruthDays({ asOf: "2026-11-10T09:45:00-05:00" }), 30);
  assert.equal(deskTruthDays(null), 30);
});

test("lessons: not studied yet, collecting, numbers only and a real AI lesson each read plainly", () => {
  const none = deskLessons({ lessons: null });
  assert.equal(none.ready, false);
  assert.match(none.note, /^The first study runs after the next market close/);
  const study = { updatedAt: "2026-11-10T17:30:00-05:00", note: "Suggestions only. Nothing here blocks, changes or places a trade.", lessons: [
    { side: "bull", rule: "level1", losses: 12, wins: 4, netPnl: -340, status: "lesson", lesson: "Most losers were bought in the first 30 minutes.",
      proposedChange: "Try waiting until 10:00.", howToTest: "Compare the next 30 trades." },
    { side: "bear", rule: "level3", losses: 2, wins: 1, netPnl: 15, status: "collecting", lesson: "Not enough losing trades yet (2 of 10).", proposedChange: "", howToTest: "" },
    { side: "bull", rule: "level2", losses: 11, wins: 0, netPnl: -500, status: "numbers", lesson: "9 of the 11 losing trades were in the group 'first 30 minutes' (0 winning trades were).", proposedChange: "", howToTest: "" },
  ] };
  const out = deskLessons({ lessons: study });
  assert.equal(out.note, "Studied 11-10 17:30 ET · each signal counted once across both accounts · Suggestions only. Nothing here blocks, changes or places a trade.");
  assert.deepEqual(out.rows.map((r) => [r.title, r.tag, r.counts]), [
    ["Level 1 · calls", "AI lesson", "12 losing · 4 winning · −$340"],
    ["Level 3 · puts", "collecting", "2 losing · 1 winning · +$15"],
    ["Level 2 · calls", "numbers only", "11 losing · 0 winning · −$500"]]);
  assert.deepEqual([out.rows[0].change, out.rows[0].test, out.rows[1].change], ["Try waiting until 10:00.", "Compare the next 30 trades.", ""]);
  assert.match(deskLessons({ lessons: { updatedAt: "2026-11-10T17:30:00-05:00", lessons: [] } }).note, /no closed paper trades in this evaluation yet\.$/);
});

test("the claim card says 'since the start' only while its read covers the whole evaluation", async () => {
  const { deskTruthLabel } = await import("./botDesk.js");
  assert.equal(deskTruthLabel({ startDay: "2026-11-10", asOf: "2026-11-12T10:00:00-05:00" }), "since 11-10");
  assert.equal(deskTruthLabel({ startDay: "2026-11-10", asOf: "2026-12-09T10:00:00-05:00" }), "since 11-10");      // day 30
  assert.equal(deskTruthLabel({ startDay: "2026-11-10", asOf: "2026-12-10T10:00:00-05:00" }), "last 30 days");     // day 31
  assert.equal(deskTruthLabel({ asOf: "2026-11-12T10:00:00-05:00" }), "last 30 days");
});

test("an AI lesson shows the counts it was checked against", () => {
  const out = deskLessons({ lessons: { updatedAt: "2026-11-10T17:30:00-05:00", lessons: [
    { side: "bull", rule: "level1", losses: 12, wins: 4, netPnl: -340, status: "lesson", lesson: "x",
      basis: "9 of 12 losing trades and 1 of 4 winning trades were 'first 30 minutes'" },
    { side: "bull", rule: "level2", losses: 2, wins: 0, netPnl: -40, status: "collecting", lesson: "y" }] } });
  assert.equal(out.rows[0].basis, "Checked against the counts: 9 of 12 losing trades and 1 of 4 winning trades were 'first 30 minutes'.");
  assert.equal(out.rows[1].basis, "");
});

// ---- the three strategies side by side (2026-11-10: "How I know which one is good")
const side = (closed, pnl, winRatePct) => ({ closed, wins: 0, winRatePct, pnl, roiPct: null });
const strat = (rule, closed, pnl, roiPct, extra = {}) => ({ rule, sent: closed + 2, filled: closed, fillRatePct: 80, closed, wins: 0,
  winRatePct: closed ? 50 : null, pnl, roiPct, perTrade: closed ? pnl / closed : null, smallSample: closed < 20,
  calls: side(closed, pnl, 50), puts: side(0, 0, null), sameAs: null, ...extra });

test("before any trade the three strategies are listed and the answer says when the first orders go out", async () => {
  const { deskStrategies } = await import("./botDesk.js");
  const out = deskStrategies({ strategies: { rows: [strat("level1", 0, 0, null), strat("level2", 0, 0, null), strat("level3", 0, 0, null)],
    best: null, allLosing: false, need: 20, mostClosed: { rule: "level1", closed: 0 } } });
  assert.deepEqual(out.rows.map((r) => [r.name, r.closed, r.pnl, r.win, r.roi, r.calls, r.tag]), [
    ["Level 1", 0, "–", "–", "–", "–", ""], ["Level 2", 0, "–", "–", "–", "–", ""], ["Level 3", 0, "–", "–", "–", "–", ""]]);
  // Was "the 10:00 AM ET buy window" (the 10:00 rule, removed 2026-11-13).
  assert.match(out.answer, /^No closed trades yet\. The first Level orders go out in the 9:35 AM ET buy window\.$/);
  assert.equal(out.rows[0].needs, "3+ Setup types and any 1 of RVOL / SQZ / Skittles");
  assert.equal(deskStrategies({ orders: {} }), null);
});

test("with too few trades no strategy is called best, and the answer says how many are needed", async () => {
  const { deskStrategies } = await import("./botDesk.js");
  const out = deskStrategies({ strategies: { rows: [strat("level1", 6, 120, 8.3), strat("level2", 6, 120, 8.3, { sameAs: "level1" }), strat("level3", 2, -40, -10)],
    best: null, allLosing: false, need: 20, mostClosed: { rule: "level1", closed: 6 } } });
  assert.equal(out.answer, "Too early to pick one. A strategy needs 20 closed real trades; the most so far is Level 1 with 6.");
  assert.deepEqual(out.rows.map((r) => r.tag), ["too few", "same trades as Level 1", "too few"]);
  assert.deepEqual([out.rows[0].pnl, out.rows[0].roi, out.rows[0].calls, out.rows[0].puts, out.rows[0].filled], ["+$120", "+8.3%", "+$120 · 50% of 6", "–", "6 of 8"]);
  assert.deepEqual([out.rows[2].pnl, out.rows[2].roi, out.rows[2].pnlTone], ["−$40", "−10.0%", "is-down"]);
  assert.equal(out.tone, "");
});

test("the best strategy is named with its return, and all-losing says none is good", async () => {
  const { deskStrategies } = await import("./botDesk.js");
  const good = deskStrategies({ strategies: { rows: [strat("level1", 40, 900, 7.5), strat("level2", 24, 1000, 13.9), strat("level3", 24, 1000, 13.9, { sameAs: "level2" })],
    best: "level2", allLosing: false, need: 20, mostClosed: { rule: "level1", closed: 40 } } });
  assert.equal(good.answer, "Best so far: Level 2 (+13.9% return, +$1,000 on 24 closed trades).");
  assert.deepEqual(good.rows.map((r) => [r.best, r.tag]), [[false, ""], [true, "best so far"], [false, "same trades as Level 2"]]);
  assert.equal(good.tone, "on");
  const bad = deskStrategies({ strategies: { rows: [strat("level1", 30, -400, -5), strat("level2", 25, -300, -4), strat("level3", 21, -90, -2)],
    best: null, allLosing: true, need: 20, mostClosed: { rule: "level1", closed: 30 } } });
  assert.equal(bad.answer, "None is good so far: every strategy with enough trades has lost money on real fills.");
  assert.equal(bad.tone, "bad");
});

// ---- rules bot against AI bot (2026-11-10: "see which strategy works good then we will use same for real money")
const bot = (account, name, closed, pnl, roiPct, balance) => ({ account, mode: name === "AI bot" ? "ai" : "rules", name,
  how: name === "AI bot" ? "an AI ranks the waiting Level signals and the best one gets a free slot" : "the newest Level signal gets a free slot",
  sent: closed + 3, closed, winRatePct: closed ? 45 : null, pnl, roiPct, perTrade: closed ? pnl / closed : null, balance });

test("before any trade the two bots are listed and nothing is called ahead", async () => {
  const { deskVersus, deskAccountLabel } = await import("./botDesk.js");
  const out = deskVersus({ versus: { rows: [bot("paper3", "Rules bot", 0, 0, null, 10000), bot("paper5", "AI bot", 0, 0, null, 10000)],
    need: 20, ready: false, leader: null, allLosing: false } }, (a) => (a === "paper3" ? "Paper 3" : "Paper 5"));
  assert.deepEqual(out.rows.map((r) => [r.name, r.account, r.closed, r.pnl, r.roi, r.balance, r.leader]),
    [["Rules bot", "Paper 3", 0, "–", "–", "$10,000", false], ["AI bot", "Paper 5", 0, "–", "–", "$10,000", false]]);
  // Was "the 10:00 AM ET buy window" (the 10:00 rule, removed 2026-11-13).
  assert.equal(out.answer, "No closed trades yet. Both bots start with the 9:35 AM ET buy window.");
  assert.equal(deskVersus({ versus: { rows: [bot("paper3", "Rules bot", 0, 0, null, 10000)] } }), null);   // one account: nothing to compare
  assert.equal(deskVersus({}), null);
  assert.equal(deskAccountLabel("paper5", { mode: { name: "AI bot" } }, "Paper 5"), "Paper 5 · AI bot");
  assert.equal(deskAccountLabel("paper5", {}, "Paper 5"), "Paper 5");
});

test("too early, ahead, both losing and level each read plainly", async () => {
  const { deskVersus } = await import("./botDesk.js");
  const v = (rows, extra) => deskVersus({ versus: { rows, need: 20, ready: false, leader: null, allLosing: false, ...extra } });
  assert.equal(v([bot("paper3", "Rules bot", 7, 60, 2.9, 10060), bot("paper5", "AI bot", 4, -30, -2.5, 9970)]).answer,
    "Too early to say. Each bot needs 20 closed real trades; they have 7 (Rules bot) and 4 (AI bot).");
  const ahead = v([bot("paper3", "Rules bot", 25, 120, 1.6, 10120), bot("paper5", "AI bot", 22, 640, 9.7, 10640)], { ready: true, leader: "paper5" });
  assert.equal(ahead.answer, "AI bot is ahead (+9.7% return, +$640 on 22 closed trades). That is the one to take to real money, once its account passes the checks.");
  assert.deepEqual(ahead.rows.map((r) => r.leader), [false, true]);
  assert.equal(ahead.tone, "on");
  const losing = v([bot("paper3", "Rules bot", 25, -120, -1.6, 9880), bot("paper5", "AI bot", 22, -640, -9.7, 9360)], { ready: true, allLosing: true });
  assert.equal(losing.answer, "Neither is good so far: both bots have lost money on real fills.");
  assert.equal(losing.tone, "bad");
  assert.equal(v([bot("paper3", "Rules bot", 25, 100, 1, 10100), bot("paper5", "AI bot", 25, 105, 1.1, 10105)], { enough: true, aiProven: true, ready: true, tooClose: true }).answer,
    "Too close to call: the gap between the two bots is smaller than one trade can move it.");
  // The AI bot's result is not an AI result when the AI did not choose its trades.
  const unfair = v([bot("paper3", "Rules bot", 25, 100, 1, 10100), bot("paper5", "AI bot", 25, 900, 9, 10900)],
    { enough: true, aiProven: false, aiChosenClosed: 3, aiClosed: 25 });
  assert.equal(unfair.answer, "Not a fair test yet: the AI chose only 3 of the AI bot's 25 closed trades (it needs 20, and at least half). The rest used the rules order, so both bots traded almost the same way.");
  assert.deepEqual(unfair.rows.map((r) => r.leader), [false, false]);
});

test("the AI bot says how many of its orders the AI really chose", async () => {
  const { deskAiPicks } = await import("./botDesk.js");
  assert.equal(deskAiPicks({ orders: {} }), "");                                                   // the rules bot: no line
  assert.match(deskAiPicks({ aiPicks: { picked: { sent: 0 }, fallback: { sent: 0 } } }), /^The AI has not chosen an order yet\./);
  assert.equal(deskAiPicks({ aiPicks: { picked: { sent: 9, closed: 6, pnl: 210 }, fallback: { sent: 2, closed: 0, pnl: 0 } } }),
    "Orders chosen by the AI: 9 picked by the AI (+$210 on 6 closed) · 2 on the rules order because the AI had no answer in time.");
});

// ---- the Scorecard as a journal (2026-11-10: "how I see the d/w/m pnl with which strategy is good for real money")
const { deskJournal, journalLabel, readJournalMode } = await import("./botDesk.js");
const split = (a, b, c) => [{ rule: "level1", trades: a[0], pnl: a[1] }, { rule: "level2", trades: b[0], pnl: b[1] }, { rule: "level3", trades: c[0], pnl: c[1] }];
const per = (key, trades, pnl, strategies, best, extra = {}) => ({ key, trades, wins: 1, losses: 1, winRatePct: 50, pnl, roiPct: 2, strategies, best, calls: pnl, puts: 0, ...extra });
const JOURNAL = { tradeCount: 5, periods: {
  day: [per("2026-11-12", 1, 60, split([1, 60], [1, 60], [0, 0]), "level1"), per("2026-11-10", 2, -45, split([2, -45], [1, 30], [1, 30]), "level2", { calls: 30, puts: -75 })],
  week: [per("2026-11-09", 3, 15, split([3, 15], [2, 90], [1, 30]), "level2")],
  month: [per("2026-11", 3, 15, split([3, 15], [2, 90], [1, 30]), "level2")] },
  trades: [{ symbol: "AAPL", side: "bull", rule: "level3", levels: ["level1", "level2", "level3"], day: "2026-11-12", closedAt: "2026-11-12T10:15:00-05:00",
    qty: 3, buy: 0.6, pnl: 60, roiPct: 33.3, exit: "stop at break-even", aiPicked: true },
  { symbol: "NVDA", side: "bear", rule: "level1", levels: ["level1"], day: "2026-11-10", closedAt: null, qty: 5, buy: 1.2, pnl: -75, roiPct: -12.5, exit: "", aiPicked: false }] };

test("the journal period is remembered and labelled plainly", () => {
  assert.deepEqual(["day", "week", "month", "year", null].map(readJournalMode), ["day", "week", "month", "day", "day"]);
  assert.equal(journalLabel("day", "2026-11-10"), "Tue 11-10");
  assert.equal(journalLabel("week", "2026-11-09"), "Week of 11-09");
  assert.equal(journalLabel("month", "2026-11"), "Nov 2026");
  assert.equal(journalLabel("month", "2026-13"), "2026-13");
});

test("the journal shows each period with every strategy's result and says which was best", () => {
  const day = deskJournal({ journal: JOURNAL }, "day");
  assert.equal(day.answer, "1 of 2 days in profit. Level 1 was the best strategy on 1 of them.");
  assert.deepEqual(day.rows.map((r) => [r.label, r.trades, r.pnl, r.level1, r.level2, r.level3, r.calls, r.puts, r.best]), [
    ["Thu 11-12", 1, "+$60", "+$60", "+$60", "–", "+$60", "$0", "Level 1"],
    ["Tue 11-10", 2, "−$45", "−$45", "+$30", "+$30", "+$30", "−$75", "Level 2"]]);
  assert.deepEqual([day.rows[1].tone, day.rows[1].tone1, day.rows[1].tone2, day.rows[0].tone3], ["is-down", "is-down", "is-up", ""]);
  const week = deskJournal({ journal: JOURNAL }, "week");
  assert.equal(week.answer, "1 of 1 weeks in profit. Level 2 was the best strategy on 1 of them.");
  assert.equal(week.rows[0].label, "Week of 11-09");
  assert.equal(deskJournal({ journal: JOURNAL }, "month").rows[0].label, "Nov 2026");
  assert.equal(deskJournal({ journal: JOURNAL }, "nonsense").mode, "day");
});

test("the journal lists each trade with its strategy, result and how it ended", () => {
  const j = deskJournal({ journal: JOURNAL }, "day");
  assert.deepEqual(j.trades.map((t) => [t.when, t.symbol, t.side, t.strategy, t.qty, t.buy, t.pnl, t.roi, t.exit, t.aiPicked]), [
    ["11-12 10:15 ET", "AAPL", "Call", "Level 3", 3, "$0.60", "+$60", "+33%", "stop at break-even", true],
    ["2026-11-10", "NVDA", "Put", "Level 1", 5, "$1.20", "−$75", "−13%", "–", false]]);
  assert.deepEqual([j.tradeCount, j.shown], [5, 2]);
});

test("an empty journal says so, and an account with no journal shows no card", () => {
  const empty = deskJournal({ journal: { periods: { day: [], week: [], month: [] }, trades: [], tradeCount: 0 } }, "week");
  assert.equal(empty.answer, "No closed trades yet. The journal fills in as real paper trades close.");
  assert.deepEqual([empty.rows, empty.trades], [[], []]);
  assert.equal(deskJournal({ orders: {} }), null);
  const red = deskJournal({ journal: { periods: { day: [per("2026-11-10", 1, -20, split([1, -20], [0, 0], [0, 0]), null)] }, trades: [], tradeCount: 1 } });
  assert.equal(red.answer, "0 of 1 days in profit. No strategy has made money in any of them yet.");
  assert.equal(red.rows[0].best, "–");
});

test("only a real saved Scorecard is shown at once; anything else is ignored", async () => {
  const { readSavedDesk, DESK_SAVED_KEY } = await import("./botDesk.js");
  const good = { asOf: "2026-11-10T08:39:08-05:00", accounts: { paper3: {} }, limits: {} };
  assert.deepEqual(readSavedDesk(JSON.stringify(good)), good);
  for (const bad of [null, undefined, "", "{broken", "[]", "5", JSON.stringify({ accounts: {} }), JSON.stringify({ asOf: "x", accounts: "no" })]) {
    assert.equal(readSavedDesk(bad), null);
  }
  assert.equal(DESK_SAVED_KEY, "bot.deskSaved");
});

// ---- the journal dashboard (2026-11-10: "Scoreboard look like not a tradezella")
const { deskCalendar, deskDailyBars, deskKpis, deskMonthKey, deskShortMoney, shiftMonth } = await import("./botDesk.js");
const DASH = {
  stats: { n: 129, wins: 32, losses: 95, winRatePct: 24.8, netPnl: -6817, avgWin: 300.41, avgLoss: -172.95, profitFactor: 0.59 },
  daySummary: { tradingDays: 16, greenDays: 3, redDays: 13 },
  days: [{ day: "2026-11-04", closed: 7, pnl: 420 }, { day: "2026-11-05", closed: 12, pnl: -1354 }, { day: "2026-11-06", closed: 0, pnl: 0 }],
  journal: { periods: { day: [{ key: "2026-11-12", trades: 1, pnl: 60 }, { key: "2026-11-10", trades: 2, pnl: -45 }, { key: "2026-10-30", trades: 4, pnl: 1250 }] } },
};

test("the five tiles: net P&L, trade win %, profit factor, day win % and average win against loss", () => {
  const tiles = deskKpis(DASH);
  assert.deepEqual(tiles.map((k) => [k.key, k.label, k.value, k.tone, k.sub]), [
    ["net", "Net P&L", "−$6,817", "is-down", "129 closed trades"],
    ["win", "Trade win %", "25%", "", "32 won · 95 lost"],
    ["factor", "Profit factor", "0.59", "", "dollars won per dollar lost"],
    ["dayWin", "Day win %", "19%", "", "3 green · 13 red"],
    ["avg", "Avg win / loss", "1.74", "", "+$300 · −$173"]]);
  assert.equal(tiles[0].share, null);                                     // money has no gauge
  assert.deepEqual(tiles.slice(1).map((k) => Math.round(k.share * 100)), [25, 37, 19, 63]);
  assert.equal(deskKpis({ stats: { n: 1, wins: 1, losses: 0, netPnl: 40, winRatePct: 100, avgWin: 40 } })[0].sub, "1 closed trade");
});

test("before any trade closes the tiles show dashes, never a made-up number or gauge", () => {
  for (const acct of [{}, null, { stats: { n: 0 }, daySummary: { tradingDays: 0 } }]) {
    const tiles = deskKpis(acct);
    assert.deepEqual(tiles.map((k) => k.value), ["$0", "–", "–", "–", "–"]);
    assert.deepEqual(tiles.map((k) => k.share), [null, null, null, null, null]);
  }
  // Only winners so far: no profit factor and no win/loss ratio can be worked out.
  const winners = deskKpis({ stats: { n: 2, wins: 2, losses: 0, netPnl: 90, winRatePct: 100, avgWin: 45, avgLoss: null, profitFactor: null } });
  assert.deepEqual([winners[2].value, winners[4].value, winners[4].sub], ["–", "–", "+$45 · –"]);
  assert.equal(winners[1].share, 1);
});

test("the calendar lays a month out Monday to Friday with each day's real result and a total per week", () => {
  const cal = deskCalendar(DASH, "2026-11", "2026-11-10");
  assert.deepEqual([cal.label, cal.prev, cal.next, cal.pnl, cal.tone, cal.days, cal.trades], ["November 2026", "2026-10", "2026-12", "+$15", "is-up", 2, 3]);
  const row = (w) => w.cells.map((c) => `${c.inMonth ? c.num : "_"}${c.pnl}${c.isToday ? "*" : ""}`).join(" ");
  assert.deepEqual(cal.weeks.map((w) => [w.key, w.pnl, w.tone, w.days, row(w)]), [
    ["2026-11-02", "–", "", 0, "2 3 4 5 6"],
    ["2026-11-09", "+$15", "is-up", 2, "9 10−$45* 11 12+$60 13"],
    ["2026-11-16", "–", "", 0, "16 17 18 19 20"],
    ["2026-11-23", "–", "", 0, "23 24 25 26 27"],
    ["2026-11-30", "–", "", 0, "30 _ _ _ _"]]);
  const tenth = cal.weeks[1].cells[1];
  assert.deepEqual([tenth.key, tenth.tone, tenth.trades, tenth.title], ["2026-11-10", "is-down", 2, "2026-11-10: −$45 on 2 trades"]);
});

test("another month shows only its own days; a month with nothing traded is all blanks", () => {
  const oct = deskCalendar(DASH, "2026-10", "2026-11-10");
  assert.deepEqual([oct.label, oct.pnl, oct.days, oct.weeks.length], ["October 2026", "+$1,250", 1, 5]);
  assert.deepEqual(oct.weeks[0].cells.map((c) => c.inMonth), [false, false, false, true, true]);   // Oct 1 was a Thursday
  assert.equal(oct.weeks[4].cells[4].pnl, "+$1.3k");
  assert.equal(oct.weeks[4].cells[4].isToday, false);
  const empty = deskCalendar({}, "2027-01", "");
  assert.deepEqual([empty.pnl, empty.tone, empty.days, empty.weeks.length], ["$0", "", 0, 5]);
  assert.equal(deskCalendar(DASH, "nonsense"), null);
  // A month that starts on a weekend has no empty first row (Aug 2026 starts on a Saturday).
  assert.equal(deskCalendar({}, "2026-08").weeks[0].cells[0].key, "2026-08-03");
});

test("month keys step across the year end and come from the server's ET stamp", () => {
  assert.deepEqual([shiftMonth("2026-12", 1), shiftMonth("2026-01", -1), shiftMonth("x", 1)], ["2027-01", "2025-12", ""]);
  assert.deepEqual([deskMonthKey("2026-11-09T18:30:00-05:00"), deskMonthKey(null), deskMonthKey("soon")], ["2026-11", "", ""]);
});

test("short money fits a calendar cell", () => {
  assert.deepEqual([0, 5.4, -340, 1250, -1000, 12345, null, NaN].map(deskShortMoney), ["$0", "+$5", "−$340", "+$1.3k", "−$1k", "+$12k", "–", "–"]);
});

test("daily bars: winners rise from the zero line, losers hang below it, days with nothing closed are left out", () => {
  const g = deskDailyBars(DASH.days);
  assert.deepEqual(g.bars.map((b) => [b.day, b.tone]), [["2026-11-04", "is-up"], ["2026-11-05", "is-down"]]);
  const [up, down] = g.bars;
  assert.ok(Math.abs(up.y + up.h - g.zeroY) < 0.2 && down.y === g.zeroY);
  assert.ok(down.h > up.h * 3);                                           // −$1,354 against +$420
  assert.ok(g.bars.every((b) => b.x >= 0 && b.x + b.w <= g.width && b.y >= 0 && b.y + b.h <= g.height));
  assert.deepEqual(deskDailyBars(null).bars, []);
  assert.deepEqual(deskDailyBars([{ day: "2026-11-04", closed: 1, pnl: 0 }]).bars.map((b) => b.h), [1]);
});

// ---- "All trades" (2026-11-10: "Scoreboard I don't see all trade")
const { deskTradeLog, readTradeFilter, TRADE_FILTERS } = await import("./botDesk.js");
const LOG = { total: 6, counts: { open: 1, closed: 1, missed: 1, waiting: 1, skipped: 2 }, rows: [
  { key: "k1", status: "open", symbol: "TSLA", side: "bull", rule: "level2", levels: ["level1", "level2"], at: "2026-11-10T09:34:00-05:00", qty: 3, buy: 1, pnl: 200, roiPct: 66.7, live: true, note: "part sold: +100%: sell 1 of 3", aiPicked: true },
  { key: "k2", status: "waiting", symbol: "NVDA", side: "bear", rule: "level2", levels: ["level1", "level2"], at: "2026-11-10T09:33:00-05:00", qty: 5, buy: null, pnl: null, roiPct: null, live: false, note: "order sent, waiting for a fill" },
  { key: "k3", status: "skipped", symbol: "IOT", side: "bull", rule: "level1", levels: ["level1"], at: "2026-11-10T09:32:00-05:00", qty: null, buy: null, pnl: null, note: "Spread 71% of the mid is over 15%" },
  { key: "k4", status: "missed", symbol: "MSFT", side: "bull", rule: "level1", levels: ["level1"], at: "2026-11-10T09:31:00-05:00", qty: 2, buy: null, pnl: null, note: "the order did not fill" },
  { key: "k5", status: "closed", symbol: "AAPL", side: "bull", rule: "level3", levels: ["level1", "level2", "level3"], at: "2026-11-10T09:30:00-05:00", qty: 3, buy: 1, pnl: -120, roiPct: -40, live: false, note: "stop loss -50%" }] };

test("All trades shows every signal with its status, and a count on each filter", () => {
  const all = deskTradeLog({ tradeLog: LOG }, "all");
  assert.deepEqual(all.filters.map((f) => [f.label, f.on]), [["All 6", true], ["Open 2", false], ["Closed 1", false], ["Not filled 1", false], ["Skipped 2", false]]);
  assert.deepEqual(all.rows.map((t) => [t.when, t.symbol, t.side, t.strategy, t.statusLabel, t.qty, t.buy, t.pnl, t.tone, t.roi, t.note]), [
    ["11-10 09:34 ET", "TSLA", "Call", "Level 2", "Open", 3, "$1.00", "+$200 now", "is-up", "+67%", "part sold: +100%: sell 1 of 3"],
    ["11-10 09:33 ET", "NVDA", "Put", "Level 2", "Waiting", 5, "–", "–", "", "–", "order sent, waiting for a fill"],
    ["11-10 09:32 ET", "IOT", "Call", "Level 1", "Skipped", "–", "–", "–", "", "–", "Spread 71% of the mid is over 15%"],
    ["11-10 09:31 ET", "MSFT", "Call", "Level 1", "Not filled", 2, "–", "–", "", "–", "the order did not fill"],
    ["11-10 09:30 ET", "AAPL", "Call", "Level 3", "Closed", 3, "$1.00", "−$120", "is-down", "−40%", "stop loss -50%"]]);
  assert.deepEqual([all.rows[0].aiPicked, all.rows[0].live, all.rows[4].live, all.empty, all.full], [true, true, false, "", true]);
  assert.equal(all.capped, "newest 5 of 6");                                   // the server sent fewer rows than exist
  assert.equal(deskTradeLog({ tradeLog: LOG }, "skipped").capped, "newest 1 of 2");
  assert.equal(deskTradeLog({ tradeLog: LOG }, "closed").capped, "");
});

test("each filter keeps only its trades; Open also holds orders still waiting for a fill", () => {
  const pick = (f) => deskTradeLog({ tradeLog: LOG }, f).rows.map((t) => t.symbol);
  assert.deepEqual([pick("open"), pick("closed"), pick("missed"), pick("skipped")], [["TSLA", "NVDA"], ["AAPL"], ["MSFT"], ["IOT"]]);
  assert.deepEqual(pick("nonsense"), pick("all"));
  assert.deepEqual([readTradeFilter("closed"), readTradeFilter(null), readTradeFilter("waiting")], ["closed", "all", "all"]);
  assert.deepEqual(TRADE_FILTERS.map(([key]) => key), ["all", "open", "closed", "missed", "signal", "skipped"]);
  const none = deskTradeLog({ tradeLog: { ...LOG, rows: LOG.rows.filter((t) => t.status !== "closed") } }, "closed");
  assert.deepEqual([none.rows.length, none.empty], [0, "Nothing under Closed right now."]);
});

test("no trades yet says so; an older server still shows the closed trades of the journal", () => {
  const empty = deskTradeLog({ tradeLog: { rows: [], counts: {}, total: 0 } });
  assert.deepEqual([empty.rows, empty.empty, empty.filters[0].label], [[], "No trades yet. Every Level signal shows here the moment the bot acts on it.", "All 0"]);
  const old = deskTradeLog({ journal: { trades: [{ symbol: "AAPL", side: "bull", rule: "level3", closedAt: "2026-11-12T10:15:00-05:00", qty: 3, buy: 0.6, pnl: 60, roiPct: 33.3, exit: "stop at break-even" }] } });
  assert.deepEqual(old.rows.map((t) => [t.when, t.symbol, t.statusLabel, t.pnl, t.note]), [["11-12 10:15 ET", "AAPL", "Closed", "+$60", "stop at break-even"]]);
  assert.deepEqual([old.full, old.filters[2].label, old.capped], [false, "Closed 1", ""]);
  assert.equal(deskTradeLog(null), null);
});

test("review fixes: today's ring only inside the month, a stale open price says its time, Skipped hides at zero, the first month", async () => {
  const { deskFirstMonth } = await import("./botDesk.js");
  const nov = deskCalendar({}, "2026-11", "2026-12-01");
  assert.equal(nov.weeks.flatMap((w) => w.cells).some((c) => c.isToday), false);          // Dec 1 is a blank cell of November
  const open = { ...LOG.rows[0], markAt: "2026-11-09T16:00:48-05:00" };
  const log = { rows: [open], counts: { open: 1 }, total: 1 };
  assert.equal(deskTradeLog({ tradeLog: log }, "all", "2026-11-10").rows[0].pnl, "+$200 at 11-09 16:00 ET");
  assert.equal(deskTradeLog({ tradeLog: log }, "all", "2026-11-09").rows[0].pnl, "+$200 now");
  assert.deepEqual(deskTradeLog({ tradeLog: log }).filters.map((f) => f.label), ["All 1", "Open 1", "Closed 0", "Not filled 0"]);
  assert.equal(deskTradeLog({ tradeLog: log }, "skipped").filters.length, 5);             // still shown while it is the chosen one
  assert.equal(deskFirstMonth({ journal: { periods: { day: [{ key: "2026-11-12" }, { key: "2026-10-30" }] } } }, "2026-12"), "2026-10");
  assert.deepEqual([deskFirstMonth({}, "2026-11"), deskFirstMonth(null, "")], ["2026-11", ""]);
});

test("with the daily loss stop switched off the strip says so and never reads as holding buys", () => {
  const acct = { control: { paused: false, dailyStop: { on: false, limit: 500, hit: false, hitAt: "2026-11-10T09:36:41-05:00", todayPnl: -574 } } };
  const c = deskControl(acct, { paused: false });
  assert.deepEqual([c.stop, c.stopHit, c.state, c.tone], ["No daily loss stop · today −$574", false, "New buys are ON", "on"]);
  // An older server that does not say on/off keeps the old wording.
  assert.equal(deskControl({ control: { dailyStop: { limit: 500, hit: false, todayPnl: 219 } } }, {}).stop, "Daily loss stop $500 · today +$219");
});

test("the Alpaca account: buying power, account value, today's change, and a warning when it is too low", async () => {
  const { deskBroker } = await import("./botDesk.js");
  const low = deskBroker({ broker: { at: "2026-11-10T12:29:00-05:00", cash: 517.93, optionsBuyingPower: 217.93, buyingPower: 2071.72, equity: 967.93, lastEquity: 2809.79 } }, 10000, "2026-11-10");
  assert.deepEqual([low.buyingPower, low.cash, low.equity, low.change, low.changeTone, low.asOf, low.tone, low.old],
    ["$218", "$518", "$968", "−$1,842", "is-down", "11-10 12:29 ET", "bad", false]);
  assert.equal(low.warning, "Under $300 to spend: Alpaca will refuse most buys until the paper account is topped up.");
  const small = deskBroker({ broker: { at: "2026-11-10T12:29:00-05:00", cash: 3838, optionsBuyingPower: 3838, equity: 4000, lastEquity: 4000 } }, 10000, "2026-11-10");
  assert.deepEqual([small.tone, small.change, small.warning], ["warn", "$0", "The Scorecard counts a $10,000 account; the paper account at Alpaca holds $4,000."]);
  const fine = deskBroker({ broker: { at: "2026-11-09T15:59:00-05:00", cash: 9000, optionsBuyingPower: 9000, equity: 10100, lastEquity: null, error: "x" } }, 10000, "2026-11-10");
  assert.deepEqual([fine.tone, fine.warning, fine.change, fine.old, fine.error], ["on", "", "", true, "Last read failed; showing the read before it."]);
  assert.equal(deskBroker({ broker: { cash: 1 } }), null);
  assert.equal(deskBroker({}), null);
});

test("All trades: Bot signals this bot did not order show as Not ordered, with their own button", () => {
  const sig = { key: "s1", status: "signal", symbol: "AVGO", side: "bull", rule: "level3", levels: ["level3"], at: "2026-11-10T09:44:00-05:00",
    qty: null, buy: null, pnl: null, roiPct: 45.3, bestPct: 133.2, live: false, note: "Not ordered: the daily loss stop was holding buys · 0.20-delta option $3.58, now +45%, best +133%" };
  const log = { total: 7, counts: { ...LOG.counts, signal: 1 }, rows: [sig, ...LOG.rows] };
  const all = deskTradeLog({ tradeLog: log }, "all");
  assert.deepEqual(all.filters.map((f) => f.label), ["All 7", "Open 2", "Closed 1", "Not filled 1", "Not ordered 1", "Skipped 2"]);
  const row = all.rows[0];
  assert.deepEqual([row.statusLabel, row.qty, row.buy, row.pnl, row.roi, row.status], ["Not ordered", "–", "–", "–", "+45%", "signal"]);
  assert.deepEqual(deskTradeLog({ tradeLog: log }, "signal").rows.map((t) => t.symbol), ["AVGO"]);
  // A server with no Bot signals: no Not ordered button.
  assert.equal(deskTradeLog({ tradeLog: LOG }).filters.some((f) => f.key === "signal"), false);
});

test("fill likelihood like Robinhood: a label on each order and how each label really did", async () => {
  const { deskFillChance } = await import("./botDesk.js");
  const rows = deskTradeLog({ tradeLog: { ...LOG, rows: [{ ...LOG.rows[0], fillChance: "high" }, { ...LOG.rows[3], fillChance: "low" }, LOG.rows[4]] } }).rows;
  assert.deepEqual(rows.map((t) => [t.fill, t.fillKey]), [["High fill", "high"], ["Low fill", "low"], ["", ""]]);
  const m = { high: { orders: 25, filledAtMid: 18, filled: 22, midPct: 72, enough: true }, medium: { orders: 4, filledAtMid: 1, midPct: 25, enough: false },
    low: { orders: 0, filledAtMid: 0, midPct: null, enough: false } };
  assert.deepEqual(deskFillChance({ fillChance: m }).parts.map((p) => p.text),
    ["High fill: 18 of 25 filled at the mid (72%)", "Medium fill: 1 of 4 filled at the mid (25%) · too few yet", "Low fill: no orders yet"]);
  assert.equal(deskFillChance({ fillChance: { high: { orders: 0 }, medium: {}, low: {} } }), null);
  assert.equal(deskFillChance({}), null);
});
