// The chart's Entry / Target / Stop plan on the Bot page (2026-11-12): how each trade's
// Entry (ask), Stop, T1, T2, T3 read on the Scorecard, the Paper pages and the Research table.
import assert from "node:assert/strict";
import { test } from "node:test";

import { DESK_OLD_PLAN_RULES, DESK_PLAN_RULES, deskEvaluation, deskPlanActive, deskPlanText, deskPlanWaiting, botTradePlan, botTradePlanLine, deskEarlier, deskJournal, deskPlan, deskStrategies, deskTradeLog, deskVersus, paperRowPlan } from "./botDesk.js";

const PLAN = { entryAsk: 1.25, entryStock: 100, stop: 97.26, stopNow: 97.26, stopKind: "stop", t1: 102, t2: 104, t3: 108, hits: [], ready: true };

test("a trade on the chart plan shows its entry at the ask, the stock stop and the three targets", () => {
  assert.deepEqual(deskPlan(PLAN), { has: true, entry: "$1.25", stock: "100.00", stop: "97.26", t1: "102.00", t2: "104.00", t3: "108.00", hit: [], result: "" });
});

test("a reached target carries a check mark and its time, and the stop shows where it moved to", () => {
  const afterT1 = deskPlan({ ...PLAN, stopNow: 100, stopKind: "break-even", hits: [{ target: "T1", at: "2026-11-13T10:20:00-05:00" }] });
  assert.deepEqual([afterT1.t1, afterT1.t2, afterT1.stop, afterT1.hit], ["102.00 ✓ 10:20", "104.00", "97.26 → 100.00 (break-even)", ["T1"]]);
  const afterT2 = deskPlan({ ...PLAN, stopNow: 102, stopKind: "T1",
    hits: [{ target: "T1", at: "2026-11-13T10:20:00-05:00" }, { target: "T2", at: "2026-11-13T10:41:00-05:00" }] });
  assert.deepEqual([afterT2.t1, afterT2.t2, afterT2.t3, afterT2.stop], ["102.00 ✓ 10:20", "104.00 ✓ 10:41", "108.00", "97.26 → 102.00 (T1)"]);
  assert.equal(deskPlan({ ...PLAN, hits: [{ target: "T3" }] }).t3, "108.00 ✓");          // a hit without a time still shows
});

test("a trade from before the chart plan, or with missing numbers, shows dashes and never a zero", () => {
  const blank = { has: false, entry: "–", stock: "–", stop: "–", t1: "–", t2: "–", t3: "–", hit: [], result: "" };
  assert.deepEqual(deskPlan(null), blank);
  assert.deepEqual(deskPlan(undefined), blank);
  const noTargets = deskPlan({ ...PLAN, t1: null, t2: null, t3: null });                    // the chain had no expected move
  assert.deepEqual([noTargets.t1, noTargets.t2, noTargets.t3, noTargets.stop], ["–", "–", "–", "97.26"]);
  const waiting = deskPlan({ entryAsk: 1.25, entryStock: 100, stop: null, stopNow: null, t1: null, t2: null, t3: null, hits: [], ready: false });
  assert.deepEqual([waiting.entry, waiting.stop, waiting.t1], ["$1.25", "waiting for the chain", "–"]);
  assert.equal(deskPlan({ ...PLAN, stop: null, ready: true }).stop, "–");
});

test("a signal no order was sent for says what three contracts would have done on the plan", () => {
  assert.equal(deskPlan({ ...PLAN, status: "closed", pct: 56, reason: "Stop at T1: stock traded through 102.00" }).result,
    "on the plan +56% (Stop at T1: stock traded through 102.00)");
  assert.equal(deskPlan({ ...PLAN, status: "closed", pct: -48.4, reason: "Stop: stock traded through 97.26" }).result,
    "on the plan −48% (Stop: stock traded through 97.26)");
  assert.equal(deskPlan({ ...PLAN, status: "open" }).result, "on the plan: still running");
  assert.equal(deskPlan({ ...PLAN, status: "closed", pct: null }).result, "");
});

