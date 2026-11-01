import test from "node:test";
import assert from "node:assert/strict";
import { buildWoodiesPivots, weekdayIndex, woodiesPeriodKey } from "./woodiesPivots.js";

const et = (iso) => Date.parse(`${iso}T01:00:00-04:00`) / 1000;

test("weekday index matches momx/buckets.py weekday_index", () => {
  assert.equal(weekdayIndex("1970-01-01"), 0);
  assert.equal(weekdayIndex("1970-01-05"), 2);
  assert.equal(weekdayIndex("2026-10-08"), 14810);
  assert.equal(weekdayIndex("2026-10-12"), 14812);
  assert.equal(weekdayIndex("2024-02-29"), 14130);
});

test("4 DAYS chunks are the TOS weekday chunks (10/2-10/7, then 10/8-10/13)", () => {
  const key = (d) => woodiesPeriodKey(d, "4 DAYS");
  assert.equal(key("2026-10-01"), "4D-3702");
  assert.equal(key("2026-10-02"), "4D-3703");
  assert.equal(key("2026-10-07"), "4D-3703");
  assert.equal(key("2026-10-08"), "4D-3704");
  assert.equal(key("2026-10-13"), "4D-3704");
});

test("levels: previous period high/low + current period open; show only today = latest period", () => {
  const days = [
    ["2026-10-02", 10, 12, 9], ["2026-10-05", 11, 14, 10], ["2026-10-06", 13, 15, 12], ["2026-10-07", 14, 16, 13],
    ["2026-10-08", 15, 17, 14],
  ].map(([d, open, high, low]) => ({ time: et(d), open, high, low, close: open, volume: 1 }));
  const out = buildWoodiesPivots(days, days, { woodiesPivotsTimeFrame: "4 DAYS", woodiesPivotsShowOnlyToday: true });
  // previous period 10/2-10/7: H 16, L 9; current open 15
  const pp = (16 + 9 + 2 * 15) / 4;
  const r1 = 2 * pp - 9;
  const s1 = 2 * pp - 16;
  assert.deepEqual(out.PP, [{ time: et("2026-10-08"), value: pp }]);
  assert.equal(out.R1[0].value, r1);
  assert.equal(out.S1[0].value, s1);
  assert.equal(out.R2[0].value, pp + r1 - s1);
  assert.equal(out.S3[0].value, (pp + s1 - r1) + s1 - pp);
  const all = buildWoodiesPivots(days, days, { woodiesPivotsTimeFrame: "4 DAYS", woodiesPivotsShowOnlyToday: false });
  assert.equal(all.PP.length, 1 + 0); // 10/2-10/7 has no earlier period in this sample
});
