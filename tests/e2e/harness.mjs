// End-to-end harness (2026-11-04): drives the REAL app in a hidden Chromium,
// the way the trader clicks it, against the running stack (Vite :5173 ->
// gateway :3001 -> api_server :3002 / momx_worker :3010).
//
// Run every spec, one browser at a time:
//   node --test --test-concurrency=1 "tests/e2e/*.e2e.test.mjs"
//
// Safety is built in, so no spec can hurt the live account:
//  * Read-only by default. Every /api request that is not GET/HEAD/OPTIONS is
//    stopped inside the browser before it leaves and recorded in `writes`.
//    A spec that tests a save/start flow answers it with a canned reply
//    (`fakeWrites`), so the screen path is real but nothing reaches Schwab,
//    Alpaca, the broker or the database.
//  * One browser at a time on this PC (a lock file), so several test writers
//    never stack Chromium instances on the trading laptop.
//  * Skipped during the regular session (09:30-16:00 ET weekdays) unless
//    E2E_ALLOW_MARKET_HOURS=1 - a full suite took the app down 2026-10-16.
//  * An isolated browser profile: the trader's own Chrome state is untouched.

import test, { after } from "node:test";
import { createRequire } from "node:module";
import { closeSync, openSync, readFileSync, unlinkSync, writeSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { OI_CHART_APPLIED_GRID_STORAGE_KEY, chartGridStamp, readChartGridMeta } from "../../frontend/src/chartWorkspaceState.js";

export const BASE_URL = process.env.E2E_BASE_URL || "http://127.0.0.1:5173";
export const DESKTOP = { width: 1440, height: 900 };
export const PHONE = { width: 375, height: 812 };

const FRONTEND_DIR = fileURLToPath(new URL("../../frontend/", import.meta.url));
const LOCK_PATH = path.join(tmpdir(), "agx-e2e-browser.lock");
const LOCK_WAIT_MS = Number(process.env.E2E_LOCK_WAIT_MS || 15 * 60 * 1000);
const READ_METHODS = new Set(["GET", "HEAD", "OPTIONS"]);

// playwright lives in frontend/node_modules, which is not an ancestor of this
// file, so a bare import cannot see it. Resolve it against frontend/ and take
// the ESM entry (see scripts/capture-learn-screenshots.mjs for why).
async function loadChromium() {
  const requireFromFrontend = createRequire(pathToFileURL(path.join(FRONTEND_DIR, "package.json")).href);
  const cjsEntry = requireFromFrontend.resolve("playwright");
  const { chromium } = await import(pathToFileURL(path.join(path.dirname(cjsEntry), "index.mjs")).href);
  return chromium;
}

// Why a spec must not run now, or null when it may.
export function marketHoursBlock(now = new Date()) {
  if (process.env.E2E_ALLOW_MARKET_HOURS === "1") return null;
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York", weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    }).formatToParts(now).map((part) => [part.type, part.value]),
  );
  const minutes = Number(parts.hour) * 60 + Number(parts.minute);
  const weekday = parts.weekday !== "Sat" && parts.weekday !== "Sun";
  return weekday && minutes >= 9 * 60 + 30 && minutes < 16 * 60
    ? `regular session (${parts.hour}:${parts.minute} ET): end-to-end runs wait for 16:00 ET, or set E2E_ALLOW_MARKET_HOURS=1`
    : null;
}

// node:test `test`, skipped in market hours, with a browser-sized timeout.
export function e2eTest(name, fn, { timeout = 180_000 } = {}) {
  return test(name, { skip: marketHoursBlock() || false, timeout }, fn);
}

const alive = (pid) => {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
};

let lockHeld = false;
async function acquireBrowserLock() {
  if (lockHeld) return;
  const deadline = Date.now() + LOCK_WAIT_MS;
  for (;;) {
    try {
      const fd = openSync(LOCK_PATH, "wx");
      writeSync(fd, String(process.pid));
      closeSync(fd);
      lockHeld = true;
      process.once("exit", releaseBrowserLock);
      return;
    } catch (error) {
      if (error.code !== "EEXIST") throw error;
    }
    let owner = 0;
    try { owner = Number(readFileSync(LOCK_PATH, "utf8")) || 0; } catch { /* raced with its release */ }
    if (owner && !alive(owner)) {
      try { unlinkSync(LOCK_PATH); } catch { /* another waiter took it */ }
      continue;
    }
    if (Date.now() > deadline) throw new Error(`another end-to-end run (pid ${owner}) held the browser for ${LOCK_WAIT_MS / 60000} min`);
    await new Promise((resolve) => setTimeout(resolve, 2000));
  }
}

