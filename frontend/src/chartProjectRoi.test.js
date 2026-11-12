import test from "node:test";
import assert from "node:assert/strict";
import {
  buildProjectRoi,
  projectRoiChartLines,
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

test("auto plan: full-move target, strike from the wall nearest the exit, entry at the ask", () => {
  const result = buildProjectRoi({ now: NOW, rows: ROWS, spot: SPOT, walls: WALLS, defaultExpiry: DAY1, expiryExpectedMoves: { [DAY1]: MOVE } });
  assert.equal(result.ready, true);
  assert.equal(result.expiry, DAY1);
  assert.equal(result.target, 205.4);
  assert.equal(result.strike, 205);
  assert.equal(result.strikeReason, "strike from 205C 9K wall — the OI / level nearest your exit");
  assert.equal(result.entry, row("CALL", 205).ask);
  assert.equal(result.entryTyped, false);
  assert.deepEqual(result.ladder.map((item) => item.strike), [200, 205, 210, 215]);
  assert.ok(result.roiPercent > 0, "a call that reaches the full move gains");
  assert.ok(result.stopStock < SPOT, "a call stop sits below spot");
  assert.equal(result.stopPremium, Math.round(row("CALL", 205).ask * 50) / 100);
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
  assert.equal(lines[0].price, 200.1);
  assert.match(lines[0].title, /^Entry 200\.10 {2}205C \$\d+\.\d\d$/);
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
  assert.deepEqual(dragged.targets.map((level) => level.stock), [202.9, 210]);
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
