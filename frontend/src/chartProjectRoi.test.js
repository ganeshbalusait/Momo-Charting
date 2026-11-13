import test from "node:test";
import assert from "node:assert/strict";
import {
  buildProjectRoi,
  projectRoiChartLines,
  projectRoiOtherPlan,
  projectRoiQuoteSpot,
  projectRoiChainEntry,
  projectRoiChainPrice,
  projectRoiPlanLines,
  projectRoiSwapSide,
  projectRoiExpiries,
  projectRoiFirstLiveExpiry,
  projectRoiOptionValue,
  projectRoiStopStock,
  readProjectRoiPlans,
  writeProjectRoiPlans,
  formatPercent,
  formatDollars,
} from "./chartProjectRoi.js";

const SPOT = 200.4;
const MOVE = 5;
const DAY1 = "2026-11-12";
const DAY2 = "2026-11-13";
const NOW = new Date("2026-11-11T15:00:00Z"); // 10:00 ET, Wednesday
const priceOf = (side, strike) => {
  const intrinsic = side === "CALL" ? Math.max(SPOT - strike, 0) : Math.max(strike - SPOT, 0);
  return Math.round((intrinsic + Math.max(0.1, 2.2 - Math.abs(SPOT - strike) * 0.2)) * 100) / 100;
};
const row = (side, strike, expiry = DAY1, dte = 1) => {
  const mark = priceOf(side, strike);
  return {
    side, strike, expiry, days_to_expiration: dte, mark, bid: mark - 0.05, ask: Math.round((mark + 0.05) * 100) / 100,
    delta: side === "CALL" ? 0.45 : -0.45, gamma: 0.04, open_interest: 500, expected_move: MOVE,
  };
};
const STRIKES = [190, 195, 200, 205, 210, 215];
const ROWS = [
  ...STRIKES.flatMap((strike) => [row("CALL", strike), row("PUT", strike)]),
  ...STRIKES.map((strike) => row("CALL", strike, DAY2, 2)),
];
const WALLS = [{ price: 205, side: "C", title: "9K" }, { price: 195, side: "P", title: "8K" }, { price: 215, side: "CALL", title: "3K" }];

test("expiries are listed nearest first", () => {
  assert.deepEqual(projectRoiExpiries(ROWS, NOW).map((item) => item.expiry), [DAY1, DAY2]);
});

test("auto plan without a 0.20-delta contract: full-move target, strike from the wall nearest the exit, entry at the chain price", () => {
  const result = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, walls: WALLS, defaultExpiry: DAY1, expiryExpectedMoves: { [DAY1]: MOVE } });
  assert.equal(result.ready, true);
  assert.equal(result.expiry, DAY1);
  assert.equal(result.target, 205.4);
  assert.equal(result.strike, 205);
  assert.equal(result.strikeReason, "strike from 205C 9K wall — the OI / level nearest your exit");
  // Entry = the chain price (bid/ask middle), the same number the Options chain shows.
  const mid205 = Math.round(((row("CALL", 205).bid + row("CALL", 205).ask) / 2) * 100) / 100;
  assert.equal(result.entry, mid205);
  assert.equal(result.entryTyped, false);
  assert.deepEqual(result.ladder.map((item) => item.strike), [200, 205, 210, 215]);
  assert.ok(result.roiPercent > 0, "a call that reaches the full move gains");
  assert.ok(result.stopStock < SPOT, "a call stop sits below spot");
  assert.equal(result.stopPremium, Math.round(mid205 * 50) / 100);
});

test("puts mirror: target below spot, wall below spot, stop above spot", () => {
  const result = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, walls: WALLS, defaultExpiry: DAY1, plan: { side: "PUT" } });
  assert.equal(result.target, 195.4);
  assert.equal(result.strike, 195);
  assert.ok(result.stopStock > SPOT);
  assert.equal(result.sideLetter, "P");
});

test("without walls the first OTM strike is picked", () => {
  const result = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, defaultExpiry: DAY1 });
  assert.equal(result.strike, 205);
  assert.equal(result.strikeReason, "first OTM strike");
});

