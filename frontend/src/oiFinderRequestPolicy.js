function normalizeOiFinderRequestSymbol(symbol) {
  return String(symbol || "").trim().toUpperCase();
}

function normalizedOiFinderRequestState(state) {
  return {
    symbol: normalizeOiFinderRequestSymbol(state?.symbol),
    epoch: Math.max(0, Number(state?.epoch) || 0),
    nextRequestId: Math.max(1, Number(state?.nextRequestId) || 1),
    latestStartedId: Math.max(0, Number(state?.latestStartedId) || 0),
    inFlight: state?.inFlight && typeof state.inFlight === "object"
      ? state.inFlight
      : {},
  };
}

export function createOiFinderRequestState(symbol = "") {
  return {
    symbol: normalizeOiFinderRequestSymbol(symbol),
    epoch: 0,
    nextRequestId: 1,
    latestStartedId: 0,
    inFlight: {},
  };
}

export function selectOiFinderRequestTarget(state, symbol) {
  const current = normalizedOiFinderRequestState(state);
  return {
    ...current,
    symbol: normalizeOiFinderRequestSymbol(symbol),
    epoch: current.epoch + 1,
  };
}

export function currentOiFinderRequestOwner(state) {
  const current = normalizedOiFinderRequestState(state);
  return { symbol: current.symbol, epoch: current.epoch };
}

export function canContinueOiFinderRequest(state, owner) {
  const current = normalizedOiFinderRequestState(state);
  return Boolean(
    owner
    && normalizeOiFinderRequestSymbol(owner.symbol) === current.symbol
    && Number(owner.epoch) === current.epoch,
  );
}

export function startOiFinderRequest(state, {
  owner,
  intent = "required",
  stage = "full",
} = {}) {
  const current = normalizedOiFinderRequestState(state);
  if (!canContinueOiFinderRequest(current, owner)) {
    return { state: current, request: null };
  }

  const normalizedIntent = intent === "poll" ? "poll" : "required";
  if (normalizedIntent === "poll" && Object.keys(current.inFlight).length > 0) {
    return { state: current, request: null };
  }

  const id = current.nextRequestId;
  const request = {
    id,
    symbol: current.symbol,
    epoch: current.epoch,
    intent: normalizedIntent,
    stage: stage === "compact" ? "compact" : "full",
  };
  return {
    state: {
      ...current,
      nextRequestId: id + 1,
      latestStartedId: id,
      inFlight: { ...current.inFlight, [id]: request },
    },
    request,
  };
}

export function settleOiFinderRequest(state, request) {
  const current = normalizedOiFinderRequestState(state);
  const stored = current.inFlight[request?.id];
  if (
    !stored
    || stored.symbol !== normalizeOiFinderRequestSymbol(request?.symbol)
    || stored.epoch !== Number(request?.epoch)
  ) return current;

  const inFlight = { ...current.inFlight };
  delete inFlight[request.id];
  return { ...current, inFlight };
}

export function canCommitOiFinderRequest(state, request) {
  const current = normalizedOiFinderRequestState(state);
  return Boolean(
    request
    && canContinueOiFinderRequest(current, request)
    && Number(request.id) === current.latestStartedId,
  );
}

export function oiFinderInitialLoadPlan(activeView, popoutMode = "") {
  const view = String(activeView || "").trim();
  const mode = String(popoutMode || "").trim().toLowerCase();
  const supportedView = ["OI Finder", "Charts & OI", "ROI Calc", "OI Level Script TOS"].includes(view);
  const enabled = mode !== "mag7" && supportedView;
  return {
    enabled,
    loadChart: enabled && (mode === "chart" || (!mode && ["OI Finder", "Charts & OI"].includes(view))),
    loadCompactChain: enabled,
    enrichFinder: enabled && !mode && view === "OI Finder",
  };
}

export function hasUsableOiFinderChain(feed) {
  if (!feed || typeof feed !== "object") return false;
  return Boolean(
    feed.currentAtm?.expiry
    || (Array.isArray(feed.selectedExpiryChainRows) && feed.selectedExpiryChainRows.length)
    || (Array.isArray(feed.callRows) && feed.callRows.length)
    || (Array.isArray(feed.putRows) && feed.putRows.length),
  );
}

