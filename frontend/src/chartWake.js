// Decides what a chart must do when its tab wakes (visibilitychange, pageshow,
// online) or when a periodic sanity check finds the newest candle old. The
// decision is driven by the age of the data on screen, not by how long the
// tab was hidden: a request that died while the phone slept, or a server tape
// that stalled while the tab stayed visible, both leave the same shape - a
// price that ticks and candles that never extend - so both must be caught by
// the same rule. Pure so it can be unit-tested without a browser.

import { MAX_LIVE_BAR_GAP_SECONDS } from "./chartStreamBars.js";
import { marketSessionState } from "./marketSession.js";

// One forming minute plus the 30s REST reconcile slack. Anything older means
// the reconcile itself is not landing and the tab must act.
export const CHART_STALE_AFTER_MS = 90_000;
// A request older than this cannot still be running; its socket died with the
// suspension and its `finally` will never clear the in-flight flag.
export const CHART_REQUEST_ABANDON_MS = 30_000;

export function chartBarAgeMs({ nowMs, latestBarTime }) {
  const barTime = Number(latestBarTime) || 0;
  if (!barTime) return Infinity;
  return Math.max(0, Number(nowMs) - barTime * 1000);
}

/**
 * @returns {{ stale: boolean, mode: "none"|"delta"|"full", abandonRequest: boolean, reconnectStream: boolean }}
 *  - `delta`: the newest candle is behind but inside the live tick gate, so a
 *    tail fetch (`loadChart(true)`) brings it current and ticks will extend it.
 *  - `full`: the gap is past MAX_LIVE_BAR_GAP_SECONDS, where stream ticks are
 *    rejected until a whole tape lands, so only `loadChart(true, true)` heals.
 */
export function chartWakeDecision({
  nowMs,
  latestBarTime,
  requestInFlight = false,
  requestStartedAt = 0,
} = {}) {
  const barAgeMs = chartBarAgeMs({ nowMs, latestBarTime });
  const stale = barAgeMs > CHART_STALE_AFTER_MS;
  const abandonRequest = Boolean(requestInFlight)
    && Number(requestStartedAt) > 0
    && Number(nowMs) - Number(requestStartedAt) > CHART_REQUEST_ABANDON_MS;
  const beyondTickGate = barAgeMs > MAX_LIVE_BAR_GAP_SECONDS * 1000;
  return {
    stale,
    barAgeMs,
    mode: !stale ? "none" : beyondTickGate ? "full" : "delta",
    abandonRequest,
    reconnectStream: stale,
  };
}

// Header text for a chart whose newest candle is older than the stale window;
// null means the normal LIVE/STREAMING label applies.
export function chartStaleLabel(barAgeMs) {
  if (!Number.isFinite(barAgeMs)) return "STALE";
  if (barAgeMs <= CHART_STALE_AFTER_MS) return null;
  const minutes = Math.round(barAgeMs / 60_000);
  if (minutes < 60) return `STALE (${minutes}m)`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `STALE (${hours}h ${rest}m)` : `STALE (${hours}h)`;
}

// May a chart ask the SERVER to rebuild its history (refresh=true)? Only while
// the extended session (weekdays 04:00-20:00 ET) can add bars to that history.
// Overnight and at weekends a rebuild cannot add one candle, yet each one cost
// 41-90s of server CPU: measured 2026-09-29 23:22 CT on DIA, where a phone
// woke onto its in-memory Friday tape, asked for a rebuild on every wake and
// every 150s, and sat on Friday candles while the server already held
// Tuesday's. A plain reload returns the server tape in ~1s. Holidays count as
// open (no calendar), which only keeps the old behaviour.
export function chartServerRebuildAllowed(nowMs = Date.now()) {
  return marketSessionState(nowMs).open;
}
