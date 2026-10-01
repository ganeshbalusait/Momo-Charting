import assert from "node:assert/strict";
import test from "node:test";

import {
  chartSessionLinesForTimeframe,
  chartSessionWindowsForTimeframe,
} from "./chartSessionDisplay.js";

const lines = [
  { date: "2026-08-07", minute: 570, label: "Open (Retail)" },
  { date: "2026-08-07", minute: 600, label: "First 30m (Smart)" },
  { date: "2026-08-07", minute: 960, label: "Close" },
  { date: "2026-08-10", minute: 570, label: "Open (Retail)" },
  { date: "2026-08-10", minute: 690, label: "First 2h (Retracement)" },
  { date: "2026-08-10", minute: 960, label: "Close" },
];

test("keeps detailed session overlays on fine intraday charts", () => {
  const windows = [{ label: "PRE" }];
  assert.equal(chartSessionWindowsForTimeframe(windows, 5), windows);
  assert.equal(chartSessionLinesForTimeframe(lines, 5), lines);
});

test("declutters 4-hour session overlays without removing session context", () => {
  assert.deepEqual(
    chartSessionLinesForTimeframe(lines, 240).map(({ date, minute, label, subdued }) => ({ date, minute, label, subdued })),
    [
      { date: "2026-08-07", minute: 570, label: "", subdued: true },
      { date: "2026-08-07", minute: 960, label: "", subdued: true },
      { date: "2026-08-10", minute: 570, label: "Open (Retail)", subdued: true },
      { date: "2026-08-10", minute: 960, label: "Close", subdued: true },
    ],
  );
  assert.deepEqual(
    chartSessionWindowsForTimeframe([{ label: "PRE", tone: "premarket" }], 240),
    [{ label: "", tone: "premarket", subdued: true }],
  );
});

test("does not paint intraday sessions on day-or-higher charts", () => {
  assert.deepEqual(chartSessionWindowsForTimeframe([{ label: "PRE" }], 1_440), []);
  assert.deepEqual(chartSessionLinesForTimeframe(lines, 10_080), []);
});