test("typed fill, target, strike and contracts are kept", () => {
  const result = buildProjectRoi({
    now: NOW, rows: ROWS, spot: SPOT, walls: WALLS, defaultExpiry: DAY1,
    plan: { strike: 210, entry: 0.5, target: 212, contracts: 3, entrySpot: 199.9 },
  });
  assert.equal(result.strike, 210);
  assert.equal(result.strikeReason, "your pick");
  assert.equal(result.entry, 0.5);
  assert.equal(result.entryTyped, true);
  assert.equal(result.target, 212);
  assert.equal(result.contracts, 3);
  assert.equal(result.entrySpot, 199.9);
  // value at 212 is at least intrinsic 2.00 -> ROI >= 300%, profit 3 contracts
  assert.ok(result.projectedValue >= 2);
  assert.equal(result.profit, Math.round((result.projectedValue - 0.5) * 300 * 100) / 100);
  assert.equal(result.stopLoss, -75);
});

test("an expiry that is not in the chain falls back to the nearest one", () => {
  const result = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, plan: { expiry: "2030-01-01" } });
  assert.equal(result.expiry, DAY1);
  const later = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, plan: { expiry: DAY2 } });
  assert.equal(later.expiry, DAY2);
});

test("empty chain is not ready and draws no lines", () => {
  const result = buildProjectRoi({ rows: [], spot: SPOT });
  assert.equal(result.ready, false);
  assert.deepEqual(projectRoiChartLines(result), []);
});

test("option value: delta-gamma path, intrinsic floor, one-cent floor", () => {
  // +5 move, delta .5, gamma .04: end delta .7, gain .6*5 = 3
  assert.equal(projectRoiOptionValue({ side: "CALL", spot: 100, price: 1, delta: 0.5, gamma: 0.04, strike: 102, target: 105 }), 4);
  // intrinsic beats a tiny model value
  assert.equal(projectRoiOptionValue({ side: "CALL", spot: 100, price: 0.1, delta: 0.01, gamma: 0, strike: 95, target: 110 }), 15);
  // a big adverse move floors at a cent
  assert.equal(projectRoiOptionValue({ side: "PUT", spot: 100, price: 0.5, delta: -0.4, gamma: 0.01, strike: 98, target: 110 }), 0.01);
});

test("stop stock: where the contract is worth half; already below gives spot", () => {
  const stop = projectRoiStopStock({ side: "CALL", spot: 100, price: 1, delta: 0.5, gamma: 0, strike: 101, stopPremium: 0.5 });
  assert.equal(stop, 99);
  assert.equal(projectRoiStopStock({ side: "CALL", spot: 100, price: 0.4, delta: 0.5, gamma: 0, strike: 101, stopPremium: 0.5 }), 100);
  assert.equal(projectRoiStopStock({ side: "PUT", spot: 100, price: 1, delta: -0.5, gamma: 0, strike: 99, stopPremium: 0.5 }), 101);
});

test("chart lines carry the entry, target and stop labels", () => {
  const result = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, walls: WALLS, defaultExpiry: DAY1, plan: { entrySpot: 200.1 } });
  const lines = projectRoiChartLines(result);
  // Three targets: half the move (202.90), the full move (205.40), and the
  // biggest-OI strike past the move within two moves (210).
  assert.deepEqual(lines.map((line) => line.key), ["entry", "t1", "target", "t3", "stop"]);
  assert.deepEqual(result.targets.map((level) => [level.label, level.stock]), [["T1", 202.9], ["T2", 205.4], ["T3", 210]]);
  // No fill typed: the entry is the live chain price, so the Entry line is the
  // live stock price (200.40), not the 200.10 the plan was made at.
  assert.equal(lines[0].price, SPOT);
  assert.match(lines[0].title, /^Entry 200\.40 {2}205C \$\d+\.\d\d$/);
  // Target and stop boxes carry the contract price to type into the broker:
  // the projected price at each target, and half the entry at the stop.
  assert.match(lines[1].title, /^T1 202\.90 {2}[+−]\d+% {2}205C \$\d+\.\d\d$/);
  assert.match(lines[2].title, /^T2 205\.40 {2}[+−]\d+% {2}205C \$\d+\.\d\d$/);
  assert.ok(lines[2].title.endsWith(`$${result.projectedValue.toFixed(2)}`));
  assert.match(lines[3].title, /^T3 210\.00 {2}[+−]\d+% {2}205C \$\d+\.\d\d$/);
  const values = result.targets.map((level) => level.value);
  assert.ok(values[0] < values[1] && values[1] < values[2], "a farther call target is worth more");
  assert.match(lines[4].title, /^Stop \d+\.\d\d {2}−50% {2}205C \$\d+\.\d\d$/);
  assert.ok(lines[4].title.endsWith(`$${result.stopPremium.toFixed(2)}`));
  assert.equal(result.stopPremium, Math.round(result.entry * 50) / 100);
});

