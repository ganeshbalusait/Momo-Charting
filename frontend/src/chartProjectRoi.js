// Chart "Project ROI" card (2026-11-11): pick an option from the chart's live
// chain and see, on the chart itself, where it pays and where it is stopped.
//
//  - strike ladder: the ATM + OTM strikes inside ±EM (same ladder as ROI Calc),
//  - default exit (target): the full expected move in the trade's direction,
//  - default strike: the ladder strike nearest the OI wall / level closest to
//    that exit ("strike from <wall> - the OI / level nearest your exit"),
//  - entry: the picked contract's live ask until the trader types a fill,
//  - projected value at the target: the Delta + Gamma path (same model as the
//    ROI Calc stop), floored at intrinsic value,
//  - stop: the stock price where the contract is down 50% from the entry.
//
// The plan (side, expiry, strike, typed entry/target, contracts, the stock
// price when it was made) is saved per ticker so the Entry / Target / Stop
// lines stay on the chart after a reload.

import { buildRoiAutofill, roiAutoTargets, roiEntryPrice, roiContractPrice, roiExpiryExpectedMove } from "./roiCalcAutofill.js";

export const PROJECT_ROI_STORAGE_KEY = "agx.chartProjectRoi.v1";

function sideOf(side) {
  return String(side || "CALL").toUpperCase() === "PUT" ? "PUT" : "CALL";
}

function round2(value) {
  return Math.round(Number(value) * 100) / 100;
}

function positive(value) {
  const number = Number(value);
  return Number.isFinite(number) && number > 0 ? number : 0;
}

// The first expiry date still trading at `now`: today until 16:15 ET (the
// last option prints), tomorrow after that. An expired 0DTE after the close
// has a stale ask and almost no expected move, so it is never offered.
export function projectRoiFirstLiveExpiry(now = new Date()) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(now).map((part) => [part.type, part.value]));
  const today = `${parts.year}-${parts.month}-${parts.day}`;
  const minutes = Number(parts.hour) * 60 + Number(parts.minute);
  if (minutes < 16 * 60 + 15) return today;
  const next = new Date(Date.UTC(Number(parts.year), Number(parts.month) - 1, Number(parts.day) + 1));
  return next.toISOString().slice(0, 10);
}

// Expiries in the loaded chain that are still trading, nearest first (the
// card shows three chips).
export function projectRoiExpiries(rows = [], now = new Date()) {
  const firstLive = projectRoiFirstLiveExpiry(now);
  const byExpiry = new Map();
  (Array.isArray(rows) ? rows : []).forEach((row) => {
    const expiry = String(row?.expiry || "").slice(0, 10);
    if (!expiry || expiry < firstLive || byExpiry.has(expiry)) return;
    byExpiry.set(expiry, Number(row?.days_to_expiration ?? 0));
  });
  return [...byExpiry.entries()]
    .map(([expiry, dte]) => ({ expiry, dte }))
    .sort((left, right) => left.dte - right.dte || left.expiry.localeCompare(right.expiry));
}

// Option value when the stock moves from `spot` to `target`: today's price
// plus the average of the start and end delta times the move, never below
// intrinsic value at the target and never below one cent.
export function projectRoiOptionValue(input = {}) {
  const value = optionValueRaw(input);
  return value > 0 ? round2(value) : 0;
}

function optionValueRaw({ side = "CALL", spot = 0, price = 0, delta = 0, gamma = 0, strike = 0, target = 0 } = {}) {
  const from = positive(spot);
  const to = positive(target);
  const now = positive(price);
  if (!from || !to || !now) return 0;
  const isCall = sideOf(side) === "CALL";
  const favourable = isCall ? to - from : from - to;
  const d = Math.min(Math.abs(Number(delta) || 0), 1);
  const g = Math.abs(Number(gamma) || 0);
  const endDelta = Math.min(Math.max(d + g * favourable, 0), 1);
  const modelled = now + ((d + endDelta) / 2) * favourable;
  const intrinsic = Math.max(isCall ? to - Number(strike) : Number(strike) - to, 0);
  return Math.max(modelled, intrinsic, 0.01);
}

// Stock price where the contract is worth `stopPremium`, walking against the
// trade from `spot`. Null when no adverse move within the stock price gets
// there (deep ITM with no delta data).
export function projectRoiStopStock({ side = "CALL", spot = 0, price = 0, delta = 0, gamma = 0, strike = 0, stopPremium = 0 } = {}) {
  const from = positive(spot);
  const now = positive(price);
  if (!from || !now || !(stopPremium > 0)) return null;
  if (now <= stopPremium) return round2(from);
  const isCall = sideOf(side) === "CALL";
  const valueAt = (adverse) => optionValueRaw({
    side, spot: from, price: now, delta, gamma, strike, target: isCall ? from - adverse : from + adverse,
  });
  let low = 0;
  let high = isCall ? from : from * 2;
  if (valueAt(high) > stopPremium) return null;
  for (let step = 0; step < 60; step += 1) {
    const mid = (low + high) / 2;
    if (valueAt(mid) > stopPremium) low = mid; else high = mid;
  }
  const adverse = (low + high) / 2;
  return round2(isCall ? from - adverse : from + adverse);
}

