const EASTERN_BUCKET_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function easternDateTimeParts(timestamp) {
  const parts = EASTERN_BUCKET_FORMATTER.formatToParts(new Date(timestamp * 1000));
  const values = Object.fromEntries(
    parts
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, Number(part.value)]),
  );
  return {
    year: values.year,
    month: values.month,
    day: values.day,
    minute: (values.hour || 0) * 60 + (values.minute || 0),
    second: values.second || 0,
  };
}

function wallClockUtc(parts) {
  return Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour || 0),
    Number(parts.minute || 0),
    Number(parts.second || 0),
  );
}

// Convert an Eastern calendar value to epoch seconds without assuming a fixed
// UTC offset. The generated D/W/M timestamps represent midnight in the
// exchange timezone, so every formatter and date-based study sees the same
// trading date on both sides of daylight-saving transitions.
function easternWallClockToTimestamp(parts) {
  const desired = wallClockUtc(parts);
  let guess = Math.floor(desired / 1000);
  for (let pass = 0; pass < 3; pass += 1) {
    const observed = easternDateTimeParts(guess);
    const observedHour = Math.floor(Number(observed?.minute || 0) / 60);
    const observedMinute = Number(observed?.minute || 0) % 60;
    const difference = desired - wallClockUtc({
      ...observed,
      hour: observedHour,
      minute: observedMinute,
    });
    if (!difference) break;
    guess += Math.round(difference / 1000);
  }
  return guess;
}

function calendarDateParts(year, month, day) {
  const date = new Date(Date.UTC(year, month - 1, day, 12));
  return {
    year: date.getUTCFullYear(),
    month: date.getUTCMonth() + 1,
    day: date.getUTCDate(),
  };
}

const EASTERN_CALENDAR_DATE_CACHE = new Map();
const CALENDAR_BUCKET_TIME_CACHE = new Map();

function easternCalendarDateParts(timestamp) {
  // The Eastern calendar date cannot change inside one UTC hour, including
  // DST transitions. Caching this lookup keeps D/W/M aggregation inexpensive
  // when thousands of one-minute source bars are shared by many chart panels.
  const utcHour = Math.floor(Number(timestamp) / 3600);
  const cached = EASTERN_CALENDAR_DATE_CACHE.get(utcHour);
  if (cached) return cached;
  const parts = easternDateTimeParts(timestamp);
  const dateParts = { year: parts.year, month: parts.month, day: parts.day };
  EASTERN_CALENDAR_DATE_CACHE.set(utcHour, dateParts);
  return dateParts;
}

function calendarBucketTime(timestamp, aggregationMinutes) {
  const parts = easternCalendarDateParts(timestamp);
  const sourceDateKey = `${parts.year}-${parts.month}-${parts.day}`;
  const cacheKey = `${aggregationMinutes}:${sourceDateKey}`;
  const cached = CALENDAR_BUCKET_TIME_CACHE.get(cacheKey);
  if (cached !== undefined) return cached;
  let bucketDate = { year: parts.year, month: parts.month, day: parts.day };
  if (aggregationMinutes === 10080) {
    const date = new Date(Date.UTC(parts.year, parts.month - 1, parts.day, 12));
    const daysSinceMonday = (date.getUTCDay() + 6) % 7;
    bucketDate = calendarDateParts(parts.year, parts.month, parts.day - daysSinceMonday);
  } else if (aggregationMinutes === 43200) {
    bucketDate = { year: parts.year, month: parts.month, day: 1 };
  }
  const bucketTime = easternWallClockToTimestamp({
    ...bucketDate,
    hour: 0,
    minute: 0,
    second: 0,
  });
  CALENDAR_BUCKET_TIME_CACHE.set(cacheKey, bucketTime);
  return bucketTime;
}