test("puts get three targets below spot, nearest first; a dragged target equal to T1 shows once", () => {
  const put = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, walls: WALLS, defaultExpiry: DAY1, plan: { side: "PUT" } });
  assert.deepEqual(put.targets.map((level) => level.stock), [197.9, 195.4, 195]); // 190 is past two moves (190.4); 195 is the OI level past the move
  assert.deepEqual(put.targets.map((level) => level.label), ["T1", "T2", "T3"]);
  const dragged = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, walls: WALLS, defaultExpiry: DAY1, plan: { target: 202.9 } });
  // Two distinct levels, three wanted: the first runner fills the third slot.
  assert.deepEqual(dragged.targets.map((level) => level.stock), [202.9, 210, 212.5]);
  // A target dragged past the OI level becomes T3, the OI level T2.
  const far = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, walls: WALLS, defaultExpiry: DAY1, plan: { target: 212 } });
  assert.deepEqual(far.targets.map((level) => [level.label, level.key, level.stock]), [["T1", "t1", 202.9], ["T2", "t3", 210], ["T3", "target", 212]]);
});

test("formatters and saved plans", () => {
  assert.equal(formatPercent(497.4), "+497%");
  assert.equal(formatPercent(-50), "−50%");
  assert.equal(formatDollars(1104.2), "+$1,104");
  assert.equal(formatDollars(-10), "−$10");
  const memory = new Map();
  const storage = { getItem: (key) => memory.get(key) ?? null, setItem: (key, value) => memory.set(key, value) };
  writeProjectRoiPlans({ QQQ: { strike: 695 } }, storage);
  assert.deepEqual(readProjectRoiPlans(storage), { QQQ: { strike: 695 } });
  assert.deepEqual(readProjectRoiPlans({ getItem: () => "not json" }), {});
  assert.deepEqual(readProjectRoiPlans({ getItem: () => { throw new Error("blocked"); } }), {});
  writeProjectRoiPlans({}, { setItem: () => { throw new Error("full"); } });
});

test("an expired 0DTE is never offered after the close", () => {
  assert.equal(projectRoiFirstLiveExpiry(new Date("2026-11-11T20:00:00Z")), "2026-11-11"); // 15:00 ET
  assert.equal(projectRoiFirstLiveExpiry(new Date("2026-11-11T21:20:00Z")), "2026-11-12"); // 16:20 ET
  assert.equal(projectRoiFirstLiveExpiry(new Date("2026-11-30T23:30:00Z")), "2026-12-01"); // month end, 18:30 ET
  const today = [row("CALL", 205, "2026-11-11", 0), ...ROWS];
  assert.deepEqual(projectRoiExpiries(today, new Date("2026-11-11T19:00:00Z")).map((item) => item.expiry), ["2026-11-11", DAY1, DAY2]);
  const after = buildProjectRoi({ now: new Date("2026-11-11T23:00:00Z"), rows: today, spot: SPOT, defaultExpiry: "2026-11-11" });
  assert.equal(after.expiry, DAY1);
  assert.deepEqual(after.expiries.map((item) => item.expiry), [DAY1, DAY2]);
});

