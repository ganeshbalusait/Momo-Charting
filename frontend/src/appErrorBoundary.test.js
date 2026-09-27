import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { REMEMBERED_VIEW_STORAGE_KEY, clearRememberedView } from "./appCrashRecovery.js";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
const mainSource = readFileSync(new URL("./main.jsx", import.meta.url), "utf8");
const boundarySource = readFileSync(new URL("./AppErrorBoundary.jsx", import.meta.url), "utf8");

test("the boundary wraps the app, or a throw is still a white screen", () => {
  assert.match(mainSource, /import AppErrorBoundary from "\.\/AppErrorBoundary"/);
  assert.match(mainSource, /<AppErrorBoundary>[\s\S]*<App \/>[\s\S]*<\/AppErrorBoundary>/);
});

// A boundary without getDerivedStateFromError never renders its fallback.
test("the boundary actually catches, and offers a way out", () => {
  assert.match(boundarySource, /static getDerivedStateFromError/);
  assert.match(boundarySource, /clearRememberedView/);
});

// The key is duplicated rather than imported from App.jsx, to keep recovery free
// of that module. This is what stops the copy drifting from the original.
test("the remembered-view key matches the one App.jsx actually writes", () => {
  assert.equal(REMEMBERED_VIEW_STORAGE_KEY, "agenticActiveView");
  assert.match(appSource, /const ACTIVE_VIEW_STORAGE_KEY = "agenticActiveView";/);
});

test("clearing the remembered view removes exactly that key", () => {
  const removed = [];
  assert.equal(clearRememberedView({ removeItem: (k) => removed.push(k) }), true);
  assert.deepEqual(removed, ["agenticActiveView"]);
});

test("blocked storage reports failure instead of throwing past the boundary", () => {
  assert.equal(clearRememberedView({ removeItem: () => { throw new Error("blocked"); } }), false);
  assert.equal(clearRememberedView(null), true);
});
