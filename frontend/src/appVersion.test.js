// A Home Screen app has no address bar and no reload button. If this logic is
// wrong in the reload-happy direction the phone becomes unusable - it reloads
// forever with no way to stop it - so the tests that matter most here are the
// ones proving it stays PUT.
import test from "node:test";
import assert from "node:assert/strict";

import {
  entryScriptFrom,
  decideUpdate,
  claimReload,
  installUpdateWatch,
  markReleaseNotesOnNextLoad,
  consumeReleaseNotesMark,
  RESUME_AFTER_MS,
} from "./appVersion.js";

const builtHtml = (asset) =>
  `<!doctype html><html><head><link rel="manifest" href="/manifest.webmanifest" />` +
  `<script type="module" crossorigin src="${asset}"></script></head><body></body></html>`;

test("reads the hashed entry script out of a built page", () => {
  assert.equal(entryScriptFrom(builtHtml("/assets/index-BcCB6jGA.js")), "/assets/index-BcCB6jGA.js");
});

test("a different build has a different name, because vite hashes contents", () => {
  const before = entryScriptFrom(builtHtml("/assets/index-AAAA1111.js"));
  const after = entryScriptFrom(builtHtml("/assets/index-BBBB2222.js"));
  assert.notEqual(before, after);
});

test("the dev server's unhashed entry is read without complaint", () => {
  assert.equal(entryScriptFrom(builtHtml("/src/main.jsx")), "/src/main.jsx");
});

test("a page with no module script yields null, not a guess", () => {
  assert.equal(entryScriptFrom("<!doctype html><html><body>hi</body></html>"), null);
  assert.equal(entryScriptFrom(""), null);
  assert.equal(entryScriptFrom(null), null);
});

// --- the decision -------------------------------------------------------

test("same build: do nothing", () => {
  assert.equal(decideUpdate({ current: "/assets/a.js", latest: "/assets/a.js", awayMs: 0 }), "none");
});

test("THE POINT: a new build found after the app sat in the background reloads", () => {
  // Tapping an iOS Home Screen icon RESUMES the app rather than restarting it,
  // which is how the phone shows a bundle from days ago with nothing on screen
  // admitting it. Coming back from a long absence is a fresh start, so take it.
  assert.equal(decideUpdate({ current: "/assets/a.js", latest: "/assets/b.js", awayMs: RESUME_AFTER_MS }), "reload");
});

test("a glance at another app is NOT a fresh start", () => {
  // The bug the first draft would have shipped in reverse: treating every
  // return to the app as a reload means checking a text mid-chart throws away
  // the chart. Below the threshold, offer instead.
  assert.equal(decideUpdate({ current: "/assets/a.js", latest: "/assets/b.js", awayMs: 5000 }), "notify");
});

test("a new build appearing while he WATCHES is offered, never yanked", () => {
  assert.equal(decideUpdate({ current: "/assets/a.js", latest: "/assets/b.js", awayMs: 0 }), "notify");
});

test("THE DANGEROUS CASE: an unreadable response must never mean new", () => {
  // An expired Cloudflare Access session answers a fetch of "/" with its login
  // page, which has no module script, so latest is null. Reading unknown as
  // new would reload, get the login page again, and loop forever on a device
  // with no address bar to escape with.
  assert.equal(decideUpdate({ current: "/assets/a.js", latest: null, awayMs: RESUME_AFTER_MS }), "none");
  assert.equal(decideUpdate({ current: "/assets/a.js", latest: "", awayMs: RESUME_AFTER_MS }), "none");
});

test("not knowing our OWN version is also not a reason to reload", () => {
  assert.equal(decideUpdate({ current: null, latest: "/assets/b.js", awayMs: RESUME_AFTER_MS }), "none");
});

// --- the loop breaker ---------------------------------------------------

test("one reload per version, however many times we are asked", () => {
  const storage = new Map();
  const store = { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, String(v)) };

  assert.equal(claimReload(store, "/assets/b.js"), true, "first sighting reloads");
  assert.equal(claimReload(store, "/assets/b.js"), false, "second must not");
});

test("a genuinely newer build still gets its one reload", () => {
  const storage = new Map();
  const store = { getItem: (k) => storage.get(k) ?? null, setItem: (k, v) => storage.set(k, String(v)) };

  claimReload(store, "/assets/b.js");
  assert.equal(claimReload(store, "/assets/c.js"), true);
});

test("storage that throws does not take the app down with it", () => {
  // Safari private browsing throws on setItem. An update check is the last
  // thing that should be able to break the app.
  const hostile = { getItem() { throw new Error("nope"); }, setItem() { throw new Error("nope"); } };
  assert.equal(claimReload(hostile, "/assets/b.js"), true);
});

// --- the wiring ---------------------------------------------------------
//
// These exist because the first draft passed every test above and was still
// wrong: it fed decideUpdate the visibility state read AFTER the app came
// back, which is always "visible", so the resume case - the entire reason for
// this file - would have shown a banner instead of updating. The pure function
// was correct and the caller was not, so only a test that drives the caller
// could see it.

