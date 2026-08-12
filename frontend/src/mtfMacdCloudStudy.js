function finiteBars(bars) {
  return (Array.isArray(bars) ? bars : [])
    .flatMap((bar) => {
      const time = Math.floor(Number(bar?.time));
      const open = Number(bar?.open);
      const high = Number(bar?.high);
      const low = Number(bar?.low);
      const close = Number(bar?.close);
      if (![time, open, high, low, close].every(Number.isFinite) || time <= 0) return [];
      return [{ ...bar, time, open, high, low, close }];
    })
    .sort((left, right) => left.time - right.time);
}

function aggregateBars(bars, minutes) {
  const seconds = Math.max(1, Number(minutes) || 1) * 60;
  const buckets = new Map();
  finiteBars(bars).forEach((bar) => {
    const bucketTime = Math.floor(bar.time / seconds) * seconds;
    const current = buckets.get(bucketTime);
    if (!current) {
      buckets.set(bucketTime, { ...bar, time: bucketTime });
      return;
    }
    current.high = Math.max(current.high, bar.high);
    current.low = Math.min(current.low, bar.low);
    current.close = bar.close;
    current.volume = Number(current.volume || 0) + Number(bar.volume || 0);
  });
  return [...buckets.values()].sort((left, right) => left.time - right.time);
}

function hhmmToMinutes(value, fallback) {
  const numeric = Number(value);
  const raw = String(Math.max(0, Number.isFinite(numeric) ? numeric : fallback)).padStart(4, "0").slice(-4);
  return Math.min(23, Number(raw.slice(0, 2)) || 0) * 60
    + Math.min(59, Number(raw.slice(2, 4)) || 0);
}

function recentSessionCutoff(source, easternSessionFormatter, options) {
  if (options.mtfMacdCloudLimitRecentSessions === false) return Number.NEGATIVE_INFINITY;
  const rthStart = hhmmToMinutes(options.mtfMacdCloudRthStartTime, 500);
  const rthEnd = hhmmToMinutes(options.mtfMacdCloudRthEndTime, 1600);
  const sessionsBack = Math.max(1, Math.min(30, Number(options.mtfMacdCloudSessionsBack) || 1));
  const sessionStarts = [];
  const seenDates = new Set();
  source.forEach((bar) => {
    const parts = easternSessionFormatter.formatToParts(new Date(bar.time * 1000));
    const values = Object.fromEntries(
      parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]),
    );
    const date = `${values.year || ""}-${values.month || ""}-${values.day || ""}`;
    const minute = Number(values.hour || 0) * 60 + Number(values.minute || 0);
    if (minute >= rthStart && minute < rthEnd && !seenDates.has(date)) {
      seenDates.add(date);
      sessionStarts.push(bar.time);
    }
  });
  if (!sessionStarts.length) return Number.NEGATIVE_INFINITY;
  return sessionStarts[Math.max(0, sessionStarts.length - sessionsBack)];
}

function emaNext(previous, value, length) {
  if (!Number.isFinite(previous)) return value;
  const multiplier = 2 / (Math.max(1, Number(length) || 1) + 1);
  return value * multiplier + previous * (1 - multiplier);
}

function timeframeDefinitions(displayedMinutes) {
  return [
    { key: "current", label: "Current", minutes: displayedMinutes, optionKey: "mtfMacdCloudCurrent" },
    { key: "15m", label: "15m", minutes: 15, optionKey: "mtfMacdCloud15m" },
    { key: "30m", label: "30m", minutes: 30, optionKey: "mtfMacdCloud30m" },
    { key: "1h", label: "1h", minutes: 60, optionKey: "mtfMacdCloud1h" },
    { key: "2h", label: "2h", minutes: 120, optionKey: "mtfMacdCloud2h" },
    { key: "4h", label: "4h", minutes: 240, optionKey: "mtfMacdCloud4h" },
  ];
}

