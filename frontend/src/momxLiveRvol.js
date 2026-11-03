// LIVE RVOL (2026-11-03, "real time same as TOS"): the scanner board is ~2.5
// minutes old by the time a Watchlist build lands, while TOS rescores the
// forming bar every tick. The worker rescores the forming bar from the Schwab
// stream (momx/live_rvol.py, his exact column formula) for the rows on screen;
// this file shapes what the RVOL cells SHOW. Display only: filters, Confirm,
// alerts and sorting keep reading the board's own values.

export const MOMX_LIVE_RVOL_ENDPOINT = "/api/momx-scanner/live-rvol";
export const MOMX_LIVE_RVOL_MS = 5000;
// A request that has not answered by now is abandoned (it must never stop the poll).
export const MOMX_LIVE_RVOL_TIMEOUT_MS = 4000;
// Without a good poll for this long the live numbers are dropped and the board shows.
export const MOMX_LIVE_RVOL_MAX_AGE_MS = 30000;

// Keep each symbol's cells object when its content did not change, so a row
// whose live RVOL is unchanged keeps the same prop (its memo skips the render).
export function reuseLiveCells(held, next) {
  const out = {};
  let changed = false;
  for (const [symbol, cells] of Object.entries(next || {})) {
    const old = held && held[symbol];
    if (old && JSON.stringify(old) === JSON.stringify(cells)) out[symbol] = old;
    else { out[symbol] = cells; changed = true; }
  }
  if (!changed && held && Object.keys(held).length === Object.keys(out).length) return held;
  return out;
}

// The cell to draw: the live one for an RVOL column when the worker has it,
// else the board's. The board's saturation note (xAvg) and pace belong to the
// board's number, so they are not carried onto a live value.
export function liveRvolCell(cell, column, liveCells) {
  if (!column || column.section !== "rvol" || !liveCells) return cell;
  const live = liveCells[column.tf];
  if (!live || live.value == null) return cell;
  const { xAvg, pace, ...base } = cell || {};
  return { ...base, value: live.value, bg: live.bg, fg: live.fg, barAt: live.barAt, live: true };
}
