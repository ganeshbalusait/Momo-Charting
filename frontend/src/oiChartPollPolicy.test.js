import test from "node:test";
import assert from "node:assert/strict";

import {
  OI_CHART_DEEP_REQUEST_TIMEOUT_MS,
  OI_CHART_FIRST_PAINT_WATCHDOG_MS,
  OI_CHART_FORCE_NEW_REQUEST_AFTER_MS,
  OI_CHART_HAS_BARS_POLL_MS,
  OI_CHART_RECONCILE_REQUEST_TIMEOUT_MS,
  OI_CHART_REFRESH_REQUEST_TIMEOUT_MS,
  OI_CHART_REQUEST_TIMEOUT_MS,
  oiChartCachedPayloadIsNewer,
  oiChartEmptyResponseIsStale,
  oiChartErrorPlacement,
  oiChartForceNewCanUseCached,
  oiChartForceNewMayJoin,
  oiChartHasBarsPollDelay,
  oiChartRequestTimeoutMs,
  oiChartResponseMayApply,
  oiChartShouldPullDeepOnHistoryReady,
  shouldForceNewOiChartRequest,
} from "./oiChartPollPolicy.js";

test("only the deep tape pull keeps the 300s time-to-first-byte budget", () => {
  assert.equal(OI_CHART_DEEP_REQUEST_TIMEOUT_MS, 300_000);
  assert.equal(oiChartRequestTimeoutMs({ deep: true }), 300_000);
  assert.equal(oiChartRequestTimeoutMs({ deep: true, refresh: true, firstPaint: true }), 300_000);
  assert.equal(oiChartRequestTimeoutMs({ firstPaint: true }), OI_CHART_REQUEST_TIMEOUT_MS);
  assert.equal(OI_CHART_REQUEST_TIMEOUT_MS, 20_000);
  // refresh=true folds a broker tail in on the request thread.
  assert.equal(oiChartRequestTimeoutMs({ refresh: true, firstPaint: true }), OI_CHART_REFRESH_REQUEST_TIMEOUT_MS);
  assert.ok(OI_CHART_REFRESH_REQUEST_TIMEOUT_MS < OI_CHART_DEEP_REQUEST_TIMEOUT_MS);
});

test("the short budget is for first paint only; a painted pane's reconcile keeps a long one", () => {
  // Review 2026-10-10: a 20s abort of a delta reconcile the server would have
  // answered at 25s threw the work away, re-requested it, and after three
  // put a timeout notice over a healthy pane.
  assert.equal(oiChartRequestTimeoutMs(), OI_CHART_RECONCILE_REQUEST_TIMEOUT_MS);
  assert.equal(oiChartRequestTimeoutMs({ firstPaint: false }), OI_CHART_RECONCILE_REQUEST_TIMEOUT_MS);
  assert.equal(oiChartRequestTimeoutMs({ refresh: true }), OI_CHART_RECONCILE_REQUEST_TIMEOUT_MS);
  assert.ok(OI_CHART_RECONCILE_REQUEST_TIMEOUT_MS >= 60_000);
  assert.ok(OI_CHART_RECONCILE_REQUEST_TIMEOUT_MS > OI_CHART_REFRESH_REQUEST_TIMEOUT_MS);
});

test("forceNew joins a sibling's young promise and skips only a stuck one", () => {
  const nowMs = 2_000_000;
  // Pane A forced P2 milliseconds ago: panes B-D join it, not P3/P4/P5.
  assert.equal(oiChartForceNewMayJoin({ promiseStartedAt: nowMs - 5, nowMs }), true);
  assert.equal(oiChartForceNewMayJoin({
    promiseStartedAt: nowMs - OI_CHART_FORCE_NEW_REQUEST_AFTER_MS, nowMs,
  }), true);
  // The stuck P1: skipped.
  assert.equal(oiChartForceNewMayJoin({
    promiseStartedAt: nowMs - OI_CHART_FORCE_NEW_REQUEST_AFTER_MS - 1, nowMs,
  }), false);
  // An entry with no start stamp predates this rule: treat it as stuck.
  assert.equal(oiChartForceNewMayJoin({ promiseStartedAt: 0, nowMs }), false);
});

test("forceNew uses bars a sibling pane stored after this pane's request went stuck", () => {
  const stuckSince = 3_000_000;
  assert.equal(oiChartForceNewCanUseCached({ cachedBarCount: 900, cachedSavedAt: stuckSince + 10, stuckSince }), true);
  // Stored BEFORE the stuck request went out: this pane already judged it stale.
  assert.equal(oiChartForceNewCanUseCached({ cachedBarCount: 900, cachedSavedAt: stuckSince - 10, stuckSince }), false);
  assert.equal(oiChartForceNewCanUseCached({ cachedBarCount: 0, cachedSavedAt: stuckSince + 10, stuckSince }), false);
  assert.equal(oiChartForceNewCanUseCached({ cachedBarCount: 900, cachedSavedAt: stuckSince + 10, stuckSince: 0 }), false);
});

