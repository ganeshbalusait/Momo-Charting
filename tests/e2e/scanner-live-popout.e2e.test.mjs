// MomX Scanner -> pop-out board windows, end to end on the live Mag7 board
// (read-only: a pop-out only polls the board). Covers: the in-app window opens
// beside the page board, a second press raises instead of duplicating, its
// place and size are remembered per list, Escape closes the front window, and
// the detached page (/?popout=momx&list=Mag7) renders the board on its own.

import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";

const SCANNER = { "momx.scanner.activeList": "Mag7", "momx.scanner.direction": "bull", "momx.scanner.activeView": "live", "momx-auto-cards-on": "0" };
const POP = "button.momx-icon-btn.momx-popout-btn[aria-label='Pop Mag7 out into its own window']";
const WINDOW = ".momx-popout-window[role='dialog'][aria-label='Mag7 scanner window']";

async function popOut(page) {
  await page.locator(POP).waitFor({ timeout: 90_000 });
  await page.locator(POP).click();
  const win = page.locator(WINDOW);
  await win.waitFor({ state: "visible", timeout: 15_000 });
  return win;
}

e2eTest("the pop-out button opens a Mag7 window beside the page board; pressing it again raises, never duplicates", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER }, async (app) => {
    const { page } = app;
    const win = await popOut(page);
    assert.equal(await page.locator(`${POP}[aria-pressed='true']`).count(), 1);
    assert.equal(await page.locator("section[data-testid='momx-scanner-view'] [data-testid='momx-scanner-panel']").first().isVisible(), true);
    // The window holds its own board for its list, without its own pop-out or bolt buttons.
    await win.locator("[data-testid='momx-scanner-panel']").waitFor({ timeout: 30_000 });
    assert.equal(await win.locator("button.momx-popout-btn").count(), 0);
    assert.equal(await win.locator("[data-testid='momx-bolt-view']").count(), 0);
    assert.match(await win.locator(".momx-popout-titlebar b").innerText(), /Mag7/);
    await page.locator(POP).click();
    await page.waitForTimeout(500);
    assert.equal(await page.locator(".momx-popout-window").count(), 1);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a window's place and size are remembered for its list", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER }, async (app) => {
    const { page } = app;
    let win = await popOut(page);
    const bar = win.locator(".momx-popout-titlebar");
    const start = await bar.boundingBox();
    await page.mouse.move(start.x + 60, start.y + start.height / 2);
    await page.mouse.down();
    await page.mouse.move(start.x + 20, start.y + start.height / 2 + 30, { steps: 8 });
    await page.mouse.up();
    const grip = win.locator(".momx-popout-resize[role='separator']");
    const corner = await grip.boundingBox();
    await page.mouse.move(corner.x + corner.width / 2, corner.y + corner.height / 2);
    await page.mouse.down();
    await page.mouse.move(corner.x - 80, corner.y - 50, { steps: 8 });
    await page.mouse.up();
    const moved = await win.boundingBox();
    assert.ok(moved, "window still on screen");

    await page.reload({ waitUntil: "domcontentloaded" });
    win = await popOut(page);
    const again = await win.boundingBox();
    for (const key of ["x", "y", "width", "height"]) {
      assert.ok(Math.abs(again[key] - moved[key]) <= 2, `${key}: ${moved[key]} before reload, ${again[key]} after`);
    }
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Escape closes the front window and leaves the page board", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER }, async (app) => {
    const { page } = app;
    await popOut(page);
    await page.keyboard.press("Escape");
    await page.locator(WINDOW).waitFor({ state: "detached", timeout: 5_000 });
    assert.equal(await page.locator(`${POP}[aria-pressed='false']`).count(), 1);
    await page.locator(POP).click();                           // and it can be opened again
    await page.locator(WINDOW).waitFor({ timeout: 10_000 });
    await page.locator(`${WINDOW} button.momx-popout-close[aria-label='Close the Mag7 window']`).click();
    await page.locator(WINDOW).waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the detached window page renders the Mag7 board on its own, with no pop-out button", async () => {
  await withApp({ path: "/?popout=momx&list=Mag7", storage: { "momx-auto-cards-on": "0" } }, async (app) => {
    const { page } = app;
    await page.locator("main.trading-popout-root.is-momx[data-testid='momx-popout-root']").waitFor({ timeout: 90_000 });
    await page.locator("[data-testid='momx-scanner-view'] [data-testid='momx-scanner-panel']").waitFor({ timeout: 60_000 });
    assert.equal(await page.locator("button.momx-popout-btn").count(), 0);
    assert.equal(await page.locator("[data-testid='momx-bolt-view']").count(), 0);
    assert.equal(await page.locator("nav button.nav-item, button.nav-item").count(), 0, "a detached window is the board and nothing else");
    const boards = app.reads.filter((read) => read.path === "/api/momx-scanner");
    assert.ok(boards.length > 0 && boards.every((read) => new URLSearchParams(read.search).get("list") === "Mag7"));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the pop-out window opens inside the screen", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE, storage: SCANNER }, async (app) => {
    const { page } = app;
    await page.locator(POP).waitFor({ timeout: 90_000 });
    await page.locator(POP).tap();
    const win = page.locator(WINDOW);
    await win.waitFor({ state: "visible", timeout: 15_000 });
    const box = await win.boundingBox();
    assert.ok(box && box.x >= 0 && box.y >= 0 && box.x + box.width <= PHONE.width + 1, `window at ${JSON.stringify(box)}`);
    // Edge to edge on a phone, and the way out stays reachable.
    const close = win.locator("button.momx-popout-close[aria-label='Close the Mag7 window']");
    const closeBox = await close.boundingBox();
    assert.ok(closeBox && closeBox.x + closeBox.width <= PHONE.width, `close button at ${JSON.stringify(closeBox)}`);
    await close.tap();
    await win.waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});
