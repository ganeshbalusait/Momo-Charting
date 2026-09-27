// The premarket gap must be named by its WINDOW, not merely reported as "late".
//
// "Tape behind" sends the trader to the chart hunting a rendering fault.
// "No data 04:00-07:00" tells him it is the Tradier hole, that his scanner is
// blind for its first hour, and that his 2H/4H labels are computed from a
// partial window. He has lost a day to exactly that ambiguity.
//
// The window is READ OUT of the backend note rather than hardcoded, so that if
// the gap ever moves the UI cannot go on confidently naming the old hours.
import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

const lift = (pattern, what) => {
  const match = pattern.exec(appSource);
  assert.ok(match, `could not lift ${what} out of App.jsx`);
  return match[0];
};

const premarketGapWindow = new Function([
  lift(/function premarketGapWindow\([\s\S]*?\n}/, "premarketGapWindow"),
  "return premarketGapWindow;",
].join("\n\n"))();

// The exact sentence the backend emits (api_server.py:10252).
const REAL_NOTE =
  "Premarket 04:00-07:00 is unavailable: the Tradier feed refused the request. " +
  "Schwab publishes no current-day bars before 07:00, so this window has no " +
  "other source. Check the Tradier access token in Settings.";

test("it names the window out of the real backend note", () => {
  assert.equal(premarketGapWindow(REAL_NOTE), "04:00-07:00");
});

test("it takes the window from the text rather than assuming it", () => {
  // The whole point of reading rather than hardcoding: a moved gap must not be
  // reported under the old hours.
  assert.equal(premarketGapWindow("Premarket 05:30-08:15 is unavailable"), "05:30-08:15");
});

test("spacing around the dash does not matter", () => {
  assert.equal(premarketGapWindow("gap 04:00 - 07:00 today"), "04:00-07:00");
});

test("a single-digit hour is still matched", () => {
  assert.equal(premarketGapWindow("gap 4:00-7:00"), "4:00-7:00");
});

test("no window in the text yields empty, so the caller can fall back", () => {
  // It must NOT invent a window. An empty string routes the UI to the generic
  // "Premarket data gap" wording instead of naming hours that were never said.
  assert.equal(premarketGapWindow("the feed refused the request"), "");
});

test("absent notes are empty, never the string 'null'", () => {
  assert.equal(premarketGapWindow(""), "");
  assert.equal(premarketGapWindow(null), "");
  assert.equal(premarketGapWindow(undefined), "");
});
