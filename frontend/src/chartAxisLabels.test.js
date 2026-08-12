import assert from "node:assert/strict";
import test from "node:test";

import { layoutChartAxisLabels, mergePivotAndOiAxisLabels } from "./chartAxisLabels.js";

test("keeps a pivot label visible when an OI wall shares the same price", () => {
  const labels = mergePivotAndOiAxisLabels([
    { key: "pivot-WEEK-R1", price: 272.5, title: "W P R1 272.50", color: "#ff0000" },
    { key: "oi-wall-0-272.5", price: 272.5, title: "C S 272.5", color: "#0099cc", preservePricePosition: true, priority: 5 },
  ]);
  assert.equal(labels.length, 1);
  assert.equal(labels[0].title, "W P R1 272.50 · C S 272.5");
  assert.equal(labels[0].preservePricePosition, true);
  assert.equal(labels[0].priority, 5);
  assert.deepEqual(labels[0].parts.map(({ color }) => color), ["#ff0000", "#0099cc"]);
});

test("does not merge nearby but different prices", () => {
  const labels = mergePivotAndOiAxisLabels([
    { key: "pivot-WEEK-R1", price: 272.5, title: "W P R1", color: "#ff0000" },
    { key: "oi-wall-0-272", price: 272, title: "C S 272", color: "#0099cc" },
  ]);
  assert.equal(labels.length, 2);
});

test("keeps every surviving label fixed to its indicator line", () => {
  const labels = layoutChartAxisLabels([
    { key: "live-price", top: 100, preservePricePosition: true },
    { key: "oi-wall-1-100", top: 103, preservePricePosition: true, priority: 5 },
    { key: "session-level-dH", top: 101 },
    { key: "9eD-100", top: 102 },
    { key: "21eD-100", top: 104 },
  ], { paneHeight: 160, minimumGap: 18 });

  assert.equal(labels.find(({ key }) => key === "live-price")?.top, 100);
  assert.equal(labels.some(({ key }) => key === "oi-wall-1-100"), false);
  assert.ok(labels.every(({ top }) => [100, 101, 102, 104].includes(top)));
  for (let index = 1; index < labels.length; index += 1) {
    assert.ok(labels[index].top - labels[index - 1].top >= 18);
  }
});

test("uses a pane-size label budget and retains higher-value study labels", () => {
  const labels = layoutChartAxisLabels([
    { key: "generic-1", top: 41 },
    { key: "generic-2", top: 42 },
    { key: "generic-3", top: 43 },
    { key: "pivot-WEEK-R1", top: 44 },
    { key: "session-level-pmH", top: 45 },
  ], { paneHeight: 60, padding: 9, minimumGap: 18 });

  assert.ok(labels.length <= 3);
  assert.ok(labels.some(({ key }) => key === "session-level-pmH"));
  assert.equal(labels.find(({ key }) => key === "session-level-pmH")?.top, 45);
  assert.equal(labels.some(({ key }) => key === "pivot-WEEK-R1"), false);
  for (let index = 1; index < labels.length; index += 1) {
    assert.ok(labels[index].top - labels[index - 1].top >= 18);
  }
});

test("does not move labels away from the pane edge", () => {
  const labels = layoutChartAxisLabels([
    { key: "near-top", top: 4 },
    { key: "inside", top: 9 },
    { key: "near-bottom", top: 156 },
  ], { paneHeight: 160, padding: 9, minimumGap: 18 });

  assert.deepEqual(labels.map(({ key, top }) => ({ key, top })), [
    { key: "inside", top: 9 },
  ]);
});
