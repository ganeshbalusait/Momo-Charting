// MomX Scanner -> Industries chips and the Sector cards, end to end, on the live
// Watchlist board (read-only: these controls never write to the server).

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const SCANNER = {
  "momx.scanner.activeList": "Watchlist",
  "momx.scanner.direction": "bull",
  "momx.scanner.activeView": "live",
  "momx-auto-cards-on": "0",
};
const ROWS = "section[data-testid='momx-scanner-view'] .momx-table-scroll tr.momx-row";
const INDUSTRY_BAR = ".momx-industry-bar:not(.momx-sector-bar)";
const SECTOR_TOGGLE = "[data-testid='momx-sector-toggle']";

async function wholeBoard(page) {
  await page.locator("[data-testid='momx-scanner-panel']").first().waitFor({ timeout: 90_000 });
  const matchesOnly = page.locator("label.momx-toggle", { hasText: "Scan matches only" }).locator("input");
  if (await matchesOnly.isChecked()) await matchesOnly.uncheck();
  await page.locator(ROWS).nth(4).waitFor({ timeout: 180_000 });
}

const industriesOnScreen = async (page) =>
  (await page.locator(`${ROWS} td.momx-col-industry`).allInnerTexts()).map((text) => text.replace("🔥", "").trim());

e2eTest("an Industries chip leaves only that industry's rows, names it while closed, and clear restores", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER }, async (app) => {
    const { page } = app;
    await wholeBoard(page);
    const before = await page.locator(ROWS).count();
    const toggle = page.locator(`${INDUSTRY_BAR} button.momx-industry-toggle`);
    await toggle.waitFor({ timeout: 30_000 });
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");    // closed by default
    assert.ok(Number(await page.locator(`${INDUSTRY_BAR} .momx-industry-total`).innerText()) > 1);
    await toggle.click();
    assert.equal(await toggle.getAttribute("aria-expanded"), "true");
    const chip = page.locator(".momx-chip-row button.momx-chip").first();
    await chip.waitFor({ timeout: 10_000 });
    const name = (await chip.evaluate((el) => el.firstChild.textContent)).trim();
    const count = Number((await chip.locator(".momx-chip-count").innerText()).trim());
    await chip.click();
    assert.equal(await chip.getAttribute("aria-pressed"), "true");
    const industries = await industriesOnScreen(page);
    assert.equal(industries.length, count, `the chip said ${count} rows`);
    assert.ok(industries.every((value) => value === name), `rows outside ${name}: ${industries}`);

    // Closing the chips must not hide that a filter is on.
    await toggle.click();
    const active = page.locator(`${INDUSTRY_BAR} .momx-industry-active`);
    assert.match(await active.innerText(), new RegExp(name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
    await active.locator("button.momx-chip-clear").click();
    await active.waitFor({ state: "detached", timeout: 5_000 });
    assert.equal(await page.locator(ROWS).count(), before);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the Sectors toggle shows the cards, the closed bar still names hot sectors, and it is remembered", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER }, async (app) => {
    const { page } = app;
    await wholeBoard(page);
    const toggle = page.locator(SECTOR_TOGGLE);
    await toggle.waitFor({ timeout: 30_000 });
    assert.equal(await toggle.getAttribute("aria-expanded"), "true");    // open by default
    const cards = page.locator(".momx-sectors[aria-label='Sector rotation'] button.momx-sector");
    await cards.first().waitFor({ timeout: 10_000 });
    assert.equal(await cards.count(), Number(await toggle.locator(".momx-industry-total").innerText()));
    for (const text of await cards.allInnerTexts()) assert.match(text, /avg [+-]\d+\.\d%/);
    const hot = (await page.locator("button.momx-sector.is-hot .momx-sector-name").allInnerTexts()).map((t) => t.trim());

    await toggle.click();
    assert.equal(await toggle.getAttribute("aria-expanded"), "false");
    const summary = page.locator(".momx-sector-bar .momx-sector-summary");
    await summary.waitFor({ state: "visible", timeout: 5_000 });
    const line = await summary.innerText();
    for (const name of hot) assert.ok(line.includes("🔥" + name), `closed bar lost the hot sector ${name}`);
    assert.equal(await page.locator(".momx-sectors").count(), 0);

    await page.reload({ waitUntil: "domcontentloaded" });
    await wholeBoard(page);
    await page.locator(SECTOR_TOGGLE).waitFor({ timeout: 30_000 });
    assert.equal(await page.locator(SECTOR_TOGGLE).getAttribute("aria-expanded"), "false");
    await page.locator(".momx-sector-bar .momx-sector-summary").waitFor({ timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the industry and sector bars fit without sideways scroll", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE, storage: SCANNER }, async (app) => {
    const { page } = app;
    await wholeBoard(page);
    const toggle = page.locator(`${INDUSTRY_BAR} button.momx-industry-toggle`);
    await toggle.waitFor({ timeout: 30_000 });
    await toggle.tap();
    await page.locator(".momx-chip-row").waitFor({ timeout: 5_000 });
    for (const selector of [`${INDUSTRY_BAR} button.momx-industry-toggle`, SECTOR_TOGGLE]) {
      const box = await page.locator(selector).boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `${selector} at ${JSON.stringify(box)} is cut off`);
    }
    assert.equal(await overflowsSideways(page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