// The OI wall / level in the trade's direction closest to the exit price.
function wallNearestExit({ walls = [], spot, target, isCall }) {
  return (Array.isArray(walls) ? walls : [])
    .filter((wall) => {
      const level = positive(wall?.price);
      return level && (isCall ? level > spot : level < spot);
    })
    .sort((left, right) => Math.abs(Number(left.price) - target) - Math.abs(Number(right.price) - target))[0] || null;
}

// Everything the card and the chart lines show, from the live chain plus the
// trader's choices. `plan` fields left null/undefined fall back to live values.
export function buildProjectRoi({
  rows = [],
  spot = 0,
  expiryExpectedMoves = null,
  defaultExpiry = "",
  walls = [],
  plan = {},
  now = new Date(),
} = {}) {
  const price = positive(spot);
  const side = sideOf(plan?.side);
  const isCall = side === "CALL";
  const sign = isCall ? 1 : -1;
  const expiries = projectRoiExpiries(rows, now);
  const wanted = String(plan?.expiry || defaultExpiry || "").slice(0, 10);
  const expiry = expiries.some((item) => item.expiry === wanted) ? wanted : expiries[0]?.expiry || "";
  const contracts = Math.max(1, Math.round(Number(plan?.contracts) || 1));
  const fill = buildRoiAutofill({ rows, spot: price, side, expiry, expiryExpectedMoves });
  const expectedMove = fill.expectedMove || roiExpiryExpectedMove({ rows, expiry, expiryExpectedMoves });
  const ready = Boolean(price && fill.ladder.length);

  const liveTarget = price && expectedMove ? round2(price + sign * expectedMove) : 0;
  const target = positive(plan?.target) || liveTarget;
  const targetMin = price ? round2(isCall ? price : Math.max(price - 2 * (expectedMove || price * 0.02), 0.01)) : 0;
  const targetMax = price ? round2(isCall ? price + 2 * (expectedMove || price * 0.02) : price) : 0;

  // Default strike: the ladder strike nearest the OI / level closest to the
  // exit. Without a wall on that side, the first OTM strike that trades.
  const wall = ready && target ? wallNearestExit({ walls, spot: price, target, isCall }) : null;
  const tradable = fill.ladder.filter((item) => item.entry > 0);
  let autoStrike = fill.defaultStrike;
  let strikeReason = "first OTM strike";
  if (wall && tradable.length) {
    const nearest = tradable.reduce((best, item) => (
      Math.abs(item.strike - Number(wall.price)) < Math.abs(best.strike - Number(wall.price)) ? item : best
    ), tradable[0]);
    autoStrike = nearest.strike;
    const wallSide = String(wall.side || "").trim().charAt(0).toUpperCase();
    const wallName = `${formatStrike(wall.price)}${wallSide === "C" || wallSide === "P" ? wallSide : ""}`;
    const wallOi = String(wall.title || "").trim();
    strikeReason = `strike from ${wallName}${wallOi ? ` ${wallOi}` : ""} wall — the OI / level nearest your exit`;
  }
  const strike = positive(plan?.strike) || autoStrike;
  if (positive(plan?.strike)) strikeReason = "your pick";
  const row = (Array.isArray(rows) ? rows : []).find((item) => (
    String(item?.expiry || "").slice(0, 10) === expiry
    && sideOf(item?.side) === side
    && Number(item?.strike) === strike
  )) || null;
  const livePrice = roiContractPrice(row);
  const askEntry = round2(roiEntryPrice(row));
  const entry = positive(plan?.entry) || askEntry;
  const delta = Number(row?.delta || 0);
  const gamma = Number(row?.gamma || 0);

  const value = row && target ? projectRoiOptionValue({ side, spot: price, price: livePrice, delta, gamma, strike, target }) : 0;
  const roiPercent = entry > 0 && value > 0 ? round2(((value - entry) / entry) * 100) : null;
  const profit = roiPercent == null ? null : round2((value - entry) * 100 * contracts);

  // Three targets (Ganesh 2026-11-11: "give me at least 3 target"), picked
  // the same way as ROI Calc: half the expected move, the target on the
  // slider (the full move unless he moved it), and the biggest-OI strike
  // past the full move (1.5x the move without OI). Each carries the
  // contract's projected price there - the limit-sell for the broker.
  const [halfMove, , wallLevel] = roiAutoTargets({ rows, spot: price, side, expiry, expectedMove });
  const levelAt = (key, stock, reason) => {
    const at = positive(stock);
    const levelValue = row && at ? projectRoiOptionValue({ side, spot: price, price: livePrice, delta, gamma, strike, target: at }) : 0;
    const levelRoi = entry > 0 && levelValue > 0 ? round2(((levelValue - entry) / entry) * 100) : null;
    return {
      key, stock: round2(at), reason, value: levelValue, roiPercent: levelRoi,
      profit: levelRoi == null ? null : round2((levelValue - entry) * 100 * contracts),
    };
  };
  const candidates = [
    levelAt("t1", halfMove, "half the expected move"),
    levelAt("target", target, positive(plan?.target) ? "your target" : "full expected move"),
    levelAt("t3", wallLevel, "OI level past the move"),
  ].filter((level) => level.stock > 0);
  // Nearest first; a level that repeats another (a slider target dragged
  // onto the half move) is shown once.
  const targets = candidates
    .filter((level, index) => candidates.findIndex((other) => Math.abs(other.stock - level.stock) < 0.005) === index)
    .sort((left, right) => sign * (left.stock - right.stock))
    .map((level, index) => ({ ...level, label: `T${index + 1}` }));
  const stopPremium = entry > 0 ? round2(entry * 0.5) : 0;
  const stopStock = row && stopPremium ? projectRoiStopStock({ side, spot: price, price: livePrice, delta, gamma, strike, stopPremium }) : null;
  const stopLoss = stopPremium ? round2(-(entry - stopPremium) * 100 * contracts) : null;
  const entrySpot = positive(plan?.entrySpot) || price;

  return {
    ready,
    side,
    sideLetter: isCall ? "C" : "P",
    expiries: expiries.slice(0, 3),
    expiry,
    expectedMove,
    ladder: fill.ladder,
    strike,
    strikeReason,
    contract: row,
    livePrice: round2(livePrice),
    askEntry,
    entry: round2(entry),
    entryTyped: positive(plan?.entry) > 0,
    entrySpot: round2(entrySpot),
    target: round2(target),
    targetTyped: positive(plan?.target) > 0,
    targetMin,
    targetMax,
    contracts,
    projectedValue: value,
    roiPercent,
    profit,
    targets,
    stopPremium,
    stopStock,
    stopLoss,
  };
}

