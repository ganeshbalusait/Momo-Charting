// ROI / Odds follow the trader across tickers (2026-11-15, Ganesh: "Whenever
// I change the tickers it's disappearing. Fix it"). Plans are per ticker, so a
// ticker with no plan showed ROI off. Now the last choices are remembered:
// ROI on/off and what the chart shows (ROI / Odds / Both) apply to every
// ticker; a ticker with no plan gets a fresh one with the last side, Both,
// number of targets and contracts. Auto side (2026-11-26) is carried the same way. Strike, entry and target stay per ticker.

export const ROI_CARRY_STORAGE_KEY = "agx.chartProjectRoi.carry.v1";

const SHOWS = new Set(["roi", "odds", "both"]);

// The choices worth carrying, from the plan just changed.
export function roiCarryFrom(plan) {
  if (!plan || typeof plan !== "object") return null;
  return {
    on: plan.hidden !== true,
    side: String(plan.side || "").toUpperCase() === "PUT" ? "PUT" : "CALL",
    // Auto (chartRoiAutoSide.js, 2026-11-26) draws one side, so it and Both are never on together.
    both: plan.both === true && plan.auto !== true,
    auto: plan.auto === true,
    show: SHOWS.has(plan.show) ? plan.show : "roi",
    targetCount: Math.min(6, Math.max(1, Math.round(Number(plan.targetCount) || 3))),
    contracts: Math.max(1, Math.round(Number(plan.contracts) || 1)),
  };
}

// Before anything was remembered: the most recently made plan says it.
export function roiCarryFromPlans(plans = {}) {
  const list = Object.values(plans || {}).filter((plan) => plan && typeof plan === "object");
  if (!list.length) return null;
  const latest = list.reduce((best, plan) => (Number(plan.createdAt) || 0) > (Number(best.createdAt) || 0) ? plan : best, list[0]);
  return roiCarryFrom(latest);
}

export function freshRoiPlan(carry, spot, now = Date.now()) {
  return {
    side: carry?.side || "CALL",
    both: carry?.both === true,
    auto: carry?.auto === true,
    contracts: carry?.contracts || 1,
    targetCount: carry?.targetCount || 3,
    show: carry?.show || "roi",
    entrySpot: Math.round((Number(spot) || 0) * 100) / 100,
    createdAt: now,
  };
}

// What a ticker's plan should become when it comes on screen, or null to
// leave it: no plan and ROI on -> a fresh plan; a plan whose on/off or
// ROI/Odds/Both differs from the last choice -> the last choice.
export function roiPlanOnArrival(current, carry, spot, now = Date.now()) {
  if (!carry) return null;
  if (!current) return carry.on && Number(spot) > 0 ? freshRoiPlan(carry, spot, now) : null;
  const hidden = !carry.on;
  const show = carry.show || "roi";
  const currentShow = SHOWS.has(current.show) ? current.show : "roi";
  // Auto is a mode like ROI / Odds / Both: the last choice applies to every ticker.
  const auto = carry.auto === true;
  const autoSame = (current.auto === true) === auto;
  if ((current.hidden === true) === hidden && currentShow === show && autoSame) return null;
  return autoSame ? { ...current, hidden, show } : { ...current, hidden, show, auto, both: auto ? false : current.both === true };
}

export function readRoiCarry(storage = globalThis.localStorage) {
  try {
    const value = JSON.parse(storage?.getItem(ROI_CARRY_STORAGE_KEY) || "null");
    return value && typeof value === "object" ? roiCarryFrom({ ...value, hidden: value.on === false }) : null;
  } catch {
    return null;
  }
}

export function writeRoiCarry(carry, storage = globalThis.localStorage) {
  try {
    if (carry) storage?.setItem(ROI_CARRY_STORAGE_KEY, JSON.stringify(carry));
  } catch {
    // Storage full or blocked: the choice lasts for this page only.
  }
}
