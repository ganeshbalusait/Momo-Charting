import assert from "node:assert/strict";
import { test } from "node:test";

import {
  BOTTOM_NAV_LABELS,
  isOverflowDestinationActive,
  overflowDestinationLabel,
  selectOverflowDestinations,
} from "./mobileOverflowNavigation.js";

// Mirrors optionNavItems.
const visibleNavItems = [
  { label: "Charts & OI" },
  { label: "Quick Options", displayLabel: "Options" },
  { label: "Auto Alert", displayLabel: "Alerts" },
  { label: "Premarket Scanner", displayLabel: "Scanner" },
  { label: "MomX Scanner" },
  { label: "Learn + Setup" },
  { label: "Watchlist" },
  { label: "News Feed" },
  { label: "Earnings Calendar" },
  { label: "Mag7 Scanner", displayLabel: "Mag7 Watchlist" },
  { label: "ROI Calc" },
  { label: "OI Level Script TOS", displayLabel: "OI Level Scripts" },
  { label: "Settings" },
];

test("the sheet offers exactly what the bar does not name", () => {
  const labels = selectOverflowDestinations(visibleNavItems).map((i) => i.label);
  assert.deepEqual(labels, [
    "Learn + Setup",
    "Watchlist",
    "News Feed",
    "Earnings Calendar",
    "Mag7 Scanner",
    "ROI Calc",
    "OI Level Script TOS",
    "Settings",
  ]);
});

// Settings has no bar button any more. If it ever falls out of the sheet too,
// there is no way to re-authorise Schwab from a phone - so this is load-bearing.
test("Settings is reachable from the sheet, or the phone cannot reach it at all", () => {
  const labels = selectOverflowDestinations(visibleNavItems).map((i) => i.label);
  assert.ok(labels.includes("Settings"));
  assert.ok(!BOTTOM_NAV_LABELS.includes("Settings"));
});

test("the bar's own destinations are never duplicated in the sheet", () => {
  const labels = selectOverflowDestinations(visibleNavItems).map((i) => i.label);
  BOTTOM_NAV_LABELS.forEach((covered) => assert.ok(!labels.includes(covered), `${covered} duplicated`));
});

test("the removed Chart + Chain board is not offered anywhere", () => {
  const labels = selectOverflowDestinations(visibleNavItems).map((i) => i.label);
  assert.ok(!labels.includes("OI Finder"));
  assert.ok(!BOTTOM_NAV_LABELS.includes("OI Finder"));
});

test("a destination absent upstream never reappears in the sheet", () => {
  const withoutWatchlist = visibleNavItems.filter((i) => i.label !== "Watchlist");
  const labels = selectOverflowDestinations(withoutWatchlist).map((i) => i.label);
  assert.ok(!labels.includes("Watchlist"));
});

test("a missing or malformed list yields an empty sheet, never a crash", () => {
  assert.deepEqual(selectOverflowDestinations(null), []);
  assert.deepEqual(selectOverflowDestinations(undefined), []);
  assert.deepEqual(selectOverflowDestinations([null, undefined, {}]), []);
});

test("More reads as active while one of its own destinations is on screen", () => {
  const overflow = selectOverflowDestinations(visibleNavItems);
  assert.equal(isOverflowDestinationActive("Watchlist", overflow), true);
  assert.equal(isOverflowDestinationActive("Settings", overflow), true);
  // A bar destination must light its own button, not More.
  assert.equal(isOverflowDestinationActive("Premarket Scanner", overflow), false);
  assert.equal(isOverflowDestinationActive("MomX Scanner", overflow), false);
  assert.equal(isOverflowDestinationActive(null, overflow), false);
  assert.equal(isOverflowDestinationActive("Watchlist", null), false);
});

test("renamed destinations show their display label, not the view key", () => {
  assert.equal(overflowDestinationLabel({ label: "OI Level Script TOS", displayLabel: "OI Level Scripts" }), "OI Level Scripts");
  assert.equal(overflowDestinationLabel({ label: "Watchlist" }), "Watchlist");
  assert.equal(overflowDestinationLabel(null), "");
});
