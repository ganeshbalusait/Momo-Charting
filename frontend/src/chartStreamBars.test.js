import assert from "node:assert/strict";
import test from "node:test";

import {
  isSchwabTosChartPacket,
  mergeLatestStreamBar,
  reconcileRestBarsWithLiveTail,
  shouldUseEquityTradeForChart,
} from "./chartStreamBars.js";

test("REST reconciliation heals a corrupt same-minute live candle", () => {
  const rest = [{
    time: 120,
    open: 357.39,
    high: 357.39,
    low: 357.30,
    close: 357.35,
    volume: 698,
  }];
  const live = [{
    time: 120,
    open: 335.21,
    high: 357.39,
    low: 335.21,
    close: 357.38,
    volume: 0,
  }];

  assert.deepEqual(reconcileRestBarsWithLiveTail(rest, live), [{
    time: 120,
    open: 357.39,
    high: 357.39,
    low: 357.30,
    close: 357.38,
    volume: 698,
  }]);
});

test("REST reconciliation keeps a strictly newer live candle", () => {
  const rest = [{ time: 120, open: 10, high: 11, low: 9, close: 10, volume: 100 }];
  const newer = { time: 180, open: 12, high: 13, low: 11, close: 12.5, volume: 25 };

  assert.deepEqual(reconcileRestBarsWithLiveTail(rest, [newer]), [...rest, newer]);
});

test("updates the forming candle without copying the full history array", () => {
  const bars = [{ time: 60, open: 10, high: 11, low: 9, close: 10, volume: 100 }];
  const result = mergeLatestStreamBar(bars, { time: 95, close: 12 }, true);

  assert.equal(result.changed, true);
  assert.equal(result.appended, false);
  assert.equal(result.bars, bars);
  assert.deepEqual(result.bars[0], {
    time: 60,
    open: 10,
    high: 12,
    low: 9,
    close: 12,
    volume: 100,
    // Day-volume bookkeeping added 2026-09-04 so the forming candle can derive
    // a real volume instead of being stamped 0. Undefined here because this
    // packet carries no totalVolume.
    volumeKnown: true,
    dayVolumeAtOpen: undefined,
    dayVolumeSeen: undefined,
  });
});

test("allocates a new history array only when a new minute is appended", () => {
  const bars = [{ time: 60, open: 10, high: 11, low: 9, close: 10, volume: 100 }];
  const result = mergeLatestStreamBar(bars, {
    time: 120,
    open: 12,
    high: 13,
    low: 11,
    close: 12.5,
    volume: 25,
  });

  assert.equal(result.changed, true);
  assert.equal(result.appended, true);
  assert.notEqual(result.bars, bars);
  assert.equal(bars.length, 1);
  assert.equal(result.bars.length, 2);
});

test("ignores delayed packets older than the newest candle", () => {
  const bars = [{ time: 120, open: 12, high: 13, low: 11, close: 12.5, volume: 25 }];
  const result = mergeLatestStreamBar(bars, { time: 60, close: 9 }, true);

  assert.equal(result.changed, false);
  assert.equal(result.bars, bars);
  assert.equal(result.bars[0].close, 12.5);
});

test("does not let a delayed chart snapshot replace a newer trade close", () => {
  const bars = [{ time: 60, open: 10, high: 12, low: 9, close: 12, volume: 100 }];
  const result = mergeLatestStreamBar(bars, {
    time: 95,
    open: 10,
    high: 15,
    low: 7,
    close: 9.75,
    volume: 140,
  }, false, true);

  assert.equal(result.changed, true);
  assert.equal(result.bars, bars);
  assert.deepEqual(result.bars[0], {
    time: 60,
    open: 10,
    high: 12,
    low: 9,
    close: 12,
    volume: 140,
    volumeKnown: true,
    dayVolumeAtOpen: undefined,
    dayVolumeSeen: undefined,
  });
});

test("uses a Level-1 trade in the same minute as CHART_EQUITY", () => {
  assert.equal(shouldUseEquityTradeForChart({
    equityTime: 125,
    latestBarTime: 120,
  }), true);
});

test("uses a newer Level-1 minute instead of waiting for a delayed chart packet", () => {
  assert.equal(shouldUseEquityTradeForChart({
    equityTime: 185,
    latestBarTime: 120,
  }), true);
});

test("rejects an old Level-1 trade timestamp after a newer candle is visible", () => {
  assert.equal(shouldUseEquityTradeForChart({
    equityTime: 119,
    latestBarTime: 120,
  }), false);
});

test("accepts only Schwab/TOS packets as visible chart writers", () => {
  assert.equal(isSchwabTosChartPacket({ data: { source: "schwab" } }), true);
  assert.equal(isSchwabTosChartPacket({ data: { source: "schwab-rest-1s" } }), true);
  assert.equal(isSchwabTosChartPacket({ data: {} }), true);
  assert.equal(isSchwabTosChartPacket({ data: { source: "alpaca:iex" } }), false);
  assert.equal(isSchwabTosChartPacket({ data: { source: "another-provider" } }), false);
});

