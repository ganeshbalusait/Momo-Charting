import assert from "node:assert/strict";
import test from "node:test";
import {
  API_UNAVAILABLE_MESSAGE,
  INVALID_JSON_MESSAGE,
  SESSION_EXPIRED_MESSAGE,
  nonJsonResponseError,
  nonJsonResponseMessage,
} from "./apiErrorMessage.js";

const ORIGIN = "https://app.agxtrade.com";

// The exact body Cloudflare returned for /api/health once the Access session
// lapsed - captured from the live tunnel on 2026-08-12.
const CLOUDFLARE_302_BODY = `<html>
<head><title>302 Found</title></head>
<body>
<center><h1>302 Found</h1></center>
<hr><center>cloudflare</center>
</body>
</html>`;

test("names an expired Access session when the login URL is the final URL", () => {
  const response = {
    status: 200,
    redirected: true,
    url: "https://agxtrade.cloudflareaccess.com/cdn-cgi/access/login/app.agxtrade.com?kid=abc",
  };
  assert.equal(nonJsonResponseMessage(response, "<html>sign in</html>", ORIGIN), SESSION_EXPIRED_MESSAGE);
});

test("names an expired Access session when the body is the Access login page", () => {
  const response = { status: 200, url: `${ORIGIN}/api/dashboard` };
  const body = '<!doctype html><html><body><script>location="/cdn-cgi/access/login/app.agxtrade.com"</script></body></html>';
  assert.equal(nonJsonResponseMessage(response, body, ORIGIN), SESSION_EXPIRED_MESSAGE);
});

test("treats the observed cloudflare 302 interstitial as an expired session", () => {
  const response = { status: 302, redirected: true, url: `${ORIGIN}/api/health` };
  assert.equal(nonJsonResponseMessage(response, CLOUDFLARE_302_BODY, ORIGIN), SESSION_EXPIRED_MESSAGE);
});

test("treats a 401/403 HTML page as an expired session", () => {
  for (const status of [401, 403]) {
    const response = { status, url: `${ORIGIN}/api/dashboard` };
    assert.equal(nonJsonResponseMessage(response, "<html>denied</html>", ORIGIN), SESSION_EXPIRED_MESSAGE);
  }
});

test("does NOT blame the session for a 5xx HTML error page", () => {
  const response = { status: 502, url: `${ORIGIN}/api/dashboard` };
  assert.equal(nonJsonResponseMessage(response, "<html>502 Bad Gateway</html>", ORIGIN), API_UNAVAILABLE_MESSAGE);
});

test("keeps the 5xx status on a non-JSON response so callers can retry", () => {
  const response = { status: 502, url: `${ORIGIN}/api/oi-finder-chart` };
  const error = nonJsonResponseError(response, "<html>502 Bad Gateway</html>", ORIGIN);
  assert.equal(error.message, API_UNAVAILABLE_MESSAGE);
  assert.equal(error.httpStatus, 502);
});

test("does NOT blame the session when the SPA shell is served for an API path", () => {
  const response = { status: 200, redirected: false, url: `${ORIGIN}/api/dashboard` };
  const shell = '<!doctype html><html><head><title>Agentic AI Trading</title></head><body><div id="root"></div></body></html>';
  assert.equal(nonJsonResponseMessage(response, shell, ORIGIN), INVALID_JSON_MESSAGE);
});

test("keeps the generic message for truncated or corrupt JSON", () => {
  const response = { status: 200, url: `${ORIGIN}/api/dashboard` };
  assert.equal(nonJsonResponseMessage(response, '{"ok": tru', ORIGIN), INVALID_JSON_MESSAGE);
  assert.equal(nonJsonResponseMessage(response, "not json at all", ORIGIN), INVALID_JSON_MESSAGE);
});

test("local development (no Access in front) keeps the generic message", () => {
  const local = "http://127.0.0.1:5173";
  const response = { status: 200, redirected: false, url: `${local}/api/dashboard` };
  assert.equal(nonJsonResponseMessage(response, "<html>oops</html>", local), INVALID_JSON_MESSAGE);
});

test("tolerates a missing/!partial response object", () => {
  assert.equal(nonJsonResponseMessage(undefined, "", ORIGIN), INVALID_JSON_MESSAGE);
  assert.equal(nonJsonResponseMessage({}, "<html></html>", ORIGIN), INVALID_JSON_MESSAGE);
});