test("both sides: switching Call <-> Put keeps each side's strike, fill and target", () => {
  const plan = { side: "CALL", both: true, strike: 210, entry: 0.9, target: 207, contracts: 2, entrySpot: 200.4 };
  const put = projectRoiSwapSide(plan, "PUT");
  assert.equal(put.side, "PUT");
  assert.deepEqual([put.strike, put.entry, put.target], [null, null, null], "the put side starts on live values");
  assert.deepEqual(put.other, { strike: 210, entry: 0.9, target: 207 });
  assert.equal(put.contracts, 2);
  assert.equal(put.both, true);
  const back = projectRoiSwapSide({ ...put, strike: 195, entry: 1.5 }, "CALL");
  assert.deepEqual([back.side, back.strike, back.entry, back.target], ["CALL", 210, 0.9, 207]);
  assert.deepEqual(back.other, { strike: 195, entry: 1.5, target: null });
  assert.equal(projectRoiSwapSide(plan, "CALL"), plan, "same side is a no-op");
  const other = projectRoiOtherPlan(back);
  assert.deepEqual([other.side, other.strike, other.entry], ["PUT", 195, 1.5]);
  assert.equal(other.other, undefined);
});

test("both sides on the chart: one Entry naming both contracts, call targets up, put targets down", () => {
  const plan = { side: "CALL", both: true, entrySpot: 200.4 };
  const call = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, walls: WALLS, defaultExpiry: DAY1, plan });
  const put = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, walls: WALLS, defaultExpiry: DAY1, plan: projectRoiOtherPlan(plan) });
  const lines = projectRoiPlanLines(call, put);
  assert.deepEqual(lines.map((line) => line.key), ["entry", "c-t1", "c-target", "c-t3", "c-stop", "p-t1", "p-target", "p-t3", "p-stop"]);
  assert.match(lines[0].title, /^Entry 200\.40 {2}205C \$\d+\.\d\d · 195P \$\d+\.\d\d$/);
  const byKey = Object.fromEntries(lines.map((line) => [line.key, line]));
  ["c-t1", "c-target", "c-t3"].forEach((key) => assert.ok(byKey[key].price > SPOT && / 205C \$/.test(byKey[key].title), key));
  ["p-t1", "p-target", "p-t3"].forEach((key) => assert.ok(byKey[key].price < SPOT && / 195P \$/.test(byKey[key].title), key));
  assert.ok(byKey["c-stop"].price < SPOT && byKey["p-stop"].price > SPOT, "each stop is on its losing side");
  // The put side's order does not change which is primary.
  assert.deepEqual(projectRoiPlanLines(put, call).map((line) => line.key), lines.map((line) => line.key));
  // One side only, or no second side: the single-side keys.
  assert.deepEqual(projectRoiPlanLines(call, null).map((line) => line.key), ["entry", "t1", "target", "t3", "stop"]);
});

test("quotes from the close: the stock the quotes imply drives the value, and the card warns", () => {
  // The fixture prices every contract around spot 200.40: parity agrees.
  assert.ok(Math.abs(projectRoiQuoteSpot({ rows: ROWS, expiry: DAY1, spot: SPOT }) - SPOT) < 0.5);
  const calm = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, defaultExpiry: DAY1 });
  assert.equal(calm.quoteMismatch, false);
  // Overnight the live price drops 6% but the quotes still belong to 200.40.
  const dropped = SPOT * 0.94;
  const stale = buildProjectRoi({ now: NOW, rows: ROWS, spot: dropped, defaultExpiry: DAY1 });
  assert.equal(stale.quoteMismatch, true);
  assert.ok(Math.abs(stale.quoteSpot - SPOT) < 0.5);
  // Each target is valued from the stock price the quote belongs to (not the
  // live price): the quote is a snapshot of that moment. The entry itself is
  // stale, which is why the card warns instead of pretending the % is real.
  const put = buildProjectRoi({ now: NOW, rows: ROWS, spot: dropped, defaultExpiry: DAY1, plan: { side: "PUT" } });
  const row = ROWS.find((item) => item.side === "PUT" && item.strike === put.strike && item.expiry === put.expiry);
  put.targets.forEach((level) => {
    assert.equal(level.value, projectRoiOptionValue({
      side: "PUT", spot: put.quoteSpot, price: row.mark, delta: row.delta, gamma: row.gamma, strike: row.strike, target: level.stock,
    }));
  });
  assert.match(projectRoiChartLines(stale)[0].title, /⚠ quotes old$/);
  assert.doesNotMatch(projectRoiChartLines(calm)[0].title, /quotes old/);
  assert.equal(projectRoiQuoteSpot({ rows: ROWS.filter((row) => row.side === "CALL"), expiry: DAY1, spot: SPOT }), 0, "no puts: no parity");
});

