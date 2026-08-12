import test from "node:test";
import assert from "node:assert/strict";

import {
  canCommitOiFinderRequest,
  canContinueOiFinderRequest,
  createOiFinderRequestState,
  currentOiFinderRequestOwner,
  hasUsableOiFinderChain,
  mergeOiFinderFeedResponse,
  nextOiChainPollDelay,
  oiChartClientCacheDecision,
  oiFinderFailureFeed,
  oiFinderInitialLoadPlan,
  oiFinderWarmingFeed,
  resolveOiFinderChainPrefetchSymbol,
  resolveOiChartPrefetchSymbol,
  selectOiFinderRequestTarget,
  settleOiFinderRequest,
  startOiFinderRequest,
} from "./oiFinderRequestPolicy.js";

test("selecting ticker B invalidates ticker A responses and continuations immediately", () => {
  let state = createOiFinderRequestState("AAPL");
  const ownerA = currentOiFinderRequestOwner(state);
  const startedA = startOiFinderRequest(state, {
    owner: ownerA,
    intent: "required",
    stage: "compact",
  });
  state = startedA.state;

  state = selectOiFinderRequestTarget(state, "MSFT");

  assert.equal(canCommitOiFinderRequest(state, startedA.request), false);
  assert.equal(canContinueOiFinderRequest(state, ownerA), false);
  assert.equal(startOiFinderRequest(state, {
    owner: ownerA,
    intent: "required",
    stage: "full",
  }).request, null);
});

test("replaying the same ticker plan advances its generation", () => {
  let state = createOiFinderRequestState("META");
  const firstOwner = currentOiFinderRequestOwner(state);
  const first = startOiFinderRequest(state, {
    owner: firstOwner,
    intent: "required",
    stage: "compact",
  });

  state = selectOiFinderRequestTarget(first.state, "META");
  const replayOwner = currentOiFinderRequestOwner(state);

  assert.equal(replayOwner.symbol, firstOwner.symbol);
  assert.equal(replayOwner.epoch, firstOwner.epoch + 1);
  assert.equal(canContinueOiFinderRequest(state, firstOwner), false);
  assert.equal(canCommitOiFinderRequest(state, first.request), false);
});

test("required compact-to-full enrichment starts despite an old ticker request", () => {
  let state = createOiFinderRequestState("AAPL");
  const ownerA = currentOiFinderRequestOwner(state);
  const startedA = startOiFinderRequest(state, {
    owner: ownerA,
    intent: "required",
    stage: "compact",
  });
  state = selectOiFinderRequestTarget(startedA.state, "MSFT");

  const ownerB = currentOiFinderRequestOwner(state);
  const compactB = startOiFinderRequest(state, {
    owner: ownerB,
    intent: "required",
    stage: "compact",
  });
  state = settleOiFinderRequest(compactB.state, compactB.request);
  assert.equal(Object.keys(state.inFlight).length, 1);
  assert.equal(state.inFlight[startedA.request.id].symbol, "AAPL");

  const fullB = startOiFinderRequest(state, {
    owner: ownerB,
    intent: "required",
    stage: "full",
  });
  state = fullB.state;

  assert.ok(fullB.request);
  assert.equal(fullB.request.symbol, "MSFT");
  assert.equal(fullB.request.stage, "full");
  assert.equal(canCommitOiFinderRequest(state, fullB.request), true);
  assert.equal(canCommitOiFinderRequest(state, startedA.request), false);
});

test("optional timer polls skip while any request remains in flight", () => {
  let state = createOiFinderRequestState("META");
  const owner = currentOiFinderRequestOwner(state);
  const required = startOiFinderRequest(state, {
    owner,
    intent: "required",
    stage: "compact",
  });
  state = required.state;

  assert.equal(startOiFinderRequest(state, {
    owner,
    intent: "poll",
    stage: "full",
  }).request, null);

  state = settleOiFinderRequest(state, required.request);
  const poll = startOiFinderRequest(state, {
    owner,
    intent: "poll",
    stage: "full",
  });
  assert.ok(poll.request);
  assert.equal(poll.request.intent, "poll");
});

test("OI Finder paints chart and compact chain together before background analytics", () => {
  assert.deepEqual(oiFinderInitialLoadPlan("OI Finder"), {
    enabled: true,
    loadChart: true,
    loadCompactChain: true,
    enrichFinder: true,
  });
  assert.deepEqual(oiFinderInitialLoadPlan("Charts & OI"), {
    enabled: true,
    loadChart: true,
    loadCompactChain: true,
    enrichFinder: false,
  });
  assert.equal(oiFinderInitialLoadPlan("OI Finder", "chart").enrichFinder, false);
  assert.equal(oiFinderInitialLoadPlan("OI Finder", "mag7").enabled, false);
});