test("a paper order as the paper bot saves it maps onto the same cells", () => {
  const row = { exitPlan: "chart", entryAsk: 1.25, entryStock: 100, stopStock0: 97.26, stopStock: 100, stopKind: "break-even",
    plan: { t1: 102, t2: 104, t3: 108 }, hits: [{ target: "T1", at: "2026-11-13T10:20:00-05:00", stock: 102.1 }] };
  assert.deepEqual(deskPlan(paperRowPlan(row)), { has: true, entry: "$1.25", stock: "100.00", stop: "97.26 → 100.00 (break-even)",
    t1: "102.00 ✓ 10:20", t2: "104.00", t3: "108.00", hit: ["T1"], result: "" });
  assert.equal(paperRowPlan({ stop: 0.5, buyFill: 1 }), null);                              // an order from before the chart plan
  assert.equal(paperRowPlan(null), null);
  assert.equal(deskPlan(paperRowPlan({ exitPlan: "chart", entryAsk: 2 })).stop, "waiting for the chain");
});

test("a Bot tracked trade reads as one line: the numbers, then what happened", () => {
  const t = { plan: { entry: 1.25, entrySpot: 100, stopStock: 97.26, t1: 102, t2: 104, t3: 108 },
    planState: { status: "closed", stopStock: 102, stopKind: "T1", pct: 56, reason: "Stop at T1: stock traded through 102.00",
      hits: [{ target: "T1", at: "2026-11-13T10:10:00-05:00" }, { target: "T2", at: "2026-11-13T10:15:00-05:00" }] } };
  assert.equal(botTradePlan(t).entryAsk, 1.25);
  assert.equal(botTradePlanLine(t),
    "Entry $1.25 · Stop 97.26 → 102.00 (T1) · T1 102.00 ✓ 10:10 · T2 104.00 ✓ 10:15 · T3 108.00 · on the plan +56% (Stop at T1: stock traded through 102.00)");
  assert.equal(botTradePlanLine({ planNote: "Not ordered: outside the buy windows (9:35-11:30, 1:00-3:30)" }), "Not ordered: outside the buy windows (9:35-11:30, 1:00-3:30)");
  assert.equal(botTradePlanLine({ rule: "go" }), "–");
  assert.equal(botTradePlan({}), null);
});

test("All trades and the journal carry the plan cells for every row", () => {
  const log = { total: 2, counts: { open: 1, closed: 0, missed: 0, waiting: 0, skipped: 0, signal: 1 }, rows: [
    { key: "k1", status: "open", symbol: "AAPL", side: "bull", rule: "level2", levels: ["level1", "level2"], at: "2026-11-13T10:05:00-05:00",
      qty: 3, buy: 1.25, pnl: 15, roiPct: 4, live: true, note: "holding", plan: PLAN },
    { key: "k2", status: "signal", symbol: "TSLA", side: "bull", rule: "level3", levels: ["level3"], at: "2026-11-13T09:41:00-05:00",
      note: "Not ordered: outside the buy windows (9:35-11:30, 1:00-3:30) · 0.20-delta option $2.00", plan: null }] };
  const out = deskTradeLog({ tradeLog: log }, "all");
  assert.deepEqual(out.rows.map((r) => [r.symbol, r.plan.entry, r.plan.stop, r.plan.t1, r.plan.t2, r.plan.t3]),
    [["AAPL", "$1.25", "97.26", "102.00", "104.00", "108.00"], ["TSLA", "–", "–", "–", "–", "–"]]);
  assert.equal(out.rows[1].note, "Not ordered: outside the buy windows (9:35-11:30, 1:00-3:30) · 0.20-delta option $2.00");
  const journal = deskJournal({ journal: { periods: { day: [] }, tradeCount: 1, trades: [
    { symbol: "AAPL", side: "bull", rule: "level2", levels: ["level2"], closedAt: "2026-11-13T10:40:00-05:00", qty: 3, buy: 1.25, pnl: -195,
      roiPct: -52, exit: "Stop: stock traded through 97.26", plan: PLAN }] } }, "day");
  assert.deepEqual([journal.trades[0].exit, journal.trades[0].plan.stop, journal.trades[0].plan.t3], ["Stop: stock traded through 97.26", "97.26", "108.00"]);
});

