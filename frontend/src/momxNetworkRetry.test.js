import assert from "node:assert/strict";
import test from "node:test";

import { fetchWithNetworkRetry, isNetworkDrop, networkDropMessage } from "./momxNetworkRetry.js";

const drop = () => Object.assign(new TypeError("FetchEvent.respondWith received an error: Load failed"));

test("a dropped refresh is retried and the second answer is used", async () => {
  let calls = 0;
  const response = await fetchWithNetworkRetry("/api/momx-scanner", {}, {
    sleep: async () => {},
    fetchImpl: async () => {
      calls += 1;
      if (calls === 1) throw drop();
      return { ok: true, status: 200 };
    },
  });
  assert.equal(calls, 2);
  assert.equal(response.status, 200);
});

test("a connection that stays down throws after the last retry", async () => {
  let calls = 0;
  await assert.rejects(
    fetchWithNetworkRetry("/x", {}, {
      sleep: async () => {},
      delays: [1, 1],
      fetchImpl: async () => { calls += 1; throw drop(); },
    }),
    TypeError,
  );
  assert.equal(calls, 3);
});

test("HTTP errors and aborts are not retried", async () => {
  let calls = 0;
  const response = await fetchWithNetworkRetry("/x", {}, {
    sleep: async () => {},
    fetchImpl: async () => { calls += 1; return { ok: false, status: 502 }; },
  });
  assert.equal(calls, 1);
  assert.equal(response.status, 502);
  const abort = Object.assign(new Error("aborted"), { name: "AbortError" });
  assert.equal(isNetworkDrop(abort), false);
  assert.equal(isNetworkDrop(new Error("The MomX scanner service answered with an error (500).")), false);
  assert.equal(isNetworkDrop(new Error("Load failed")), true);
});

test("the banner is plain words with the last update time in ET", () => {
  const text = networkDropMessage("2026-10-02T15:44:10Z");
  assert.match(text, /^Connection to the scanner dropped - retrying\. The rows below are from 11:44 AM ET\.$/);
  assert.equal(networkDropMessage(null), "Connection to the scanner dropped - retrying.");
});
