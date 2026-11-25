// Scanner -> BOT capture lines on the Bot page (BotPanel.jsx, "Scanner → BOT capture").
// Pure wording, kept out of the panel so node:test can check it.

function count(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

// "N · reason" rows for the signals the BOT did not enter, biggest group first.
// The reasons are the BOT's own plain-language text (momx/option_track.py), e.g.
// "No buyers when it was checked (bid $0): no trade taken".
export function captureReasonLines(capture) {
  const reasons = capture && typeof capture.reasons === "object" && capture.reasons ? capture.reasons : {};
  return Object.entries(reasons)
    .map(([reason, n]) => ({ reason: String(reason), n: count(n) }))
    .filter((row) => row.n > 0 && row.reason)
    .sort((a, b) => b.n - a.n || a.reason.localeCompare(b.reason));
}

// Why an entered trade has no 0.20-delta option (the one the paper accounts buy). One
// sentence per cause, each with its own count (capture.entryLeg, momx/option_track.py
// ENTRY_LEG_GAP_KINDS). "No buyers (bid $0)" is only the trades whose bid really was $0.
const ENTRY_LEG_WORDS = [
  ["buyers", (n) => `${n === 1 ? "has" : "have"} no 0.20-delta option with buyers (bid $0)`],
  ["sellers", (n) => `${n === 1 ? "has" : "have"} no 0.20-delta option with sellers (no ask)`],
  ["unusable", (n) => `${n === 1 ? "has" : "have"} a 0.20-delta option whose quote is not usable (buyers far below sellers, or crossed)`],
  ["missing", () => "had no quote for the 0.20-delta option"],
  ["none", (n) => `${n === 1 ? "has" : "have"} no strike under 0.20 delta on that expiry`],
];

// [{ kind, n, text }] in the fixed order above; [] when there are none or the server is older.
export function captureEntryLegLines(capture) {
  const counts = capture && typeof capture.entryLeg === "object" && capture.entryLeg ? capture.entryLeg : {};
  const lines = [];
  for (const [kind, words] of ENTRY_LEG_WORDS) {
    const n = count(counts[kind]);
    if (!n) continue;
    const trades = n === 1 ? "1 entered trade" : `${n} entered trades`;
    lines.push({ kind, n, text: `${trades} ${words(n)}, so the paper accounts skip ${n === 1 ? "it" : "them"}.` });
  }
  return lines;
}
