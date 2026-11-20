import assert from "node:assert/strict";
import test from "node:test";

import { STORY_POLL_PENDING_MS, STORY_POLL_READY_MS, storyUrl } from "./tickerStory.js";

test("one ticker asks for one story; a blank asks for nothing", () => {
  assert.equal(storyUrl(" intc "), "/api/ticker-story?symbol=INTC");
  assert.equal(storyUrl("BRK.B"), "/api/ticker-story?symbol=BRK.B");
  assert.equal(storyUrl(""), "");
  assert.equal(storyUrl(null), "");
});

test("it asks again quickly only while the ticker is still being read", () => {
  assert.ok(STORY_POLL_PENDING_MS < STORY_POLL_READY_MS);
  assert.equal(STORY_POLL_READY_MS, 300000);
});