// Use the same shape as the server's cold-cache response when a short browser
// request deadline expires. The server refresh continues independently, so a
// visible retry error only adds delay and clears a useful same-symbol chain.
export function oiFinderWarmingFeed(symbol) {
  return {
    symbol: normalizeOiFinderRequestSymbol(symbol),
    live: false,
    warming: true,
    refreshing: true,
    cached: false,
    stale: false,
    source: "Schwab/TOS option chain",
    currentAtm: {},
    selectedExpiryChainRows: [],
    tosScriptLevels: [],
    callRows: [],
    putRows: [],
    expiryExpectedMoves: {},
    errors: [],
  };
}

// Finder-only analytics that the compact chain endpoint never computes.
const OI_FINDER_COMPACT_ANALYTICS_KEYS = [
  "volumeMomentum",
  "dailyLiquidityHeatmap",
  "unusualOtmActivity",
  "unusualOtmDashboard",
  "persistentActivity",
  "liveWallTrend",
];

function isEmptyOiFinderAnalyticsValue(value) {
  if (value == null) return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value).length === 0;
  return false;
}

export function mergeOiFinderFeedResponse(current, incoming, { compact = false } = {}) {
  const next = incoming && typeof incoming === "object" ? incoming : {};
  const target = String(next.symbol || "").trim().toUpperCase();
  const currentTarget = String(current?.symbol || "").trim().toUpperCase();
  const sameSymbol = Boolean(target && target === currentTarget);
  const incomingFailed = Array.isArray(next.errors) && next.errors.length > 0 && !hasUsableOiFinderChain(next);
  const incomingWarming = Boolean(next.warming || next.refreshing) && !hasUsableOiFinderChain(next);
  if (sameSymbol && incomingWarming && hasUsableOiFinderChain(current)) {
    return {
      ...current,
      cached: true,
      stale: true,
      warming: true,
      refreshing: true,
      errors: [],
    };
  }
  if (sameSymbol && incomingFailed && hasUsableOiFinderChain(current)) {
    return {
      ...current,
      cached: true,
      stale: true,
      refreshing: false,
      errors: next.errors,
    };
  }
  // The compact chain endpoint never computes the Finder-only analytics; the
  // server ships those keys as empty objects. Spreading an empty value over a
  // previously enriched one blanked the volume-momentum and heatmap columns
  // on the very next 15s compact poll. During a compact merge an empty
  // analytics value means "not computed", never "cleared", so keep whatever
  // the enrich pass loaded. A FULL response (compact=false) remains
  // authoritative and may genuinely clear them.
  let compactNext = next;
  if (compact && sameSymbol) {
    const preservedKeys = OI_FINDER_COMPACT_ANALYTICS_KEYS.filter((key) => (
      key in next
      && isEmptyOiFinderAnalyticsValue(next[key])
      && !isEmptyOiFinderAnalyticsValue(current?.[key])
    ));
    if (preservedKeys.length) {
      compactNext = { ...next };
      preservedKeys.forEach((key) => delete compactNext[key]);
    }
  }
  const merged = compact && sameSymbol ? { ...current, ...compactNext } : next;
  // A closed session (or a server cache hit) returns a byte-identical ~1.5MB
  // chain on every 15s poll. Keep the current reference in that case so the
  // poll cannot re-render the whole workspace and recompute every OI wall
  // model for unchanged data. The compare costs ~tens of ms; a re-render of
  // the chart workspace costs hundreds.
  if (current && sameSymbol) {
    try {
      if (JSON.stringify(merged) === JSON.stringify(current)) return current;
    } catch {
      // Non-serializable feed content falls through to the normal merge.
    }
  }
  return merged;
}

