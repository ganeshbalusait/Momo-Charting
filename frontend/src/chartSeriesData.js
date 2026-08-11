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
export const OI_CHART_LIVE_TAPE_MAX_BARS = 3_000;
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
    value.setData = (data) => {
      const normalized = normalizeLightweightChartSeriesData(data);
      try {
        return setData(normalized);
      } catch (error) {
        // A malformed optional study must never unmount the chart workspace.
        // Clear only that series and leave candles/option chain operational.
        console.error(`Skipped invalid ${path} series data.`, error);
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