/**
 * Match the primary TOS equity 4H chart clock used by the workspace.
 *
 * TOS aggregates equity Last-price bars from midnight Central by default.
 * Central is one hour behind Eastern, so the four-hour boundaries visible on
 * this Eastern-time chart are 01:00, 05:00, 09:00, 13:00, 17:00 and 21:00.
 * Keep this primary-chart contract separate from the native secondary-study
 * aggregation clocks used by the backend CALL1H/CALL2H signal engine.
 */
export function tosCentralMidnightBucketTime(timestamp, aggregationMinutes) {
  const time = Math.floor(Number(timestamp || 0));
  if (!Number.isFinite(time) || time <= 0) return null;
  const easternMidnight = calendarBucketTime(time, 1440);
  const midnightCentralInEastern = easternMidnight + 60 * 60;
  const seconds = Math.max(Number(aggregationMinutes || 1), 1) * 60;
  return midnightCentralInEastern
    + Math.floor((time - midnightCentralInEastern) / seconds) * seconds;
}

export function tosFourHourBucketTime(timestamp) {
  return tosCentralMidnightBucketTime(timestamp, 240);
}

// TOS 2h bars share the midnight-Central clock with extended hours on:
// 01:00, 03:00, 05:00, 07:00, 09:00, 11:00 ... ET (TOS support: nine 2h
// candles in a 16-hour session). Verified against the trader's chart
// 2026-08-24 - its P2H 07:00 and CALL2H 09:10 sit on those boundaries;
// even-hour buckets straddled the 4h boundaries and never matched.
export function tosTwoHourBucketTime(timestamp) {
  return tosCentralMidnightBucketTime(timestamp, 120);
}

export function chartAggregationBucketTime(timestamp, minutes) {
  const time = Math.floor(Number(timestamp || 0));
  const aggregationMinutes = Math.max(Number(minutes || 1), 1);
  if (!Number.isFinite(time) || time <= 0) return null;
  if (aggregationMinutes === 240) return tosFourHourBucketTime(time);
  if (aggregationMinutes === 120) return tosTwoHourBucketTime(time);
  if ([1440, 10080, 43200].includes(aggregationMinutes)) {
    return calendarBucketTime(time, aggregationMinutes);
  }
  return Math.floor(time / (aggregationMinutes * 60)) * aggregationMinutes * 60;
}

function normalizeChartCandleBar(bar) {
  if (
    bar?.time == null
    || bar?.open == null
    || bar?.high == null
    || bar?.low == null
    || bar?.close == null
  ) return null;
  const time = Math.floor(Number(bar?.time || 0));
  const open = Number(bar?.open);
  const high = Number(bar?.high);
  const low = Number(bar?.low);
  const close = Number(bar?.close);
  if (
    !Number.isFinite(time)
    || time <= 0
    || !Number.isFinite(open)
    || !Number.isFinite(high)
    || !Number.isFinite(low)
    || !Number.isFinite(close)
  ) return null;
  const volume = Number(bar?.volume);
  return {
    ...bar,
    time,
    open,
    // A provider can occasionally publish a forming bar whose reported high
    // or low has not caught up with its close. Repair those bounds instead of
    // passing an invalid OHLC point to Lightweight Charts, which rejects the
    // entire series and leaves a blank chart behind.
    high: Math.max(high, open, close),
    low: Math.min(low, open, close),
    close,
    // Volume stays numeric all the way to the histogram (Lightweight Charts
    // rejects null points); chartStreamBars marks honesty with `volumeKnown`.
    volume: Number.isFinite(volume) ? volume : 0,
  };
}

// Sum of the finite volumes; a non-finite reading (null/undefined/NaN from a
// caller that skipped normalisation) contributes nothing rather than NaN-ing
// the bucket. Number(null) is 0, so the filter runs BEFORE any cast.
function addVolume(current, incoming) {
  const known = [current, incoming].filter((value) => Number.isFinite(value));
  return known.reduce((sum, value) => sum + value, 0);
}

// The larger of two volume readings for the same bucket.
function maxVolume(left, right) {
  const known = [left, right].filter((value) => Number.isFinite(value));
  return known.length ? Math.max(...known) : 0;
}

