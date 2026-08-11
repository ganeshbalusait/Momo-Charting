function normalizedDateKey(value) {
  const match = String(value || "").match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return "";
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])));
  return Number.isNaN(date.getTime()) ? "" : date.toISOString().slice(0, 10);
}

export function completedPivotPeriodKey(dateKey, timeframe) {
  const date = normalizedDateKey(dateKey);
  if (!date) return "";
  const normalizedTimeframe = String(timeframe || "DAY").toUpperCase();
  if (normalizedTimeframe === "DAY") return date;
  const [year, month, day] = date.split("-").map(Number);
  if (normalizedTimeframe === "MONTH") {
    return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}`;
  }
  if (normalizedTimeframe === "WEEK") {
    const monday = new Date(Date.UTC(year, month - 1, day));
    monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7));
    return monday.toISOString().slice(0, 10);
  }
  return date;
}

export function previousCompletedPivotPeriod(orderedDays, currentDate, timeframe) {
  const currentKey = completedPivotPeriodKey(currentDate, timeframe);
  if (!currentKey) return null;
  const periods = new Map();
  (Array.isArray(orderedDays) ? orderedDays : []).forEach((day) => {
    const date = normalizedDateKey(day?.date);
    const key = completedPivotPeriodKey(date, timeframe);
    if (!date || !key || key >= currentKey) return;
    const open = Number(day?.open);
    const high = Number(day?.high);
    const low = Number(day?.low);
    const close = Number(day?.close);
    if (![open, high, low, close].every(Number.isFinite)) return;
    const existing = periods.get(key);
    periods.set(key, existing ? {
      key,
      open: existing.open,
      high: Math.max(existing.high, high),
      low: Math.min(existing.low, low),
      close,
    } : { key, open, high, low, close });
  });
  return [...periods.values()].sort((left, right) => left.key.localeCompare(right.key)).at(-1) || null;
}

function aggregatePivotDays(days) {
  const sourceDays = Array.isArray(days) ? days : [];
  if (!sourceDays.length) return null;
  return sourceDays.reduce((period, day) => ({
    open: Number.isFinite(period.open) ? period.open : Number(day.open),
    high: Math.max(period.high, Number(day.high)),
    low: Math.min(period.low, Number(day.low)),
    close: Number(day.close),
  }), {
    open: Number.NaN,
    high: Number.NEGATIVE_INFINITY,
    low: Number.POSITIVE_INFINITY,
    close: Number.NaN,
  });
}

function ohlcBar(bar, dateForBar) {
  const date = normalizedDateKey(
    typeof dateForBar === "function" ? dateForBar(bar) : bar?.date,
  );
  const open = Number(bar?.open);
  const high = Number(bar?.high);
  const low = Number(bar?.low);
  const close = Number(bar?.close);
  if (!date || ![open, high, low, close].every(Number.isFinite)) return null;
  return { date, open, high, low, close };
}

// A daily-history response can lag the live chart by one session. When that
// happens the current day/week key does not exist, so previous-day and
// previous-week levels disappear even though the completed source period is
// present. Keep daily OHLC authoritative for historical sessions, then merge
// only chart dates that daily history is missing plus the live chart date.
export function mergeDailyAndChartOhlcDays(dailyBars, chartBars, dateForBar) {
  const days = new Map();
  const merge = (bar) => {
    const normalized = ohlcBar(bar, dateForBar);
    if (!normalized) return;
    const current = days.get(normalized.date);
    days.set(normalized.date, current ? {
      date: normalized.date,
      open: current.open,
      high: Math.max(current.high, normalized.high),
      low: Math.min(current.low, normalized.low),
      close: normalized.close,
    } : normalized);
  };

  (Array.isArray(dailyBars) ? dailyBars : []).forEach(merge);
  const chartSource = (Array.isArray(chartBars) ? chartBars : [])
    .map((bar) => ({ bar, normalized: ohlcBar(bar, dateForBar) }))
    .filter(({ normalized }) => normalized);
  const latestChartDate = chartSource.at(-1)?.normalized.date || "";
  const dailyDates = new Set(days.keys());
  const missingChartDates = new Set(chartSource
    .map(({ normalized }) => normalized.date)
    .filter((date) => !dailyDates.has(date)));
  chartSource.forEach(({ bar, normalized }) => {
    if (normalized.date === latestChartDate || missingChartDates.has(normalized.date)) merge(bar);
  });

  return [...days.values()].sort((left, right) => left.date.localeCompare(right.date));
}

// The pivot studies call the rolling lookup once per chart date, and a weekly
// chart can carry two decades of dates over a similarly sized day list.
// Normalizing and sorting the day list on every call made that quadratic
// (~58s of one blocked task on a 20-year weekly tape), so the prepared list
// is cached per input-array reference and each call only binary-searches it.
const preparedPivotDaysCache = new WeakMap();

function preparedPivotDays(orderedDays) {
  if (!Array.isArray(orderedDays)) return [];
  const cached = preparedPivotDaysCache.get(orderedDays);
  if (cached) return cached;
  const prepared = orderedDays
    .map((day) => ({ ...day, date: normalizedDateKey(day?.date) }))
    .filter((day) => (
      day.date
      && [day.open, day.high, day.low, day.close].every((value) => Number.isFinite(Number(value)))
    ))
    .sort((left, right) => left.date.localeCompare(right.date));
  preparedPivotDaysCache.set(orderedDays, prepared);
  return prepared;
}

// First index whose date sorts >= the target (the list is sorted ascending).
function lowerBoundByDate(days, target) {
  let low = 0;
  let high = days.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (days[middle].date < target) low = middle + 1;
    else high = middle;
  }
  return low;
}

// Schwab's built-in PivotPoints differs from PersonsPivots for WEEK and
// MONTH. PivotPoints counts the period backward from each chart day, so the
// levels can change daily; PersonsPivots uses the last completed calendar
// week/month. Keep this rolling source independent of the completed-period
// helper above.
export function rollingPivotSourcePeriod(orderedDays, currentDate, timeframe) {
  const current = normalizedDateKey(currentDate);
  if (!current) return null;
  const preparedDays = preparedPivotDays(orderedDays);
  const priorEnd = lowerBoundByDate(preparedDays, current);
  if (!priorEnd) return null;

  const normalizedTimeframe = String(timeframe || "DAY").toUpperCase();
  if (normalizedTimeframe !== "WEEK" && normalizedTimeframe !== "MONTH") {
    return aggregatePivotDays([preparedDays[priorEnd - 1]]);
  }

  const [year, month, day] = current.split("-").map(Number);
  const cutoff = new Date(Date.UTC(year, month - 1, day));
  if (normalizedTimeframe === "WEEK") {
    cutoff.setUTCDate(cutoff.getUTCDate() - 7);
  } else {
    cutoff.setUTCDate(1);
    cutoff.setUTCMonth(cutoff.getUTCMonth() - 1);
    const lastDay = new Date(Date.UTC(
      cutoff.getUTCFullYear(),
      cutoff.getUTCMonth() + 1,
      0,
    )).getUTCDate();
    cutoff.setUTCDate(Math.min(day, lastDay));
  }
  const cutoffDate = cutoff.toISOString().slice(0, 10);
  return aggregatePivotDays(preparedDays.slice(lowerBoundByDate(preparedDays, cutoffDate), priorEnd));
}
