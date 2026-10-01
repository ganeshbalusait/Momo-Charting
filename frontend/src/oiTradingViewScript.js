import { classifyHighOiStrength } from "./oiChartLevels.js";

const SYMBOL_PATTERN = /^[A-Z][A-Z0-9.-]{0,9}$/;
// Charts & OI renders at most the five three-level TOS tiers per option side.
// Keep an exported study on the same bound.
const MAX_SCRIPT_LEVELS_PER_SIDE = 15;
// Weeklies plus the next monthly OPEX (e.g. a 9/18 wall seen from early
// August). Matches the server's OI_LEVELS_MONTHLY_WINDOW_DTE.
const MAX_WINDOW_DTE = 46;
const SIDE_STYLES = {
  CALL: {
    short: "C",
    strong: "#22c55e",
    moderate: "#84cc16",
    weak: "#166534",
  },
  PUT: {
    short: "P",
    strong: "#ef4444",
    moderate: "#f97316",
    weak: "#7f1d1d",
  },
};

function number(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function expiryKey(value) {
  return String(value || "").slice(0, 10);
}

function pineNumber(value) {
  const numeric = number(value, NaN);
  if (!Number.isFinite(numeric)) return "na";
  return numeric.toFixed(4).replace(/\.?0+$/, "");
}

function compactNumber(value) {
  const numeric = Math.abs(number(value));
  if (numeric >= 1_000_000) return `${(numeric / 1_000_000).toFixed(numeric >= 10_000_000 ? 0 : 1).replace(/\.0$/, "")}M`;
  if (numeric >= 1_000) return `${(numeric / 1_000).toFixed(numeric >= 100_000 ? 0 : 1).replace(/\.0$/, "")}K`;
  return String(Math.round(numeric));
}

function pineText(value) {
  return String(value || "")
    .replaceAll("\\", "\\\\")
    .replaceAll('"', '\\"')
    .replaceAll("\n", "\\n");
}

function styleRankedLevels(levels, side) {
  const ranked = levels
    .filter((level) => level.strike > 0 && level.openInterest > 0)
    .sort((left, right) => (
      right.openInterest - left.openInterest
      || right.volume - left.volume
      || (side === "CALL" ? left.strike - right.strike : right.strike - left.strike)
    ))
    .slice(0, MAX_SCRIPT_LEVELS_PER_SIDE);
  const leadingOpenInterest = ranked[0]?.openInterest || 0;
  const style = SIDE_STYLES[side];
  return ranked.map((level, index) => {
    const strength = classifyHighOiStrength(level.openInterest, leadingOpenInterest);
    return {
      ...level,
      side,
      sideShort: style.short,
      strength,
      color: style[strength],
      lineWidth: strength === "strong" ? (index === 0 ? 4 : 3) : strength === "moderate" ? 2 : 1,
      lineStyle: strength === "weak" ? 1 : 0,
    };
  });
}

function normalizeLevels(levelSet, side) {
  const source = side === "CALL" ? levelSet?.callLevels : levelSet?.putLevels;
  const levels = (Array.isArray(source) ? source : [])
    .map((level) => ({
      strike: number(level?.strike),
      openInterest: Math.max(number(level?.openInterest ?? level?.open_interest), 0),
      volume: Math.max(number(level?.volume), 0),
      expiries: [expiryKey(levelSet?.expiry)],
      daysToExpirations: [number(levelSet?.daysToExpiration, 0)],
    }));
  return styleRankedLevels(levels, side);
}

// Trading Alphas attribution: for each strike keep the single DOMINANT expiry
// (the one holding the most OI) instead of summing across expirations, so a
// level reads "21K · 09-18" — the wall's real size at the expiry where it
// actually lives.
function aggregateWindowLevels(levelSets, side) {
  const byStrike = new Map();
  levelSets.forEach((levelSet) => {
    const source = side === "CALL" ? levelSet?.callLevels : levelSet?.putLevels;
    (Array.isArray(source) ? source : []).forEach((level) => {
      const strike = number(level?.strike);
      const openInterest = Math.max(number(level?.openInterest ?? level?.open_interest), 0);
      if (strike <= 0 || openInterest <= 0) return;
      const entry = {
        strike,
        openInterest,
        volume: Math.max(number(level?.volume), 0),
        expiries: [expiryKey(levelSet?.expiry)],
        daysToExpirations: [number(levelSet?.daysToExpiration, 0)],
      };
      const existing = byStrike.get(strike);
      if (!existing
        || entry.openInterest > existing.openInterest
        || (entry.openInterest === existing.openInterest && entry.volume > existing.volume)) {
        byStrike.set(strike, entry);
      }
    });
  });
  return styleRankedLevels([...byStrike.values()], side);
}

function compactExpiryRange(expiries) {
  const values = [...new Set((Array.isArray(expiries) ? expiries : []).map(expiryKey).filter(Boolean))].sort();
  if (!values.length) return "";
  const compact = (value) => value.slice(5);
  return values.length === 1 ? compact(values[0]) : `${compact(values[0])}→${compact(values.at(-1))}`;
}

export function normalizeTradingViewSymbols(value) {
  const candidates = Array.isArray(value)
    ? value
    : String(value || "").split(/[\s,;]+/);
  return [...new Set(candidates
    .map((item) => String(item || "").trim().toUpperCase())
    .filter((item) => SYMBOL_PATTERN.test(item)))];
}

export function buildTradingViewOiSnapshot(payload, { scope = "window" } = {}) {
  const symbol = normalizeTradingViewSymbols([payload?.symbol])[0] || "";
  const levelSets = (Array.isArray(payload?.tosScriptLevels) ? payload.tosScriptLevels : [])
    .filter((levelSet) => expiryKey(levelSet?.expiry))
    .filter((levelSet) => number(levelSet?.daysToExpiration, 0) >= 0 && number(levelSet?.daysToExpiration, 0) <= MAX_WINDOW_DTE)
    .sort((left, right) => (
      number(left?.daysToExpiration, 999) - number(right?.daysToExpiration, 999)
      || expiryKey(left?.expiry).localeCompare(expiryKey(right?.expiry))
    ));
  if (!symbol || !levelSets.length) return null;
  const requestedExpiry = expiryKey(payload?.currentAtm?.expiry);
  // "expiry" pins the script to the chart's synchronized option expiry (one
  // exact chain). "window" (default) sums OI and volume per strike across the
  // full 0-31 DTE window, matching the generated all-expirations TOS study.
  const scopedSets = scope === "expiry"
    ? [levelSets.find((item) => expiryKey(item?.expiry) === requestedExpiry) || levelSets[0]]
    : levelSets;
  const frontSet = scopedSets.find((item) => expiryKey(item?.expiry) === requestedExpiry) || scopedSets[0];
  const expiry = expiryKey(frontSet?.expiry);
  const lastExpiry = expiryKey(scopedSets.at(-1)?.expiry);
  const daysToExpiration = number(frontSet?.daysToExpiration, 0);
  const maxDaysToExpiration = scopedSets.reduce(
    (max, item) => Math.max(max, number(item?.daysToExpiration, 0)),
    0,
  );
  const callLevels = scope === "expiry" ? normalizeLevels(frontSet, "CALL") : aggregateWindowLevels(scopedSets, "CALL");
  const putLevels = scope === "expiry" ? normalizeLevels(frontSet, "PUT") : aggregateWindowLevels(scopedSets, "PUT");
  if (!callLevels.length && !putLevels.length) return null;
  const currentAtmStrike = expiry === requestedExpiry
    ? number(payload?.currentAtm?.call?.strike ?? payload?.currentAtm?.put?.strike)
    : 0;
  const atmStrike = number(frontSet?.atmStrike) || currentAtmStrike || null;
  return {
    symbol,
    expiry,
    frontExpiry: expiry,
    lastExpiry,
    expiryCount: scopedSets.length,
    expiryRange: compactExpiryRange(scopedSets.map((item) => item?.expiry)),
    daysToExpiration,
    maxDaysToExpiration,
    atmStrike,
    underlyingPrice: number(frontSet?.underlyingPrice ?? payload?.underlyingPrice) || null,
    generatedAt: String(payload?.scannedAt || ""),
    source: String(payload?.source || "Schwab/TOS option chain"),
    expiryGroups: scopedSets.map((levelSet) => ({
      expiry: expiryKey(levelSet?.expiry),
      daysToExpiration: number(levelSet?.daysToExpiration, 0),
      atmStrike: number(levelSet?.atmStrike) || null,
      callLevels: normalizeLevels(levelSet, "CALL"),
      putLevels: normalizeLevels(levelSet, "PUT"),
    })),
    callLevels,
    putLevels,
  };
}

export function buildTradingViewOiScript(snapshots, {
  indicatorTitle = "Agentic OI Levels",
} = {}) {
  const normalizedSnapshots = [];
  const seenSymbols = new Set();
  (Array.isArray(snapshots) ? snapshots : []).forEach((snapshot) => {
    const symbol = normalizeTradingViewSymbols([snapshot?.symbol])[0] || "";
    if (!symbol || seenSymbols.has(symbol) || !expiryKey(snapshot?.expiry)) return;
    const callLevels = Array.isArray(snapshot?.callLevels) ? snapshot.callLevels : [];
    const putLevels = Array.isArray(snapshot?.putLevels) ? snapshot.putLevels : [];
    if (!callLevels.length && !putLevels.length) return;
    seenSymbols.add(symbol);
    normalizedSnapshots.push({ ...snapshot, symbol, callLevels, putLevels });
  });
  if (!normalizedSnapshots.length) return "";

  const lines = [
    "//@version=6",
    "// Auto-generated front-expiry OI snapshot matching Charts & OI. Rebuild to refresh levels.",
    `// Symbols: ${normalizedSnapshots.map((snapshot) => snapshot.symbol).join(", ")}`,
    `indicator("${pineText(indicatorTitle)}", overlay = true, max_lines_count = 500, max_labels_count = 500)`,
    "",
    'showATM = input.bool(true, "Show ATM")',
    'showCalls = input.bool(true, "Show call OI resistance")',
    'showPuts = input.bool(true, "Show put OI support")',
    'showLabels = input.bool(true, "Show level labels")',
    'showStamp = input.bool(true, "Show snapshot stamp")',
    'labelOffset = input.int(3, "Labels: bars right of last candle", minval = 0, maxval = 250)',
    'labelSpread = input.int(15, "Labels: horizontal spread", minval = 0, maxval = 250)',
    "",
    "var line[] allLines = array.new_line()",
    "var label[] allLabels = array.new_label()",
    "",
    "drawLevel(bool visible, float price, color levelColor, int lineWidth, int styleCode, string levelText, float spread) =>",
    "    if visible and not na(price)",
    "        lineStyle = styleCode == 1 ? line.style_dashed : line.style_solid",
    "        array.push(allLines, line.new(bar_index, price, bar_index + 1, price, extend = extend.both, color = levelColor, width = lineWidth, style = lineStyle))",
    "        if showLabels",
    "            labelX = bar_index + labelOffset + int(math.round(labelSpread * spread))",
    "            array.push(allLabels, label.new(labelX, price, levelText, xloc = xloc.bar_index, yloc = yloc.price, style = label.style_label_left, color = color.new(#07131d, 10), textcolor = levelColor, size = size.small))",
    "",
    "stamp(string stampText) =>",
    "    if showStamp",
    "        var table stampTable = table.new(position.bottom_right, 1, 1)",
    "        table.cell(stampTable, 0, 0, stampText, text_color = #9ca3af, text_size = size.small, bgcolor = color.new(#07131d, 10))",
    "",
    "clearDrawings() =>",
    "    while array.size(allLines) > 0",
    "        line.delete(array.pop(allLines))",
    "    while array.size(allLabels) > 0",
    "        label.delete(array.pop(allLabels))",
  ];

  const renderCalls = [];
  normalizedSnapshots.forEach((snapshot, snapshotIndex) => {
    const functionName = `render_${snapshot.symbol.replace(/[^A-Z0-9_]/g, "_")}_${snapshotIndex + 1}`;
    const snapshotDte = number(snapshot.daysToExpiration ?? snapshot.maxDaysToExpiration, 0);
    const levels = [
      ...snapshot.callLevels.map((level) => ({ ...level, enabled: "showCalls" })),
      ...snapshot.putLevels.map((level) => ({ ...level, enabled: "showPuts" })),
    ];
    const denominator = Math.max(levels.length - 1, 1);
    lines.push(
      "",
      `// ── ${snapshot.symbol} ──`,
      `${functionName}() =>`,
      `    stamp("${pineText(`${snapshot.symbol} · ${snapshot.expiry} · ${snapshotDte} DTE OI snapshot`)}")`,
    );
    if (number(snapshot.atmStrike) > 0) {
      lines.push(`    drawLevel(showATM, ${pineNumber(snapshot.atmStrike)}, #facc15, 3, 0, "${pineText(`ATM ${pineNumber(snapshot.atmStrike)} · ${snapshot.expiry}`)}", 0.0)`);
    }
    levels.forEach((level, index) => {
      const expiryRange = compactExpiryRange(level.expiries);
      const levelText = `${level.sideShort} OI ${String(level.strength || "").toUpperCase()} ${pineNumber(level.strike)} · ${compactNumber(level.openInterest)} OI · ${compactNumber(level.volume)} Vol${expiryRange ? ` · ${expiryRange}` : ""}`;
      lines.push(
        `    drawLevel(${level.enabled}, ${pineNumber(level.strike)}, ${level.color}, ${Math.max(1, Math.min(4, number(level.lineWidth, 1)))}, ${number(level.lineStyle, 0)}, "${pineText(levelText)}", ${(index / denominator).toFixed(3)})`,
      );
    });
    renderCalls.push(
      `if barstate.islast and syminfo.ticker == "${pineText(snapshot.symbol)}"`,
      "    clearDrawings()",
      `    ${functionName}()`,
      "",
    );
  });

  lines.push("", "// Small per-symbol dispatch blocks avoid Pine's oversized-if compiler limit.", ...renderCalls);
  return lines.join("\n");
}
