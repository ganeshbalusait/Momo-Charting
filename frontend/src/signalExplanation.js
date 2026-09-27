// Plain-English explanation of one premarket scanner row, for the trader
// who wants to know WHY a row says what it says before risking money on it.
// Pure and deterministic: every sentence derives from fields already in the
// row (the same server data the chart draws), so the explanation can never
// disagree with the chart. No network, no model.

const TIMEFRAME_WORDS = {
  "1H": "1-hour",
  "2H": "2-hour",
  "4H": "4-hour",
  "5": "5-minute",
  "1h": "1-hour",
  "2h": "2-hour",
  "4h": "4-hour",
  D: "daily",
  "2D": "2-day",
  "3D": "3-day",
  "4D": "4-day",
  W: "weekly",
  M: "monthly",
};

function timeframeWord(label) {
  const match = String(label || "").match(/(\d+[HhDd]|[DWM]|\d+)$/);
  const key = match ? match[1] : "";
  return TIMEFRAME_WORDS[key] || TIMEFRAME_WORDS[key.toUpperCase()] || String(key || "that");
}

export function explainCyanHigherSignal(label, printedAt) {
  const tf = timeframeWord(label);
  const when = printedAt ? ` (printed ${printedAt})` : "";
  return `${label} (cyan, higher timeframe)${when}: on the ${tf} chart, the 9-period average crossed above the 20-period average. Daily-and-up cyan is the slowest, rarest cross the app tracks — it marks a trend change, not a scalp, which is why the scanner watches for it from midnight.`;
}

export function explainYellowHigherSignal(label, printedAt) {
  const tf = timeframeWord(label);
  const when = printedAt ? ` (printed ${printedAt})` : "";
  return `${label} (yellow, higher timeframe)${when}: on the ${tf} chart, the 4-period average crossed above the 8-period average. A daily-and-up yellow is earlier but less confirmed than its cyan twin — strongest when cyan follows it.`;
}

export function explainMacdHigherSignal(label, printedAt) {
  const tf = timeframeWord(label);
  const when = printedAt ? ` (printed ${printedAt})` : "";
  return `${label}${when}: the MACD turned bullish on the ${tf} chart. MACD is confirmation, not a cross — it raises the row's score but never sets the strength tier by itself.`;
}

export function explainCallSignal(label, family) {
  const tf = timeframeWord(label);
  if (family === "9x20") {
    return `${label} (cyan): on the ${tf} chart, the 9-period average crossed above the 20-period average. Cyan is the stronger, slower family — it confirms a real trend more often than it fakes.`;
  }
  return `${label} (yellow): on the ${tf} chart, the 4-period average crossed above the 8-period average. Yellow is the faster, earlier family — it fires sooner but fakes more often than cyan.`;
}

export function explainFire(label, firedAt) {
  const tf = timeframeWord(label);
  const when = firedAt ? ` (fired ${firedAt})` : "";
  return `🔥${label}: a squeeze fired on the ${tf} chart${when} — price volatility compressed like a spring and released upward. Fires show energy, not direction quality: alone they are the weakest signal class.`;
}

export function explainStrength(strength, score, forming) {
  const grade = String(strength || "").toUpperCase();
  let rule;
  if (grade === "STRONG") {
    rule = "STRONG means cyan and yellow agree (or two cyan) — both signal families confirm the same move.";
  } else if (grade === "MODERATE") {
    rule = "MODERATE means one real confirmation — a single cyan signal, two yellows, or multiple fires. Tradeable, but wants a catalyst or more confirmation.";
  } else {
    rule = "WEAK means only one weak signal — a single yellow or a lone fire. Your own rule: fire-only rows are the ones to skip.";
  }
  const formingNote = forming
    ? " FORMING: the candle behind the newest signal has not closed yet, so this row can still repaint away if price reverses — the same way META dropped off mid-morning on Aug 21."
    : "";
  return `${grade} (score ${score ?? 0}): ${rule}${formingNote}`;
}

export function explainCatalyst(catalyst) {
  if (catalyst && catalyst.headline) {
    const age = Number.isFinite(catalyst.ageMinutes)
      ? (catalyst.ageMinutes >= 60
        ? `${Math.round(catalyst.ageMinutes / 60)}h ago`
        : `${catalyst.ageMinutes}m ago`)
      : "";
    return `News behind the move — ${catalyst.tag || "NEWS"}: ${catalyst.headline}${age ? ` (${age})` : ""}.`;
  }
  return "No fresh news found behind this move — it is technical only. Moves without fuel fade more often.";
}

// The whole row, as ordered plain-English lines for the explain panel.
export function explainScannerRow(row) {
  if (!row || typeof row !== "object") return [];
  const lines = [];
  for (const label of Array.isArray(row.signalsCyanHigher) ? row.signalsCyanHigher : []) {
    lines.push(explainCyanHigherSignal(label, row.higherSignalTimes?.[label] || ""));
  }
  for (const label of Array.isArray(row.signalsYellowHigher) ? row.signalsYellowHigher : []) {
    lines.push(explainYellowHigherSignal(label, row.higherSignalTimes?.[label] || ""));
  }
  for (const label of Array.isArray(row.signalsMacdHigher) ? row.signalsMacdHigher : []) {
    lines.push(explainMacdHigherSignal(label, row.higherSignalTimes?.[label] || ""));
  }
  for (const label of Array.isArray(row.signals920) ? row.signals920 : []) {
    lines.push(explainCallSignal(label, "9x20"));
  }
  for (const label of Array.isArray(row.signals48) ? row.signals48 : []) {
    lines.push(explainCallSignal(label, "4x8"));
  }
  for (const label of Array.isArray(row.fires) ? row.fires : []) {
    lines.push(explainFire(label, row.fireDates?.[label] || ""));
  }
  lines.push(explainStrength(row.strength, row.score, Boolean(row.forming)));
  lines.push(explainCatalyst(row.catalyst));
  return lines;
}
