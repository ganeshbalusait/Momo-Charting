// MomoX chart palette: call/resistance walls red, put/support walls green,
// with dimmer variants for weak walls.
const DEFAULT_CALL_COLOR = "#f23645";
const DEFAULT_CALL_MODERATE_COLOR = "#f23645";
const DEFAULT_CALL_WEAK_COLOR = "#8c2a30";
const DEFAULT_PUT_COLOR = "#2ddf86";
const DEFAULT_PUT_MODERATE_COLOR = "#2ddf86";
const DEFAULT_PUT_WEAK_COLOR = "#1b6e4a";
const OPTION_SURFACE_COLORS = Object.freeze({
  call: Object.freeze({
    strong: "#22c55e",
    moderate: "#84cc16",
    weak: "#166534",
  }),
  put: Object.freeze({
    strong: "#ef4444",
    moderate: "#f97316",
    weak: "#7f1d1d",
  }),
});
// Chart walls can rank up to 30 strikes per side so moderate levels near the
// money (e.g. MSFT 495 puts) are not displaced by the far-dated leaders; the
// exported TOS script keeps its own 15-plot bound.
const MAX_TOS_LEVELS_PER_SIDE = 30;
const TOS_LEVELS_PER_TIER = 3;
const TOS_LINE_WIDTH_BY_TIER = Object.freeze({
  // MomoX draws walls bold: leading tiers get thick dash blocks so a 21k
  // 9/18-style wall reads instantly, secondary tiers stay clearly visible.
  1: 2,
  2: 2,
  3: 2,
  4: 3,
  5: 4,
});
export const OI_CHART_NEARBY_AUTOSCALE_PERCENT = 5;

