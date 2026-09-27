import test from "node:test";
import assert from "node:assert/strict";
import { newsSentimentTone } from "./newsSentimentTone.js";

test("Strong and Positive headlines flash green", () => {
  assert.deepEqual(newsSentimentTone("Strong"), { key: "positive", label: "Strong", flash: true });
  assert.deepEqual(newsSentimentTone("Positive"), { key: "positive", label: "Positive", flash: true });
  assert.equal(newsSentimentTone(" positive ").key, "positive");
});

test("Negative headlines flash red", () => {
  assert.deepEqual(newsSentimentTone("Negative"), { key: "negative", label: "Negative", flash: true });
  assert.equal(newsSentimentTone("NEGATIVE").key, "negative");
});

test("Neutral, unknown and missing sentiment stay grey and still", () => {
  assert.deepEqual(newsSentimentTone("Neutral"), { key: "neutral", label: "Neutral", flash: false });
  assert.deepEqual(newsSentimentTone(""), { key: "neutral", label: "Neutral", flash: false });
  assert.deepEqual(newsSentimentTone(undefined), { key: "neutral", label: "Neutral", flash: false });
  assert.deepEqual(newsSentimentTone("Unclassified"), { key: "neutral", label: "Unclassified", flash: false });
});

