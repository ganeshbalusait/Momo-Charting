const number = v => v == null || v === "" || !Number.isFinite(Number(v)) ? null : Number(v);

// Never substitute requested quantity for filled quantity. Legacy closed rows
// can recover it from their recorded sell fills, including partial entries.
export function paperMetrics(row, now = Date.now()) {
  const buy = number(row.buyFill);
  const sells = row.sells || [];
  const sold = sells.reduce((n, f) => n + (number(f.qty) || 0), 0);
  const held = row.status === "closed" ? 0 : number(row.held);
  const filled = number(row.buyFilledQty) ?? (buy != null && held != null ? sold + held : row.status === "closed" && sold > 0 ? sold : null);
  const validFills = sells.every(f => number(f.price) != null && number(f.price) >= 0 && number(f.qty) > 0);
  const completeClosed = row.status !== "closed" || (number(row.held) || 0) === 0 && (number(row.buyFilledQty) == null || Math.abs(sold - Number(row.buyFilledQty)) < 1e-8);
  const realized = buy > 0 && completeClosed && (sells.length > 0 || row.status !== "closed") && validFills
    ? sells.reduce((n, f) => n + (Number(f.price) - buy) * Number(f.qty) * 100, 0)
    : row.status === "closed" && sells.length === 0 ? number(row.pl) : null;
  const age = (now - Date.parse(row.markAt)) / 1000;
  const mark = number(row.markMid);
  const open = held === 0 ? 0 : held > 0 && buy != null && mark != null && mark >= 0 && age >= -5 && age <= 120
    ? (mark - buy) * held * 100 : null;
  const cost = buy > 0 && filled > 0 ? buy * filled * 100 : null;
  return { filled, held, sold, realized, open, cost, roi: row.status === "closed" && cost != null && realized != null ? realized / cost * 100 : null };
}

export function paperTotals(rows, now = Date.now()) {
  const values = rows.map(row => ({ row, ...paperMetrics(row, now) }));
  const closed = values.filter(v => v.row.status === "closed" && v.realized != null);
  const unpricedClosed = values.filter(v => v.row.status === "closed" && v.realized == null).length;
  const incompleteRealized = values.some(v => v.realized == null && (v.row.status === "closed" || v.row.sells?.length));
  const open = values.filter(v => v.held > 0);
  const costKnown = closed.every(v => v.cost != null);
  const cost = costKnown ? closed.reduce((n, v) => n + v.cost, 0) : null;
  const closedPl = closed.reduce((n, v) => n + v.realized, 0);
  return { closed, cost, unpricedClosed, roi: !unpricedClosed && cost > 0 ? closedPl / cost * 100 : null,
    realized: incompleteRealized ? null : values.reduce((n, v) => n + (v.realized || 0), 0),
    open: open.every(v => v.open != null) ? open.reduce((n, v) => n + v.open, 0) : null,
    unpriced: open.filter(v => v.open == null).length,
    wins: closed.filter(v => v.realized > 0).length, losses: closed.filter(v => v.realized < 0).length };
}

export function reviewLabel(team, key, row) {
  if (!team) return "Review status unavailable";
  const state = team.tradeStates?.[key];
  if (state) return ({ complete: "Reviewed · batch", running: "Reviewing · batch", pending: "Queued", failed: "Review failed" })[state] || "Unknown";
  if (row.status === "missed" || row.status === "ai_skipped") return "Not eligible · no fill";
  if (row.status !== "closed") return "Awaiting close";
  return team.enabled ? "Not queued" : "Reviews paused";
}

export function paperTime(value) {
  const date = new Date(value);
  return !value || !Number.isFinite(date.getTime()) ? "—" : date.toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false });
}
