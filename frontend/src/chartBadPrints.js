// Extended-hours bad prints (2026-11-11).
//
// Schwab's minute candles carry the occasional off-market trade print outside
// the regular session. On 2026-11-11 SPY's 16:38 and 16:55 ET minutes opened
// at $711.46 while SPY traded at ~$760, so the 16:55 five-minute candle drew a
// body from $711 to $760. TradingView and TOS drop those prints; we drew them.
// Measured over the chart cache the same thing appears on most liquid names
// (SLV $43.75 against ~$46.6, GOOGL $324.89 against ~$295, MSFT $531 against
// ~$552), a few times a month each.
//
// A price is treated as a bad print only when ALL of these hold:
//   - the minute is outside 09:30-16:00 ET (regular-session prints are left
//     alone - the exchanges already filter those);
//   - it is at least 1% (and at least $0.05) away from the median close of the
//     ten minutes on each side, or further when the ticker is jumpy;
//   - nothing traded near that price in the five minutes on either side.
// A real after-hours move (earnings) keeps trading at the new price, so it is
// never touched. On the newest minutes, where the following minutes do not
// exist yet, only a spike that came back inside its own minute (bad open, high
// or low with a normal close) is removed - a fresh move is never hidden while
// it is happening.

const NEIGHBOUR_WINDOW = 10;
const CONFIRM_WINDOW = 5;
const MIN_FRACTION = 0.01;
const MIN_DOLLARS = 0.05;
const JUMPINESS_MULTIPLE = 25;
const FIELDS = ["open", "high", "low", "close"];
const REGULAR_OPEN_MINUTE = 9 * 60 + 30;
const REGULAR_CLOSE_MINUTE = 16 * 60;

const easternMinuteFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

// Intl is slow enough to matter on a 30k-bar tape re-checked every second, so
// the Eastern hour is looked up once per UTC hour and the minute is added on.
// Eastern offsets are whole hours, so the minute within the hour never shifts.
const easternHourCache = new Map();

function easternMinuteOfDay(epochSeconds) {
  const utcHour = Math.floor(epochSeconds / 3600);
  let hour = easternHourCache.get(utcHour);
  if (hour === undefined) {
    const parts = easternMinuteFormatter.formatToParts(new Date(utcHour * 3600 * 1000));
    hour = Number(parts.find((part) => part.type === "hour")?.value);
    if (easternHourCache.size > 20000) easternHourCache.clear();
    easternHourCache.set(utcHour, hour);
  }
  return hour * 60 + Math.floor((epochSeconds % 3600) / 60);
}

export function isExtendedHoursTime(epochSeconds) {
  const minute = easternMinuteOfDay(Number(epochSeconds));
  return minute < REGULAR_OPEN_MINUTE || minute >= REGULAR_CLOSE_MINUTE;
}

