import assert from "node:assert/strict";
import { test } from "node:test";

import {
  initialChartChainOpen,
  isMobileChartWidth,
  shouldAutoLoadDeepChartHistory,
  shouldRequestDeepChartHistory,
} from "./mobileChartPerformance.js";

test("the mobile breakpoint is inclusive and rejects unusable widths", () => {
  assert.equal(isMobileChartWidth(390), true);
  assert.equal(isMobileChartWidth(760), true);
  assert.equal(isMobileChartWidth(761), false);
  assert.equal(isMobileChartWidth(0), false);
  assert.equal(isMobileChartWidth("unknown"), false);
});

test("phones start chart-only while desktop and popouts retain the chain", () => {
  assert.equal(initialChartChainOpen({ isMobile: true, saved: "true" }), false);
  assert.equal(initialChartChainOpen({ isMobile: false, saved: null }), true);
  assert.equal(initialChartChainOpen({ isMobile: false, saved: "false" }), false);
  assert.equal(initialChartChainOpen({ embedded: true, isMobile: true, saved: "false" }), true);
});

test("desktop promotes deep history automatically and mobile waits for demand", () => {
  assert.equal(shouldAutoLoadDeepChartHistory({ isMobile: false }), true);
  assert.equal(shouldAutoLoadDeepChartHistory({ isMobile: true, requested: false }), false);
  assert.equal(shouldAutoLoadDeepChartHistory({ isMobile: true, requested: true }), true);
});

test("mobile requests deep history only when the compact tape reaches the viewport", () => {
  const base = { isMobile: true, historyLoading: true, barCount: 180 };
  assert.equal(shouldRequestDeepChartHistory({ ...base, logicalRange: { from: 120, to: 160 } }), false);
  assert.equal(shouldRequestDeepChartHistory({ ...base, logicalRange: { from: 20, to: 60 } }), true);
  assert.equal(shouldRequestDeepChartHistory({ ...base, logicalRange: { from: -4, to: 36 } }), true);
  assert.equal(shouldRequestDeepChartHistory({ ...base, alreadyRequested: true, logicalRange: { from: 0, to: 40 } }), false);
  assert.equal(shouldRequestDeepChartHistory({ ...base, historyLoading: false, logicalRange: { from: 0, to: 40 } }), false);
  assert.equal(shouldRequestDeepChartHistory({ ...base, isMobile: false, logicalRange: { from: 0, to: 40 } }), false);
});