const releaseBrowserLock = () => {
  if (!lockHeld) return;
  lockHeld = false;
  try { unlinkSync(LOCK_PATH); } catch { /* already gone */ }
};

// A browser that has not applied the saved default chart grid is steered to
// "Charts & OI" a moment after boot (App.jsx, "Save grid on one device...").
// Every test browser is new, so a spec opening Settings would watch its page
// swap out mid-test. Mark the server's current grid as applied already.
async function gridAlreadyApplied(view) {
  if (!view || view === "Charts & OI") return {};
  try {
    const response = await fetch(`${BASE_URL}/api/chart-grids`);
    if (!response.ok) return {};
    const { defaultGrid } = readChartGridMeta((await response.json())?.grids);
    return defaultGrid ? { [OI_CHART_APPLIED_GRID_STORAGE_KEY]: chartGridStamp(defaultGrid.name, defaultGrid.savedAt) } : {};
  } catch {
    return {};
  }
}

let browserPromise = null;
async function sharedBrowser() {
  await acquireBrowserLock();
  if (!browserPromise) browserPromise = loadChromium().then((chromium) => chromium.launch());
  return browserPromise;
}

// A live browser keeps the test process from exiting, so close it once this
// spec file's tests are done, then hand the lock to the next run.
after(async () => {
  if (browserPromise) await (await browserPromise).close().catch(() => {});
  browserPromise = null;
  releaseBrowserLock();
});

/**
 * Open the app in a fresh, isolated browser context.
 *
 * view        the left-rail page to open on ("Settings", "MomX Scanner", ...)
 * viewport    DESKTOP (default) or PHONE
 * storage     extra localStorage keys to seed before the app boots
 * fakeWrites  {"POST /api/path": reply | (write) => reply}, reply = {status, json}
 * allowWrites ["POST /api/path"] - read-only POSTs that may reach the server
 *             (searches, batched reads). Never a route that changes anything.
 */
export async function openApp({ view, viewport = DESKTOP, storage = {}, fakeWrites = {}, allowWrites = [] } = {}) {
  const browser = await sharedBrowser();
  const seeded = { agxLearnSeen: "1", ...(view ? { agenticActiveView: view } : {}), ...(await gridAlreadyApplied(view)), ...storage };
  const context = await browser.newContext({ viewport });
  const writes = [];
  await context.route(/\/api\//, async (route) => {
    const request = route.request();
    const method = request.method();
    if (READ_METHODS.has(method)) return route.continue();
    const url = new URL(request.url());
    const write = { method, path: url.pathname, search: url.search, body: request.postData() || "" };
    writes.push(write);
    const key = `${method} ${url.pathname}`;
    if (allowWrites.includes(key)) return route.continue();
    const fake = fakeWrites[key];
    if (fake) {
      const reply = typeof fake === "function" ? await fake(write) : fake;
      return route.fulfill({ status: reply.status || 200, contentType: "application/json", body: JSON.stringify(reply.json ?? {}) });
    }
    return route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "Blocked by the end-to-end harness: writes are not sent." }) });
  });
  const page = await context.newPage();
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error?.stack || error).slice(0, 600)));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text().slice(0, 300));
  });
  await page.addInitScript((state) => {
    for (const [key, value] of Object.entries(state)) {
      try { window.localStorage.setItem(key, value); } catch { /* blocked storage */ }
    }
  }, seeded);
  await page.goto(BASE_URL, { waitUntil: "domcontentloaded", timeout: 60_000 });
  return {
    context, page, writes, pageErrors, consoleErrors,
    async close() {
      await context.close().catch(() => {});
    },
  };
}

// Run fn with an open app and always close it, even when an assertion fails.
export async function withApp(options, fn) {
  const app = await openApp(options);
  try {
    return await fn(app);
  } finally {
    await app.close();
  }
}

// True when the page scrolls sideways - the phone-width layout check.
export async function overflowsSideways(page) {
  return page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
}
