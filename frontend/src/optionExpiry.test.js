import assert from "node:assert/strict";
import test from "node:test";

import { isCurrentOptionExpiry, optionExpiryDte } from "./optionExpiry.js";

test("keeps same-day and future expiries in the current option window", () => {
  assert.equal(optionExpiryDte("2026-07-30", "2026-07-30"), 0);
  assert.equal(optionExpiryDte("2026-07-31", "2026-07-30"), 1);
  assert.equal(isCurrentOptionExpiry("2026-08-28", "2026-07-30", 31), true);
});

test("does not relabel expired contracts as zero DTE", () => {
  assert.equal(optionExpiryDte("2026-07-24", "2026-07-30"), -6);
  assert.equal(isCurrentOptionExpiry("2026-07-24", "2026-07-30", 31), false);
});

test("rejects expiries outside the configured window and invalid dates", () => {
  assert.equal(isCurrentOptionExpiry("2026-09-18", "2026-07-30", 31), false);
  assert.equal(optionExpiryDte("", "2026-07-30"), null);
  assert.equal(optionExpiryDte("2026-07-31", ""), null);
});
