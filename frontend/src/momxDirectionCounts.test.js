import assert from "node:assert/strict";
import test from "node:test";

import { countScanMatches, directionCounts, otherDirectionUrl, otherSideRows, showsDirectionCounts } from "./momxDirectionCounts.js";

// 2026-11-16 "for mag7 show bull and bear scanner below bull so I no need to switch".
test("the other side's table: its scan matches (rows and rest), losers first under BULL, gainers first under BEAR", () => {
  const bear = { list: "Mag7", rows: [
    { symbol: "SPY", scanPass: true, pctChange: -0.7 },
    { symbol: "AAPL", scanPass: false, pctChange: -2 },
    { symbol: "QQQ", scanPass: true, pctChange: -1.5 },
  ], rest: [{ symbol: "TSLA", scanPass: true, pctChange: -0.2 }] };
  assert.deepEqual(otherSideRows({ direction: "bull", otherPayload: bear, listName: "Mag7" }).map((row) => row.symbol), ["QQQ", "SPY", "TSLA"]);
  const bull = { list: "Mag7", rows: [{ symbol: "NVDA", scanPass: true, pctChange: 0.4 }, { symbol: "AAPL", scanPass: true, pctChange: 1.2 }] };
  assert.deepEqual(otherSideRows({ direction: "bear", otherPayload: bull, listName: "Mag7" }).map((row) => row.symbol), ["AAPL", "NVDA"]);
  assert.equal(otherSideRows({ direction: "bull", otherPayload: { ...bear, list: "Watchlist" }, listName: "Mag7" }), null, "a stale read for another list");
  assert.equal(otherSideRows({ direction: "bull", otherPayload: null, listName: "Mag7" }), null, "not read yet");
  assert.equal(otherSideRows({ direction: "bull", otherPayload: { list: "Mag7", rows: [], warming: true }, listName: "Mag7" }), null, "still building");
  assert.deepEqual(otherSideRows({ direction: "bull", otherPayload: { list: "Mag7", rows: [{ symbol: "X", scanPass: true }, null] }, listName: "Mag7" }).map((row) => row.symbol), ["X"], "no % change sorts last, junk dropped");
});

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
