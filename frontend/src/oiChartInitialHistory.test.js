import test from "node:test";
import assert from "node:assert/strict";

import {
  OI_CHART_FOUR_HOUR_SEED_MIN_BARS,
  OI_CHART_STUDY_SEED_RETRY_MAX_DELAY_MS,
  nextStudySeedRetryDelayMs,
  oiChartHasInitialStudySeed,
  oiChartNeedsInitialStudySeed,
} from "./oiChartInitialHistory.js";

test("every timeframe that reads the study tape requests the seed", () => {
  // Was "only 4H requests the compact study seed". The 30-minute tape is the
  // history source for every timeframe at least as coarse as its cadence:
  // 30m/1h/2h/4h aggregate their candles from it, and D/W/M read it for
  // studies. Seeding only 4H left the others rendering from the fast-paint
  // slice, whose studyBars is empty, so they fell back to the ~5-day
  // one-minute tape - about 17-20 candles on 4H and fewer above it.
  for (const minutes of [3, 5, 10, 15]) {
    assert.equal(oiChartNeedsInitialStudySeed(minutes), false, `${minutes}m must not pay for the seed`);
  }
  for (const minutes of [30, 60, 120, 240, 1_440, 10_080, 43_200]) {
    assert.equal(oiChartNeedsInitialStudySeed(minutes), true, `${minutes}m needs the study seed`);
  }
});

test("a prefetched shallow payload cannot strand a 4H opening viewport", () => {
  assert.equal(oiChartHasInitialStudySeed({ studyBars: [] }), false);
  assert.equal(oiChartHasInitialStudySeed({
    initialSlim: true,
    studyBars: Array.from({ length: 900 }),
  }), false, "aliased 5m bars are not a real 4H seed");
  assert.equal(oiChartHasInitialStudySeed({
    initialSlim: true,
    initialStudySeed: true,
    studyBars: Array.from({ length: OI_CHART_FOUR_HOUR_SEED_MIN_BARS }),
  }), true);
  assert.equal(oiChartHasInitialStudySeed({
    historyLoading: false,
    studyBars: Array.from({ length: OI_CHART_FOUR_HOUR_SEED_MIN_BARS }),
  }), true, "a complete tape remains valid without the initial marker");
});

// The guard above only recognises the aliased tape when the payload still
// carries `initialSlim`. The effect that gates the 4H seed request holds the
// TAPE, not the response, so it calls this with a synthetic `{ studyBars }`
// rebuilt from React state - and that object has no flags at all.
//
// normalizeOiChartPayload aliases the ONE-MINUTE live tape into studyBars
// whenever a response omits the study tape, which is exactly what the slim
// first paint does. 900 aliased one-minute rows cleared the 80-bar test, the
// effect returned early, and a pane opened on 5m and switched to 4H never
// requested its 30-minute seed: 6 candles instead of ~1,100.
//
// Cadence is the property that survives losing the flag - a genuine study tape
// is 5m or 30m, never 1m.
test("a flag-less one-minute tape is not mistaken for the 4H seed", () => {
  const tape = (count, stepMinutes) => Array.from({ length: count }, (_, index) => ({
    time: 1_700_000_000 + index * stepMinutes * 60,
    open: 1, high: 1, low: 1, close: 1, volume: 0,
  }));

  assert.equal(
    oiChartHasInitialStudySeed({ studyBars: tape(900, 1) }),
    false,
    "the aliased one-minute live tape must still request the 30-minute seed",
  );
  assert.equal(
    oiChartHasInitialStudySeed({ studyBars: tape(1_600, 30) }),
    true,
    "a genuine 30-minute seed is accepted without any flag",
  );
  assert.equal(
    oiChartHasInitialStudySeed({ studyBars: tape(400, 5) }),
    true,
    "the five-minute tape the backend has also shipped stays valid",
  );
});

// A seed fetch that resolves EMPTY used to strand the pane forever: the effect
// that requests it re-runs only when symbol, timeframe or studyBars.length
// changes, and an empty result changes none of them. That is how a 4H pane
// ended up with a populated OHLC header, live streaming and price lines drawn
// over a blank canvas with no price axis, while the 5m pane beside it (which
// never needs the seed) rendered normally.
test("an empty study seed backs off instead of giving up", () => {
  assert.equal(nextStudySeedRetryDelayMs(1), 1_500);
  assert.equal(nextStudySeedRetryDelayMs(2), 3_000);
  assert.equal(nextStudySeedRetryDelayMs(3), 6_000);
  assert.equal(nextStudySeedRetryDelayMs(4), 12_000);
});

test("retries never stop and never hammer", () => {
  // A blank chart is not an acceptable resting state, so retries continue -
  // but capped, so a server that stays cold is polled gently rather than hit
  // in a tight loop.
  assert.equal(nextStudySeedRetryDelayMs(5), OI_CHART_STUDY_SEED_RETRY_MAX_DELAY_MS);
  assert.equal(nextStudySeedRetryDelayMs(50), OI_CHART_STUDY_SEED_RETRY_MAX_DELAY_MS);
  assert.ok(OI_CHART_STUDY_SEED_RETRY_MAX_DELAY_MS >= 10_000, "cap must be gentle");
});

test("a nonsense attempt count still produces a usable delay", () => {
  assert.equal(nextStudySeedRetryDelayMs(0), 1_500);
  assert.equal(nextStudySeedRetryDelayMs(Number.NaN), 1_500);
  assert.equal(nextStudySeedRetryDelayMs(undefined), 1_500);
});
