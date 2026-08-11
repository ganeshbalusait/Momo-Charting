import test from "node:test";
import assert from "node:assert/strict";

import {
  OI_CHART_FOUR_HOUR_SEED_MIN_BARS,
  oiChartHasInitialStudySeed,
  oiChartNeedsInitialStudySeed,
} from "./oiChartInitialHistory.js";

test("only 4H requests the compact study seed", () => {
  for (const minutes of [3, 5, 10, 15, 30, 60, 120, 1440, 10080, 43200]) {
    assert.equal(oiChartNeedsInitialStudySeed(minutes), false);
  }
  assert.equal(oiChartNeedsInitialStudySeed(240), true);
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
