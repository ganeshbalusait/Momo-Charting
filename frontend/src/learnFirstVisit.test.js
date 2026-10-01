import assert from "node:assert/strict";
import { test } from "node:test";

import {
  LEARN_CONTENT_VERSION,
  LEARN_SEEN_STORAGE_KEY,
  markLearnSeen,
  readLearnSeen,
  shouldAutoOpenLearn,
} from "./learnFirstVisit.js";

function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    dump: () => Object.fromEntries(map),
  };
}

const throwingStorage = {
  getItem() { throw new Error("storage disabled"); },
  setItem() { throw new Error("storage disabled"); },
};

test("a brand-new browser is shown the Learn page", () => {
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: null, popoutMode: "", savedView: "" }),
    true,
  );
});

test("a browser that has already seen this version is not shown it again", () => {
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: LEARN_CONTENT_VERSION, popoutMode: "", savedView: "" }),
    false,
  );
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: LEARN_CONTENT_VERSION + 5, popoutMode: "", savedView: "" }),
    false,
  );
});

test("a rewrite can re-introduce itself to a browser that saw an older version", () => {
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: LEARN_CONTENT_VERSION - 1, popoutMode: "", savedView: "" }),
    true,
  );
});

// A pop-out chart window turning itself into the Learn page would be absurd,
// and the trader keeps several open.
test("a pop-out window never becomes the Learn page", () => {
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: null, popoutMode: "chart", savedView: "" }),
    false,
  );
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: null, popoutMode: "chain", savedView: "" }),
    false,
  );
});

// A returning browser is mid-workflow. Yanking it to Learn is the failure mode
// this whole gate exists to prevent.
test("a browser with a remembered page is left where it was", () => {
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: null, popoutMode: "", savedView: "MomX Scanner" }),
    false,
  );
});

// App.jsx holds a user with mustChangePassword on Settings and filters the nav
// down to Settings alone. A newly invited user on a fresh browser has no
// remembered page and no seen-version, so without this condition the auto-open
// would move them off that hold and offer them the pages it just removed.
test("a user who must change their password is left on the Settings hold", () => {
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: null, popoutMode: "", savedView: "", mustChangePassword: true }),
    false,
  );
  // The hold outranks nothing else changing: this is the only differing input.
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: null, popoutMode: "", savedView: "", mustChangePassword: false }),
    true,
  );
});

test("missing and malformed input never throws", () => {
  assert.equal(shouldAutoOpenLearn(undefined), true);
  assert.equal(shouldAutoOpenLearn({}), true);
  assert.equal(shouldAutoOpenLearn({ seenVersion: "not a number" }), true);
});

test("readLearnSeen reads a stored number and rejects junk", () => {
  assert.equal(readLearnSeen(fakeStorage({ [LEARN_SEEN_STORAGE_KEY]: "1" })), 1);
  assert.equal(readLearnSeen(fakeStorage({ [LEARN_SEEN_STORAGE_KEY]: "banana" })), null);
  assert.equal(readLearnSeen(fakeStorage()), null);
});

test("markLearnSeen writes the version", () => {
  const storage = fakeStorage();
  markLearnSeen(storage, 1);
  assert.equal(storage.dump()[LEARN_SEEN_STORAGE_KEY], "1");
});

// localStorage throws outright in some privacy modes. A storage exception must
// never take the app down with it.
test("a throwing storage is swallowed, not propagated", () => {
  assert.doesNotThrow(() => readLearnSeen(throwingStorage));
  assert.equal(readLearnSeen(throwingStorage), null);
  assert.doesNotThrow(() => markLearnSeen(throwingStorage, 1));
  assert.doesNotThrow(() => readLearnSeen(null));
  assert.doesNotThrow(() => markLearnSeen(null, 1));
});
