import assert from "node:assert/strict";
import test from "node:test";

import { limitChainRowsAroundAtm } from "./chainStrikeWindow.js";

const rows = Array.from({ length: 80 }, (_, index) => ({ strike: 736 + index }));

test("strike depth means the requested count on each side of ATM", () => {
  assert.deepEqual(
    limitChainRowsAroundAtm(rows, 776, 5).map((row) => row.strike),
    Array.from({ length: 11 }, (_, index) => 771 + index),
  );
  assert.equal(limitChainRowsAroundAtm(rows, 776, 10).length, 21);
  assert.equal(limitChainRowsAroundAtm(rows, 776, 15).length, 31);
});

test("All returns every listed strike instead of the previous delta-band subset", () => {
  assert.strictEqual(limitChainRowsAroundAtm(rows, 776, "all"), rows);
  assert.equal(limitChainRowsAroundAtm(rows, 776, "all").length, 80);
});

test("strike depth remains safe when ATM is near a listed-chain edge", () => {
  assert.deepEqual(
    limitChainRowsAroundAtm(rows, 738, 15).map((row) => row.strike),
    rows.slice(0, 18).map((row) => row.strike),
  );
});
