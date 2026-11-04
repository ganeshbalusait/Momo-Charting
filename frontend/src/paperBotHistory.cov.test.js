// Paper 3 / Paper 5 on the Bot page: what loadPaperHistory does with the
// answers the server really sends. The worker-down body is the exact shape
// api_server's proxy returns when :3010 is unreachable
// (tests/test_cov_trading_worker_routes_api.py pins it from the server side) -
// the page must call that "unavailable", never an empty account with $0 P/L.
import test from "node:test";
import assert from "node:assert/strict";

import { loadPaperHistory } from "./paperBotHistory.js";

const WORKER_DOWN = { warming: true, rows: [], events: [], lists: [], message: "MomX worker unavailable: <urlopen error [WinError 10061]>" };
const reply = (body, ok = true, status = 200) => async () => ({ ok, status, json: async () => body });

test("the proxy's worker-down body is 'unavailable', not an empty paper account", async () => {
  await assert.rejects(loadPaperHistory("paper3", 1, { fetcher: reply(WORKER_DOWN) }), /^Error: paper3 is unavailable from the paper service$/);
  await assert.rejects(loadPaperHistory("paper5", 5, { fetcher: reply(WORKER_DOWN) }), /paper5 is unavailable/);
});

test("a 403 for a non-admin and a 502 from the proxy surface their status", async () => {
  await assert.rejects(loadPaperHistory("paper3", 1, { fetcher: reply({ error: "admin only" }, false, 403) }), /HTTP 403/);
  await assert.rejects(loadPaperHistory("paper3", 1, { fetcher: reply({ error: "Bad Gateway" }, false, 502) }), /HTTP 502/);
});

test("an unknown account is refused before any request is made", async () => {
  let calls = 0;
  const fetcher = async () => { calls += 1; return { ok: true, json: async () => ({ mirrors: [] }) }; };
  await assert.rejects(loadPaperHistory("paper4", 1, { fetcher }), /Unknown paper account/);
  await assert.rejects(loadPaperHistory("", 1, { fetcher }), /Unknown paper account/);
  assert.equal(calls, 0);
});

test("the range is sent as days= and a reconciling snapshot is handed back untouched", async () => {
  const saved = { account: "paper5", snapshotSource: "saved", reconciling: true, rows: [{ buyId: "b1" }], history: [] };
  let asked = "";
  const fetcher = async (url) => { asked = url; return { ok: true, json: async () => ({ mirrors: [{ account: "paper3" }, saved] }) }; };
  assert.equal(await loadPaperHistory("paper5", 20, { fetcher }), saved);
  assert.equal(asked, "/api/momx-scanner/paper-mirror?days=20");
});
