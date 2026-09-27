import test from "node:test";
import assert from "node:assert/strict";

import { latestUsableHeatmapDay } from "./oiHeatmapDay.js";

const row = (date, delta) => ({ date, delta, strike: 220 });

test("the overnight zero-delta day does not empty the heatmap", () => {
  // 2026-08-20 00:19 ET: the recorder wrote a full new day whose greeks were
  // all zero because the broker stops publishing them after the close.
  const days = ["2026-08-18", "2026-08-19", "2026-08-20"];
  const rows = [
    row("2026-08-19", 0.45),
    row("2026-08-19", 0.62),
    ...Array.from({ length: 20 }, () => row("2026-08-20", 0)),
  ];
  assert.equal(latestUsableHeatmapDay(days, rows), "2026-08-19");
});

test("a fresh session day with real greeks still wins", () => {
  const days = ["2026-08-19", "2026-08-20"];
  const rows = [row("2026-08-19", 0.45), row("2026-08-20", 0.51)];
  assert.equal(latestUsableHeatmapDay(days, rows), "2026-08-20");
});

test("deltas outside the band never select a day", () => {
  const days = ["2026-08-19", "2026-08-20"];
  const rows = [row("2026-08-19", 0.45), row("2026-08-20", 0.99), row("2026-08-20", 0.05)];
  assert.equal(latestUsableHeatmapDay(days, rows), "2026-08-19");
});

test("puts count through their negative delta", () => {
  const days = ["2026-08-19", "2026-08-20"];
  assert.equal(latestUsableHeatmapDay(days, [row("2026-08-20", -0.36)]), "2026-08-20");
});

test("an empty payload still reports its newest day", () => {
  assert.equal(latestUsableHeatmapDay(["2026-08-19", "2026-08-20"], []), "2026-08-20");
  assert.equal(latestUsableHeatmapDay([], [row("2026-08-20", 0.4)]), "");
});
