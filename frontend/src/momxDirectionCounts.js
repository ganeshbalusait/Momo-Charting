// BULL n / BEAR n pills on the Mag7 scanner (asked 2026-11-05: "Add bull/bear
// number how many ticker in scanner ... see how news show"). Each number is
// the count of Mag7 tickers that pass that side's scan - the same test as
// MATCHES - so the side on screen reads its own board and the other side is
// read from its board (a cache read on the worker, never a rebuild).

export const DIRECTION_COUNT_LISTS = Object.freeze(["Mag7"]);

export function showsDirectionCounts(listName) {
  return DIRECTION_COUNT_LISTS.includes(listName);
}

// Scan matches in a board payload, rows AND rest (a match past the row cap
// arrives in rest). Null when there is no usable board, so the pill can show
// "–" instead of a false 0 while the first read is in flight.
export function countScanMatches(payload) {
  if (!payload || typeof payload !== "object") return null;
  const rows = [...(Array.isArray(payload.rows) ? payload.rows : []), ...(Array.isArray(payload.rest) ? payload.rest : [])];
  if (!rows.length && payload.warming) return null;
  return rows.reduce((total, row) => (row && row.scanPass ? total + 1 : total), 0);
}

// The URL that reads the OTHER side's board for a list.
export function otherDirectionUrl(endpoint, listName, direction) {
  const other = direction === "bear" ? "" : "&dir=bear";
  return endpoint + "?list=" + encodeURIComponent(listName) + other;
}

// {bull, bear}: the side on screen uses its own match count, the other side
// the count read from its board. An other-side board for a different list
// (a stale read after a switch) is ignored.
export function directionCounts({ direction, currentMatches, otherPayload, listName }) {
  const usable = otherPayload && (!otherPayload.list || otherPayload.list === listName) ? otherPayload : null;
  const other = countScanMatches(usable);
  const current = Number.isFinite(currentMatches) ? currentMatches : null;
  return direction === "bear" ? { bull: other, bear: current } : { bull: current, bear: other };
}