function median(values) {
  if (!values.length) return 0;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = sorted.length >> 1;
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

// Cheap first pass: a minute can only hold a bad print if one of its prices is
// at least MIN_FRACTION away from the previous close. Almost no minute is, so
// the full neighbour check runs on a handful of bars per tape.
function isCandidate(bars, index) {
  const previous = bars[index - 1] || bars[index + 1];
  const anchor = Number(previous?.close);
  if (!(anchor > 0)) return false;
  const bar = bars[index];
  // Plain comparisons, no closures: this runs on every bar of a 30k-bar tape.
  const limit = Math.max(anchor * MIN_FRACTION, MIN_DOLLARS);
  const high = Number(bar.high);
  const low = Number(bar.low);
  // high and low bound open and close on a normalised bar, so checking the two
  // extremes covers all four prices.
  return high - anchor > limit || anchor - low > limit
    || Math.abs(Number(bar.open) - anchor) > limit
    || Math.abs(Number(bar.close) - anchor) > limit;
}

function badFieldsAt(bars, index) {
  const count = bars.length;
  const from = Math.max(0, index - NEIGHBOUR_WINDOW);
  const to = Math.min(count, index + NEIGHBOUR_WINDOW + 1);
  const closes = [];
  for (let cursor = from; cursor < to; cursor += 1) {
    if (cursor !== index) closes.push(Number(bars[cursor].close));
  }
  if (closes.length < 4) return null;
  const reference = median(closes);
  if (!(reference > 0)) return null;
  const moves = [];
  for (let cursor = Math.max(from, 1); cursor < to; cursor += 1) {
    if (cursor === index || cursor - 1 === index) continue;
    moves.push(Math.abs(Number(bars[cursor].close) - Number(bars[cursor - 1].close)) / reference);
  }
  const tolerance = Math.max(
    reference * Math.max(MIN_FRACTION, JUMPINESS_MULTIPLE * median(moves)),
    MIN_DOLLARS,
  );
  const nearby = [];
  for (
    let cursor = Math.max(0, index - CONFIRM_WINDOW);
    cursor < Math.min(count, index + CONFIRM_WINDOW + 1);
    cursor += 1
  ) {
    if (cursor === index) continue;
    FIELDS.forEach((field) => nearby.push(Number(bars[cursor][field])));
  }
  const bar = bars[index];
  const bad = FIELDS.filter((field) => {
    const value = Number(bar[field]);
    if (Math.abs(value - reference) <= tolerance) return false;
    return !nearby.some((other) => Math.abs(other - value) <= tolerance / 2);
  });
  if (!bad.length) return null;
  // Not enough minutes after this one to know whether price stayed there:
  // only a spike that came back inside its own minute counts.
  const hasFollowers = count - 1 - index >= CONFIRM_WINDOW;
  if (!hasFollowers && bad.includes("close")) return null;
  return { bad, reference };
}

function repairedBar(bars, index, bad, reference) {
  const bar = bars[index];
  const previousClose = Number(bars[index - 1]?.close);
  const clean = (value) => Number.isFinite(value) && value > 0;
  const badSet = new Set(bad);
  const open = badSet.has("open")
    ? (clean(previousClose) && !badSet.has("close") ? previousClose : reference)
    : Number(bar.open);
  const close = badSet.has("close") ? open : Number(bar.close);
  const high = badSet.has("high") ? Math.max(open, close) : Number(bar.high);
  const low = badSet.has("low") ? Math.min(open, close) : Number(bar.low);
  return {
    ...bar,
    open,
    high: Math.max(high, open, close),
    low: Math.min(low, open, close),
    close,
    badPrintRemoved: true,
  };
}

// Returns the SAME array when nothing was removed, so identity-keyed caches
// downstream (aggregation, studies) are not invalidated by this pass.
export function removeExtendedHoursBadPrints(bars) {
  if (!Array.isArray(bars) || bars.length < 5) return bars;
  let repaired = null;
  for (let index = 0; index < bars.length; index += 1) {
    if (!isCandidate(bars, index)) continue;
    if (!isExtendedHoursTime(bars[index].time)) continue;
    const verdict = badFieldsAt(bars, index);
    if (!verdict) continue;
    if (!repaired) repaired = bars.slice();
    repaired[index] = repairedBar(bars, index, verdict.bad, verdict.reference);
  }
  return repaired || bars;
}

// buildChartDisplayBars runs on every render and once a second for the forming
// candle. Cache by tape identity plus a signature of its tail (the live buffer
// is mutated in place), so an unchanged tape returns the same cleaned array
// and the aggregation cache behind it keeps hitting.
const cleanedCache = new WeakMap();

function tapeSignature(bars) {
  const last = bars[bars.length - 1];
  return `${bars.length}|${Number(bars[0]?.time)}|${Number(last?.time)}|${Number(last?.open)}|${Number(last?.high)}|${Number(last?.low)}|${Number(last?.close)}`;
}

export function withoutExtendedHoursBadPrints(bars) {
  if (!Array.isArray(bars) || bars.length < 5) return bars;
  const signature = tapeSignature(bars);
  const cached = cleanedCache.get(bars);
  if (cached && cached.signature === signature) return cached.cleaned;
  const cleaned = removeExtendedHoursBadPrints(bars);
  cleanedCache.set(bars, { signature, cleaned });
  return cleaned;
}