// A stale tape plus a live quote produced the "gap": the REST tape ended at
// yesterday's close (or hours ago, for a watchlist ticker not opened today)
// while the 1s quote fallback drew a single synthetic bar at "now" with
// O=H=L=C and Vol 0. Lightweight Charts spaces bars by index, so the future
// whitespace projected from that lone bar rendered as a wide empty band with
// a disconnected spike on the right.
test("a quote far ahead of a stale tape is not admitted as a candle", () => {
  const hour = 3_600;
  const latestBarTime = 1_786_000_000;
  // Same session, a few minutes ahead: this is the normal live candle.
  assert.equal(shouldUseEquityTradeForChart({
    equityTime: latestBarTime + 120,
    latestBarTime,
  }), true);
  // Hours ahead of the newest bar: the tape is stale, not the quote early.
  // Drawing this creates the disconnected spike, so it must be refused until
  // real candles close the distance.
  assert.equal(shouldUseEquityTradeForChart({
    equityTime: latestBarTime + 6 * hour,
    latestBarTime,
  }), false);
});

test("an empty tape still accepts the first live bar", () => {
  // With no bars at all there is nothing to be disconnected from, and
  // refusing here would leave a cold chart permanently blank.
  assert.equal(shouldUseEquityTradeForChart({
    equityTime: 1_786_000_000,
    latestBarTime: 0,
  }), true);
});

test("a quote older than the newest bar is still refused", () => {
  assert.equal(shouldUseEquityTradeForChart({
    equityTime: 1_786_000_000 - 120,
    latestBarTime: 1_786_000_000,
  }), false);
});


// ---------------------------------------------------------------------------
// The forming candle's volume, derived from Schwab's day-cumulative
// totalVolume. Before this, a trade tick opening a minute stamped a hard 0 and
// the OHLC strip read "Vol 0" over a bar the server had at 22,821.
// ---------------------------------------------------------------------------
const MIN = 1788558240;   // a real minute boundary from the live stream

test("a trade opening a new minute derives volume from the day total", () => {
  // Previous minute ended with the day at 1,000,000. Now the day is at
  // 1,000,250 -> 250 shares traded inside this minute.
  const prior = [{
    time: MIN - 60, open: 1, high: 1, low: 1, close: 1,
    volume: 500, dayVolumeSeen: 1_000_000,
  }];
  const out = mergeLatestStreamBar(prior, { time: MIN, close: 2, totalVolume: 1_000_250 }, true);
  const bar = out.bars.at(-1);
  assert.equal(bar.time, MIN);
  assert.equal(bar.volume, 250);
  assert.equal(bar.volumeKnown, true);
});

test("later trades in the same minute accumulate, they do not reset", () => {
  const prior = [{
    time: MIN - 60, open: 1, high: 1, low: 1, close: 1, volume: 5, dayVolumeSeen: 1_000_000,
  }];
  let out = mergeLatestStreamBar(prior, { time: MIN, close: 2, totalVolume: 1_000_100 }, true);
  out = mergeLatestStreamBar(out.bars, { time: MIN + 10, close: 3, totalVolume: 1_000_400 }, true);
  assert.equal(out.bars.at(-1).volume, 400);
});

test("volume never goes backwards on a late or out-of-order packet", () => {
  const prior = [{
    time: MIN - 60, open: 1, high: 1, low: 1, close: 1, volume: 5, dayVolumeSeen: 1_000_000,
  }];
  let out = mergeLatestStreamBar(prior, { time: MIN, close: 2, totalVolume: 1_000_900 }, true);
  assert.equal(out.bars.at(-1).volume, 900);
  // a stale packet reporting an older day total must not shrink it
  out = mergeLatestStreamBar(out.bars, { time: MIN + 5, close: 2, totalVolume: 1_000_200 }, true);
  assert.equal(out.bars.at(-1).volume, 900);
});

test("a chart packet's authoritative volume wins over the derived one", () => {
  // Schwab computes the real per-minute volume and sends it on the chart
  // event; anything derived here is an approximation by comparison.
  const prior = [{
    time: MIN - 60, open: 1, high: 1, low: 1, close: 1, volume: 5, dayVolumeSeen: 1_000_000,
  }];
  let out = mergeLatestStreamBar(prior, { time: MIN, close: 2, totalVolume: 1_000_250 }, true);
  assert.equal(out.bars.at(-1).volume, 250);
  out = mergeLatestStreamBar(out.bars, { time: MIN, close: 2, volume: 1100 }, false);
  assert.equal(out.bars.at(-1).volume, 1100);
});

test("with no day total at all the volume is flagged unknown, not asserted as 0", () => {
  // The 0 still ships because Lightweight Charts rejects a null histogram
  // value and would blank the whole series - the honesty rides on volumeKnown.
  const out = mergeLatestStreamBar([], { time: MIN, close: 2 }, true);
  const bar = out.bars.at(-1);
  assert.equal(bar.volume, 0);
  assert.equal(bar.volumeKnown, false);
});

test("the first minute ever seen anchors on its own total rather than inventing one", () => {
  const out = mergeLatestStreamBar([], { time: MIN, close: 2, totalVolume: 1_000_000 }, true);
  const bar = out.bars.at(-1);
  assert.equal(bar.volume, 0);          // nothing accumulated yet against that anchor
  assert.equal(bar.dayVolumeAtOpen, 1_000_000);
  assert.equal(bar.volumeKnown, true);
});