export function normalizeChartCandleBars(bars) {
  const byTime = new Map();
  (Array.isArray(bars) ? bars : []).forEach((bar) => {
    const normalized = normalizeChartCandleBar(bar);
    if (normalized) byTime.set(normalized.time, normalized);
  });
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

// Every multi-timeframe study aggregates the SAME source tape to the same
// handful of timeframes, and they all recompute together whenever the tape
// reference changes. Measured in the six-across workspace: fifteen study
// memos each re-aggregating an 11.7k-bar tape to 15m/30m/1h/2h/4h/D/W was a
// large share of a 1-2s render per tape change. Cache by source-array
// identity (a WeakMap, so a dropped tape frees its aggregations) and by
// timeframe, so each aggregation happens once per tape instead of once per
// study. Results are shared, so callers must treat them as read-only - which
// every study already does.
const aggregationCache = new WeakMap();

// Identity alone is not enough: the live stream buffer is mutated IN PLACE
// every tick (same array, restated last bar) and is aggregated once a second
// for the forming candle. A pure identity cache handed that path the stale
// aggregation and froze the live candle for the rest of each minute. The
// signature covers everything a tail mutation can change; a tape whose
// history changes always arrives as a new array.
function aggregationSignature(bars) {
  const last = bars[bars.length - 1];
  const first = bars[0];
  return `${bars.length}|${Number(first?.time)}|${Number(last?.time)}|${Number(last?.open)}|${Number(last?.high)}|${Number(last?.low)}|${Number(last?.close)}|${Number(last?.volume)}`;
}

export function aggregateChartBars(bars, minutes) {
  if (!Array.isArray(bars)) return aggregateChartBarsUncached(bars, minutes);
  const aggregationMinutes = Math.max(Number(minutes || 1), 1);
  let byMinutes = aggregationCache.get(bars);
  if (!byMinutes) {
    byMinutes = new Map();
    aggregationCache.set(bars, byMinutes);
  }
  const signature = aggregationSignature(bars);
  const cached = byMinutes.get(aggregationMinutes);
  if (cached && cached.signature === signature) return cached.aggregated;
  const aggregated = aggregateChartBarsUncached(bars, aggregationMinutes);
  byMinutes.set(aggregationMinutes, { signature, aggregated });
  return aggregated;
}

function aggregateChartBarsUncached(bars, minutes) {
  const aggregationMinutes = Math.max(Number(minutes || 1), 1);
  const buckets = new Map();
  normalizeChartCandleBars(bars).forEach((bar) => {
    const time = bar.time;
    const bucketTime = chartAggregationBucketTime(time, aggregationMinutes);
    if (!Number.isFinite(bucketTime) || bucketTime <= 0) return;
    const current = buckets.get(bucketTime);
    if (!current) {
      buckets.set(bucketTime, { ...bar, time: bucketTime });
      return;
    }
    current.high = Math.max(Number(current.high || 0), Number(bar?.high || 0));
    current.low = Math.min(Number(current.low || 0), Number(bar?.low || 0));
    current.close = Number(bar?.close || current.close || 0);
    current.volume = addVolume(current.volume, bar?.volume);
  });
  return [...buckets.values()].sort((left, right) => left.time - right.time);
}

function normalizedChartSourceBars(bars) {
  return normalizeChartCandleBars(bars);
}

/**
 * Native cadence of a bar series in minutes, from the modal gap of its first
 * rows. The backend has shipped `studyBars` at five-minute AND thirty-minute
 * cadences across builds; consumers must adapt to what actually arrived
 * instead of assuming, or a 30-minute tape bucketed at 5 minutes renders one
 * candle per six slots with ghost gaps between.
 */
export function chartSourceBarSpacingMinutes(bars, fallbackMinutes = 5) {
  const source = Array.isArray(bars) ? bars : [];
  const counts = new Map();
  const probe = Math.min(source.length, 60);
  for (let index = 1; index < probe; index += 1) {
    const gap = Number(source[index]?.time) - Number(source[index - 1]?.time);
    if (Number.isFinite(gap) && gap > 0) counts.set(gap, (counts.get(gap) || 0) + 1);
  }
  let bestGap = 0;
  let bestCount = 0;
  counts.forEach((count, gap) => {
    if (count > bestCount) {
      bestGap = gap;
      bestCount = count;
    }
  });
  return bestGap > 0 ? Math.max(1, Math.round(bestGap / 60)) : fallbackMinutes;
}

/**
 * Build the candles shown by the chart without changing short-timeframe
 * behavior. The 4H view needs the longer five-minute study history to match a
 * TOS 15-day chart. Merge both sources at a common five-minute cadence and
 * let the fresher live bucket replace its cached counterpart, so overlapping
 * OHLC and volume are never counted twice even if the live window starts in
 * the middle of a five-minute candle.
 */
// A coarse timeframe needs enough candles to read structure from. Below this
// the pane is a stub, not a chart: a fast-start payload rendered 10 candles on
// 4H and 3 on D for an uncached symbol (measured on MMM, 2026-08-18).
export const COARSE_TIMEFRAME_MIN_CANDLES = 60;

// True when a coarse timeframe is showing a stub because the deep seed has not
// finished downloading. Judged on the RENDERED CANDLE COUNT, deliberately: an
// earlier version checked whether the seed arrays were empty and never fired
// once, because a fast-start payload ships SHORT seeds, not empty ones.
export function chartDeepHistoryPending({
  renderedCandleCount = 0,
  aggregationMinutes = 5,
  historyLoading = false,
} = {}) {
  if (!historyLoading) return false;
  const minutes = Math.max(Number(aggregationMinutes || 1), 1);
  // 1H and coarser lean on the study seed and stay stubs until it lands - 35
  // candles on 1H, 18 on 2H, 11 on 4H, 3 on D measured mid-rebuild. Below 1H
  // the live tape reconstructs every bucket within seconds.
  if (minutes < 60) return false;
  return Number(renderedCandleCount || 0) < COARSE_TIMEFRAME_MIN_CANDLES;
}

// Lay the live tape over a historical (TOS price-history) tape at a shared
// cadence. The historical tape is the source of truth for every bucket it has
// CLOSED: a live bucket may replace the historical tape's LAST bucket (the
// forming candle), add buckets newer than it, or FILL a bucket the historical
// tape is missing - but never replace a closed candle that exists. Two
// measurements on 2026-09-04 (AMZN): a single trade-only live minute
// (O=H=L=C, volume 0) had landed on top of a complete five-minute candle while
// the Schwab feed was stalled; and the night of Sep 2 had 32/96 candles in the
// five-minute tape but 96/96 in the one-minute tape, so holes must stay
// fillable or that night loses two thirds of its candles.
export function overlayLiveOnHistorical(historical, liveShared) {
  const merged = new Map(historical.map((bar) => [Number(bar.time), bar]));
  const lastClosed = historical.length ? Number(historical[historical.length - 1].time) : -Infinity;
  liveShared.forEach((bar) => {
    const time = Number(bar.time);
    if (!merged.has(time)) {
      merged.set(time, bar);
      return;
    }
    if (time >= lastClosed) {
      // The live forming bucket owns price, but it is closed minutes plus a
      // volume-less forming minute, so it always understates volume. The
      // server's bar for the same bucket is complete but may be a reconcile
      // behind. Take the larger reading rather than whichever arrived last -
      // measured 2026-09-04: Vol 60,858 -> Vol 0 at every 5m boundary.
      merged.set(time, { ...bar, volume: maxVolume(bar.volume, merged.get(time)?.volume) });
    }
  });
  return [...merged.values()].sort((left, right) => Number(left.time) - Number(right.time));
}

export function buildChartDisplayBars({
  studyBars,
  fineStudyBars,
  liveBars,
  dailyBars,
  aggregationMinutes = 5,
  sourcesNormalized = false,
} = {}) {
  const minutes = Math.max(Number(aggregationMinutes || 1), 1);
  const live = sourcesNormalized && Array.isArray(liveBars)
    ? liveBars
    : normalizedChartSourceBars(liveBars);
  if (minutes >= 1440) {
    // D/W/M views draw from the long daily seed (decades) instead of the
    // short one-minute tape. Both use Eastern-midnight epoch timestamps, so
    // the live tape's aggregated current day replaces the seed's same-day
    // row and the visible candle keeps ticking.
    const seed = sourcesNormalized && Array.isArray(dailyBars)
      ? dailyBars
      : normalizedChartSourceBars(dailyBars);
    if (seed.length) {
      const liveDaily = aggregateChartBars(live, 1440);
      return aggregateChartBars(overlayLiveOnHistorical(seed, liveDaily), minutes);
    }
  }
  const normalizedStudyBars = sourcesNormalized && Array.isArray(studyBars)
    ? studyBars
    : normalizedChartSourceBars(studyBars);
  // The study tape is usable for ANY timeframe at least as coarse as its own
  // cadence, not just 4H. Only 240 took this path before, so 1h and 2h were
  // aggregated from the one-minute live tape - capped at ~5 days by the
  // ingestion contract, which is 63 candles on 1h and 32 on 2h. The study
  // tape carries ~260 days at 30 minutes.
  const studyCadence = chartSourceBarSpacingMinutes(normalizedStudyBars);
  const usesStudyHistory = normalizedStudyBars.length > 0 && (
    // 4H has always merged the study tape regardless of its cadence, including
    // sparse/daily-ish shapes the backend has shipped over time. Keep that.
    minutes >= 240
    // New: any timeframe at least as coarse as the tape's own cadence can use
    // it too. Without this, 1h and 2h aggregated from the one-minute live tape,
    // which the ingestion contract caps at ~5 days - 63 candles on 1h, 32 on 2h.
    || (studyCadence > 0 && studyCadence <= minutes)
  );
  if (!usesStudyHistory) {
    // Low timeframes: prefer the 60-day FIVE-minute tape over the live
    // one-minute tape, which the ingestion contract caps for render cost and
    // which therefore ran out about two weeks back. The 5m tape is finer than
    // any of these buckets, so it reconstructs them exactly; the live tape is
    // still merged on top so the forming candle keeps ticking.
    const fine = sourcesNormalized && Array.isArray(fineStudyBars)
      ? fineStudyBars
      : normalizedChartSourceBars(fineStudyBars);
    const fineCadence = chartSourceBarSpacingMinutes(fine);
    // Only prefer the fine tape when it actually reaches FURTHER BACK than the
    // live tape. Measured 2026-08-12: the server's five-minute tape covered
    // two days while the client's one-minute tape covered two weeks, so using
    // it unconditionally would have SHORTENED the chart. The guard makes the
    // client correct regardless of how deep the server's tape happens to be.
    const earliest = (rows) => (rows.length ? Number(rows[0].time) || 0 : 0);
    const fineReachesFurther = fine.length > 0
      && (!live.length || earliest(fine) < earliest(live));
    if (fine.length && fineCadence > 0 && fineCadence <= minutes && fineReachesFurther) {
      const historical = aggregateChartBars(fine, minutes);
      const liveShared = aggregateChartBars(live, minutes);
      return overlayLiveOnHistorical(historical, liveShared);
    }
    return aggregateChartBars(live, minutes);
  }
  // Bucket BOTH tapes at the study history's native cadence — 5-minute or
  // 30-minute, the two shapes the backend has shipped — so no ghost
  // sub-cadence slots appear AND a live partial bucket still replaces its
  // overlapping cached counterpart at the same key (never double-counted).
  const sharedCadence = chartSourceBarSpacingMinutes(normalizedStudyBars) >= 30 ? 30 : 5;
  const historical = aggregateChartBars(normalizedStudyBars, sharedCadence);
  const liveShared = aggregateChartBars(live, sharedCadence);
  return aggregateChartBars(overlayLiveOnHistorical(historical, liveShared), minutes);
}
