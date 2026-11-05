// BOT truth check (2026-11-04): plain-language lines for the card that puts the
// BOT's virtual mid-price trades next to what the real Alpaca paper account got
// (GET /api/momx-scanner/bot-truth, momx/truth_check.py).

const ACCOUNT_LABELS = { paper3: "Paper 3", paper5: "Paper 5" };

export function truthAccountLabel(account) {
  return ACCOUNT_LABELS[account] || String(account || "");
}

export function truthPct(v) {
  if (typeof v !== "number" || !Number.isFinite(v)) return "-";
  return (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(1) + "%";
}

export function truthMoney(v) {
  if (typeof v !== "number" || !Number.isFinite(v)) return "-";
  const sign = v > 0 ? "+" : v < 0 ? "−" : "";
  return sign + "$" + Math.round(Math.abs(v)).toLocaleString("en-US");
}

/** One sentence a trader reads first: how many mid orders filled, and the gap. */
export function truthHeadline(acct) {
  if (!acct || !acct.orders) return "No paper orders yet.";
  const decided = (acct.filled || 0) + (acct.missed || 0);
  const fill = decided ? `Your mid orders filled ${acct.filled} of ${decided} (${Math.round((acct.filled / decided) * 100)}%).` : "No order has filled or missed yet.";
  const m = acct.matched || {};
  if (!m.n) return fill + " No filled trade has closed yet to compare.";
  return `${fill} On the ${m.n} trades that filled and closed, the BOT says ${truthPct(m.botRoi)}, your paper account got ${truthPct(m.paperRoi)}.`;
}

/** How far the BOT's number is from the paper number, in plain words. */
export function truthGapLabel(botRoi, paperRoi) {
  if (typeof botRoi !== "number" || typeof paperRoi !== "number") return "Not enough closed trades";
  const gap = Math.round((botRoi - paperRoi) * 10) / 10;
  if (Math.abs(gap) < 1) return "BOT matches paper";
  return gap > 0 ? `BOT looks ${gap.toFixed(1)} pts better than real` : `BOT looks ${Math.abs(gap).toFixed(1)} pts worse than real`;
}
