import { aggregateChartBars, chartAggregationBucketTime } from "./chartAggregation.js";

const EASTERN_SIGNAL_DATE_FORMATTER = new Intl.DateTimeFormat("en-CA", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

const EASTERN_SIGNAL_TIME_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

export const GANESH_HIGHER_TIMEFRAMES = Object.freeze([
  { key: "D", days: 1, atrMultiplier: 0.2 },
  { key: "2D", days: 2, atrMultiplier: 0.6 },
  { key: "3D", days: 3, atrMultiplier: 1.0 },
  { key: "4D", days: 4, atrMultiplier: 1.4 },
  { key: "W", days: 0, atrMultiplier: 1.8 },
  { key: "M", days: 0, atrMultiplier: 2.2 },
]);

// The supplied MACD study evaluates every declared secondary aggregation.
// Monthly is a real 6/12/8 crossover source, not only a W→M confirmation
// input for the EMA families.
export const GANESH_MACD_TIMEFRAMES = GANESH_HIGHER_TIMEFRAMES;

const nextConfirmationTimeframe = Object.freeze({
  D: "2D",
  "2D": "3D",
  "3D": "4D",
  "4D": "W",
  W: "M",
});

const timeframeRank = Object.freeze(
  Object.fromEntries(GANESH_HIGHER_TIMEFRAMES.map(({ key }, index) => [key, index])),
);

const familyRank = Object.freeze({
  ganesh48: 0,
  ganesh920: 1,
  ganeshMacd: 2,
});

const familyPrefetchBuckets = Object.freeze({
  ganesh48: 32,
  ganesh920: 80,
  ganeshMacd: 48,
});

function finite(value) {
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function dateKeyFromTimestamp(timestamp) {
  const time = finite(timestamp);
  if (time == null || time <= 0) return "";
  const parts = EASTERN_SIGNAL_DATE_FORMATTER.formatToParts(new Date(time * 1000));
  const values = Object.fromEntries(
    parts.filter((part) => part.type !== "literal").map((part) => [part.type, part.value]),
  );
  return `${values.year}-${values.month}-${values.day}`;
}

function dateKeyForBar(bar) {
  const supplied = String(bar?.date || "");
  return /^\d{4}-\d{2}-\d{2}$/.test(supplied)
    ? supplied
    : dateKeyFromTimestamp(bar?.time);
}

function easternMinuteFromTimestamp(timestamp) {
  const time = finite(timestamp);
  if (time == null || time <= 0) return null;
  const parts = Object.fromEntries(
    EASTERN_SIGNAL_TIME_FORMATTER.formatToParts(new Date(time * 1000))
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  const hour = Number(parts.hour);
  const minute = Number(parts.minute);
  return Number.isFinite(hour) && Number.isFinite(minute) ? hour * 60 + minute : null;
}

function isWeekday(dateKey) {
  const [year, month, day] = String(dateKey).split("-").map(Number);
  if (![year, month, day].every(Number.isFinite)) return false;
  const weekday = new Date(Date.UTC(year, month - 1, day, 12)).getUTCDay();
  return weekday !== 0 && weekday !== 6;
}

/**
 * ThinkScript secondary DAY-or-higher prices exclude equity extended hours.
 * Keep intraday primary-candle timing, but feed higher-TF studies only the
 * portion overlapping 09:30-16:00 ET. A primary candle crossing 16:00 (the
 * 13:00 4H candle) settles to the official daily close, not its 17:00
 * after-hours close.
 */
function higherTimeframeSourceBar(bar, dailyCloseByDate, aggregationMinutes) {
  const minutes = Math.max(1, Number(aggregationMinutes) || 1);
  const date = dateKeyForBar(bar);
  const close = finite(bar?.close);
  if (!date || close == null) return null;
  if (minutes >= 1_440) return { ...bar, date, sourceClose: close };
  if (!isWeekday(date)) return null;

  const startMinute = easternMinuteFromTimestamp(bar?.time);
  if (!Number.isFinite(startMinute)) return null;
  const endMinute = startMinute + minutes;
  const rthStart = 9 * 60 + 30;
  const rthEnd = 16 * 60;
  if (endMinute <= rthStart || startMinute >= rthEnd) return null;

  const settledClose = finite(dailyCloseByDate.get(date));
  const sourceClose = endMinute >= rthEnd && settledClose != null ? settledClose : close;
  return { ...bar, date, sourceClose };
}

function weekKey(dateKey) {
  const [year, month, day] = String(dateKey).split("-").map(Number);
  if (![year, month, day].every(Number.isFinite)) return "";
  const date = new Date(Date.UTC(year, month - 1, day, 12));
  date.setUTCDate(date.getUTCDate() - ((date.getUTCDay() + 6) % 7));
  return date.toISOString().slice(0, 10);
}

function calendarDayOrdinal(dateKey) {
  const [year, month, day] = String(dateKey).split("-").map(Number);
  if (![year, month, day].every(Number.isFinite)) return null;
  return Math.floor(Date.UTC(year, month - 1, day) / 86_400_000);
}

function timeframeGroupKey(timeframe, dateKey) {
  if (timeframe === "D") return dateKey;
  if (timeframe === "W") return `W-${weekKey(dateKey)}`;
  if (timeframe === "M") return `M-${dateKey.slice(0, 7)}`;
  const days = Number.parseInt(timeframe, 10);
  const ordinal = calendarDayOrdinal(dateKey);
  if (!Number.isInteger(ordinal)) return "";
  // TOS multi-day constants are fixed-duration calendar aggregations. Anchor
  // their phase to a fixed epoch, never to the first row returned by a rolling
  // history request; prefixing older data must not move today's 2D/3D/4D bar.
  return `${timeframe}-${Math.floor(ordinal / Math.max(1, days))}`;
}

function finalizedTimeframeGroupKey(timeframe, dateKey, orderedSessionDates) {
  if (timeframe === "D") return dateKey;
  if (timeframe === "W") return `W-${weekKey(dateKey)}`;
  if (timeframe === "M") return `M-${dateKey.slice(0, 7)}`;
  const days = Number.parseInt(timeframe, 10);
  const sessions = Array.isArray(orderedSessionDates) ? orderedSessionDates : [];
  const sessionIndex = sessions.indexOf(dateKey);
  if (sessionIndex < 0) return "";
  return `${timeframe}-R${Math.floor((sessions.length - 1 - sessionIndex) / Math.max(1, days))}`;
}

function nextEma(previous, value, length) {
  if (!Number.isFinite(previous)) return value;
  const alpha = 2 / (Math.max(1, Number(length)) + 1);
  return value * alpha + previous * (1 - alpha);
}

function createSourceState() {
  return {
    groupKey: "",
    committed: null,
    provisional: null,
    previous: null,
    completedBuckets: 0,
  };
}

function sourceValues(base, close) {
  const source = base || {};
  const ema4 = nextEma(source.ema4, close, 4);
  const ema8 = nextEma(source.ema8, close, 8);
  const ema9 = nextEma(source.ema9, close, 9);
  const ema20 = nextEma(source.ema20, close, 20);
  const ema6 = nextEma(source.ema6, close, 6);
  const ema12 = nextEma(source.ema12, close, 12);
  const macdValue = ema6 - ema12;
  const macdAverage = nextEma(source.macdAverage, macdValue, 8);
  return { ema4, ema8, ema9, ema20, ema6, ema12, macdValue, macdAverage };
}

function advanceSourceState(state, groupKey, close) {
  if (state.groupKey && state.groupKey !== groupKey && state.provisional) {
    state.committed = state.provisional;
    state.completedBuckets += 1;
  }
  if (state.groupKey !== groupKey) state.groupKey = groupKey;
  const next = sourceValues(state.committed, close);
  const previous = state.previous;
  state.provisional = next;
  state.previous = next;
  return {
    base: state.committed,
    previous,
    current: next,
    completedBuckets: state.completedBuckets,
  };
}

function crossedAbove(previousFast, previousSlow, fast, slow) {
  return [previousFast, previousSlow, fast, slow].every(Number.isFinite)
    && previousFast <= previousSlow
    && fast > slow;
}

function crossedBelow(previousFast, previousSlow, fast, slow) {
  return [previousFast, previousSlow, fast, slow].every(Number.isFinite)
    && previousFast >= previousSlow
    && fast < slow;
}

function normalizeBars(bars) {
  const byTime = new Map();
  (Array.isArray(bars) ? bars : []).forEach((bar) => {
    const time = Math.floor(Number(bar?.time || 0));
    const close = finite(bar?.close);
    if (!Number.isFinite(time) || time <= 0 || close == null) return;
    byTime.set(time, { ...bar, time, close });
  });
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

function optionEnabled(options, family, timeframe, direction, suffix = "") {
  const key = `${family}Show${direction === "CALL" ? "Call" : "Put"}${timeframe}${suffix}`;
  return options?.[key] !== false;
}

function eventRecord({ family, timeframe, direction, time, bar, atrMultiplier = 0 }) {
  const label = family === "ganesh48"
    ? `${direction}${timeframe}`
    : family === "ganesh920"
      ? `${direction}${timeframe}`
      : `MACD-${timeframe}`;
  return {
    key: `${family}-${timeframe}-${direction}-${time}`,
    family,
    timeframe,
    direction,
    label,
    time: Number(time),
    low: finite(bar?.low),
    high: finite(bar?.high),
    atrMultiplier,
    stateSnapshot: false,
    sourceEvent: true,
  };
}

function compareEvents(left, right) {
  return Number(left.time) - Number(right.time)
    || (familyRank[left.family] ?? 99) - (familyRank[right.family] ?? 99)
    || (timeframeRank[left.timeframe] ?? 99) - (timeframeRank[right.timeframe] ?? 99)
    || String(left.direction).localeCompare(String(right.direction));
}

function sourceReady(snapshot, family) {
  return Number(snapshot?.completedBuckets || 0) >= Number(familyPrefetchBuckets[family] || 0);
}

function dailyTradingSessions(dailyBars) {
  const byDate = new Map();
  normalizeBars(dailyBars).forEach((bar) => {
    const date = dateKeyForBar(bar);
    if (!date || !isWeekday(date)) return;
    byDate.set(date, { ...bar, date });
  });
  return [...byDate.values()].sort((left, right) => left.date.localeCompare(right.date));
}

function finalizedMacdStatesByDate(dailyBars, carrierBars = []) {
  const daily = dailyTradingSessions(dailyBars);
  const orderedSessionDates = [...new Set([
    ...daily.map(({ date }) => date),
    ...normalizeBars(carrierBars).map(dateKeyForBar).filter((date) => date && isWeekday(date)),
  ])].sort();
  const result = Object.fromEntries(GANESH_MACD_TIMEFRAMES.map(({ key }) => [key, new Map()]));
  if (!orderedSessionDates.length) return result;

  GANESH_MACD_TIMEFRAMES.forEach(({ key: timeframe }) => {
    const groups = [];
    orderedSessionDates.forEach((date) => {
      const groupKey = finalizedTimeframeGroupKey(timeframe, date, orderedSessionDates);
      const previous = groups.at(-1);
      if (previous?.key === groupKey) previous.dates.push(date);
      else groups.push({ key: groupKey, close: null, dates: [date] });
    });
    const groupByDate = new Map();
    groups.forEach((group) => group.dates.forEach((date) => groupByDate.set(date, group)));
    daily.forEach((bar) => {
      const group = groupByDate.get(bar.date);
      if (group) group.close = Number(bar.close);
    });

    let base = null;
    let completedBuckets = 0;
    groups.forEach((group, index) => {
      const current = Number.isFinite(group.close) ? sourceValues(base, group.close) : null;
      const snapshot = {
        groupKey: group.key,
        completedBuckets,
        base,
        current,
        open: index === groups.length - 1,
      };
      group.dates.forEach((date) => result[timeframe].set(date, snapshot));
      if (current) {
        base = current;
        completedBuckets += 1;
      }
    });
  });
  return result;
}

/**
 * Build the exact selected primary-chart cadence before evaluating any
 * secondary D/2D/3D/4D/W/M study. Older 5m history stops where the live 1m
 * history begins so one market interval is never replayed twice.
 */
export function buildGaneshPrimaryBars({
  studyBars,
  liveBars,
  aggregationMinutes = 5,
} = {}) {
  const minutes = Math.max(1, Number(aggregationMinutes) || 1);
  const live = normalizeBars(liveBars);
  const canUseFiveMinuteHistory = minutes >= 5 && minutes % 5 === 0;
  const historical = canUseFiveMinuteHistory ? normalizeBars(studyBars) : [];
  const liveStart = Number(live[0]?.time || 0);
  const source = liveStart > 0
    ? [...historical.filter((bar) => Number(bar.time) < liveStart), ...live]
    : historical;
  return aggregateChartBars(source, minutes);
}

/**
 * Replays the three supplied ThinkScript studies on canonical 4H primary
 * candles. Daily history warms each EMA. The 4x8/9x20 families retain their
 * source-candle cross lifecycle; MACD replays the literal D/2D/3D/4D/W/M
 * secondary 6/12/8 crossover on the primary candle where TOS evaluates it.
 */
export function calculateGaneshHigherTimeframeSignals({
  intradayBars,
  dailyBars,
  aggregationMinutes = 5,
  options = {},
} = {}) {
  const intraday = normalizeBars(intradayBars);
  if (!intraday.length) return [];
  const daily = normalizeBars(dailyBars).map((bar) => ({ ...bar, date: dateKeyForBar(bar) }));
  const carrierBars = intraday
    .map((bar) => ({ ...bar, date: dateKeyForBar(bar) }))
    .filter((bar) => isWeekday(bar.date));
  if (!carrierBars.length) return [];
  const finalizedSecondaryStates = finalizedMacdStatesByDate(daily, carrierBars);
  const events = [];
  const macdFired = Object.fromEntries(
    GANESH_MACD_TIMEFRAMES.map(({ key }) => [key, { CALL: false, PUT: false }]),
  );
  const previous48Secondary = Object.fromEntries(
    GANESH_HIGHER_TIMEFRAMES.map(({ key }) => [key, null]),
  );
  const activeClose = Number(carrierBars.at(-1)?.close);
  let previousDate = "";

  // ThinkScript evaluates `crosses` on the declared secondary aggregation,
  // then expands that Boolean onto every contained primary chart bar. This is
  // why the supplied daily latches intentionally print W/4D/3D/2D again on
  // later days inside the same crossed secondary candle.
  carrierBars.forEach((bar) => {
    const date = dateKeyForBar(bar);
    const newDay = date !== previousDate;
    previousDate = date;
    const snapshots = {};

    GANESH_MACD_TIMEFRAMES.forEach(({ key: timeframe }) => {
      const finalized = finalizedSecondaryStates[timeframe]?.get(date);
      if (!finalized?.base) return;
      snapshots[timeframe] = {
        ...finalized,
        current: finalized.current || (finalized.open && Number.isFinite(activeClose)
          ? sourceValues(finalized.base, activeClose)
          : null),
      };
    });

    GANESH_HIGHER_TIMEFRAMES.forEach(({ key: timeframe, atrMultiplier }) => {
      const snapshot = snapshots[timeframe];
      const current = snapshot?.current;
      const base = snapshot?.base;
      if (!snapshot || !current || !base) return;

      const ready48 = sourceReady(snapshot, "ganesh48");
      const previous48 = previous48Secondary[timeframe] || base;
      const cross48 = {
        CALL: ready48 && crossedAbove(previous48.ema4, previous48.ema8, current.ema4, current.ema8),
        PUT: ready48 && crossedBelow(previous48.ema4, previous48.ema8, current.ema4, current.ema8),
      };
      const confirmationKey = nextConfirmationTimeframe[timeframe];
      const confirmation = confirmationKey ? snapshots[confirmationKey]?.current : null;
      [["CALL", cross48.CALL], ["PUT", cross48.PUT]].forEach(([direction, crossed]) => {
        if (!crossed || !optionEnabled(options, "ganesh48", timeframe, direction)) return;
        const confirmed = !confirmation || (direction === "CALL"
          ? confirmation.ema4 >= confirmation.ema8
          : confirmation.ema4 <= confirmation.ema8);
        if (confirmed) {
          events.push(eventRecord({ family: "ganesh48", timeframe, direction, time: bar.time, bar }));
        }
      });

      const ready920 = sourceReady(snapshot, "ganesh920");
      const raw920 = {
        CALL: ready920 && crossedAbove(base.ema9, base.ema20, current.ema9, current.ema20),
        PUT: ready920 && crossedBelow(base.ema9, base.ema20, current.ema9, current.ema20),
      };
      if (newDay) {
        [["CALL", raw920.CALL], ["PUT", raw920.PUT]].forEach(([direction, rawSignal]) => {
          if (rawSignal && optionEnabled(options, "ganesh920", timeframe, direction)) {
            events.push(eventRecord({
              family: "ganesh920",
              timeframe,
              direction,
              time: bar.time,
              bar,
              atrMultiplier,
            }));
          }
        });
      }
      const readyMacd = sourceReady(snapshot, "ganeshMacd");
      const rawMacd = {
        CALL: readyMacd && crossedAbove(
          base.macdValue, base.macdAverage, current.macdValue, current.macdAverage,
        ),
        PUT: readyMacd && crossedBelow(
          base.macdValue, base.macdAverage, current.macdValue, current.macdAverage,
        ),
      };
      [["CALL", rawMacd.CALL], ["PUT", rawMacd.PUT]].forEach(([direction, rawSignal]) => {
        const previouslyFired = Boolean(macdFired[timeframe][direction]);
        if (rawSignal
          && !previouslyFired
          && optionEnabled(options, "ganeshMacd", timeframe, direction)) {
          events.push(eventRecord({
            family: "ganeshMacd",
            timeframe,
            direction,
            time: bar.time,
            bar,
          }));
        }
        // Match CompoundValue ordering: the plot reads fired[1], while the
        // current bar's latch can reset for a new Eastern trading day.
        if (newDay) macdFired[timeframe][direction] = false;
        else if (rawSignal) macdFired[timeframe][direction] = true;
        else macdFired[timeframe][direction] = previouslyFired;
      });
      previous48Secondary[timeframe] = current;
    });
  });

  const unique = new Map(events.map((event) => [event.key, event]));
  return [...unique.values()].sort(compareEvents);
}

// The ThinkScript offset base is `Average(TrueRange(...), 14)` — a plain
// 14-bar arithmetic mean, not Wilder's smoothed ATR. Wilder smoothing lags a
// rolling mean after volatility shifts and visibly moves the bubble stack.
function simpleAtr14(bars) {
  const window = [];
  let total = 0;
  const output = [];
  bars.forEach((bar, index) => {
    const high = Number(bar.high);
    const low = Number(bar.low);
    const previousClose = index ? Number(bars[index - 1].close) : Number(bar.close);
    const trueRange = Math.max(high - low, Math.abs(high - previousClose), Math.abs(low - previousClose));
    window.push(trueRange);
    total += trueRange;
    if (window.length > 14) total -= window.shift();
    output.push(total / window.length);
  });
  return output;
}

/** Project source events onto the containing candle of any selected chart timeframe. */
export function projectGaneshSignalsToChart(signals, chartBars, aggregationMinutes = 5) {
  const bars = normalizeBars(chartBars);
  if (!bars.length) return [];
  const atr = simpleAtr14(bars);
  const barsByTime = new Map(bars.map((bar, index) => [Number(bar.time), { bar, index }]));
  const projected = [];
  const seen = new Set();
  (Array.isArray(signals) ? signals : []).forEach((signal) => {
    const sourceTime = Number(signal?.time || 0);
    const bucketTime = chartAggregationBucketTime(sourceTime, aggregationMinutes);
    const match = barsByTime.get(Number(bucketTime));
    if (!match) return;
    const { bar, index } = match;
    const direction = signal.direction === "PUT" ? "PUT" : "CALL";
    const multiplier = Math.max(0, Number(signal.atrMultiplier) || 0);
    const price = direction === "CALL"
      ? Number(bar.low) - atr[index] * multiplier
      : Number(bar.high) + atr[index] * multiplier;
    const key = `${signal.family}-${signal.timeframe}-${direction}-${bar.time}`;
    if (seen.has(key)) return;
    seen.add(key);
    projected.push({
      ...signal,
      key,
      sourceTime,
      time: Number(bar.time),
      price,
      position: direction === "CALL" ? "belowBar" : "aboveBar",
    });
  });
  return projected.sort(compareEvents);
}

/**
 * Stable fingerprint for every projected Ganesh visual, not only the newest
 * candle. Background history can add an older signal while the chart keeps the
 * same first/last candle; the render fast path must notice that change and
 * rebuild the native overlay instead of updating only the live candle.
 */
export function ganeshSignalVisualSignature(signals) {
  return (Array.isArray(signals) ? signals : [])
    .map((signal) => {
      const price = finite(signal?.price);
      return [
        String(signal?.key || ""),
        String(signal?.family || ""),
        String(signal?.timeframe || ""),
        signal?.direction === "PUT" ? "PUT" : "CALL",
        Math.floor(Number(signal?.time || 0)),
        Math.floor(Number(signal?.sourceTime || 0)),
        String(signal?.label || ""),
        String(signal?.position || ""),
        price == null ? "" : price.toFixed(8),
      ].join(":");
    })
    .sort()
    .join(",");
}

export const __ganeshSignalInternals = Object.freeze({
  calendarDayOrdinal,
  dateKeyFromTimestamp,
  easternMinuteFromTimestamp,
  finalizedMacdStatesByDate,
  finalizedTimeframeGroupKey,
  higherTimeframeSourceBar,
  timeframeGroupKey,
  simpleAtr14,
});
