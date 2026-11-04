// Integration (2026-11-04 coverage): the new-version watch's network half.
// fetchLatestEntryScript is run with the REAL fetch against a local HTTP
// server answering '/' the ways the live origin can: a built index.html, the
// Cloudflare Access login page (no module script), a server error and a
// redirect. Then the built dist on disk is checked: the entry script that
// index.html names must exist, or every client would read "new version".
import test, { after, before } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { existsSync, readFileSync } from "node:fs";

import { decideUpdate, entryScriptFrom, fetchLatestEntryScript } from "./appVersion.js";

const BUILT = '<!doctype html><html><head><script type="module" crossorigin src="/assets/index-Bx12abCd.js"></script></head><body><div id="root"></div></body></html>';
const LOGIN = "<!doctype html><html><head><title>Sign in - Cloudflare Access</title><script src=\"/cdn-cgi/access/login.js\"></script></head><body>Get a login code</body></html>";
let server;
let base;
const seen = [];

before(async () => {
  server = createServer((request, response) => {
    seen.push({ path: request.url, cache: request.headers["cache-control"] || "" });
    const routes = {
      "/": [200, BUILT],
      "/login": [200, LOGIN],
      "/broken": [500, "Internal Server Error"],
      "/moved": [302, ""],
    };
    const [status, body] = routes[request.url] || [404, ""];
    response.writeHead(status, { "content-type": "text/html", ...(status === 302 ? { location: "/login" } : {}) });
    response.end(body);
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});

after(() => new Promise((resolve) => server.close(resolve)));

const realFetch = (url, options) => fetch(new URL(url, base), options);

test("a built index.html yields its hashed entry script, fetched with no-store", async () => {
  assert.equal(await fetchLatestEntryScript(realFetch, "/"), "/assets/index-Bx12abCd.js");
  assert.match(seen.at(-1).cache, /no-cache|no-store/, "the check must never be answered from the HTTP cache");
});

test("an expired-login page has no module script: nothing is learned, nothing reloads", async () => {
  const latest = await fetchLatestEntryScript(realFetch, "/login");
  assert.equal(latest, null);
  assert.equal(decideUpdate({ current: "/src/main.jsx", latest, awayMs: 10 * 60_000 }), "none");
});

test("a redirect to the login page is followed and still learns nothing", async () => {
  assert.equal(await fetchLatestEntryScript(realFetch, "/moved"), null);
});

test("a server error is not a new version", async () => {
  assert.equal(await fetchLatestEntryScript(realFetch, "/broken"), null);
});

test("an unreachable origin is not a new version", async () => {
  const closed = (url, options) => fetch(new URL(url, "http://127.0.0.1:9"), options);
  assert.equal(await fetchLatestEntryScript(closed, "/"), null);
});

const DIST_INDEX = new URL("../dist/index.html", import.meta.url);
test("the built dist names an entry script that exists on disk", { skip: existsSync(DIST_INDEX) ? false : "no frontend/dist build on this machine" }, () => {
  const entry = entryScriptFrom(readFileSync(DIST_INDEX, "utf8"));
  assert.match(entry, /^\/assets\/index-[\w-]+\.js$/);
  assert.ok(existsSync(new URL(`../dist${entry}`, import.meta.url)), `${entry} is missing from dist/assets`);
});