function number(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function expiryKey(value) {
  return String(value || "").slice(0, 10);
}

function compactNumber(value) {
  const numeric = Math.abs(number(value));
  if (numeric >= 1_000_000) return `${(numeric / 1_000_000).toFixed(numeric >= 10_000_000 ? 0 : 1).replace(/\.0$/, "")}M`;
  if (numeric >= 1_000) return `${(numeric / 1_000).toFixed(numeric >= 100_000 ? 0 : 1).replace(/\.0$/, "")}K`;
  return String(Math.round(numeric));
}

function oiBubbleText(value) {
  const thousands = Math.abs(number(value)) / 1_000;
  if (thousands >= 100) return `${Math.round(thousands)}K`;
  return `${thousands.toFixed(1).replace(/\.0$/, "")}K`;
}

// "2026-09-18" -> " 9/18" for wall bubbles; empty when the level has no
// dominant-expiry attribution (single-expiry level sets).
function expiryBubbleSuffix(value) {
  const key = expiryKey(value);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return "";
  return ` ${Number(key.slice(5, 7))}/${Number(key.slice(8, 10))}`;
}

function strikeLabel(value) {
  const numeric = number(value);
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

function selectLevelSet(levelSets, requestedExpiry) {
  const sets = (Array.isArray(levelSets) ? levelSets : []).filter((item) => item && expiryKey(item.expiry));
  const requested = expiryKey(requestedExpiry);
  const exact = sets.find((item) => expiryKey(item.expiry) === requested);
  if (requested) return exact || null;
  return sets.slice().sort((left, right) => (
      number(left?.daysToExpiration, 999) - number(right?.daysToExpiration, 999)
      || expiryKey(left?.expiry).localeCompare(expiryKey(right?.expiry))
    ))[0] || null;
}

export function classifyHighOiStrength(openInterest, leadingOpenInterest) {
  const ratio = number(openInterest) / Math.max(number(leadingOpenInterest), 1);
  if (ratio >= 0.66) return "strong";
  if (ratio >= 0.33) return "moderate";
  return "weak";
}

function normalizeSideLevels(levelSet, side, options) {
  const isCall = side === "C";
  const source = isCall ? levelSet?.callLevels : levelSet?.putLevels;
  const spot = number(options.underlyingPrice);
  const maxPerSide = Math.max(1, Math.min(
    MAX_TOS_LEVELS_PER_SIDE,
    Math.round(number(options.maxPerSide, MAX_TOS_LEVELS_PER_SIDE)),
  ));
  const levels = (Array.isArray(source) ? source : [])
    .map((level) => ({
      strike: number(level?.strike),
      openInterest: Math.max(number(level?.openInterest ?? level?.open_interest), 0),
      volume: Math.max(number(level?.volume), 0),
      delta: Math.abs(number(level?.delta)),
      expiry: expiryKey(level?.expiry),
    }))
    .filter((level) => (
      level.strike > 0
      && level.openInterest > 0
      && (!options.directional || spot <= 0 || (isCall ? level.strike > spot : level.strike < spot))
    ))
    .sort((left, right) => (
      right.openInterest - left.openInterest
      || right.volume - left.volume
      || (isCall ? left.strike - right.strike : right.strike - left.strike)
    ))
    .slice(0, maxPerSide);
  const leadingOpenInterest = levels[0]?.openInterest || 0;
  const maxDistancePercent = Math.max(0, number(options.maxDistancePercent, 20));

  const normalizedLevels = levels.map((level, index) => {
    const strength = classifyHighOiStrength(level.openInterest, leadingOpenInterest);
    const strengthOnly = options.presentation === "strength";
    // The source ThinkScript declares five groups with three plots each.
    // Dynamic chains are ranked by OI into those same 3-level buckets.
    const tier = Math.max(1, 5 - Math.floor(index / TOS_LEVELS_PER_TIER));
    const weakStrength = strength === "weak";
    const secondary = tier <= 2 || weakStrength;
    const color = strengthOnly
      ? OPTION_SURFACE_COLORS[isCall ? "call" : "put"][strength]
      : isCall
        ? (secondary ? options.callWeakColor : options.callColor)
        : (secondary ? options.putWeakColor : options.putColor);
    const distancePercent = spot > 0 ? Math.abs(level.strike - spot) / spot * 100 : 0;
    return {
      ...level,
      price: level.strike,
      side,
      rank: index + 1,
      strength,
      tier: strengthOnly ? null : tier,
      color,
      lineWidth: strengthOnly
        ? (strength === "strong" ? 2 : 1)
        : weakStrength ? Math.max(2, TOS_LINE_WIDTH_BY_TIER[tier]) : TOS_LINE_WIDTH_BY_TIER[tier],
      tosLineWeight: strengthOnly ? null : tier === 5 ? 5 : tier === 4 ? 4 : tier === 3 ? 2 : 3,
      // Weak walls always use a visible short dash. Remaining source tiers
      // preserve the ThinkScript short-dash/long-dash split.
      lineStyle: strengthOnly ? (weakStrength ? 2 : 0) : (tier <= 2 || weakStrength) ? 2 : 3,
      distancePercent,
      visible: maxDistancePercent === 0 || spot <= 0 || distancePercent <= maxDistancePercent,
      title: strengthOnly
        ? `${side} OI ${strength.toUpperCase()} ${strikeLabel(level.strike)} · ${compactNumber(level.openInterest)}${expiryBubbleSuffix(level.expiry)}`
        : `${side} OI T${tier} ${strikeLabel(level.strike)} · ${compactNumber(level.openInterest)}${expiryBubbleSuffix(level.expiry)}`,
      // TOS bubbles carry the compact OI amount plus the wall's dominant
      // expiry ("21K 9/18" style); the price scale already shows the strike.
      displayTitle: `${side} ${strength.charAt(0).toUpperCase()} ${oiBubbleText(level.openInterest)}${expiryBubbleSuffix(level.expiry)}`,
    };
  });
  // OI rank still determines strength, tier, color, and line weight. For live
  // resistance/support surfaces, display the actionable strikes from nearest
  // to farthest: calls ascend above spot and puts descend below spot.
  return options.directional
    ? normalizedLevels.sort((left, right) => (isCall
      ? left.strike - right.strike
      : right.strike - left.strike))
    : normalizedLevels;
}

export function mergeOiChartLevelSources(levelGroups = []) {
  const groups = Array.isArray(levelGroups) && levelGroups.some(Array.isArray)
    ? levelGroups
    : [levelGroups];
  const byPrice = new Map();
  groups.flatMap((levels) => (Array.isArray(levels) ? levels : []))
    .filter((level) => level?.visible !== false)
    .forEach((level) => {
    const key = number(level.price).toFixed(6);
    if (number(level.price) <= 0) return;
    const existing = byPrice.get(key);
    if (!existing) {
      byPrice.set(key, { ...level, sides: [level.side] });
      return;
    }
    const leading = level.openInterest > existing.openInterest ? level : existing;
    byPrice.set(key, {
      ...leading,
      sides: [...new Set([...(existing.sides || [existing.side]), level.side])],
      title: `${existing.title} / ${level.title}`,
      lineWidth: Math.max(existing.lineWidth, level.lineWidth),
      lineStyle: Math.min(existing.lineStyle, level.lineStyle),
    });
  });
  return [...byPrice.values()].sort((left, right) => left.price - right.price);
}

function mergeVisibleLevels(levels) {
  return mergeOiChartLevelSources([levels]);
}

export function expandPriceRangeForNearbyOiLevels({
  low,
  high,
  referencePrice,
  levels,
  maxDistancePercent = OI_CHART_NEARBY_AUTOSCALE_PERCENT,
} = {}) {
  const initialLow = number(low, NaN);
  const initialHigh = number(high, NaN);
  if (!Number.isFinite(initialLow) || !Number.isFinite(initialHigh) || initialHigh < initialLow) return null;
  const spot = number(referencePrice, NaN);
  const distanceLimit = Math.max(0, number(maxDistancePercent, OI_CHART_NEARBY_AUTOSCALE_PERCENT));
  let expandedLow = initialLow;
  let expandedHigh = initialHigh;
  const includedPrices = [];
  (Array.isArray(levels) ? levels : []).forEach((level) => {
    const price = number(level?.price ?? level?.strike, NaN);
    if (!Number.isFinite(price) || price <= 0 || !Number.isFinite(spot) || spot <= 0) return;
    const distancePercent = Math.abs(price - spot) / spot * 100;
    if (distancePercent > distanceLimit) return;
    expandedLow = Math.min(expandedLow, price);
    expandedHigh = Math.max(expandedHigh, price);
    includedPrices.push(price);
  });
  return {
    low: expandedLow,
    high: expandedHigh,
    includedPrices,
  };
}

// Synthetic level-set expiry used when the chart shows the whole DTE window
// (all expirations combined, like the generated TOS script) instead of the
// single expiry selected in the option chain.
export const OI_WINDOW_LEVEL_SET_EXPIRY = "window";

export function buildAggregateOiLevelSet(levelSets, { maxDaysToExpiration = 0 } = {}) {
  const sets = (Array.isArray(levelSets) ? levelSets : []).filter((item) => (
    item
    && expiryKey(item.expiry)
    && expiryKey(item.expiry) !== OI_WINDOW_LEVEL_SET_EXPIRY
    && (maxDaysToExpiration <= 0 || number(item.daysToExpiration, 0) <= maxDaysToExpiration)
  ));
  if (!sets.length) return null;
  // Retain the full 30-per-side ranking here — NOT the display cap. The
  // aggregate has no idea where spot is, so an early cap let below-spot
  // near-the-money OI consume every call slot; the chart's directional pass
  // then discarded those strikes and real above-spot walls (PLTR 175 with
  // 4.5K OI while spot sat at 172) vanished from the chart entirely.
  // normalizeSideLevels applies the trader's maxPerSide after its
  // directional filter, which is the only place the cap is meaningful.
  const limit = MAX_TOS_LEVELS_PER_SIDE;
  // Trading Alphas attribution: each strike keeps its DOMINANT expiry (the
  // one holding the most OI) rather than a sum, so a wall reads "21K 9/18".
  const aggregateSide = (key, ascending) => {
    const byStrike = new Map();
    sets.forEach((set) => {
      (Array.isArray(set?.[key]) ? set[key] : []).forEach((level) => {
        const strike = number(level?.strike);
        const openInterest = Math.max(number(level?.openInterest ?? level?.open_interest), 0);
        if (strike <= 0 || openInterest <= 0) return;
        const candidate = {
          strike,
          openInterest,
          volume: Math.max(number(level?.volume), 0),
          delta: Math.abs(number(level?.delta)),
          expiry: expiryKey(set?.expiry),
          daysToExpiration: number(set?.daysToExpiration, 0),
        };
        const existing = byStrike.get(strike);
        if (!existing
          || candidate.openInterest > existing.openInterest
          || (candidate.openInterest === existing.openInterest && candidate.volume > existing.volume)) {
          byStrike.set(strike, candidate);
        }
      });
    });
    return [...byStrike.values()]
      .sort((left, right) => (
        right.openInterest - left.openInterest
        || right.volume - left.volume
        || (ascending ? left.strike - right.strike : right.strike - left.strike)
      ))
      .slice(0, limit);
  };
  const nearest = sets.slice().sort((left, right) => (
    number(left?.daysToExpiration, 999) - number(right?.daysToExpiration, 999)
    || expiryKey(left?.expiry).localeCompare(expiryKey(right?.expiry))
  ))[0];
  return {
    expiry: OI_WINDOW_LEVEL_SET_EXPIRY,
    daysToExpiration: number(nearest?.daysToExpiration, 0),
    atmStrike: number(nearest?.atmStrike) || null,
    callLevels: aggregateSide("callLevels", true),
    putLevels: aggregateSide("putLevels", false),
  };
}

export function buildHighOiLevelSetFromRows({
  rows,
  requestedExpiry,
  atmStrike,
  deferLimit = false,
  maxPerSide = MAX_TOS_LEVELS_PER_SIDE,
} = {}) {
  const requested = expiryKey(requestedExpiry);
  if (!requested) return null;
  const exactRows = (Array.isArray(rows) ? rows : []).filter((row) => expiryKey(row?.expiry) === requested);
  if (!exactRows.length) return null;
  const limit = Math.max(1, Math.min(
    MAX_TOS_LEVELS_PER_SIDE,
    Math.round(number(maxPerSide, MAX_TOS_LEVELS_PER_SIDE)),
  ));
  const levelsForSide = (side) => {
    const levels = exactRows
      .filter((row) => String(row?.side || "").toUpperCase() === side)
      .map((row) => ({
        strike: number(row?.strike),
        openInterest: Math.max(number(row?.openInterest ?? row?.open_interest), 0),
        volume: Math.max(number(row?.volume), 0),
        delta: Math.abs(number(row?.delta)),
      }))
      .filter((level) => level.strike > 0 && level.openInterest > 0)
      .sort((left, right) => (
        right.openInterest - left.openInterest
        || right.volume - left.volume
        || (side === "CALL" ? left.strike - right.strike : right.strike - left.strike)
      ));
    return deferLimit ? levels : levels.slice(0, limit);
  };
  const daysToExpiration = exactRows
    .map((row) => number(row?.days_to_expiration ?? row?.daysToExpiration, NaN))
    .find((value) => Number.isFinite(value));
  return {
    expiry: requested,
    daysToExpiration: daysToExpiration ?? null,
    atmStrike: number(atmStrike) || null,
    callLevels: levelsForSide("CALL"),
    putLevels: levelsForSide("PUT"),
  };
}

export function buildHighOiLevelModel({
  levelSets,
  requestedExpiry,
  underlyingPrice,
  maxPerSide = MAX_TOS_LEVELS_PER_SIDE,
  maxDistancePercent = 0,
  callColor = DEFAULT_CALL_COLOR,
  callModerateColor = DEFAULT_CALL_MODERATE_COLOR,
  callWeakColor = DEFAULT_CALL_WEAK_COLOR,
  putColor = DEFAULT_PUT_COLOR,
  putModerateColor = DEFAULT_PUT_MODERATE_COLOR,
  putWeakColor = DEFAULT_PUT_WEAK_COLOR,
  presentation = "tos",
  directional = false,
} = {}) {
  const levelSet = selectLevelSet(levelSets, requestedExpiry);
  const options = {
    underlyingPrice,
    maxPerSide,
    maxDistancePercent,
    callColor,
    callModerateColor,
    callWeakColor,
    putColor,
    putModerateColor,
    putWeakColor,
    presentation: presentation === "strength" ? "strength" : "tos",
    directional: Boolean(directional),
  };
  const callLevels = normalizeSideLevels(levelSet, "C", options);
  const putLevels = normalizeSideLevels(levelSet, "P", options);
  return {
    expiry: expiryKey(levelSet?.expiry),
    daysToExpiration: levelSet?.daysToExpiration ?? null,
    atmStrike: number(levelSet?.atmStrike) || null,
    callLevels,
    putLevels,
    allLevels: [...callLevels, ...putLevels],
    visibleLevels: mergeVisibleLevels([...callLevels, ...putLevels]),
  };
}
