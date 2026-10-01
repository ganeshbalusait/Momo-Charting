import assert from "node:assert/strict";
import test from "node:test";
import { rvolNotLiveReason } from "./momxCells.js";

// barAt is epoch SECONDS. 2026-09-25 is a Friday, EDT (UTC-4).
const et = (iso) => Date.parse(iso) / 1000;
const NOW_FRI_1030 = Date.parse("2026-09-25T10:30:00-04:00");
const NOW_SAT = Date.parse("2026-09-26T18:00:00-04:00");

test("a normal intraday reading today is live", () => {
  assert.equal(rvolNotLiveReason({ barAt: et("2026-09-25T10:22:00-04:00") }, NOW_FRI_1030), "");
});

test("the closing auction window 15:50-16:10 ET is not live, even the same day", () => {
  const eve = Date.parse("2026-09-25T16:20:00-04:00");
  for (const t of ["15:50", "15:57", "16:00", "16:09"]) {
    assert.equal(rvolNotLiveReason({ barAt: et(`2026-09-25T${t}:00-04:00`) }, eve), "auction", t);
  }
  assert.equal(rvolNotLiveReason({ barAt: et("2026-09-25T15:49:00-04:00") }, eve), "");
  assert.equal(rvolNotLiveReason({ barAt: et("2026-09-25T16:10:00-04:00") }, eve), "");
});

test("Friday's reading shown on the weekend is old (DHI 1d2)", () => {
  assert.equal(rvolNotLiveReason({ barAt: et("2026-09-25T14:05:00-04:00") }, NOW_SAT), "old");
  assert.equal(rvolNotLiveReason({ barAt: et("2026-09-25T15:58:00-04:00") }, NOW_SAT), "auction");
});

test("missing / bad stamps are left alone", () => {
  for (const cell of [null, {}, { barAt: 0 }, { barAt: "x" }]) assert.equal(rvolNotLiveReason(cell, NOW_SAT), "");
});
