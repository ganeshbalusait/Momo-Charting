import test from "node:test";
import assert from "node:assert/strict";

import {
  OI_CHART_FOUR_HOUR_SEED_MIN_BARS,
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