export function oiFinderFailureFeed(current, symbol, message) {
  const target = String(symbol || "").trim().toUpperCase();
  const currentTarget = String(current?.symbol || "").trim().toUpperCase();
  const errors = [{ error: String(message || "OI Finder feed unavailable.") }];
  if (target && target === currentTarget && hasUsableOiFinderChain(current)) {
    return {
      ...current,
      cached: true,
      stale: true,
      refreshing: false,
      errors,
    };
  }
  return {
    symbol: target,
    live: false,
    callRows: [],
    putRows: [],
    selectedExpiryChainRows: [],
    currentAtm: {},
    tosScriptLevels: [],
    errors,
  };
}

export function oiChartClientCacheDecision(entry, {
  bypassCache = false,
  now = Date.now(),
  freshMs = 30_000,
  maxStaleMs = 15 * 60_000,
} = {}) {
  const hasPayload = Boolean(entry?.payload);
  const savedAt = Number(entry?.savedAt);
  const currentTime = Number(now);
  const ageMs = hasPayload && Number.isFinite(savedAt) && Number.isFinite(currentTime)
    ? Math.max(0, currentTime - savedAt)
    : Number.POSITIVE_INFINITY;
  const freshWindow = Math.max(0, Number(freshMs) || 0);
  const staleWindow = Math.max(freshWindow, Number(maxStaleMs) || 0);
  const useCached = hasPayload && bypassCache !== true && ageMs <= staleWindow;
  return {
    ageMs,
    useCached,
    revalidate: useCached && ageMs >= freshWindow,
  };
}

export function resolveOiChartPrefetchSymbol(draft, tickerOptions = [], currentSymbol = "") {
  const query = String(draft || "").trim().toUpperCase();
  const current = String(currentSymbol || "").trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(query) || query === current) return null;

  const options = [...new Set((Array.isArray(tickerOptions) ? tickerOptions : [])
    .map((item) => String(
      item && typeof item === "object" ? (item.symbol || item.ticker || "") : (item || ""),
    ).trim().toUpperCase())
    .filter((item) => /^[A-Z][A-Z0-9.\-]{0,9}$/.test(item)))];
  if (options.includes(query)) return query;

  // A unique saved-watchlist prefix is safe to warm before Enter/click. This
  // makes common MAG7 searches such as "MS" and "NV" useful without sending
  // invalid partial symbols such as "M" or "MSF" to Schwab.
  const prefixMatches = options.filter((item) => item.startsWith(query));
  if (prefixMatches.length === 1) return prefixMatches[0];

  // Preserve custom-symbol search, but wait for enough input to avoid most
  // speculative broker requests while someone is still typing.
  if (query.length >= 3 && prefixMatches.length === 0) return query;
  return null;
}

// An option-chain request is materially more expensive than the chart warm-up:
// it asks the broker for every listed expiry through 31 DTE.  Only warm a
// symbol we already know from the user's watchlist, while preserving the more
// permissive chart prefetch for a custom-symbol search.
export function resolveOiFinderChainPrefetchSymbol(draft, tickerOptions = [], currentSymbol = "") {
  const candidate = resolveOiChartPrefetchSymbol(draft, tickerOptions, currentSymbol);
  if (!candidate) return null;
  const knownSymbols = new Set((Array.isArray(tickerOptions) ? tickerOptions : [])
    .map((item) => String(
      item && typeof item === "object" ? (item.symbol || item.ticker || "") : (item || ""),
    ).trim().toUpperCase())
    .filter((item) => /^[A-Z][A-Z0-9.\-]{0,9}$/.test(item)));
  return knownSymbols.has(candidate) ? candidate : null;
}

export function nextOiChainPollDelay({
  ready = false,
  warming = false,
  failed = false,
  rateLimited = false,
  failureCount = 0,
} = {}) {
  const failures = Math.max(0, Math.floor(Number(failureCount) || 0));
  if (failed && failures > 0) {
    // 15s -> 30s -> 60s -> 120s cap; a provider 429 never retries under 45s.
    // A busy open degrades to slower polls instead of hammering the API.
    const backoff = Math.min(120_000, 15_000 * 2 ** Math.min(failures, 3));
    return rateLimited ? Math.max(45_000, backoff) : backoff;
  }
  if (!ready && warming) return 700;
  return 15_000;
}