test("the earlier period is its own card with its own totals", () => {
  const acct = { earlier: [{ startDay: "2026-11-10", endDay: "2026-11-12", strategy: "Level 1 / 2 / 3, bought at the mid from 9:30",
    why: "Ended 11-12.", orders: 40, trades: 12, wins: 4, losses: 8, winRatePct: 33.3, netPnl: -4621, worstTrade: -520, bestTrade: 310, stillOpen: 1,
    rows: [{ symbol: "NVDA", side: "bear", closedAt: "2026-11-12T10:02:00-05:00", qty: 3, buy: 1.4, pnl: -330, roiPct: -78.6, exit: "stop loss -50%" }] }] };
  const [card] = deskEarlier(acct);
  assert.equal(card.title, "Earlier period: 11-10 to 11-12");
  assert.equal(card.summary, "12 closed trades · 4 wins (33%) · net −$4,621 · worst trade −$520 · best trade +$310 · 1 still open on the old exit plan");
  assert.equal(card.tone, "is-down");
  assert.deepEqual(card.trades.map((t) => [t.when, t.symbol, t.side, t.qty, t.buy, t.pnl, t.roi, t.exit]),
    [["11-12 10:02 ET", "NVDA", "Put", 3, "$1.40", "−$330", "−79%", "stop loss -50%"]]);
  assert.deepEqual(deskEarlier({ earlier: [] }), []);
  assert.deepEqual(deskEarlier({}), []);
  assert.deepEqual(deskEarlier(null), []);
  const none = deskEarlier({ earlier: [{ startDay: "2026-11-10", endDay: "2026-11-12", trades: 0, rows: [] }] })[0];
  assert.deepEqual([none.summary, none.tone, none.trades], ["0 closed trades", "", []]);
  assert.equal(deskEarlier({ earlier: [{ startDay: "2026-11-10", endDay: "2026-11-12", trades: 1, wins: 1, winRatePct: 100, netPnl: 50, rows: [] }] })[0].summary,
    "1 closed trade · 1 win (100%) · net +$50");
});

test("older-plan orders sent after the switch are named on the earlier period's card", () => {
  const period = { startDay: "2026-11-10", endDay: "2026-11-12", trades: 2, wins: 0, winRatePct: 0, netPnl: -831, worstTrade: -591, bestTrade: -240, rows: [] };
  assert.equal(deskEarlier({ earlier: [{ ...period, afterSwitch: 1 }] })[0].summary,
    "2 closed trades · 0 wins (0%) · net −$831 · worst trade −$591 · best trade −$240 · includes 1 older-plan order sent after 11-12, before the bot was restarted");
  assert.ok(deskEarlier({ earlier: [{ ...period, afterSwitch: 3 }] })[0].summary.endsWith("includes 3 older-plan orders sent after 11-12, before the bot was restarted"));
  for (const none of [0, null, undefined]) {
    assert.ok(!deskEarlier({ earlier: [{ ...period, afterSwitch: none }] })[0].summary.includes("older-plan"));
  }
});

test("the rules text says the plan in his words", () => {
  const text = DESK_PLAN_RULES.join(" ");
  // Was: "No new buys from 9:30 to 10:00 AM ET", "10:00 to 11:00 AM", "Not ordered: before ten"
  // (the 10:00 rule, removed 2026-11-13: "if you see the setup then trade it").
  assert.ok(!text.includes("10:00"), "the 10:00 rule is gone");
  for (const words of ["from 9:35 to 11:30 AM", "1:00 to 3:30 PM", "as soon as the setup is there", "from 9:45 it has to stay there for 5 minutes",
    "A signal with no chart plan",
    "limit order at the option's ask", "down 50%", "stock trades through it, everything is sold", "T1 is half the expected move",
    "T2 is the full expected move", "biggest open-interest strike", "sell 1 at T1 and move the stop to break-even",
    "sell 1 at T2 and move the stop to T1", "sell the last at T3", "With 2 contracts: T1 and T2. With 1: T2.", "21 EMA"]) {
    assert.ok(text.includes(words), words);
  }
  assert.ok(!text.includes("+100%"));
});

