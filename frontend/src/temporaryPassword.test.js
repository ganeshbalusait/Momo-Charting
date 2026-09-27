import assert from "node:assert/strict";
import test from "node:test";

import { generateTemporaryPassword } from "./temporaryPassword.js";

// Mirrors auth_service._validate_password. If these drift apart the admin gets
// "Password must include a number" from a password they never chose and cannot
// see, on a button whose whole point is that it needs no typing.
const satisfiesServerRules = (value) =>
  typeof value === "string" &&
  value.length >= 10 &&
  value.length <= 256 &&
  /[a-z]/.test(value) &&
  /[A-Z]/.test(value) &&
  /[0-9]/.test(value);

test("every generated password satisfies the server's password rules", () => {
  for (let i = 0; i < 500; i += 1) {
    const password = generateTemporaryPassword();
    assert.ok(
      satisfiesServerRules(password),
      `generated password the server would reject: ${JSON.stringify(password)}`
    );
  }
});

test("passwords are not reused between resets", () => {
  const seen = new Set();
  for (let i = 0; i < 500; i += 1) seen.add(generateTemporaryPassword());
  assert.equal(seen.size, 500);
});

test("omits characters that are misread when a password is read aloud", () => {
  // These get handed over by phone, so 0/O and 1/l/I are a support call.
  for (let i = 0; i < 500; i += 1) {
    assert.doesNotMatch(generateTemporaryPassword(), /[0O1lI]/);
  }
});
