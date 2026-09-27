export function normalizeLightweightChartSeriesData(data) {
  const byTime = new Map();
  (Array.isArray(data) ? data : []).forEach((item) => {
    const time = Math.floor(Number(item?.time));
    if (!Number.isFinite(time) || time <= 0) return;
    // Keep the newest point for a timestamp. Broker snapshots can overlap the
    // live-forming candle, and several studies project onto the same bucket.
    byTime.set(time, { ...item, time });
  });
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

// O(1) sameness check for a candle tape. Candle arrays are append-only with
// only the final bar restating, so length + endpoints + the full last bar
// identify the content. Used to return the CURRENT state array (same
// reference) when a periodic REST reconcile delivers identical data — during
// closed sessions every 30s poll re-parsed identical JSON into new arrays,
// and each new reference forced every study useMemo (all enabled indicators)
// to recompute in one synchronous render: the recurring multi-second freeze.
export function chartTapeContentUnchanged(current, next) {
  if (!Array.isArray(current) || !Array.isArray(next)) return false;
  if (current.length !== next.length) return false;
  if (!current.length) return true;
  const currentFirst = current[0];
  const nextFirst = next[0];
  if (Number(currentFirst?.time) !== Number(nextFirst?.time)) return false;
  const currentLast = current[current.length - 1];
  const nextLast = next[next.length - 1];
  return Number(currentLast?.time) === Number(nextLast?.time)
    && Number(currentLast?.open) === Number(nextLast?.open)
    && Number(currentLast?.high) === Number(nextLast?.high)
    && Number(currentLast?.low) === Number(nextLast?.low)
    && Number(currentLast?.close) === Number(nextLast?.close)
    && Number(currentLast?.volume) === Number(nextLast?.volume);
}

/**
 * Choose the history series a chart should hold after a refresh response.
 *
 * Higher timeframes are built from these long series rather than the short
 * live tape: D/W/M aggregate `dailyBars`, and 4H aggregates the 60-day
 * five-minute `studyBars`. When one is empty the chart falls back to bucketing
 * the ~1-day live tape, which paints a single misleading candle.
 *
 * The server sends both series empty (or short) while it promotes a ticker's
 * full history — `initial=true` carries `studyBars: []`, and a refresh landing
 * mid-promotion answers with `dailyBars: []` — so replacing what is on screen
 * with that emptied the higher timeframes. Ignore an empty replacement while
 * the server reports history still loading; once it reports the history
 * complete an empty series is authoritative, so a symbol with genuinely no
 * history never shows stale candles.
 */
/**
 * True when a payload must not be allowed to shrink or clear the history
 * series (studyBars / fineStudyBars / dailyBars).
 *
 * `initialSlim` is the case that bit: the fast first-paint response OMITS
 * those tapes by contract, so its empty array means "not included in this
 * response", not "this symbol has no history". Passing only historyLoading
 * meant that once a full rebuild finished, every `initial=true` poll wiped
 * the 1600-bar study tape - 4H then re-aggregated from the ~900-bar
 * one-minute tape (~10 candles instead of hundreds), the seed re-fetch
 * restored it, the next poll wiped it again, and each cycle refit the
 * viewport. That oscillation is what made the chart shake.
 */
export function chartHistorySeriesGuarded(payload) {
  return payload?.historyLoading === true
    || payload?.warming === true
    || payload?.refreshing === true
    || payload?.initialSlim === true;
}

export function resolveHistorySeriesUpdate(current, incoming, historyLoading = false) {
  const held = Array.isArray(current) ? current : [];
  const next = Array.isArray(incoming) ? incoming : [];
  // While the server is still promoting a ticker it answers from the
  // fast-start build, whose study tape is a few days of one-minute bars
  // rather than the 60-day five-minute tape 4H needs. Accepting that shorter
  // series visibly degraded a pane that was already correct, so during
  // loading only a series at least as long as the one on screen replaces it.
  // Once loading finishes the server is authoritative and any length wins.
  if (historyLoading && held.length && next.length < held.length) return held;
  return chartTapeContentUnchanged(held, next) ? held : next;
}

// Each history series has a documented lookback contract: the live tape is
// ~2 sessions of one-minute bars, studyBars is the ~180-day intraday
// aggregate feeding 4H, and dailyBars is the ~10-year daily seed for D/W/M.
// On 2026-08-10 the server's archive promotion outgrew all three (a 28,005
// one-minute-bar live tape, a 13,307-bar studyBars with YEARS of daily rows
// concatenated before the 30-minute tape, and ~20 years of dailies). Study
// builds and the native chart primitive's per-paint geometry then iterated
// those tapes continuously and the workstation froze for 30-85s at a time.
// Enforce the contracts at ingestion so a server-side tape regression can
// never scale frontend cost unbounded again.
// The one-minute tape is the ONLY source for 3m/5m/10m/15m, so this cap is
// their history depth: 3,000 bars is about five days, which is why a 5m chart
// could not be panned back past last week. The server ships ~28,000 (30 days).
//
// Raised to 12,000 (~2 weeks, ~2,400 five-minute candles). Deliberately not
// the full 28,000: this tape also feeds the study builders on those
// timeframes, and an unbounded tape is what produced the 30-85s render
// freezes the cap was introduced to stop. 4x the history at a bounded cost.
//
// Going deeper on 5m needs the backend to ship a FIVE-minute study tape (it
// has shipped one before); the current 30-minute tape is too coarse to
// reconstruct 5m candles.
export const OI_CHART_LIVE_TAPE_MAX_BARS = 12_000;
// 4H is aggregated from studyBars, so this window bounds the 4H chart's
// depth. It is a SAFETY cap only - the real boundary is cadence, enforced by
// dropCoarseHistoryPrefix below. Widening this alone is not enough and in
// fact breaks the chart: the server concatenates a daily-cadence archive in
// front of the 30-minute tape, and pulling those rows in makes 4H buckets
// span years, which crushes every real candle against the left edge.
export const OI_CHART_STUDY_TAPE_MAX_AGE_SECONDS = 400 * 86_400;
export const OI_CHART_DAILY_TAPE_MAX_AGE_SECONDS = 3_700 * 86_400;

// The server ships studyBars as a daily-cadence archive concatenated in front
// of the recent 30-minute tape. Only the fine tail may feed 4H aggregation:
// mixing the archive in spreads 4H buckets across years of near-empty time.
//
// Age cannot separate them (the boundary moves per symbol and per day), but
// cadence can: inside the 30-minute tape the only day-plus gaps are weekends,
// and a weekend gap is ISOLATED - the gaps either side of it are minutes. In
// the daily archive, day-plus gaps repeat. So the last place two consecutive
// gaps are both >= 1 day is where the archive ends.
function dropCoarseHistoryPrefix(series) {
  if (series.length < 3) return series;
  const DAY_SECONDS = 86_400;
  for (let i = series.length - 3; i >= 0; i -= 1) {
    const gap = Number(series[i + 1].time) - Number(series[i].time);
    const nextGap = Number(series[i + 2].time) - Number(series[i + 1].time);
    if (gap >= DAY_SECONDS && nextGap >= DAY_SECONDS) return series.slice(i + 2);
  }
  return series;
}

function trimSeriesToNewestWindow(series, maxAgeSeconds) {
  if (!series.length) return series;
  const newestTime = Number(series[series.length - 1].time);
  if (!Number.isFinite(newestTime)) return series;
  const cutoff = newestTime - maxAgeSeconds;
  if (Number(series[0].time) >= cutoff) return series;
  return series.filter((bar) => Number(bar.time) >= cutoff);
}

export function normalizeOiChartPayload(payload) {
  const source = payload && typeof payload === "object" ? payload : {};
  const bars = normalizeLightweightChartSeriesData(source.bars)
    .slice(-OI_CHART_LIVE_TAPE_MAX_BARS);
  return {
    ...source,
    bars,
    studyBars: dropCoarseHistoryPrefix(
      trimSeriesToNewestWindow(
        normalizeLightweightChartSeriesData(
          Array.isArray(source.studyBars) ? source.studyBars : bars,
        ),
        OI_CHART_STUDY_TAPE_MAX_AGE_SECONDS,
      ),
    ),
    // 60-day FIVE-minute tape. studyBars is 30-minute (chosen for 4H depth)
    // and cannot reconstruct a 5m candle, so 3m/5m/10m/15m previously had only
    // the render-capped 1-minute tape - about two weeks - to draw from.
    fineStudyBars: trimSeriesToNewestWindow(
      normalizeLightweightChartSeriesData(source.fineStudyBars),
      OI_CHART_STUDY_TAPE_MAX_AGE_SECONDS,
    ),
    dailyBars: trimSeriesToNewestWindow(
      normalizeLightweightChartSeriesData(source.dailyBars),
      OI_CHART_DAILY_TAPE_MAX_AGE_SECONDS,
    ),
  };
}

// The chart endpoint is cache-first and normally answers immediately.  If the
// browser loses that tiny request during a busy market-open burst, preserve
// the workspace and let its readiness poll collect the server-side seed
// instead of turning a transient transport delay into a retry error.
export function createOiChartWarmingPayload(symbol) {
  return {
    symbol: String(symbol || "").trim().toUpperCase(),
    timeframe: "1Min",
    source: "Schwab/TOS API",
    live: false,
    warming: true,
    refreshing: true,
    historyLoading: true,
    bars: [],
    studyBars: [],
    dailyBars: [],
    ganeshHigherTimeframeSignals: {
      historyReady: false,
      signals: [],
    },
    mtfSignals: [],
    mtfSignalStates: [],
    mtfLiveSignalContexts: [],
    mtfSignalMode: "loading",
    watchlistMtfStates: [],
    error: "",
    updatedAt: new Date().toISOString(),
  };
}

// Masking a transient failure is right for ONE dropped request and wrong
// forever after. Returning a plain warming payload for every 5xx/timeout made
// a broken backend indistinguishable from a warming one: the chart sat on
// "Loading live one-minute candles from Schwab/TOS..." with no error, no
// status and no visible retry, while the phone (which uses the plain API error
// path) correctly reported the underlying 5xx. Carry the cause on the payload
// so the caller can keep quiet at first and then tell the truth.
export function createOiChartTransportFailurePayload(symbol, error) {
  return {
    ...createOiChartWarmingPayload(symbol),
    transportFailure: true,
    transportError: {
      name: String(error?.name || "Error"),
      message: String(error?.message || ""),
      httpStatus: Number(error?.httpStatus || 0) || 0,
    },
  };
}

// How many consecutive transport failures stay invisible before the chart
// admits it is not merely warming. Two covers a service restart and a single
// market-open burst; a third failure means something is actually wrong.
export const OI_CHART_TRANSPORT_FAILURE_GRACE_ATTEMPTS = 2;

export function describeOiChartTransportFailure(error) {
  const status = Number(error?.httpStatus || 0);
  if (status >= 500 && status <= 599) {
    return `API is unavailable right now (server error ${status}).`;
  }
  if (String(error?.name || "") === "AbortError") {
    return "The chart request timed out - the API did not answer in time.";
  }
  return "Lost the connection to the API.";
}

// Empty string means "stay on the loading placeholder". A non-empty notice is
// shown in place of the chart while the background loop keeps retrying, so it
// must never read as a terminal, user-must-act failure.
export function oiChartTransportFailureNotice(
  consecutiveFailures,
  error,
  graceAttempts = OI_CHART_TRANSPORT_FAILURE_GRACE_ATTEMPTS,
) {
  const failures = Number(consecutiveFailures);
  if (!Number.isFinite(failures) || failures <= graceAttempts) return "";
  return `${describeOiChartTransportFailure(error)} Retrying automatically.`;
}

// A local service restart makes browser fetch reject before it receives an
// HTTP status. Treat that separately from a broker/API response: the chart's
// readiness poll can reconnect in the next moment, while a real HTTP error is
// still surfaced to the trader.
export function isTransientOiChartTransportError(error) {
  if (error?.name === "AbortError") return true;
  const status = Number(error?.httpStatus || 0);
  if (status >= 500 && status <= 599) return true;
  if (error?.name !== "TypeError") return false;
  return /(?:failed to fetch|networkerror|network request failed)/i.test(String(error?.message || ""));
}

export function chartWallLevelSignature(levels) {
  return JSON.stringify((Array.isArray(levels) ? levels : []).map((level) => ({
    price: Number(level?.price),
    color: String(level?.color || ""),
    lineWidth: Number(level?.lineWidth || 0),
    lineStyle: Number(level?.lineStyle || 0),
    title: String(level?.title || ""),
    tier: Number(level?.tier || 0),
    side: String(level?.side || ""),
    strength: String(level?.strength || ""),
    openInterest: Number(level?.openInterest || 0),
  })));
}

export function guardLightweightChartSeriesTree(value, path = "chart", visited = new Set()) {
  if (!value || typeof value !== "object" || visited.has(value)) return;
  visited.add(value);
  if (typeof value.setData === "function") {
    const setData = value.setData.bind(value);
    // Re-applying the identical array is pure cost: normalizing it and handing it
    // back to Lightweight Charts profiled at ~350ms of one 773ms task during a
    // ticker switch, across every series of every pane. The tape helpers in this
    // module already preserve the reference for unchanged data (see
    // resolveHistorySeriesUpdate), so "same reference" is this codebase's own
    // definition of unchanged. Length is compared too as a cheap backstop, and a
    // rebuilt series tree gets a fresh closure, so its first apply always runs.
    let appliedData;
    let appliedLength = -1;
    value.setData = (data) => {
      if (data === appliedData && Array.isArray(data) && data.length === appliedLength) {
        return undefined;
      }
      const normalized = normalizeLightweightChartSeriesData(data);
      try {
        const result = setData(normalized);
        appliedData = data;
        appliedLength = Array.isArray(data) ? data.length : -1;
        return result;
      } catch (error) {
        // A malformed optional study must never unmount the chart workspace.
        // Clear only that series and leave candles/option chain operational.
        console.error(`Skipped invalid ${path} series data.`, error);
        // The series now holds [], not `data`. Forget what was applied so a retry
        // with the same reference is never skipped as already-drawn.
        appliedData = undefined;
        appliedLength = -1;
        try {
          return setData([]);
        } catch {
          return undefined;
        }
      }
    };
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => guardLightweightChartSeriesTree(item, `${path}[${index}]`, visited));
    return;
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return;
  Object.entries(value).forEach(([key, item]) => {
    guardLightweightChartSeriesTree(item, `${path}.${key}`, visited);
  });
}

// ---------------------------------------------------------------------------
// Tail-diff series apply.
//
// Lightweight Charts rebuilds the shared time index across EVERY series on each
// setData() call (DataLayer.setSeriesData), so its cost is the total number of
// points in the chart, not the size of the series being set. A six-across
// workspace measured 38 setData calls per apply pass on ~60 series of ~11.7k
// points: "persons-pivots" alone took ~2s, a full pass ~3.4s, and the pass
// re-ran every ~10s per panel because a tape TAIL change (the forming candle,
// one new bar) invalidates every study array. Six panels of that saturate the
// main thread and starve the study ladder - the lower panes never activate.
//
// update(point) only touches the tail. Nearly every routine apply changes just
// the last point (or appends a bar) while the history is byte-identical, so
// diff against what this series last received and prefer update() for that
// shape; anything else (history changed, points removed, older times) falls
// back to setData(). The comparison is a linear scan of primitives, microseconds
// against the milliseconds a time-index rebuild costs.
// ---------------------------------------------------------------------------
const appliedSeriesData = new WeakMap();

function seriesPointsEqual(left, right) {
  if (left === right) return true;
  if (!left || !right || typeof left !== "object" || typeof right !== "object") return false;
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  if (leftKeys.length !== rightKeys.length) return false;
  for (const key of leftKeys) {
    const a = left[key];
    const b = right[key];
    if (a === b) continue;
    // NaN !== NaN, and whitespace points carry no value at all.
    if (Number.isNaN(a) && Number.isNaN(b)) continue;
    return false;
  }
  return true;
}

function seriesPointTime(point) {
  const time = point?.time;
  return typeof time === "number" ? time : Number(time);
}

export function applySeriesData(series, data, options = {}) {
  if (!series || typeof series.setData !== "function") return "none";
  const next = Array.isArray(data) ? data : [];
  const previous = appliedSeriesData.get(series);
  if (options.force === true || !Array.isArray(previous) || typeof series.update !== "function") {
    series.setData(next);
    appliedSeriesData.set(series, next);
    return "setData";
  }
  if (previous === next) return "skip";
  if (!next.length) {
    if (!previous.length) {
      appliedSeriesData.set(series, next);
      return "skip";
    }
    series.setData(next);
    appliedSeriesData.set(series, next);
    return "setData";
  }
  const maxAppended = Number.isFinite(options.maxAppended) ? Math.max(0, options.maxAppended) : 8;
  const appended = next.length - previous.length;
  if (previous.length && appended >= 0 && appended <= maxAppended) {
    // Everything before the previous last point must be identical: update()
    // can restate the last point and append newer ones, nothing older.
    const stableCount = previous.length - 1;
    let historyIdentical = true;
    for (let index = 0; index < stableCount; index += 1) {
      if (!seriesPointsEqual(previous[index], next[index])) {
        historyIdentical = false;
        break;
      }
    }
    if (historyIdentical) {
      let lastTime = seriesPointTime(previous[stableCount]);
      let changed = false;
      let ordered = true;
      const updates = [];
      for (let index = stableCount; index < next.length; index += 1) {
        const point = next[index];
        const time = seriesPointTime(point);
        if (!Number.isFinite(time) || time < lastTime) {
          ordered = false;
          break;
        }
        // A point that already exists in the series may only be RESTATED at
        // its own time. update() cannot remove a point, so a last point that
        // moved to a later time would leave the old one behind as a phantom.
        if (index < previous.length && time !== seriesPointTime(previous[index])) {
          ordered = false;
          break;
        }
        if (index >= previous.length || !seriesPointsEqual(previous[index], point)) {
          updates.push(point);
          changed = true;
        }
        lastTime = time;
      }
      if (ordered) {
        if (!changed) {
          appliedSeriesData.set(series, next);
          return "skip";
        }
        // The candle and volume series are also advanced imperatively by the
        // live stream (a newer bar than this baseline knows about), and
        // Lightweight Charts throws "Cannot update oldest data" for a point
        // older than the series' own last one. That must never take the panel
        // down: fall back to a full setData, which re-baselines everything.
        try {
          updates.forEach((point) => series.update(point));
          appliedSeriesData.set(series, next);
          return "update";
        } catch {
          series.setData(next);
          appliedSeriesData.set(series, next);
          return "setData";
        }
      }
    }
  }
  series.setData(next);
  appliedSeriesData.set(series, next);
  return "setData";
}

// Tests and diagnostics: what this series last received through applySeriesData.
export function appliedSeriesDataFor(series) {
  return appliedSeriesData.get(series);
}
