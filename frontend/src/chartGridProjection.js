/**
 * Project a higher-aggregation study onto the chart's own bar grid.
 *
 * ThinkOrSwim never gives a secondary aggregation its own bar column. A Daily
 * study on a 4H chart is drawn as a step held across the 4H bars until the next
 * daily bar completes. Lightweight Charts behaves differently: it unions every
 * series' timestamps into logical indices, so a study point that does not land
 * on a chart bar time silently inserts an empty candle slot.
 *
 * The two grids genuinely never coincide. The primary 4H grid is anchored to
 * midnight Central (01:00/05:00/09:00/13:00/17:00/21:00 ET, see
 * tosFourHourBucketTime) while Daily/Weekly/Monthly buckets are anchored to
 * Eastern midnight (calendarBucketTime). A Daily study therefore contributed one
 * phantom 00:00 ET column every session, which rendered as a missing candle.
 *
 * Both helpers guarantee the returned timestamps are a subset of the displayed
 * bar times, which is what keeps the candle series gap-free.
 */

function displayedBarTimes(displayedBars) {
  return (Array.isArray(displayedBars) ? displayedBars : [])
    .map((bar) => Math.floor(Number(bar?.time ?? bar)))
    .filter((time) => Number.isFinite(time) && time > 0)
    .sort((left, right) => left - right);
}

function sortedStudyPoints(points) {
  return (Array.isArray(points) ? points : [])
    .filter((point) => Number.isFinite(Number(point?.time)) && Number(point.time) > 0)
    .slice()
    .sort((left, right) => Number(left.time) - Number(right.time));
}

/**
 * TOS step behavior: every chart bar carries the most recently completed study
 * value. Chart bars before the first study point stay empty, exactly as TOS
 * leaves the leading edge of a secondary aggregation blank.
 *
 * Pass `extendPastLastPoint: false` for plots that must stop where their source
 * data stops (Ichimoku's Chikou is drawn kijun periods back and must not be
 * held forward to the right edge).
 */
export function holdStudyPointsOnChartGrid(points, displayedBars, options = {}) {
  const bars = displayedBarTimes(displayedBars);
  const source = sortedStudyPoints(points);
  if (!bars.length || !source.length) return [];
  const extendPastLastPoint = options.extendPastLastPoint !== false;
  const limit = extendPastLastPoint ? Number.POSITIVE_INFINITY : Number(source.at(-1).time);
  const projected = [];
  let index = -1;
  bars.forEach((time) => {
    while (index + 1 < source.length && Number(source[index + 1].time) <= time) index += 1;
    if (index < 0 || time > limit) return;
    projected.push({ ...source[index], time });
  });
  return projected;
}

/**
 * Place each study point once, on the chart bar that contains it. Use this for
 * discrete events (crossovers, markers) where holding a value across later bars
 * would invent signals that never fired.
 */
export function snapStudyPointsToChartGrid(points, displayedBars) {
  const bars = displayedBarTimes(displayedBars);
  const source = sortedStudyPoints(points);
  if (!bars.length || !source.length) return [];
  const byTime = new Map();
  let barIndex = -1;
  source.forEach((point) => {
    const time = Number(point.time);
    while (barIndex + 1 < bars.length && bars[barIndex + 1] <= time) barIndex += 1;
    if (barIndex < 0) return;
    byTime.set(bars[barIndex], { ...point, time: bars[barIndex] });
  });
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

/**
 * True when a study built at `studyMinutes` already lands on the chart's own bar
 * times and needs no projection. Equal aggregations always match. Everything
 * else is checked against the real bar times rather than assumed, because the
 * 2H/4H/Daily grids use three different anchors.
 */
export function studyMatchesChartGrid(points, displayedBars) {
  const bars = new Set(displayedBarTimes(displayedBars));
  if (!bars.size) return false;
  return sortedStudyPoints(points).every((point) => bars.has(Math.floor(Number(point.time))));
}
