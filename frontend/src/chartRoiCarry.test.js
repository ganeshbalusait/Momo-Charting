// Unit (2026-11-15, "Whenever I change the tickers it's disappearing"): ROI on
// and ROI / Odds / Both follow the trader to every ticker.
import assert from "node:assert/strict";
import test from "node:test";

import { freshRoiPlan, readRoiCarry, roiCarryFrom, roiCarryFromPlans, roiPlanOnArrival, writeRoiCarry } from "./chartRoiCarry.js";

const NOW = 1_800_000_000_000;

test("the choices carried from a plan", () => {
  assert.deepEqual(roiCarryFrom({ side: "PUT", both: false, show: "odds", targetCount: 2, contracts: 3, strike: 744, entry: 0.99 }),
    { on: true, side: "PUT", both: false, auto: false, show: "odds", targetCount: 2, contracts: 3 });
  assert.equal(roiCarryFrom({ hidden: true }).on, false);
  assert.equal(roiCarryFrom({ show: "weird" }).show, "roi");
  assert.equal(roiCarryFrom(null), null);
});

test("a ticker with no plan gets a fresh one when ROI is on, nothing when off", () => {
  const carry = roiCarryFrom({ side: "PUT", show: "odds", targetCount: 2 });
  assert.deepEqual(roiPlanOnArrival(null, carry, 357.5, NOW),
    { side: "PUT", both: false, auto: false, contracts: 1, targetCount: 2, show: "odds", entrySpot: 357.5, createdAt: NOW });
  assert.equal(roiPlanOnArrival(null, { ...carry, on: false }, 357.5, NOW), null);
  assert.equal(roiPlanOnArrival(null, carry, 0, NOW), null, "waits for a price");
  assert.equal(roiPlanOnArrival(null, null, 357.5, NOW), null, "nothing remembered yet: unchanged");
});

test("an existing plan follows ROI on/off and ROI / Odds / Both, and keeps its own strike and side", () => {
  const plan = { side: "CALL", strike: 362.5, entry: 0.22, target: 359.55, hidden: true, show: "roi" };
  const carry = roiCarryFrom({ side: "PUT", show: "odds" });
  assert.deepEqual(roiPlanOnArrival(plan, carry, 357.5, NOW), { ...plan, hidden: false, show: "odds" });
  assert.equal(roiPlanOnArrival({ ...plan, hidden: false, show: "odds" }, carry, 357.5, NOW), null, "already matches");
  assert.deepEqual(roiPlanOnArrival({ ...plan, hidden: false }, { ...carry, on: false }, 357.5, NOW), { ...plan, hidden: true, show: "odds" });
});

test("before anything is remembered, the newest plan decides; storage failures are harmless", () => {
  assert.equal(roiCarryFromPlans({ SPY: { createdAt: 2, show: "odds", side: "PUT" }, AAPL: { createdAt: 1, hidden: true } }).show, "odds");
  assert.equal(roiCarryFromPlans({}), null);
  const box = new Map();
  const storage = { getItem: (key) => box.get(key) ?? null, setItem: (key, value) => box.set(key, value) };
  writeRoiCarry({ on: false, side: "PUT", both: true, show: "both", targetCount: 4, contracts: 2 }, storage);
  assert.deepEqual(readRoiCarry(storage), { on: false, side: "PUT", both: true, auto: false, show: "both", targetCount: 4, contracts: 2 });
  assert.equal(readRoiCarry({ getItem: () => "{bad" }), null);
  assert.equal(readRoiCarry({ getItem: () => { throw new Error("blocked"); } }), null);
  writeRoiCarry({ on: true }, { setItem: () => { throw new Error("full"); } });
  assert.equal(freshRoiPlan(null, 10, NOW).side, "CALL");
});

// 2026-11-26 "can we do automatic": Auto is a mode like ROI / Odds / Both, so it follows the trader to every ticker.
test("Auto follows the trader: a new ticker starts on Auto, a ticker with a plan switches to it and back", () => {
  const carry = roiCarryFrom({ side: "PUT", auto: true, both: true });
  assert.equal(carry.auto, true);
  assert.equal(carry.both, false, "Auto draws one side");
  assert.equal(freshRoiPlan(carry, 100, NOW).auto, true);
  const plan = { side: "CALL", strike: 362.5, hidden: false, show: "roi", both: true };
  assert.deepEqual(roiPlanOnArrival(plan, carry, 100, NOW), { ...plan, auto: true, both: false });
  assert.equal(roiPlanOnArrival({ ...plan, auto: true, both: false }, carry, 100, NOW), null, "already on Auto");
  assert.deepEqual(roiPlanOnArrival({ ...plan, auto: true, both: false }, roiCarryFrom({ side: "CALL" }), 100, NOW), { ...plan, auto: false, both: false });
  const box = new Map();
  const storage = { getItem: (key) => box.get(key) ?? null, setItem: (key, value) => box.set(key, value) };
  writeRoiCarry(carry, storage);
  assert.equal(readRoiCarry(storage).auto, true);
});
