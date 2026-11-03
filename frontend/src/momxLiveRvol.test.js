import test from "node:test";
import assert from "node:assert/strict";

import { liveRvolCell, reuseLiveCells } from "./momxLiveRvol.js";

const rvol5 = { section: "rvol", tf: "5m" };
const board = { value: -0.1, bg: "black", fg: "black", pace: 0.2, xAvg: 9.1, barAt: 1 };

test("an RVOL column shows the live value and drops the board-only notes", () => {
  const live = { "5m": { value: 0.8, bg: "black", fg: "dark_green", barAt: 5 } };
  assert.deepEqual(liveRvolCell(board, rvol5, live), { value: 0.8, bg: "black", fg: "dark_green", barAt: 5, live: true });
});

test("other columns, missing timeframes and no live data keep the board cell", () => {
  const live = { "5m": { value: 0.8 } };
  assert.equal(liveRvolCell(board, { section: "sqz", tf: "5m" }, live), board);
  assert.equal(liveRvolCell(board, { section: "rvol", tf: "1h" }, live), board);
  assert.equal(liveRvolCell(board, rvol5, null), board);
  assert.equal(liveRvolCell(board, rvol5, { "5m": { value: null } }), board);
});

test("unchanged live cells keep their identity between polls (no row re-render)", () => {
  const first = reuseLiveCells({}, { AAA: { "5m": { value: 0.8 } } });
  const again = reuseLiveCells(first, { AAA: { "5m": { value: 0.8 } } });
  assert.equal(again, first);
  const moved = reuseLiveCells(first, { AAA: { "5m": { value: 0.9 } } });
  assert.notEqual(moved, first);
});