test("option-chain warm-up only uses known ticker suggestions", () => {
  const symbols = ["AAPL", "AMZN", "NVDA"];
  assert.equal(resolveOiFinderChainPrefetchSymbol("NV", symbols, "AAPL"), "NVDA");
  assert.equal(resolveOiFinderChainPrefetchSymbol("MSFT", symbols, "AAPL"), null);
});

test("compact chain responses preserve already-loaded Finder analytics", () => {
  const current = {
    symbol: "META",
    currentAtm: { expiry: "2026-08-07" },
    callRows: [{ strike: 600 }],
    dailyLiquidityHeatmap: { call: [{ strike: 600 }] },
  };
  const merged = mergeOiFinderFeedResponse(current, {
    symbol: "META",
    currentAtm: { expiry: "2026-08-14" },
    callRows: [{ strike: 610 }],
    errors: [],
  }, { compact: true });

  assert.equal(merged.currentAtm.expiry, "2026-08-14");
  assert.equal(merged.callRows[0].strike, 610);
  assert.deepEqual(merged.dailyLiquidityHeatmap, current.dailyLiquidityHeatmap);
});

test("a compact poll never blanks analytics the enrich pass loaded", () => {
  const enriched = {
    symbol: "META",
    currentAtm: { expiry: "2026-08-14" },
    selectedExpiryChainRows: [{ strike: 590 }],
    callRows: [{ strike: 600 }],
    putRows: [{ strike: 580 }],
    volumeMomentum: { atmCallVolumeDelta: 1200 },
    dailyLiquidityHeatmap: { days: ["2026-08-10"] },
    unusualOtmActivity: { rows: [{ strike: 700 }] },
    errors: [],
  };
  // The compact endpoint ships analytics keys as EMPTY values (it never
  // computes them). They must not clobber the enriched data on screen.
  const compactPoll = {
    symbol: "META",
    currentAtm: { expiry: "2026-08-14" },
    selectedExpiryChainRows: [{ strike: 590, bid: 1.2 }],
    callRows: [{ strike: 600 }],
    putRows: [{ strike: 580 }],
    volumeMomentum: {},
    dailyLiquidityHeatmap: {},
    unusualOtmActivity: {},
    errors: [],
  };
  const merged = mergeOiFinderFeedResponse(enriched, compactPoll, { compact: true });
  assert.equal(merged.volumeMomentum.atmCallVolumeDelta, 1200);
  assert.deepEqual(merged.dailyLiquidityHeatmap.days, ["2026-08-10"]);
  assert.equal(merged.unusualOtmActivity.rows.length, 1);
  // Non-analytics fields still update from the compact poll.
  assert.equal(merged.selectedExpiryChainRows[0].bid, 1.2);

  // A compact poll carrying REAL analytics values may still overwrite.
  const withData = { ...compactPoll, volumeMomentum: { atmCallVolumeDelta: 55 } };
  assert.equal(
    mergeOiFinderFeedResponse(enriched, withData, { compact: true }).volumeMomentum.atmCallVolumeDelta,
    55,
  );

  // A FULL response stays authoritative: empty means genuinely cleared.
  const fullClear = { ...compactPoll };
  assert.deepEqual(mergeOiFinderFeedResponse(enriched, fullClear).volumeMomentum, {});
});

test("an identical chain poll keeps the current feed reference", () => {
  const current = {
    symbol: "META",
    live: false,
    cached: true,
    currentAtm: { expiry: "2026-08-07" },
    selectedExpiryChainRows: [{ strike: 590, openInterest: 1200 }],
    callRows: [{ strike: 600 }],
    putRows: [{ strike: 580 }],
    errors: [],
  };
  const identicalCopy = JSON.parse(JSON.stringify(current));
  assert.equal(mergeOiFinderFeedResponse(current, identicalCopy), current);
  assert.equal(mergeOiFinderFeedResponse(current, identicalCopy, { compact: true }), current);

  const changed = JSON.parse(JSON.stringify(current));
  changed.callRows[0].strike = 605;
  assert.notEqual(mergeOiFinderFeedResponse(current, changed), current);
  assert.equal(mergeOiFinderFeedResponse(current, changed).callRows[0].strike, 605);
});

test("failed refreshes keep a usable same-symbol option chain on screen", () => {
  const current = {
    symbol: "META",
    live: true,
    currentAtm: { expiry: "2026-08-07" },
    selectedExpiryChainRows: [{ strike: 590 }],
    callRows: [{ strike: 600 }],
    putRows: [{ strike: 580 }],
    errors: [],
  };
  const message = "The live option chain timed out.";
  const failed = oiFinderFailureFeed(current, "META", message);
  const serverFailed = mergeOiFinderFeedResponse(current, {
    symbol: "META",
    live: false,
    currentAtm: {},
    callRows: [],
    putRows: [],
    errors: [{ error: message }],
  });

  assert.equal(hasUsableOiFinderChain(failed), true);
  assert.equal(failed.callRows[0].strike, 600);
  assert.equal(failed.stale, true);
  assert.equal(serverFailed.putRows[0].strike, 580);
  assert.equal(serverFailed.errors[0].error, message);
});

