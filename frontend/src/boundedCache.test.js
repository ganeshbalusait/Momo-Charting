import test from "node:test";
import assert from "node:assert/strict";

import { setBoundedCacheEntry } from "./boundedCache.js";

test("evicts the least-recently-used settled entry", () => {
  const cache = new Map([["AAPL", { payload: 1 }], ["MSFT", { payload: 2 }]]);
  setBoundedCacheEntry(cache, "AAPL", cache.get("AAPL"), 2);
  setBoundedCacheEntry(cache, "NVDA", { payload: 3 }, 2);
  assert.deepEqual([...cache.keys()], ["AAPL", "NVDA"]);
});

test("does not evict an in-flight request", () => {
  const promise = Promise.resolve();
  const cache = new Map([["AAPL", { promise }], ["MSFT", { payload: 2 }]]);
  setBoundedCacheEntry(cache, "NVDA", { payload: 3 }, 2, (entry) => Boolean(entry?.promise));
  assert.deepEqual([...cache.keys()], ["AAPL", "NVDA"]);
});
