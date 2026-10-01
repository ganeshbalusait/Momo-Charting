"""Tests for the forming candle's derived volume. Appended to chartStreamBars.test.js."""
import io

TESTS = '''

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
'''

p = "frontend/src/chartStreamBars.test.js"
s = io.open(p, encoding="utf-8", newline="").read()
assert "totalVolume" not in s, "already appended"
io.open(p, "w", encoding="utf-8", newline="").write(s.rstrip("\n") + "\n" + TESTS)
print("appended forming-candle volume tests")
