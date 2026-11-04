// Integration (2026-11-04 coverage): the Release Notes page and the unread dot
// both fetch /release-notes.json?t=<ms> with cache: no-store and hand the
// answer to parseReleases / unreadCount. Here a real HTTP server serves the
// real frontend/public folder the way Vite and the built dist do, and the real
// fetch + the real functions read it - no browser, no live server.
import test, { after, before } from "node:test";
import assert from "node:assert/strict";
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";

import { newestAt, parseReleases, readSeen, unreadCount } from "./releaseNotes.js";

const PUBLIC = new URL("../public/", import.meta.url);
let server;
let base;
const requests = [];

before(async () => {
  server = createServer(async (request, response) => {
    const url = new URL(request.url, "http://local");
    requests.push(url.pathname + url.search);
    if (url.pathname === "/missing-notes.json") {
      response.writeHead(404, { "content-type": "text/plain" });
      response.end("not found");
      return;
    }
    try {
      const body = await readFile(new URL(`.${url.pathname}`, PUBLIC));
      response.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" });
      response.end(body);
    } catch {
      response.writeHead(404);
      response.end();
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});

after(() => new Promise((resolve) => server.close(resolve)));

// What ReleaseNotesPanel and the App's dot effect do, step for step.
async function loadNotes(path = "/release-notes.json") {
  const response = await fetch(`${base}${path}?t=${Date.now()}`, { cache: "no-store" });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

test("the served file parses to one release per entry, newest first", async () => {
  const raw = JSON.parse(await readFile(new URL("release-notes.json", PUBLIC), "utf8"));
  const served = await loadNotes();
  assert.match(requests.at(-1), /^\/release-notes\.json\?t=\d+$/, "the cache-buster rides on the request");
  const releases = parseReleases(served);
  assert.equal(releases.length, raw.releases.length);
  for (let index = 1; index < releases.length; index += 1) {
    assert.ok(releases[index - 1].ms >= releases[index].ms, `release ${index} is out of order on the page`);
  }
});

test("the dot over HTTP: a marker at the newest stamp shows none, one at the second shows its newer count", async () => {
  const releases = parseReleases(await loadNotes());
  const storage = new Map([["agx:release-notes-seen", newestAt(releases)]]);
  const asStorage = { getItem: (key) => storage.get(key) ?? null };
  assert.equal(unreadCount(releases, readSeen(asStorage)), 0);
  const second = releases.find((release) => release.ms !== null && release.ms < releases[0].ms);
  storage.set("agx:release-notes-seen", second.at);
  assert.equal(unreadCount(releases, readSeen(asStorage)), releases.filter((r) => r.ms > second.ms).length);
  storage.clear();
  assert.equal(unreadCount(releases, readSeen(asStorage)), releases.length, "a brand-new browser sees every release as new");
});

test("a 404 is an error the page can show, never a parsed empty list", async () => {
  await assert.rejects(loadNotes("/missing-notes.json"), /HTTP 404/);
});
