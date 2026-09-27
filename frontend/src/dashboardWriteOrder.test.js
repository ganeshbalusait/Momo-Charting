import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { createDashboardWriteOrder } from "./dashboardWriteOrder.js";

// The exact reported bug: "why is delete not working?" - it was working, and a
// stale poll response was putting the row back.
test("a poll issued before a delete must not overwrite the delete", () => {
  const order = createDashboardWriteOrder();
  const pollToken = order.beginPoll();   // poll leaves, server still has 358
  order.commitMutation();                // DELETE lands, state is now 357
  assert.equal(order.shouldApplyPoll(pollToken), false, "stale poll resurrected the deleted ticker");
});

test("an uncontended poll still applies, or the dashboard would freeze", () => {
  const order = createDashboardWriteOrder();
  const pollToken = order.beginPoll();
  assert.equal(order.shouldApplyPoll(pollToken), true);
});

test("a poll issued after the mutation is fresh and applies", () => {
  const order = createDashboardWriteOrder();
  order.commitMutation();
  const pollToken = order.beginPoll();
  assert.equal(order.shouldApplyPoll(pollToken), true);
});

test("several mutations during one poll still invalidate it exactly once", () => {
  const order = createDashboardWriteOrder();
  const pollToken = order.beginPoll();
  order.commitMutation();
  order.commitMutation();
  order.commitMutation();
  assert.equal(order.shouldApplyPoll(pollToken), false);
  // ...and the next poll, issued after all of them, is clean again.
  assert.equal(order.shouldApplyPoll(order.beginPoll()), true);
});

test("two write orders do not share state", () => {
  const a = createDashboardWriteOrder();
  const b = createDashboardWriteOrder();
  const tokenB = b.beginPoll();
  a.commitMutation();
  assert.equal(b.shouldApplyPoll(tokenB), true);
});

test("the poll captures its token at request time, not at arrival", () => {
  const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  // Token must be taken BEFORE the await, or it would read the post-mutation
  // value and the guard could never fire.
  assert.match(
    appSource,
    /const pollToken = dashboardWriteOrder\.current\.beginPoll\(\);[\s\S]{0,600}?await fetch\(`\/api\/dashboard/,
  );
});

test("every mutation commits, and stale poll responses are dropped", () => {
  const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  assert.match(appSource, /dashboardWriteOrder\.current\.commitMutation\(\)/);
  assert.match(appSource, /if \(!dashboardWriteOrder\.current\.shouldApplyPoll\(pollToken\)\) return;/);
});

// Every writer of dashboard state must claim the write. A writer that forgets
// is a writer a stale poll can silently undo - which is the bug this file
// exists for. loadDashboard is the reader; everything else must commit.
test("all three dashboard writers are accounted for", () => {
  const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  const writes = appSource.match(/setDashboard\(\(current\) => mergeDashboardPayload\(/g) || [];
  const commits = appSource.match(/dashboardWriteOrder\.current\.commitMutation\(\)/g) || [];
  // One of the writers is the poll itself, which is guarded rather than
  // committing, so commits are always writers minus one.
  assert.equal(writes.length - 1, commits.length,
    `${writes.length} dashboard writers but only ${commits.length} claim the write`);
});
