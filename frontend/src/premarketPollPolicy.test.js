import { test } from "node:test";
import assert from "node:assert/strict";
import { premarketPollEnabled } from "./premarketPollPolicy.js";
test("background tabs and other workspaces cannot trigger heavy premarket analysis", () => {
  for (const view of ["MomX Scanner","Bot","Charts & OI","Quick Options"]) assert.equal(premarketPollEnabled(view,null),false);
  assert.equal(premarketPollEnabled("Premarket Scanner",null),true);
  assert.equal(premarketPollEnabled("Premarket Scanner","chart"),false);
});