test("the historyReady deep pull waits until the pane has painted", () => {
  // Review 2026-10-10: on a blank pane it raced the ordinary paint request and
  // the loser's multi-MB tape was downloaded a second time.
  assert.equal(oiChartShouldPullDeepOnHistoryReady({ autoDeep: true, panePainted: false }), false);
  assert.equal(oiChartShouldPullDeepOnHistoryReady({ autoDeep: true, panePainted: true }), true);
  // Phones (autoDeep false) never auto-pull, painted or not.
  assert.equal(oiChartShouldPullDeepOnHistoryReady({ autoDeep: false, panePainted: true }), false);
});

test("a hasBars status keeps polling until the pane has painted", () => {
  // The swallowed-signal bug: loadChart returned at the in-flight guard and
  // nothing rescheduled the poll.
  assert.equal(oiChartHasBarsPollDelay({ panePainted: false }), OI_CHART_HAS_BARS_POLL_MS);
  assert.equal(oiChartHasBarsPollDelay(), OI_CHART_HAS_BARS_POLL_MS);
  assert.equal(oiChartHasBarsPollDelay({ panePainted: true }), null);
});

test("a never-painted pane overtakes a request stuck for more than 3s", () => {
  const nowMs = 1_000_000;
  const stuck = {
    serverHasBars: true,
    panePainted: false,
    requestInFlight: true,
    requestStartedAt: nowMs - OI_CHART_FORCE_NEW_REQUEST_AFTER_MS - 1,
    concurrentRequests: 1,
    nowMs,
  };
  assert.equal(shouldForceNewOiChartRequest(stuck), true);
  // Young request: wait for it.
  assert.equal(shouldForceNewOiChartRequest({ ...stuck, requestStartedAt: nowMs - 2_000 }), false);
  // Exactly at the threshold is still young.
  assert.equal(shouldForceNewOiChartRequest({
    ...stuck, requestStartedAt: nowMs - OI_CHART_FORCE_NEW_REQUEST_AFTER_MS,
  }), false);
  // Painted pane: the ordinary in-flight guard applies.
  assert.equal(shouldForceNewOiChartRequest({ ...stuck, panePainted: true }), false);
  // No server evidence of bars: a second request would only warm again.
  assert.equal(shouldForceNewOiChartRequest({ ...stuck, serverHasBars: false }), false);
  // Nothing in flight: a normal request goes out anyway.
  assert.equal(shouldForceNewOiChartRequest({ ...stuck, requestInFlight: false }), false);
  // A contended server never gets more than two requests per pane.
  assert.equal(shouldForceNewOiChartRequest({ ...stuck, concurrentRequests: 2 }), false);
  // Unknown start time: do not guess.
  assert.equal(shouldForceNewOiChartRequest({ ...stuck, requestStartedAt: 0 }), false);
});

test("an older request resolving last cannot roll newer bars back", () => {
  // Request 1 stuck, request 2 (forceNew) painted first.
  assert.equal(oiChartResponseMayApply({ requestSeq: 2, appliedSeq: 0 }), true);
  assert.equal(oiChartResponseMayApply({ requestSeq: 1, appliedSeq: 2 }), false);
  // The stuck one may still paint when the newer one has not.
  assert.equal(oiChartResponseMayApply({ requestSeq: 1, appliedSeq: 0 }), true);
  // Its empty/failed answer is stale once a newer request exists.
  assert.equal(oiChartEmptyResponseIsStale({ requestSeq: 1, latestRequestSeq: 2 }), true);
  assert.equal(oiChartEmptyResponseIsStale({ requestSeq: 2, latestRequestSeq: 2 }), false);
  // Shared cache: a held payload from a later generation wins.
  assert.equal(oiChartCachedPayloadIsNewer(5, 4), true);
  assert.equal(oiChartCachedPayloadIsNewer(4, 5), false);
  assert.equal(oiChartCachedPayloadIsNewer(undefined, 1), false);
});

test("a chart error overlays painted candles instead of unmounting the chart", () => {
  assert.equal(oiChartErrorPlacement({ error: "", barCount: 0 }), "none");
  assert.equal(oiChartErrorPlacement({ error: "", barCount: 400 }), "none");
  assert.equal(oiChartErrorPlacement({ error: "API is unavailable", barCount: 0 }), "replace");
  assert.equal(oiChartErrorPlacement({ error: "API is unavailable", barCount: 1 }), "overlay");
});

test("the first-paint watchdog fires only once a stuck request may be overtaken", () => {
  assert.ok(OI_CHART_FIRST_PAINT_WATCHDOG_MS > OI_CHART_FORCE_NEW_REQUEST_AFTER_MS);
  const nowMs = 50_000;
  assert.equal(shouldForceNewOiChartRequest({
    serverHasBars: true,
    requestInFlight: true,
    requestStartedAt: nowMs - OI_CHART_FIRST_PAINT_WATCHDOG_MS,
    concurrentRequests: 1,
    nowMs,
  }), true);
});