test("the chart plan's text shows only when the server says the paper bots run it", () => {
  // The page is live in the browser before the servers restart: no "plan" field (an old server)
  // or plan "old" = the older plan is still trading, and the page says exactly that.
  for (const doc of [{ startDay: "2026-11-13" }, { startDay: "2026-11-13", plan: "old" }]) {
    assert.equal(deskPlanActive(doc), false);
    assert.equal(deskPlanWaiting(doc), true);
    const said = deskPlanText(doc);
    assert.equal(said.active, false);
    assert.equal(said.title, "How the bot trades right now (the older plan is still running):");
    assert.equal(said.rules, DESK_OLD_PLAN_RULES);
    assert.match(said.note, /built but not running yet\. It starts when the servers are restarted\..*never counted in the chart plan's evaluation/);
    const text = said.rules.join(" ");
    assert.ok(text.includes("+100%") && text.includes("limit order at the middle") && !text.includes("T1"));
  }
  const live = { startDay: "2026-11-13", plan: "chart", planSince: "2026-11-13T08:05:00-05:00" };
  assert.equal(deskPlanActive(live), true);
  assert.equal(deskPlanWaiting(live), false);
  assert.deepEqual(deskPlanText(live), { active: true, rules: DESK_PLAN_RULES, note: "",
    title: "How the bot trades since 11-13 (the chart's Entry / Target / Stop plan):" });
  assert.equal(deskPlanText({ startDay: "2026-11-13", plan: "chart" }).title, "How the bot trades since 11-13 (the chart's Entry / Target / Stop plan):");
  assert.equal(deskPlanWaiting(null), false);                 // no Scorecard on screen: nothing to say
});

test("the evaluation line does not claim the chart plan before the server runs it", () => {
  // plan "old" is what the restarted server sends while a paper bot still lacks the chart-plan marker (2026-11-13:
  // without the field the page now says its numbers are the older plan's; see the test at the end of this file).
  const doc = { plan: "old", startDay: "2026-11-13", strategy: "Level 1 / 2 / 3 bought at the ask from 9:30 on the chart's plan",
    oldStrategy: "Level 1 / 2 / 3 bought at the mid from 9:30", limits: { accountSize: 10000 } };
  assert.equal(deskEvaluation(doc, { balance: 10000 }),
    "The chart-plan evaluation has not started: the paper bots still trade the older plan (Level 1 / 2 / 3 bought at the mid from 9:30) "
    + "until the servers are restarted. Orders sent until then are counted in the earlier period below · $10,000 account · balance now $10,000");
  assert.equal(deskEvaluation({ ...doc, plan: "chart" }, { balance: 10120 }),
    "Evaluation started 11-13 · trades only Level 1 / 2 / 3 bought at the ask from 9:30 on the chart's plan · $10,000 account · balance now $10,120");
});

// Was "before any trade the answers name the 10:00 buy window" (the 10:00 rule, removed 2026-11-13).
test("before any trade the answers name the 9:35 buy window", () => {
  const strat = (rule) => ({ rule, sent: 0, filled: 0, closed: 0, wins: 0, winRatePct: null, pnl: 0, roiPct: null, perTrade: null, smallSample: true,
    calls: { closed: 0 }, puts: { closed: 0 }, sameAs: null });
  const strategies = deskStrategies({ strategies: { rows: [strat("level1"), strat("level2"), strat("level3")], best: null, allLosing: false, need: 20,
    mostClosed: { rule: "level1", closed: 0 } } });
  assert.equal(strategies.answer, "No closed trades yet. The first Level orders go out in the 9:35 AM ET buy window.");
  const versus = deskVersus({ versus: { rows: [{ account: "paper3", name: "Rules bot", mode: "rules", sent: 0, filled: 0, closed: 0 },
    { account: "paper5", name: "AI bot", mode: "ai", sent: 0, filled: 0, closed: 0 }], ready: false, leader: null, need: 20 } });
  assert.equal(versus.answer, "No closed trades yet. Both bots start with the 9:35 AM ET buy window.");
});

test("before the restart (no plan field) the page says its numbers are the older plan's, not 'the earlier period below'", async () => {
  const { deskEvaluation } = await import("./botDesk.js");
  const doc = { startDay: "2026-11-10", limits: { accountSize: 10000 }, strategy: "Level 1 / 2 / 3" };
  const said = deskEvaluation(doc, { balance: 5379 });
  if (/has not started/.test(said)) {
    assert.match(said, /The numbers on this page are the older plan's/);
    assert.doesNotMatch(said, /earlier period below/);
  }
  assert.match(deskEvaluation({ ...doc, plan: "old", oldStrategy: "x" }, { balance: 5379 }), /counted in the earlier period below/);
});
