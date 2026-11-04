// Learn + Setup page end to end in the real app (2026-11-04 coverage): the
// eight steps and their screenshots, the Setup tab's links, the 'Take me there'
// buttons, 'Everything else', the topbar '?' button, the collapsible sections,
// and the page's own scroll container at desktop and phone width. Read-only.

import assert from "node:assert/strict";

import { LEARN_SETUP_SECTIONS } from "../../frontend/src/learnContent.js";
import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const VIEW = "Learn + Setup";
const PAGE = '[data-testid="learn-view"] div.learn-page';
const TABS = `${PAGE} [role="tablist"][aria-label="Learn and Setup"]`;

async function open(app) {
  await app.page.locator(`${PAGE} section.learn-steps`).waitFor({ state: "visible", timeout: 60_000 });
}

e2eTest("Learn: eight numbered steps whose screenshots all load", async () => {
  await withApp({ view: VIEW }, async (app) => {
    await open(app);
    assert.equal((await app.page.locator(`${PAGE} .learn-hero h1`).innerText()).trim(), "Welcome to AGX");
    assert.equal((await app.page.locator("#learn-steps-heading").innerText()).trim(), "Your first 15 minutes");
    const steps = app.page.locator(`${PAGE} article.learn-step`);
    assert.equal(await steps.count(), 8);
    assert.deepEqual((await app.page.locator(`${PAGE} .learn-step-number`).allInnerTexts()).map((t) => t.trim()), ["1", "2", "3", "4", "5", "6", "7", "8"]);
    const images = app.page.locator(`${PAGE} figure.learn-shot img`);
    assert.ok(await images.count() > 0);
    for (const image of await images.all()) {
      await image.scrollIntoViewIfNeeded();
      await image.evaluate((img) => (img.complete ? null : new Promise((resolve) => { img.onload = img.onerror = resolve; })));
      const width = await image.evaluate((img) => img.naturalWidth);
      assert.ok(width > 0, `${await image.getAttribute("src")} did not load`);
    }
    assert.ok(await app.page.locator(`${PAGE} span.learn-shot-marker`).count() > 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Setup: every section is open and only the sites to visit are links", async () => {
  await withApp({ view: VIEW }, async (app) => {
    await open(app);
    const setup = app.page.locator(`${TABS} button.learn-tab[role="tab"]`, { hasText: "Setup" });
    await setup.click();
    assert.equal(await setup.getAttribute("aria-selected"), "true");
    assert.match(await setup.getAttribute("class"), /learn-tab-active/);
    assert.equal(await app.page.locator(`${TABS} button.learn-tab`, { hasText: "Learn" }).getAttribute("aria-selected"), "false");
    assert.equal(await app.page.locator(`${PAGE} section.learn-setup-section`).count(), LEARN_SETUP_SECTIONS.length);
    assert.deepEqual((await app.page.locator(`${PAGE} h3.learn-setup-title`).allInnerTexts()).map((t) => t.trim()),
      LEARN_SETUP_SECTIONS.map((section) => section.title));
    const schwab = app.page.locator(`${PAGE} a.learn-link[href="https://developer.schwab.com"]`).first();
    assert.equal(await schwab.getAttribute("target"), "_blank");
    assert.match(await schwab.getAttribute("rel"), /noopener/);
    assert.equal(await app.page.locator(`${PAGE} a.learn-link[href="https://app.alpaca.markets"]`).count() > 0, true);
    assert.equal(await app.page.locator(`${PAGE} a[href*="127.0.0.1"]`).count(), 0, "the callback address is typed, never tapped");
    assert.deepEqual(app.pageErrors, []);
  });
});

for (const [label, page] of [["Open Premarket", "Premarket Scanner"], ["Open the Watchlist", "Watchlist"]]) {
  e2eTest(`'${label}' takes him to ${page}`, async () => {
    await withApp({ view: VIEW }, async (app) => {
      await open(app);
      await app.page.locator(`${PAGE} button.learn-goto`, { hasText: label }).first().click();
      await app.page.locator(".command-context h1", { hasText: page }).waitFor({ timeout: 15_000 });
      assert.equal((await app.page.locator(".command-context h1").innerText()).trim(), page);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

e2eTest("'Everything else' > Sectors opens the Sectors page", async () => {
  await withApp({ view: VIEW }, async (app) => {
    await open(app);
    await app.page.locator(`${PAGE} section.learn-elsewhere button`, { hasText: /^Sectors$/ }).click();
    await app.page.locator('[data-testid="sectors-view"]').waitFor({ timeout: 15_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the topbar '?' opens Learn from Settings", async () => {
  await withApp({ view: "Settings" }, async (app) => {
    const help = app.page.locator('button.header-icon-button[aria-label="Learn how to use AGX"]');
    await help.waitFor({ state: "visible", timeout: 60_000 });
    await help.click();
    await open(app);
    assert.equal((await app.page.locator(".command-context h1").innerText()).trim(), VIEW);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a 'How each part works' section opens on click and shows its blocks", async () => {
  await withApp({ view: VIEW }, async (app) => {
    await open(app);
    const section = app.page.locator(`${PAGE} details.learn-section`).first();
    assert.equal(await section.evaluate((el) => el.open), false);
    await section.locator("summary").click();
    assert.equal(await section.evaluate((el) => el.open), true);
    await section.locator(".learn-section-body").waitFor({ state: "visible" });
    assert.ok(await section.locator(".learn-section-body .learn-block").count() > 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

for (const viewport of [undefined, PHONE]) {
  const name = viewport ? "phone" : "desktop";
  e2eTest(`${name}: a real scroll of the page reaches 'Everything else'`, async () => {
    await withApp({ view: VIEW, ...(viewport ? { viewport } : {}) }, async (app) => {
      await open(app);
      const target = app.page.locator(`${PAGE} section.learn-elsewhere`);
      const height = viewport ? viewport.height : 900;
      const inView = async () => {
        const box = await target.boundingBox();
        return Boolean(box && box.y < height && box.y + box.height > 0);
      };
      await app.page.mouse.move((viewport ? viewport.width : 1440) / 2, height / 2);
      for (let step = 0; step < 80 && !(await inView()); step += 1) await app.page.mouse.wheel(0, 900);
      assert.ok(await inView(), "the Learn page cannot be scrolled to its end");
      if (viewport) assert.equal(await overflowsSideways(app.page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}
