// MomX Scanner -> the LIVE ⚡ bolt (ALL / ⚡ NOW / ⚡ TODAY), end to end.
// The board is the live Mag7 board (read-only); the per-second bolt state is a
// fixture naming two of the board's own tickers (NVDA, AAPL), so what each view must show is
// known exactly: one name lit NOW, one that fired earlier TODAY.

import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";

const SCANNER = {
  "momx.scanner.activeList": "Mag7",
  "momx.scanner.direction": "bull",
  "momx.scanner.activeView": "live",
  "momx-auto-cards-on": "0",
};
const ROWS = "section[data-testid='momx-scanner-view'] .momx-table-scroll tr.momx-row";
const BOLT = "[data-testid='momx-bolt-view']";

async function wholeBoard(page) {
  await page.locator("[data-testid='momx-scanner-panel']").first().waitFor({ timeout: 90_000 });
  const matchesOnly = page.locator("label.momx-toggle", { hasText: "Scan matches only" }).locator("input");
  if (await matchesOnly.isChecked()) await matchesOnly.uncheck();
  await page.locator(ROWS).nth(1).waitFor({ timeout: 120_000 });
}

const symbolsOnScreen = async (page) =>
  (await page.locator(`${ROWS} .momx-symbol`).allInnerTexts()).map((text) => text.trim()).filter(Boolean);

async function rowsBecome(page, expected) {
  const want = [...expected].sort().join(",");
  let seen = "";
  for (let i = 0; i < 40; i += 1) {
    seen = (await symbolsOnScreen(page)).sort().join(",");
    if (seen === want) return;
    await page.waitForTimeout(250);
  }
  assert.equal(seen, want);
}

// One name passing the gates this second, one that fired 40 minutes ago.
function boltsFor(picked) {
  if (picked.length < 2) return { bolts: {}, serving: true };
  const now = new Date();
  const earlier = new Date(now.getTime() - 40 * 60 * 1000);
  return {
    asOf: now.toISOString(),
    serving: true,
    bolts: {
      [picked[0]]: { on: true, now: true, firedAt: now.toISOString(), firstAt: earlier.toISOString(), families: ["MACD"], opacity: 1, hlMomentum: 2.4, hlPos: 0.9 },
      [picked[1]]: { on: false, now: false, firedAt: earlier.toISOString(), firstAt: earlier.toISOString(), families: ["EMA"], opacity: null, hlMomentum: -0.5, hlPos: 0.3 },
    },
  };
}

e2eTest("the bolt button cycles ALL -> NOW -> TODAY -> ALL, filters to the lit names and is remembered", async () => {
  const picked = [];
  await withApp({ view: "MomX Scanner", storage: SCANNER, fakeReads: { "GET /api/momx-live-bolts": () => ({ json: boltsFor(picked) }) } }, async (app) => {
    const { page } = app;
    await wholeBoard(page);
    const all = await symbolsOnScreen(page);
    assert.ok(all.includes("NVDA") && all.includes("AAPL"), `Mag7 board lacks NVDA/AAPL: ${all}`);
    picked.push("NVDA", "AAPL");
    const button = page.locator(BOLT);
    await button.waitFor({ state: "visible", timeout: 30_000 });
    assert.match(await button.getAttribute("class"), /\bis-all\b/);
    await page.locator(`${BOLT} .momx-bolt-count`, { hasText: /^2$/ }).waitFor({ timeout: 15_000 });   // TODAY count shown on ALL
    // The lit name carries the live bolt badge on its row.
    await page.locator(ROWS, { has: page.locator(".momx-symbol", { hasText: new RegExp(`^${picked[0]}$`) }) })
      .locator("button.momx-badge.is-live-bolt").first().waitFor({ timeout: 15_000 });

    await button.click();
    assert.match(await button.getAttribute("class"), /\bis-now\b/);
    assert.match(await button.innerText(), /⚡ NOW/);
    assert.equal((await page.locator(`${BOLT} .momx-bolt-count`).innerText()).trim(), "1");
    await rowsBecome(page, [picked[0]]);

    await button.click();
    assert.match(await button.getAttribute("class"), /\bis-today\b/);
    assert.match(await button.innerText(), /⚡ TODAY/);
    await rowsBecome(page, picked);

    await button.click();
    assert.match(await button.getAttribute("class"), /\bis-all\b/);
    await rowsBecome(page, all);

    // Leave it on TODAY and reload: the choice survives.
    await button.click();
    await button.click();
    assert.match(await button.getAttribute("class"), /\bis-today\b/);
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator(BOLT).waitFor({ timeout: 90_000 });
    assert.match(await page.locator(BOLT).getAttribute("class"), /\bis-today\b/);
    await rowsBecome(page, picked);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on the BEAR board the bolt button and the H/L sort are gone", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER }, async (app) => {
    const { page } = app;
    await page.locator(BOLT).waitFor({ timeout: 90_000 });
    const hlSort = page.locator("label.momx-toggle", { hasText: "H/L⚡ sort" });
    assert.equal(await hlSort.count(), 1);
    await page.locator("[data-testid='momx-direction-toggle']").click();
    await page.locator("[data-testid='momx-direction-toggle'][aria-pressed='true']").waitFor({ timeout: 10_000 });
    assert.match(await page.locator("[data-testid='momx-direction-toggle']").innerText(), /BEAR/);
    await page.locator(BOLT).waitFor({ state: "detached", timeout: 10_000 });
    assert.equal(await hlSort.count(), 0);
    // And the bear board stops polling the bull-only bolt.
    const before = app.reads.filter((read) => read.path === "/api/momx-live-bolts").length;
    await page.waitForTimeout(3_000);
    assert.equal(app.reads.filter((read) => read.path === "/api/momx-live-bolts").length, before);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a hidden tab stops polling the bolt", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER, fakeReads: { "GET /api/momx-live-bolts": { json: { bolts: {}, serving: true } } } }, async (app) => {
    const { page } = app;
    await page.locator(BOLT).waitFor({ timeout: 90_000 });
    const polls = () => app.reads.filter((read) => read.path === "/api/momx-live-bolts").length;
    const start = polls();
    await page.waitForTimeout(2_500);
    assert.ok(polls() > start, "the bolt polls every second while the tab is visible");
    await page.evaluate(() => {
      Object.defineProperty(document, "visibilityState", { configurable: true, get: () => "hidden" });
      document.dispatchEvent(new Event("visibilitychange"));
    });
    await page.waitForTimeout(1_200);                          // one in-flight poll may land
    const hiddenAt = polls();
    await page.waitForTimeout(5_000);
    assert.equal(polls(), hiddenAt, "no bolt request while hidden");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the bolt button fits on screen", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE, storage: SCANNER }, async (app) => {
    const button = app.page.locator(BOLT);
    await button.waitFor({ state: "visible", timeout: 90_000 });
    await button.scrollIntoViewIfNeeded();
    const box = await button.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `bolt button at ${JSON.stringify(box)} is cut off`);
    assert.deepEqual(app.pageErrors, []);
  });
});