test("the result carries the live spot the card's stale-quote warning prints", () => {
  const stale = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT * 0.94, defaultExpiry: DAY1 });
  assert.equal(stale.spot, Math.round(SPOT * 0.94 * 100) / 100);
  assert.equal(typeof stale.quoteSpot.toFixed, "function");
});

test("target switch 1-6: default 3, runners T4-T6 past T3", () => {
  const plan = { entrySpot: 200.4 };
  const build = (extra = {}) => buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, walls: WALLS, defaultExpiry: DAY1, plan: { ...plan, ...extra } });
  const three = build();
  assert.equal(three.targetCount, 3, "default is three targets");
  assert.deepEqual(three.targets.map((level) => level.stock), [202.9, 205.4, 210]);
  assert.deepEqual(build({ targetCount: 1 }).targets.map((level) => [level.label, level.stock]), [["T1", 202.9]]);
  assert.deepEqual(build({ targetCount: 2 }).targets.map((level) => level.label), ["T1", "T2"]);
  // T3 is the OI level at 210. Runners: 2x the move (210.40) is only 0.40
  // past T3, so T4 = T3 + half a move = 212.50; 2.5x (212.90) is only 0.40
  // past T4, so T5 = 215; 3x (215.40) likewise, so T6 = 217.50.
  const six = build({ targetCount: 6 });
  assert.equal(six.targetCount, 6);
  assert.deepEqual(six.targets.map((level) => [level.label, level.key, level.stock]), [
    ["T1", "t1", 202.9], ["T2", "target", 205.4], ["T3", "t3", 210],
    ["T4", "t4", 212.5], ["T5", "t5", 215], ["T6", "t6", 217.5],
  ]);
  const values = six.targets.map((level) => level.value);
  assert.ok(values.every((value, index) => !index || value > values[index - 1]), "each farther call target pays more");
  assert.deepEqual(projectRoiChartLines(six).map((line) => line.key), ["entry", "t1", "target", "t3", "t4", "t5", "t6", "stop"]);
  assert.match(projectRoiChartLines(six)[6].title, /^T6 217\.50 {2}\+\d+% {2}205C \$\d+\.\d\d$/);
  assert.deepEqual(build({ targetCount: 5 }).targets.map((level) => level.label), ["T1", "T2", "T3", "T4", "T5"]);
  assert.equal(build({ targetCount: 9 }).targetCount, 6, "capped at six");
  assert.equal(build({ targetCount: 0 }).targetCount, 3, "nonsense falls back to three");
  // Puts: runners below T3 (195). 2x = 190.40, 2.5x = 187.90, 3x = 185.40.
  const put = build({ side: "PUT", targetCount: 6 });
  assert.deepEqual(put.targets.map((level) => level.stock), [197.9, 195.4, 195, 190.4, 187.9, 185.4]);
});

