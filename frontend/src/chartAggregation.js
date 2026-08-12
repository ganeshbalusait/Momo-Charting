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
export function tosFourHourBucketTime(timestamp) {
  const time = Math.floor(Number(timestamp || 0));
  if (!Number.isFinite(time) || time <= 0) return null;
  const easternMidnight = calendarBucketTime(time, 1440);
  const midnightCentralInEastern = easternMidnight + 60 * 60;
  const fourHours = 240 * 60;
  return midnightCentralInEastern
    + Math.floor((time - midnightCentralInEastern) / fourHours) * fourHours;
}

function easternSessionAlignedBucketTime(timestamp, aggregationMinutes) {
  // Two-hour UTC epoch buckets move one hour on the Eastern clock when DST
  // changes. Anchor them to exchange midnight so 04:00 remains 04:00 all year.
  const easternMidnight = calendarBucketTime(timestamp, 1440);
  const seconds = aggregationMinutes * 60;
  return easternMidnight + Math.floor((timestamp - easternMidnight) / seconds) * seconds;
}

export function chartAggregationBucketTime(timestamp, minutes) {
  const time = Math.floor(Number(timestamp || 0));
  const aggregationMinutes = Math.max(Number(minutes || 1), 1);
  if (!Number.isFinite(time) || time <= 0) return null;
  if (aggregationMinutes === 240) return tosFourHourBucketTime(time);
  if (aggregationMinutes === 120) return easternSessionAlignedBucketTime(time, aggregationMinutes);
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
    volume: Number.isFinite(volume) ? volume : 0,
  };
}

export function normalizeChartCandleBars(bars) {
  const byTime = new Map();
  (Array.isArray(bars) ? bars : []).forEach((bar) => {
    const normalized = normalizeChartCandleBar(bar);
    if (normalized) byTime.set(normalized.time, normalized);
  });
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

export function aggregateChartBars(bars, minutes) {
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
    current.volume = Number(current.volume || 0) + Number(bar?.volume || 0);
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
export function buildChartDisplayBars({
  studyBars,
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
      const merged = new Map(seed.map((bar) => [Number(bar.time), bar]));
      liveDaily.forEach((bar) => merged.set(Number(bar.time), bar));
      return aggregateChartBars([...merged.values()], minutes);
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
  if (!usesStudyHistory) return aggregateChartBars(live, minutes);
  // Bucket BOTH tapes at the study history's native cadence — 5-minute or
  // 30-minute, the two shapes the backend has shipped — so no ghost
  // sub-cadence slots appear AND a live partial bucket still replaces its
  // overlapping cached counterpart at the same key (never double-counted).
  const sharedCadence = chartSourceBarSpacingMinutes(normalizedStudyBars) >= 30 ? 30 : 5;
  const historical = aggregateChartBars(normalizedStudyBars, sharedCadence);
  const liveShared = aggregateChartBars(live, sharedCadence);
  const merged = new Map(historical.map((bar) => [Number(bar.time), bar]));
  liveShared.forEach((bar) => merged.set(Number(bar.time), bar));
  return aggregateChartBars(
    [...merged.values()].sort((left, right) => Number(left.time) - Number(right.time)),
    minutes,
  );
}
