const EASTERN_TIME_ZONE = "America/New_York";

const EASTERN_PARTS_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: EASTERN_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});

function easternParts(timestamp) {
  const time = Math.floor(Number(timestamp || 0));
  if (!Number.isFinite(time) || time <= 0) return null;
  const values = Object.fromEntries(
    EASTERN_PARTS_FORMATTER
      .formatToParts(new Date(time * 1000))
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  return {
    year: Number(values.year),
    month: Number(values.month),
    day: Number(values.day),
    weekday: values.weekday,
    hour: Number(values.hour),
    minute: Number(values.minute),
    second: Number(values.second),
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

// Convert an Eastern wall-clock date to epoch seconds without assuming a
// fixed UTC offset. Two passes handle both standard/daylight time boundaries.
function easternWallClockToTimestamp(parts) {
  const desired = wallClockUtc(parts);
  let guess = Math.floor(desired / 1000);
  for (let pass = 0; pass < 3; pass += 1) {
    const observed = easternParts(guess);
    if (!observed) break;
    const difference = desired - wallClockUtc(observed);
    if (!difference) break;
    guess += Math.round(difference / 1000);
  }
  return guess;
}

function isWeekend(parts) {
  return parts?.weekday === "Sat" || parts?.weekday === "Sun";
}

function calendarDateKey(parts) {
  return [
    String(Number(parts?.year || 0)).padStart(4, "0"),
    String(Number(parts?.month || 0)).padStart(2, "0"),
    String(Number(parts?.day || 0)).padStart(2, "0"),
  ].join("-");
}

function utcDateParts(date) {
  return {
    year: date.getUTCFullYear(),
    month: date.getUTCMonth() + 1,
    day: date.getUTCDate(),
    weekday: ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"][date.getUTCDay()],
  };
}

function observedFixedHoliday(year, month, day) {
  const actual = new Date(Date.UTC(year, month - 1, day, 12));
  const weekday = actual.getUTCDay();
  if (weekday === 6) actual.setUTCDate(actual.getUTCDate() - 1);
  else if (weekday === 0) actual.setUTCDate(actual.getUTCDate() + 1);
  return utcDateParts(actual);
}

function observedNewYearsDay(year) {
  const actual = new Date(Date.UTC(year, 0, 1, 12));
  // NYSE does not carry a Saturday New Year's Day back into the preceding
  // calendar year. Sunday is observed on Monday; weekdays close as usual.
  if (actual.getUTCDay() === 0) actual.setUTCDate(actual.getUTCDate() + 1);
  return utcDateParts(actual);
}

function nthWeekdayOfMonth(year, month, weekday, occurrence) {
  const first = new Date(Date.UTC(year, month - 1, 1, 12));
  const offset = (weekday - first.getUTCDay() + 7) % 7;
  first.setUTCDate(1 + offset + (occurrence - 1) * 7);
  return utcDateParts(first);
}

function lastWeekdayOfMonth(year, month, weekday) {
  const last = new Date(Date.UTC(year, month, 0, 12));
  last.setUTCDate(last.getUTCDate() - ((last.getUTCDay() - weekday + 7) % 7));
  return utcDateParts(last);
}

// Gregorian Easter (Meeus/Jones/Butcher); NYSE observes Good Friday.
function easterSunday(year) {
  const a = year % 19;
  const b = Math.floor(year / 100);
  const c = year % 100;
  const d = Math.floor(b / 4);
  const e = b % 4;
  const f = Math.floor((b + 8) / 25);
  const g = Math.floor((b - f + 1) / 3);
  const h = (19 * a + b - d - g + 15) % 30;
  const i = Math.floor(c / 4);
  const k = c % 4;
  const l = (32 + 2 * e + 2 * i - h - k) % 7;
  const m = Math.floor((a + 11 * h + 22 * l) / 451);
  const month = Math.floor((h + l - 7 * m + 114) / 31);
  const day = ((h + l - 7 * m + 114) % 31) + 1;
  return new Date(Date.UTC(year, month - 1, day, 12));
}

const US_EQUITY_HOLIDAY_CACHE = new Map();

function usEquityHolidayKeys(year) {
  if (US_EQUITY_HOLIDAY_CACHE.has(year)) return US_EQUITY_HOLIDAY_CACHE.get(year);
  const holidays = [
    observedNewYearsDay(year),
    nthWeekdayOfMonth(year, 1, 1, 3), // Martin Luther King Jr. Day
    nthWeekdayOfMonth(year, 2, 1, 3), // Washington's Birthday
    lastWeekdayOfMonth(year, 5, 1), // Memorial Day
    observedFixedHoliday(year, 6, 19), // Juneteenth
    observedFixedHoliday(year, 7, 4),
    nthWeekdayOfMonth(year, 9, 1, 1), // Labor Day
    nthWeekdayOfMonth(year, 11, 4, 4), // Thanksgiving
    observedFixedHoliday(year, 12, 25),
  ];
  const goodFriday = easterSunday(year);
  goodFriday.setUTCDate(goodFriday.getUTCDate() - 2);
  holidays.push(utcDateParts(goodFriday));
  const keys = new Set(holidays.map(calendarDateKey));
  US_EQUITY_HOLIDAY_CACHE.set(year, keys);
  return keys;
}

function isUsEquityHoliday(parts) {
  const key = calendarDateKey(parts);
  const year = Number(parts?.year || 0);
  return usEquityHolidayKeys(year).has(key);
}

function isClosedBusinessDate(parts) {
  return isWeekend(parts) || isUsEquityHoliday(parts);
}

function moveEasternDate(parts, days) {
  const date = new Date(Date.UTC(parts.year, parts.month - 1, parts.day + days, 12));
  return {
    ...parts,
    year: date.getUTCFullYear(),
    month: date.getUTCMonth() + 1,
    day: date.getUTCDate(),
  };
}

function nextBusinessDate(parts) {
  let next = moveEasternDate(parts, 1);
  let weekday = easternParts(easternWallClockToTimestamp({ ...next, hour: 12, minute: 0, second: 0 }));
  while (isClosedBusinessDate(weekday)) {
    next = moveEasternDate(next, 1);
    weekday = easternParts(easternWallClockToTimestamp({ ...next, hour: 12, minute: 0, second: 0 }));
  }
  return next;
}

function intradayBucketTimes(parts, timeframeMinutes) {
  const minutes = Math.max(1, Math.floor(Number(timeframeMinutes) || 1));
  if (minutes === 120) {
    // Keep 2H candles anchored to the Eastern extended-hours session through
    // both EST and EDT instead of inheriting alternating UTC epoch boundaries.
    return Array.from({ length: 8 }, (_, index) => 240 + index * 120).map((minute) => (
      easternWallClockToTimestamp({
        ...parts,
        hour: Math.floor(minute / 60),
        minute: minute % 60,
        second: 0,
      })
    ));
  }
  if (minutes === 240) {
    // Primary TOS equity Last-price bars begin at midnight Central, so the
    // Eastern buckets are 01/05/09/13/17/21. With the extended-hours session
    // enabled TOS draws the 21:00 bar as the opening bar of the NEXT trading
    // date - Sunday 21:00 ET starts the week - so it is anchored to the day
    // before this business date. Emitting it keeps the projection at the same
    // six-bars-per-day density as the history it extends; omitting it made bar
    // spacing change at the history/future seam.
    const previousDate = moveEasternDate(parts, -1);
    return [
      easternWallClockToTimestamp({ ...previousDate, hour: 21, minute: 0, second: 0 }),
      ...[60, 300, 540, 780, 1020].map((minute) => (
        easternWallClockToTimestamp({
          ...parts,
          hour: Math.floor(minute / 60),
          minute: minute % 60,
          second: 0,
        })
      )),
    ];
  }

  // Remaining intraday candles are floor(timestamp / interval) UTC buckets in
  // aggregateChartBars. Derive every bucket intersecting the 04:00-20:00 ET
  // session so future whitespace follows that identical contract.
  const seconds = minutes * 60;
  const sessionStart = easternWallClockToTimestamp({ ...parts, hour: 4, minute: 0, second: 0 });
  const sessionEnd = easternWallClockToTimestamp({ ...parts, hour: 20, minute: 0, second: 0 });
  const first = Math.floor(sessionStart / seconds) * seconds;
  const last = Math.floor((sessionEnd - 1) / seconds) * seconds;
  const buckets = [];
  for (let time = first; time <= last; time += seconds) buckets.push(time);
  return buckets;
}

function nextIntradayTimestamp(timestamp, timeframeMinutes) {
  let parts = easternParts(timestamp);
  if (!parts) return 0;
  // A business date's bucket list can open before the date itself (the 4H EXTO
  // session starts at 21:00 the previous evening), so always filter the next
  // date's buckets against the cursor instead of taking its first entry.
  for (let attempt = 0; attempt < 8; attempt += 1) {
    const candidate = intradayBucketTimes(parts, timeframeMinutes)
      .find((time) => time > timestamp);
    if (candidate) return candidate;
    parts = nextBusinessDate(parts);
  }
  return 0;
}

function easternMidnightTimestamp(parts) {
  return easternWallClockToTimestamp({
    ...parts,
    hour: 0,
    minute: 0,
    second: 0,
  });
}

function nextDailyBucketTimestamp(timestamp) {
  const parts = easternParts(timestamp);
  return parts ? easternMidnightTimestamp(nextBusinessDate(parts)) : 0;
}

function nextWeeklyBucketTimestamp(timestamp) {
  const parts = easternParts(timestamp);
  if (!parts) return 0;
  const date = new Date(Date.UTC(parts.year, parts.month - 1, parts.day, 12));
  const daysSinceMonday = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - daysSinceMonday + 7);
  return easternMidnightTimestamp(utcDateParts(date));
}

function nextMonthlyBucketTimestamp(timestamp) {
  const parts = easternParts(timestamp);
  if (!parts) return 0;
  const nextMonth = new Date(Date.UTC(parts.year, parts.month, 1, 12));
  return easternMidnightTimestamp(utcDateParts(nextMonth));
}

/**
 * Build native Lightweight Charts whitespace points for TradingView-like
 * future time slots. These points carry no price value; they only teach the
 * time scale which Eastern timestamps belong in the reserved projection area.
 */
export function buildFutureChartTimes(bars, timeframeMinutes, count = 256) {
  const source = (Array.isArray(bars) ? bars : [])
    .filter((bar) => Number.isFinite(Number(bar?.time)) && Number(bar.time) > 0)
    .sort((left, right) => Number(left.time) - Number(right.time));
  const requestedCount = Math.max(0, Math.min(2000, Math.floor(Number(count) || 0)));
  if (!source.length || !requestedCount) return [];

  const minutes = Math.max(1, Math.floor(Number(timeframeMinutes) || 1));
  let time = Math.floor(Number(source.at(-1).time));
  const future = [];
  for (let index = 0; index < requestedCount; index += 1) {
    let next = 0;
    if (minutes < 1440) next = nextIntradayTimestamp(time, minutes);
    // D/W/M bars use Eastern calendar buckets in aggregateChartBars. Future
    // whitespace must use the identical calendar contract or DST, weekends,
    // and month length can insert phantom logical points.
    else if (minutes === 1440) next = nextDailyBucketTimestamp(time);
    else if (minutes === 10080) next = nextWeeklyBucketTimestamp(time);
    else if (minutes === 43200) next = nextMonthlyBucketTimestamp(time);
    else if (minutes > 1440) next = time + minutes * 60;
    else next = time + minutes * 60;
    if (!Number.isFinite(next) || next <= time) break;
    future.push(next);
    time = next;
  }
  return future;
}

/** Project a timestamp when the pointer is panned beyond generated points. */
export function projectFutureChartTime(bars, timeframeMinutes, logicalSlots) {
  const slots = Math.max(0, Math.ceil(Number(logicalSlots) || 0));
  if (!slots) return Number((Array.isArray(bars) ? bars : []).at(-1)?.time || 0);
  return Number(buildFutureChartTimes(bars, timeframeMinutes, slots).at(-1) || 0);
}
