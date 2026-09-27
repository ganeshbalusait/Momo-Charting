function finiteNumber(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

export const MOBILE_OPTIONS_SECTIONS = Object.freeze([
  { key: "chain", label: "Chain" },
  { key: "heatmap", label: "Heatmap" },
  { key: "flow", label: "Flow" },
  { key: "levels", label: "C/P Levels" },
  { key: "news", label: "News" },
]);

export function mobileOptionsSectionRequestMode(section) {
  const key = String(section || "").trim().toLowerCase();
  if (key === "levels") return "chain";
  if (["heatmap", "flow"].includes(key)) return "analytics";
  if (key === "news") return "news";
  return "none";
}

function preferredContract(current, candidate) {
  if (!current) return candidate;
  const currentLiquidity = Math.max(0, finiteNumber(current.volume) || 0)
    + Math.max(0, finiteNumber(current.open_interest) || 0);
  const candidateLiquidity = Math.max(0, finiteNumber(candidate.volume) || 0)
    + Math.max(0, finiteNumber(candidate.open_interest) || 0);
  return candidateLiquidity > currentLiquidity ? candidate : current;
}

export function buildMobileQuickOptionRows(rows, pivotStrike, maxStrikes = 13) {
  const grouped = new Map();
  (Array.isArray(rows) ? rows : []).forEach((row) => {
    const strike = finiteNumber(row?.strike);
    const side = String(row?.side || "").trim().toUpperCase();
    if (strike == null || strike <= 0 || !["CALL", "PUT"].includes(side)) return;
    const current = grouped.get(strike) || { strike, call: null, put: null };
    if (side === "CALL") current.call = preferredContract(current.call, row);
    else current.put = preferredContract(current.put, row);
    grouped.set(strike, current);
  });

  const ordered = [...grouped.values()].sort((left, right) => left.strike - right.strike);
  const limit = Math.max(1, Math.floor(Number(maxStrikes) || 13));
  if (ordered.length <= limit) return ordered;

  const pivot = finiteNumber(pivotStrike);
  const target = pivot != null && pivot > 0 ? pivot : ordered[Math.floor(ordered.length / 2)].strike;
  let nearestIndex = 0;
  ordered.forEach((row, index) => {
    if (Math.abs(row.strike - target) < Math.abs(ordered[nearestIndex].strike - target)) nearestIndex = index;
  });
  const start = Math.max(0, Math.min(ordered.length - limit, nearestIndex - Math.floor(limit / 2)));
  return ordered.slice(start, start + limit);
}

export function mobileQuickOptionMark(row) {
  if (!row) return null;
  for (const value of [row.mark, row.last]) {
    const numeric = finiteNumber(value);
    if (numeric != null && numeric >= 0) return numeric;
  }
  const bid = finiteNumber(row.bid);
  const ask = finiteNumber(row.ask);
  if (bid != null && ask != null && bid >= 0 && ask >= 0) return (bid + ask) / 2;
  return bid ?? ask;
}