test("a delayed compact request keeps the last usable chain visible and polls", () => {
  const current = {
    symbol: "META",
    live: true,
    currentAtm: { expiry: "2026-08-07" },
    selectedExpiryChainRows: [{ strike: 590 }],
    callRows: [{ strike: 600 }],
    putRows: [{ strike: 580 }],
    errors: [],
  };
  const warming = oiFinderWarmingFeed("META");
  const merged = mergeOiFinderFeedResponse(current, warming, { compact: true });

  assert.equal(warming.warming, true);
  assert.equal(warming.errors.length, 0);
  assert.equal(hasUsableOiFinderChain(merged), true);
  assert.equal(merged.callRows[0].strike, 600);
  assert.equal(merged.warming, true);
  assert.equal(merged.refreshing, true);
});

test("a failed new ticker request never leaves the previous ticker visible", () => {
  const failed = oiFinderFailureFeed({
    symbol: "AAPL",
    currentAtm: { expiry: "2026-08-07" },
    callRows: [{ strike: 220 }],
  }, "META", "Timed out");

  assert.equal(failed.symbol, "META");
  assert.deepEqual(failed.callRows, []);
  assert.equal(hasUsableOiFinderChain(failed), false);
});

test("recent chart history paints immediately without another request", () => {
  assert.deepEqual(
    oiChartClientCacheDecision(
      { payload: { symbol: "MSFT" }, savedAt: 90_000 },
      { now: 100_000, freshMs: 30_000, maxStaleMs: 900_000 },
    ),
    { ageMs: 10_000, useCached: true, revalidate: false },
  );
});

test("stale chart history paints first and refreshes in the background", () => {
  assert.deepEqual(
    oiChartClientCacheDecision(
      { payload: { symbol: "MSFT" }, savedAt: 60_000 },
      { now: 100_000, freshMs: 30_000, maxStaleMs: 900_000 },
    ),
    { ageMs: 40_000, useCached: true, revalidate: true },
  );
});

test("expired or explicitly bypassed chart history waits for a fresh response", () => {
  assert.equal(
    oiChartClientCacheDecision(
      { payload: { symbol: "MSFT" }, savedAt: 0 },
      { now: 900_001, freshMs: 30_000, maxStaleMs: 900_000 },
    ).useCached,
    false,
  );
  assert.equal(
    oiChartClientCacheDecision(
      { payload: { symbol: "MSFT" }, savedAt: 99_000 },
      { now: 100_000, bypassCache: true },
    ).useCached,
    false,
  );
});

test("ticker prefetch waits for an exact or unique saved-watchlist symbol", () => {
  const options = ["AAPL", "AMZN", "META", "MSFT", "NVDA", "TSLA"];
  assert.equal(resolveOiChartPrefetchSymbol("M", options, "AAPL"), null);
  assert.equal(resolveOiChartPrefetchSymbol("MS", options, "AAPL"), "MSFT");
  assert.equal(resolveOiChartPrefetchSymbol("MSF", options, "AAPL"), "MSFT");
  assert.equal(resolveOiChartPrefetchSymbol("NVDA", options, "AAPL"), "NVDA");
  assert.equal(resolveOiChartPrefetchSymbol("AAPL", options, "AAPL"), null);
});

test("ticker prefetch still supports a custom symbol after meaningful input", () => {
  assert.equal(resolveOiChartPrefetchSymbol("XY", ["AAPL"], "AAPL"), null);
  assert.equal(resolveOiChartPrefetchSymbol("XYZ", ["AAPL"], "AAPL"), "XYZ");
});

test("nextOiChainPollDelay keeps the healthy cadence", () => {
  assert.equal(nextOiChainPollDelay({ ready: true }), 15_000);
  assert.equal(nextOiChainPollDelay({}), 15_000);
  assert.equal(nextOiChainPollDelay({ ready: false, warming: true }), 700);
});

test("nextOiChainPollDelay doubles on consecutive failures and caps at 120s", () => {
  assert.equal(nextOiChainPollDelay({ failed: true, failureCount: 1 }), 30_000);
  assert.equal(nextOiChainPollDelay({ failed: true, failureCount: 2 }), 60_000);
  assert.equal(nextOiChainPollDelay({ failed: true, failureCount: 3 }), 120_000);
  assert.equal(nextOiChainPollDelay({ failed: true, failureCount: 9 }), 120_000);
});

test("nextOiChainPollDelay enforces a 45s floor when rate limited", () => {
  assert.equal(nextOiChainPollDelay({ failed: true, rateLimited: true, failureCount: 1 }), 45_000);
  assert.equal(nextOiChainPollDelay({ failed: true, rateLimited: true, failureCount: 3 }), 120_000);
});

test("nextOiChainPollDelay ignores stale failure counts once a poll succeeds", () => {
  assert.equal(nextOiChainPollDelay({ ready: true, failed: false, failureCount: 0 }), 15_000);
});