// The three chart lines. Entry sits at the stock price when the plan was made.
export function projectRoiChartLines(result) {
  if (!result?.ready || !result.contract) return [];
  const contract = `${formatStrike(result.strike)}${result.sideLetter}`;
  const lines = [{
    key: "entry",
    price: result.entrySpot,
    color: "#e8f4ff",
    title: `Entry ${result.entrySpot.toFixed(2)}  ${contract} $${result.entry.toFixed(2)}`,
  }];
  // One box per target: stock level, ROI, and the contract's projected price
  // there - the limit-sell price for the broker (Ganesh 2026-11-11: "target
  // and exit price to set my broker", "give me at least 3 target").
  (result.targets || []).forEach((level) => {
    lines.push({
      key: level.key,
      price: level.stock,
      color: "#3dff8a",
      title: `${level.label} ${level.stock.toFixed(2)}${level.roiPercent == null ? "" : `  ${formatPercent(level.roiPercent)}`}${level.value > 0 ? `  ${contract} $${level.value.toFixed(2)}` : ""}`,
    });
  });
  if (result.stopStock > 0) {
    // Stock stop and the contract's stop price (half the entry) for the broker.
    lines.push({ key: "stop", price: result.stopStock, color: "#ff5a6a", title: `Stop ${result.stopStock.toFixed(2)}  −50%  ${contract} $${result.stopPremium.toFixed(2)}` });
  }
  return lines;
}

export function formatStrike(strike) {
  const value = Number(strike);
  return Number.isInteger(value) ? String(value) : value.toFixed(2).replace(/0$/, "");
}

export function formatPercent(value) {
  const number = Math.round(Number(value) || 0);
  return `${number >= 0 ? "+" : "−"}${Math.abs(number)}%`;
}

export function formatDollars(value) {
  const number = Math.round(Number(value) || 0);
  return `${number >= 0 ? "+" : "−"}$${Math.abs(number).toLocaleString("en-US")}`;
}

// Saved plans, one per ticker. Storage can be missing or throw (private
// window, phone storage full); the card then works for the session only.
export function readProjectRoiPlans(storage = globalThis.localStorage) {
  try {
    const parsed = JSON.parse(storage?.getItem(PROJECT_ROI_STORAGE_KEY) || "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

export function writeProjectRoiPlans(plans, storage = globalThis.localStorage) {
  try {
    storage?.setItem(PROJECT_ROI_STORAGE_KEY, JSON.stringify(plans || {}));
  } catch {
    /* Storage unavailable: the plan lives until the page reloads. */
  }
}
