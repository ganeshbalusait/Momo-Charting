import assert from "node:assert/strict";
import test from "node:test";

import { buildInviteMessage } from "./inviteMessage.js";

// Accounts have no password now: the administrator enters an email, Cloudflare
// Access checks it with a one-time code, and the app matches it to the
// account. So the invite has nothing secret in it - it is directions, not a
// credential - and it must never imply a password exists to be typed.

test("the invite names the address to sign in with and where to go", () => {
  const message = buildInviteMessage({
    loginUrl: "https://app.agxtrade.com",
    email: "member@example.com",
    displayName: "Member",
  });

  assert.match(message, /member@example\.com/);
  assert.match(message, /app\.agxtrade\.com/);
  assert.match(message, /Hi Member/);
});

test("the invite carries no password and asks for none", () => {
  // Saying "there is no password" is helpful and allowed. What must not
  // appear is a password VALUE, or an instruction to type or choose one -
  // that is what sends someone hunting for a credential nobody issued.
  const message = buildInviteMessage({
    loginUrl: "https://app.agxtrade.com",
    email: "member@example.com",
    displayName: "Member",
  });

  assert.doesNotMatch(message, /(temporary|your) password:/i);
  assert.doesNotMatch(message, /(enter|type|choose|change) (your |a )?password/i);
});

test("it explains the emailed code, since that is the only step they take", () => {
  const message = buildInviteMessage({ email: "member@example.com" });

  assert.match(message, /code/i);
});

test("an invite without an address is not built", () => {
  assert.equal(buildInviteMessage({ displayName: "Member" }), "");
  assert.equal(buildInviteMessage({}), "");
});

test("it falls back to the public address when no origin is given", () => {
  assert.match(buildInviteMessage({ email: "member@example.com" }), /https:\/\/app\.agxtrade\.com/);
});