test("the chart picks the same ENTRY as the Options chain: OTM, delta just under 0.20, bid/ask middle", () => {
  // CRM-like chain: calls 217.5-227.5, deltas falling with the strike.
  const crm = [
    [217.5, 0.53, 3.10, 3.26], [220, 0.40, 1.95, 2.07], [222.5, 0.27, 1.15, 1.23],
    [225, 0.17, 0.63, 0.71], [227.5, 0.10, 0.31, 0.37],
  ].map(([strike, delta, bid, ask]) => ({ side: "CALL", strike, expiry: DAY1, days_to_expiration: 1, bid, ask, mark: (bid + ask) / 2, delta, gamma: 0.03, open_interest: 300, expected_move: 5.5 }));
  const puts = [
    [215, -0.34, 0.33, 0.37], [212.5, -0.24, 0.22, 0.26], [210, -0.16, 0.14, 0.18], [207.5, -0.09, 0.08, 0.12],
  ].map(([strike, delta, bid, ask]) => ({ side: "PUT", strike, expiry: DAY1, days_to_expiration: 1, bid, ask, mark: (bid + ask) / 2, delta, gamma: 0.03, open_interest: 300, expected_move: 5.5 }));
  const rows = [...crm, ...puts];
  assert.deepEqual(projectRoiChainEntry({ rows, spot: 217.3, side: "CALL", expiry: DAY1 }), { strike: 225, price: 0.67, delta: 0.17 });
  assert.deepEqual(projectRoiChainEntry({ rows, spot: 217.3, side: "PUT", expiry: DAY1 }), { strike: 210, price: 0.16, delta: 0.16 });
  const call = buildProjectRoi({ now: NOW, rows, spot: 217.3, walls: [{ price: 222.5, side: "C", title: "188" }], defaultExpiry: DAY1 });
  assert.equal(call.strike, 225, "the chain ENTRY beats the wall rule");
  assert.equal(call.entry, 0.67);
  assert.match(call.strikeReason, /chain ENTRY/);
  assert.ok(call.ladder.some((item) => item.strike === 225), "the ENTRY strike is in the ladder even past the move");
  // A typed strike or fill still wins.
  const typed = buildProjectRoi({ now: NOW, rows, spot: 217.3, defaultExpiry: DAY1, plan: { strike: 222.5, entry: 1.1 } });
  assert.deepEqual([typed.strike, typed.entry, typed.strikeReason], [222.5, 1.1, "your pick"]);
  // The server's own ENTRY mark, when the chain carries one, is used as-is.
  const marked = rows.map((item) => (item.side === "CALL" && item.strike === 227.5
    ? { ...item, roiEstimate: { kind: "entry", source: "chain-mark", entryStrike: 227.5, entryPrice: 0.33, entryDelta: 0.1 } } : item));
  assert.deepEqual(projectRoiChainEntry({ rows: marked, spot: 217.3, side: "CALL", expiry: DAY1 }), { strike: 227.5, price: 0.33, delta: 0.1 });
  assert.equal(projectRoiChainPrice({ bid: 0, ask: 0, mark: 0, last: 0.4 }), 0.4);
});

test("the stop is never on the wrong side of the entry (SPY 2026-11-12: stop drew above entry)", () => {
  // Plan made at 198.00, stock has since risen to 200.40.
  const old = { createdAt: 1, entrySpot: 198 };
  for (const side of ["CALL", "PUT"]) {
    const live = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, walls: WALLS, defaultExpiry: DAY1, plan: { ...old, side } });
    assert.equal(live.entrySpot, SPOT, `${side}: with no fill typed the entry follows the live price`);
    if (side === "CALL") assert.ok(live.stopStock < live.entrySpot, `call stop ${live.stopStock} above entry ${live.entrySpot}`);
    else assert.ok(live.stopStock > live.entrySpot, `put stop ${live.stopStock} below entry ${live.entrySpot}`);
    // A typed fill keeps the stock price it was typed at, and the stop is
    // measured from that same point with the fill price.
    const filled = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, walls: WALLS, defaultExpiry: DAY1, plan: { ...old, side, entry: 1.2 } });
    assert.equal(filled.entrySpot, 198);
    if (side === "CALL") assert.ok(filled.stopStock < 198, `call stop ${filled.stopStock} above the fill's 198`);
    else assert.ok(filled.stopStock > 198, `put stop ${filled.stopStock} below the fill's 198`);
    const lines = projectRoiChartLines(filled);
    const entryLine = lines.find((line) => line.key === "entry");
    const stopLine = lines.find((line) => line.key === "stop");
    assert.ok(side === "CALL" ? stopLine.price < entryLine.price : stopLine.price > entryLine.price, `${side} stop line on the wrong side`);
  }
});