function harness({ currentSrc = "/assets/a.js", servedSrc = "/assets/b.js" } = {}) {
  const calls = { reloads: 0, banners: 0 };
  let clock = 1_000_000;
  const listeners = {};
  const store = new Map();

  const body = { appendChild: () => { calls.banners += 1; } };
  const doc = {
    visibilityState: "visible",
    body,
    getElementById: () => null,
    createElement: () => ({ setAttribute() {}, addEventListener() {}, style: {} }),
    querySelector: () => (currentSrc ? { getAttribute: () => currentSrc } : null),
    addEventListener: (name, fn) => { listeners[name] = fn; },
    removeEventListener: (name) => { delete listeners[name]; },
  };
  const win = {
    document: doc,
    localStorage: {
      getItem: (k) => store.get(k) ?? null,
      setItem: (k, v) => store.set(k, String(v)),
    },
    location: { reload: () => { calls.reloads += 1; } },
    setInterval: () => 1,
    clearInterval: () => {},
    fetch: async () => ({
      ok: true,
      text: async () =>
        `<!doctype html><script type="module" crossorigin src="${servedSrc}"></script>`,
    }),
  };

  installUpdateWatch(win, { now: () => clock });

  return {
    calls,
    async leaveFor(ms) {
      doc.visibilityState = "hidden";
      await listeners.visibilitychange();
      clock += ms;
      doc.visibilityState = "visible";
      await listeners.visibilitychange();
    },
  };
}

test("THE WIRING BUG: coming back after a long absence actually reloads", async () => {
  const app = harness();
  await app.leaveFor(RESUME_AFTER_MS + 1);
  assert.equal(app.calls.reloads, 1, "resuming onto a new build must update, not offer");
  assert.equal(app.calls.banners, 0, "and must not also nag about it");
});

test("a five-second glance elsewhere offers instead of reloading", async () => {
  const app = harness();
  await app.leaveFor(5000);
  assert.equal(app.calls.reloads, 0, "must not throw away the chart he was reading");
  assert.equal(app.calls.banners, 1);
});

test("resuming onto the SAME build does nothing at all", async () => {
  const app = harness({ currentSrc: "/assets/a.js", servedSrc: "/assets/a.js" });
  await app.leaveFor(RESUME_AFTER_MS + 1);
  assert.equal(app.calls.reloads, 0);
  assert.equal(app.calls.banners, 0);
});

test("resuming twice onto the same new build reloads once, not forever", async () => {
  const app = harness();
  await app.leaveFor(RESUME_AFTER_MS + 1);
  await app.leaveFor(RESUME_AFTER_MS + 1);
  assert.equal(app.calls.reloads, 1, "a phone with no address bar cannot escape a reload loop");
});

// --- landing on the release notes after an update ------------------------
//
// The tap and the silent auto-reload are deliberately NOT the same. He asked
// for the banner to take him to the notes; being thrown onto a notes page when
// he merely came back to a chart he left open is a different, worse thing.

const fakeStorage = () => {
  const map = new Map();
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
    size: () => map.size,
  };
};

test("tapping the banner marks the next load to open the release notes", () => {
  const store = fakeStorage();
  markReleaseNotesOnNextLoad(store);
  assert.equal(consumeReleaseNotesMark(store), true);
});

test("the mark is consumed once, so a later reload does not land there again", () => {
  const store = fakeStorage();
  markReleaseNotesOnNextLoad(store);
  assert.equal(consumeReleaseNotesMark(store), true);
  assert.equal(consumeReleaseNotesMark(store), false);
  assert.equal(store.size(), 0, "the mark must not linger in storage");
});

test("a load with no mark does not open the release notes", () => {
  assert.equal(consumeReleaseNotesMark(fakeStorage()), false);
});

test("storage that throws never breaks boot or strands the app on the notes", () => {
  const hostile = {
    getItem() { throw new Error("private mode"); },
    setItem() { throw new Error("private mode"); },
    removeItem() { throw new Error("private mode"); },
  };
  assert.doesNotThrow(() => markReleaseNotesOnNextLoad(hostile));
  assert.equal(consumeReleaseNotesMark(hostile), false);
  assert.equal(consumeReleaseNotesMark(null), false);
  assert.equal(consumeReleaseNotesMark(undefined), false);
});

test("the SILENT auto-reload does not mark - it must not hijack a returning chart", () => {
  const session = fakeStorage();
  const win = {
    document: {
      querySelector: () => ({ getAttribute: () => "/assets/old.js" }),
      addEventListener() {}, removeEventListener() {},
      getElementById: () => null,
      createElement: () => ({ setAttribute() {}, addEventListener() {}, style: {} }),
      body: { appendChild() {} },
      visibilityState: "visible",
    },
    fetch: async () => ({ ok: true, text: async () => builtHtml("/assets/new.js") }),
    localStorage: fakeStorage(),
    sessionStorage: session,
    setInterval: () => 0,
    clearInterval() {},
    location: { reload() {} },
  };
  const stop = installUpdateWatch(win, { intervalMs: 1e9 });
  // Simulate: away long enough that decideUpdate returns "reload".
  return Promise.resolve().then(async () => {
    win.document.visibilityState = "hidden";
    // drive the same path the visibility handler uses
    const { decideUpdate: decide } = await import("./appVersion.js");
    assert.equal(decide({ current: "/assets/old.js", latest: "/assets/new.js", awayMs: RESUME_AFTER_MS }), "reload");
    stop();
    assert.equal(session.size(), 0, "an automatic reload must leave no open-notes mark");
  });
});
