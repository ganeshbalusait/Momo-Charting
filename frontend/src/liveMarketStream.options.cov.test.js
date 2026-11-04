// Unit: merging live option ticks into the open chain (mergeLiveOptionRows),
// 2026-11-04 coverage. The chain rows carry Schwab's PADDED OCC key
// ("AAPL  261120C00230000", two spaces); the streamer echoes the leased key
// back, so a merge must match that exact key and nothing looser.
import assert from "node:assert/strict";
import test from "node:test";

import { mergeLiveOptionRows } from "./liveMarketStream.js";

const CALL = "AAPL  261120C00230000";
const PUT = "AAPL  261120P00225000";
const rows = Object.freeze([
  Object.freeze({ symbol: CALL, side: "CALL", strike: 230, bid: 4.0, ask: 4.2, mark: 4.1, last: 4.05, volume: 900, open_interest: 9000, delta: 0.5, gamma: 0.03 }),
  Object.freeze({ symbol: PUT, side: "PUT", strike: 225, bid: 1.0, ask: 1.1, mark: 1.05, last: 1.02, volume: 400, open_interest: 3000, delta: 0.3, gamma: 0.02 }),
]);

test("a tick on the padded OCC key updates only that row and stamps when it arrived", () => {
  const merged = mergeLiveOptionRows(rows, {
    symbol: CALL, receivedAt: "2026-11-04T15:00:01Z",
    data: { bid: 4.3, ask: 4.5, mark: 4.4, last: 4.45, totalVolume: 1250, openInterest: 9100, delta: 0.56 },
  });
  assert.notEqual(merged, rows);
  assert.deepEqual(
    [merged[0].bid, merged[0].ask, merged[0].mark, merged[0].volume, merged[0].open_interest, merged[0].delta],
    [4.3, 4.5, 4.4, 1250, 9100, 0.56],
  );
  assert.equal(merged[0].liveQuoteAt, "2026-11-04T15:00:01Z");
  assert.equal(merged[0].volume_oi_ratio, 0.14);
  assert.equal(merged[1], rows[1], "the untouched row keeps its identity (its memo does not re-render)");
});

test("a key without the OCC padding does not match a chain row", () => {
  for (const loose of ["AAPL261120C00230000", "AAPL_112026C230", "AAPL 261120C00230000"]) {
    assert.equal(mergeLiveOptionRows(rows, { symbol: loose, data: { mark: 9.99 } }), rows, loose);
  }
});

test("lower-case or edge-padded keys still match: only the inner padding is significant", () => {
  const merged = mergeLiveOptionRows(rows, { symbol: ` ${CALL.toLowerCase()} `, data: { mark: 4.4 } });
  assert.equal(merged[0].mark, 4.4);
});

test("another underlying's tick never touches this chain", () => {
  const tsla = { symbol: "TSLA  261120C00400000", data: { mark: 12.5, bid: 12, ask: 13 } };
  assert.equal(mergeLiveOptionRows(rows, tsla), rows);
});

test("a put's streamed delta is kept as a magnitude, like the REST chain", () => {
  const merged = mergeLiveOptionRows(rows, { symbol: PUT, data: { delta: -0.34, mark: 1.2 } });
  assert.equal(merged[1].delta, 0.34);
});

test("a tick with a missing field keeps the REST value instead of blanking the cell", () => {
  const merged = mergeLiveOptionRows(rows, { symbol: CALL, data: { mark: 4.25, bid: null, ask: "", openInterest: "n/a" } });
  assert.deepEqual([merged[0].mark, merged[0].bid, merged[0].ask, merged[0].open_interest], [4.25, 4.0, 4.2, 9000]);
});

test("several ticks in one coalesced flush each land on their own row", () => {
  const merged = mergeLiveOptionRows(rows, [
    { symbol: CALL, data: { mark: 4.6 } },
    { symbol: PUT, data: { mark: 0.95 } },
  ]);
  assert.deepEqual(merged.map((row) => row.mark), [4.6, 0.95]);
});
