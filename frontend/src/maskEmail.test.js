import test from "node:test";
import assert from "node:assert/strict";

import { maskEmail } from "./maskEmail.js";

test("keeps one leading character and the domain", () => {
  assert.equal(maskEmail("ganeshbalusait@gmail.com"), "g••••@gmail.com");
});

test("does not leak the name it is masking", () => {
  const masked = maskEmail("ganeshbalusait@gmail.com");

  assert.doesNotMatch(masked, /ganesh/i);
  assert.doesNotMatch(masked, /balusait/i);
});

test("keeps two accounts for the same person tellable apart", () => {
  assert.notEqual(
    maskEmail("ganeshbalusait@gmail.com"),
    maskEmail("ganeshbalusait2025@yahoo.com"),
  );
});

test("masks a short local part without revealing its length", () => {
  assert.equal(maskEmail("a@example.com"), "a••••@example.com");
});

test("trims surrounding whitespace", () => {
  assert.equal(maskEmail("  amaraja@example.com  "), "a••••@example.com");
});

test("returns an empty string for a missing address", () => {
  assert.equal(maskEmail(""), "");
  assert.equal(maskEmail(null), "");
  assert.equal(maskEmail(undefined), "");
});

test("falls back to a full mask when there is no local part", () => {
  assert.equal(maskEmail("@gmail.com"), "••••");
  assert.equal(maskEmail("not-an-address"), "••••");
});
