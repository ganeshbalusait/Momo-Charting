// "What is this page?" boxes (2026-11-20, "Build like this 10 kid can understand"): every guide explains its words in
// short sentences and always says it is information, not advice, with paper trading first.
import assert from "node:assert/strict";
import test from "node:test";

import { GUIDES, guideFor } from "./beginnerGuide.js";

test("each page has a guide with a title, explained words and a warning", () => {
  for (const key of ["agxFlow", "leapOi"]) {
    const guide = guideFor(key);
    assert.ok(guide, key);
    assert.equal(guide.title, "What is this page?");
    assert.ok(guide.steps.length >= 5, key);
    for (const [term, meaning] of guide.steps) {
      assert.ok(term && meaning, `${key}: ${term}`);
      // Short sentences a beginner can read: no sentence longer than 30 words.
      for (const sentence of meaning.split(/(?<=[.!?])\s+/)) assert.ok(sentence.split(/\s+/).length <= 30, `${key} "${term}": ${sentence}`);
    }
    assert.match(guide.warning, /not advice/);
    assert.match(guide.warning, /paper trading/);
  }
  assert.equal(guideFor("nope"), null);
});

test("the words a beginner meets on each page are explained", () => {
  const terms = (key) => GUIDES[key].steps.map(([term]) => term).join(" | ");
  for (const word of ["Option", "Call or Put", "Bought or Sold", "Unusual"]) assert.match(terms("agxFlow"), new RegExp(word));
  for (const word of ["Open interest", "Stock price", "From price", "Delta", "ENTRY"]) assert.match(terms("leapOi"), new RegExp(word));
});

test("nothing in a guide tells anyone what to buy", () => {
  for (const guide of Object.values(GUIDES)) {
    const text = JSON.stringify(guide).toLowerCase();
    assert.doesNotMatch(text, /you should buy|buy this|best trade|guaranteed|sure win/);
  }
});
