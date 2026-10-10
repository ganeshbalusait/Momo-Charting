// Decisions for the chart pane's readiness poll and its /api/oi-finder-chart
// requests. Pure so the timing rules can be unit-tested without a browser.
//
// 2026-10-10: a MomX scanner click could sit on "Loading live one-minute
// candles" for minutes while the server ALREADY held the bars. Three client
// habits combined to hide them:
//   1. the readiness poll stopped as soon as the status said hasBars, then
//      called loadChart - which returned at the in-flight guard while an
//      older request was still waiting, so nobody ever asked again;
//   2. every caller joined that older request's shared promise, so even a
//      fresh request could not be issued;
//   3. that older request had a 300s time-to-first-byte budget.
// The server now acknowledges a cold symbol immediately (warming) and paints
// candles off-thread, so only the deep-tape pull still needs the long budget.

// The deferred deep-history pull (deep=true) is the one request that can
// legitimately take minutes before its first byte on a cold symbol (measured
// 2026-08-19: COIN 199s). Keep the old budget for it only.
export const OI_CHART_DEEP_REQUEST_TIMEOUT_MS = 300_000;
// A FIRST-PAINT request (the pane has never shown REST candles) is answered
// from memory or disk, or acknowledged as warming, within a few seconds. 20s
// is several times the contended p99 and caps how long a stuck request can
// hide bars. A timeout already becomes a transient warming payload, so the
// poll simply asks again.
export const OI_CHART_REQUEST_TIMEOUT_MS = 20_000;
// refresh=true folds the broker's tail in ON the request thread (candles only,
// api_server.py "refresh=true is the 'this tape is wrong' lever"). That is one
// Schwab call with its own timeouts, so it can outlast 20s on a slow broker;
// aborting it would turn every stream-down reconcile into a transport failure.
export const OI_CHART_REFRESH_REQUEST_TIMEOUT_MS = 45_000;
// Everything else - the 30s since=/delta reconciles of a pane that already
// shows candles, prefetches. Review 2026-10-10: the 20s budget used to apply
// here too. An abort only cancels the CLIENT; the server keeps building the
// answer (3-60s lock waits on a contended warm symbol), the next reconcile
// asks for the same work again, and three such aborts (~90s) put the
// "request timed out" notice over a healthy pane - on every pane of a grid
// at once. Nothing is hidden behind a slow reconcile (the candles are
// already up), so it gets the long budget back.
export const OI_CHART_RECONCILE_REQUEST_TIMEOUT_MS = 90_000;

// How long an in-flight request may hold a never-painted pane before a poll
// that saw hasBars:true is allowed to issue a second, independent request.
export const OI_CHART_FORCE_NEW_REQUEST_AFTER_MS = 3_000;
// At most this many chart requests per pane at once. One stuck request plus
// one fresh one; a contended server must not get a new request every poll.
export const OI_CHART_MAX_CONCURRENT_REQUESTS = 2;

// Poll cadence while the server says hasBars but the pane has painted nothing.
export const OI_CHART_HAS_BARS_POLL_MS = 450;
// The readiness poll only starts once a request ANSWERS (warming/historyLoading).
// A first request that never answers started no poll, so nothing could ever
// overtake it. One status check just past the force-new threshold covers that.
export const OI_CHART_FIRST_PAINT_WATCHDOG_MS = OI_CHART_FORCE_NEW_REQUEST_AFTER_MS + 500;

// firstPaint defaults to false: a caller that does not know (prefetch, the
// shared loader's other users) keeps the long budget it always had.
export function oiChartRequestTimeoutMs({ deep = false, refresh = false, firstPaint = false } = {}) {
  if (deep) return OI_CHART_DEEP_REQUEST_TIMEOUT_MS;
  if (!firstPaint) return OI_CHART_RECONCILE_REQUEST_TIMEOUT_MS;
  if (refresh) return OI_CHART_REFRESH_REQUEST_TIMEOUT_MS;
  return OI_CHART_REQUEST_TIMEOUT_MS;
}