// ThinkScript evaluates a secondary aggregation from every primary chart bar.
// During a forming 1h candle, for example, ExpAverage(close(period = HOUR))
// keeps the completed hourly EMA state but substitutes the latest partial-hour
// close. A zero cross can therefore appear on a 5m bar even if the final hourly
// candle later reverses. Collapsing to completed higher-timeframe candles first
// loses exactly those TOS cloud strips.
function calculateExpandedTimeframeClouds(primaryBars, definition, cutoffTime, lengths) {
  const targetSeconds = definition.minutes * 60;
  const primarySeconds = lengths.displayedMinutes * 60;
  const clouds = [];
  let committed = null;
  let currentBucketTime = null;
  let currentBucket = null;
  let previousDiff = null;

  const commitBucket = () => {
    if (!currentBucket) return;
    const close = currentBucket.close;
    if (!committed) {
      committed = { fast: close, slow: close, signal: 0, ema9: close, ema20: close };
      return;
    }
    const fast = emaNext(committed.fast, close, lengths.fast);
    const slow = emaNext(committed.slow, close, lengths.slow);
    const macd = fast - slow;
    committed = {
      fast,
      slow,
      signal: emaNext(committed.signal, macd, lengths.signal),
      ema9: emaNext(committed.ema9, close, 9),
      ema20: emaNext(committed.ema20, close, 20),
    };
  };

  primaryBars.forEach((bar) => {
    const bucketTime = Math.floor(bar.time / targetSeconds) * targetSeconds;
    if (currentBucketTime !== bucketTime) {
      commitBucket();
      currentBucketTime = bucketTime;
      currentBucket = { high: bar.high, low: bar.low, close: bar.close };
    } else {
      currentBucket.high = Math.max(currentBucket.high, bar.high);
      currentBucket.low = Math.min(currentBucket.low, bar.low);
      currentBucket.close = bar.close;
    }

    const close = currentBucket.close;
    const provisionalFast = committed ? emaNext(committed.fast, close, lengths.fast) : close;
    const provisionalSlow = committed ? emaNext(committed.slow, close, lengths.slow) : close;
    const provisionalMacd = provisionalFast - provisionalSlow;
    const provisionalSignal = committed
      ? emaNext(committed.signal, provisionalMacd, lengths.signal)
      : provisionalMacd;
    const diff = provisionalMacd - provisionalSignal;
    const provisionalEma9 = committed ? emaNext(committed.ema9, close, 9) : close;
    const provisionalEma20 = committed ? emaNext(committed.ema20, close, 20) : close;
    const crossedUp = previousDiff != null
      && previousDiff <= 0
      && diff > 0
      && provisionalEma9 >= provisionalEma20;
    const crossedDown = previousDiff != null
      && previousDiff >= 0
      && diff < 0
      && provisionalEma9 <= provisionalEma20;

    if ((crossedUp || crossedDown) && bar.time >= cutoffTime) {
      clouds.push({
        key: `${definition.key}-${bar.time}-${crossedUp ? "bull" : "bear"}`,
        timeframe: definition.label,
        startTime: bar.time,
        endTime: bar.time + primarySeconds,
        anchor: crossedUp ? currentBucket.low : currentBucket.high,
        tone: crossedUp ? "bull" : "bear",
        family: "mtf-macd",
      });
    }
    previousDiff = diff;
  });

  return clouds;
}

export function calculateMtfMacdTrendClouds(bars, currentMinutes, easternSessionFormatter, options = {}) {
  const source = finiteBars(bars);
  if (!source.length || !easternSessionFormatter?.formatToParts) return [];
  const displayedMinutes = Math.max(1, Number(currentMinutes) || 1);
  const primaryBars = aggregateBars(source, displayedMinutes);
  const cutoffTime = recentSessionCutoff(primaryBars, easternSessionFormatter, options);
  const lengths = {
    displayedMinutes,
    fast: Math.max(1, Math.min(200, Number(options.mtfMacdFastLength) || 6)),
    slow: Math.max(1, Math.min(200, Number(options.mtfMacdSlowLength) || 12)),
    signal: Math.max(1, Math.min(200, Number(options.mtfMacdSignalLength) || 8)),
  };

  return timeframeDefinitions(displayedMinutes).flatMap((definition) => {
    if (definition.minutes < displayedMinutes || options[definition.optionKey] === false) return [];
    return calculateExpandedTimeframeClouds(primaryBars, definition, cutoffTime, lengths);
  });
}
