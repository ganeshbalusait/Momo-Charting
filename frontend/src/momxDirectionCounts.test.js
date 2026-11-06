import assert from "node:assert/strict";
import test from "node:test";

import { countScanMatches, directionCounts, otherDirectionUrl, showsDirectionCounts } from "./momxDirectionCounts.js";

test("only the Mag7 scanner shows the BULL / BEAR pills", () => {
  assert.equal(showsDirectionCounts("Mag7"), true);
  assert.equal(showsDirectionCounts("Watchlist"), false);
  assert.equal(showsDirectionCounts(null), false);
});

test("counts scan matches in rows and rest; no board or a warming stub is unknown, not 0", () => {
  const board = { rows: [{ scanPass: true }, { scanPass: false }], rest: [{ scanPass: true }] };
  assert.equal(countScanMatches(board), 2);
  assert.equal(countScanMatches({ rows: [], rest: [] }), 0);
  assert.equal(countScanMatches({ rows: [], warming: true }), null);
  assert.equal(countScanMatches(null), null);
});

test("the side on screen uses its own count, the other side comes from its board", () => {
  const other = { list: "Mag7", rows: [{ scanPass: true }] };
  assert.deepEqual(directionCounts({ direction: "bear", currentMatches: 9, otherPayload: other, listName: "Mag7" }), { bull: 1, bear: 9 });
  assert.deepEqual(directionCounts({ direction: "bull", currentMatches: 1, otherPayload: { list: "Mag7", rows: [{ scanPass: true }, { scanPass: true }] }, listName: "Mag7" }), { bull: 1, bear: 2 });
  assert.deepEqual(directionCounts({ direction: "bull", currentMatches: 3, otherPayload: { list: "Watchlist", rows: [{ scanPass: true }] }, listName: "Mag7" }), { bull: 3, bear: null }, "a stale read for another list is ignored");
});

test("the other side's URL flips the direction", () => {
  assert.equal(otherDirectionUrl("/api/momx-scanner", "Mag7", "bull"), "/api/momx-scanner?list=Mag7&dir=bear");
  assert.equal(otherDirectionUrl("/api/momx-scanner", "Mag7", "bear"), "/api/momx-scanner?list=Mag7");
});