// forceNew may skip the shared in-flight promise for a symbol only when that
// promise is itself stuck. Review 2026-10-10: skipping ANY promise let every
// pane of a 4-grid on one symbol issue its own forced request in the same
// poll window (4 extra requests where 1 was meant), each skipping the one a
// sibling had started milliseconds earlier. A young promise is joined.
export function oiChartForceNewMayJoin({ promiseStartedAt = 0, nowMs = Date.now() } = {}) {
  const startedAt = Number(promiseStartedAt) || 0;
  if (startedAt <= 0) return false;
  return Number(nowMs) - startedAt <= OI_CHART_FORCE_NEW_REQUEST_AFTER_MS;
}

// ...and a payload with bars that a sibling pane stored AFTER this pane's
// stuck request went out is newer than anything that request can return:
// use it instead of forcing a request at all.
export function oiChartForceNewCanUseCached({ cachedBarCount = 0, cachedSavedAt = 0, stuckSince = 0 } = {}) {
  const since = Number(stuckSince) || 0;
  if (since <= 0 || !(Number(cachedBarCount) > 0)) return false;
  return Number(cachedSavedAt || 0) >= since;
}

// The historyReady poll's deep (multi-MB) pull is for a pane that has
// painted. On a blank pane it raced the ordinary paint request: whichever
// lost was dropped and the deep tape re-downloaded (review 2026-10-10). The
// blank pane asks for the cheap paint; the next poll after paint pulls deep.
export function oiChartShouldPullDeepOnHistoryReady({ autoDeep = false, panePainted = false } = {}) {
  return Boolean(autoDeep && panePainted);
}

// After a hasBars status, keep polling while the pane holds no REST bars: the
// loadChart it triggers may have been swallowed by the in-flight guard, and
// nothing else would ask again until the 30s reconcile. null = stop polling
// here (loadChart owns the cadence once candles are on screen).
export function oiChartHasBarsPollDelay({ panePainted = false } = {}) {
  return panePainted ? null : OI_CHART_HAS_BARS_POLL_MS;
}

// Whether loadChart may bypass its in-flight guard AND the shared promise for
// this symbol. Only for a pane that has never painted, only when the server
// said it holds bars, and only once the current request is clearly stuck.
export function shouldForceNewOiChartRequest({
  serverHasBars = false,
  panePainted = false,
  requestInFlight = false,
  requestStartedAt = 0,
  concurrentRequests = 0,
  nowMs = Date.now(),
} = {}) {
  if (!serverHasBars || panePainted || !requestInFlight) return false;
  if (Number(concurrentRequests) >= OI_CHART_MAX_CONCURRENT_REQUESTS) return false;
  const startedAt = Number(requestStartedAt) || 0;
  if (startedAt <= 0) return false;
  return Number(nowMs) - startedAt > OI_CHART_FORCE_NEW_REQUEST_AFTER_MS;
}

// Two requests can now overlap for one pane. A response may paint only if no
// LATER-issued request has already painted: the older request's server
// snapshot can predate the newer one's, and it must never roll the tape back.
export function oiChartResponseMayApply({ requestSeq, appliedSeq = 0 } = {}) {
  return Number(requestSeq) > Number(appliedSeq || 0);
}

// A response with no bars from a request that has since been superseded says
// nothing new (the newer request will report its own warming/failure), so it
// must not bump the failure streak or rewrite the pane's notice.
export function oiChartEmptyResponseIsStale({ requestSeq, latestRequestSeq = 0 } = {}) {
  return Number(requestSeq) < Number(latestRequestSeq || 0);
}

// Same rule for the module-wide payload cache: a request may store its
// payload only when no later-issued request for that symbol already stored one.
export function oiChartCachedPayloadIsNewer(heldGeneration, generation) {
  return Number(heldGeneration || 0) > Number(generation || 0);
}

// Where a chart error is shown. Once candles are on screen the Lightweight
// Charts host must stay mounted: its create effect keys on the SHAPE of the
// pane (symbol, has-bars), so swapping the host for a notice and back left the
// pane permanently blank after a transport notice cleared.
export function oiChartErrorPlacement({ error = "", barCount = 0 } = {}) {
  if (!error) return "none";
  return Number(barCount) > 0 ? "overlay" : "replace";
}
