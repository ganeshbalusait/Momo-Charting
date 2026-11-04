// The option chain column beside the Charts & OI chart (2026-11-04
// coverage), end to end in the real app. The chain read is a fixture
// (options-fixtures.mjs: spot $100, strikes 80-120, three expiries), so
// rows, ATM and strikes are exact; the chart itself loads live candles for
// the page's ticker. The contract lease and the SSE price feed are answered
// in the browser (see the fixture file for why).

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { CHAIN_READS, STRIKES, chartStorage, expectedMoveFor, sleep } from "./options-fixtures.mjs";

const VIEW = '[data-testid="charts-and-oi-view"]';
const CHAIN = "#charts-oi-option-chain";
const TABS = `${CHAIN} .charts-oi-chain-view-tabs[aria-label="Option chain view"]`;
const OPEN = `${CHAIN} .charts-oi-expiry-accordion .charts-oi-expiry-item.is-open`;
const DEPTH = `${OPEN} select[aria-label="Strikes displayed above and below ATM"]`;

async function chainShown(page) {
  await page.locator(CHAIN).waitFor({ state: "visible", timeout: 90_000 });
  await page.locator(`${CHAIN} > header`, { hasText: "Spot $100" }).waitFor({ timeout: 60_000 });
}

async function openChainTab(page) {
  await page.locator(`${TABS} [role="tab"]`, { hasText: /^Chain$/ }).click();
  await page.locator(OPEN).first().waitFor({ state: "visible", timeout: 30_000 });
}

const rowCount = (page) => page.locator(`${OPEN} tbody tr`).count();
const options = async (extra = {}) => ({ view: "Charts & OI", storage: await chartStorage(), fakeReads: CHAIN_READS, ...extra });

e2eTest("the chain column shows the live chain with High OI as the default tab", async () => {
  await withApp(await options(), async (app) => {
    await chainShown(app.page);
    const header = await app.page.locator(`${CHAIN} > header`).innerText();
    assert.match(header, /LIVE OPTION CHAIN/);
    assert.match(header, /· 2 DTE/);
    assert.equal(await app.page.locator(`${TABS} [role="tab"][aria-selected="true"]`).innerText(), "High OI");
    assert.ok(app.reads.some((read) => read.path === "/api/oi-finder-chain"), "the chain came from the compact feed");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Chain tab: the nearest expiry opens on its ATM row and the ± selector bounds the strikes", async () => {
  await withApp(await options(), async (app) => {
    await chainShown(app.page);
    await openChainTab(app.page);
    assert.equal(await app.page.locator(OPEN).count(), 1, "only the nearest expiry starts open");
    assert.equal(await app.page.locator(`${OPEN} .charts-oi-expiry-row`).getAttribute("aria-expanded"), "true");
    assert.equal(await rowCount(app.page), 21, "±10 strikes around ATM by default");
    const atm = app.page.locator(`${OPEN} tr.is-atm`);
    assert.equal(await atm.count(), 1);
    assert.equal((await atm.locator(".tos-strike-alert b").innerText()).trim(), "100");
    assert.equal((await atm.locator("td.option-mark").first().innerText()).trim(), "$4.10");
    // EM HIGH / EM LOW sit on the strikes nearest spot ± that expiry's expected move.
    const em = expectedMoveFor(2);
    assert.equal((await app.page.locator(`${OPEN} tr.is-em-high .tos-strike-alert b`).innerText()).trim(), String(Math.round(100 + em)));
    assert.equal((await app.page.locator(`${OPEN} tr.is-em-low .tos-strike-alert b`).innerText()).trim(), String(Math.round(100 - em)));
    await app.page.locator(DEPTH).selectOption("5");
    await app.page.waitForFunction((selector) => document.querySelectorAll(selector).length === 11, `${OPEN} tbody tr`);
    await app.page.locator(DEPTH).selectOption("all");
    await app.page.waitForFunction(([selector, wanted]) => document.querySelectorAll(selector).length === wanted, [`${OPEN} tbody tr`, STRIKES.length]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("tapping a strike opens a price alert prefilled with that strike and saves nothing", async () => {
  await withApp(await options(), async (app) => {
    await chainShown(app.page);
    await openChainTab(app.page);
    await app.page.locator(`${OPEN} button.tos-strike-alert[aria-label$=" at strike 105"]`).click();
    await app.page.waitForFunction(() => [...document.querySelectorAll('input[aria-label="Price alert value"]')]
      .some((input) => input.offsetParent !== null && input.value === "105.00"), null, { timeout: 15_000 });
    assert.deepEqual(app.writes, [], "the alert form opens; nothing is sent until he saves it");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Hide chain is remembered across a reload and Show chain brings it back", async () => {
  await withApp(await options(), async (app) => {
    await chainShown(app.page);
    const toggle = app.page.locator(`${VIEW} button[aria-controls="charts-oi-option-chain"]`);
    assert.match(await toggle.innerText(), /Hide chain/);
    await toggle.click();
    await app.page.waitForFunction((selector) => !document.querySelector(selector), CHAIN);
    assert.equal(await app.page.evaluate(() => localStorage.getItem("chartsOiChainOpen")), "false");
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await app.page.locator(VIEW).waitFor({ timeout: 90_000 });
    await sleep(2_000);
    assert.equal(await app.page.locator(CHAIN).count(), 0, "the hidden chain came back after a reload");
    await app.page.locator(`${VIEW} button[aria-controls="charts-oi-option-chain"]`, { hasText: "Show chain" }).click();
    await chainShown(app.page);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("fullscreen exits on Escape, the chain moves sides, and the keyboard resizes it inside 360-720", async () => {
  await withApp(await options(), async (app) => {
    await chainShown(app.page);
    await app.page.locator(`${CHAIN} > header button[aria-label="Fill the screen with the option chain"]`).click();
    await app.page.locator(`${CHAIN}.is-fullscreen`).waitFor({ timeout: 10_000 });
    await app.page.keyboard.press("Escape");
    await app.page.waitForFunction((selector) => !document.querySelector(`${selector}.is-fullscreen`), CHAIN);

    const page = app.page.locator(VIEW);
    assert.match(await page.getAttribute("class"), /\bchain-right\b/);
    await app.page.locator(`${CHAIN} .charts-oi-chain-side-toggle`).click();
    await app.page.locator(`${VIEW}.chain-left`).waitFor({ timeout: 10_000 });
    await app.page.locator(`${CHAIN} .charts-oi-chain-side-toggle`).click();
    await app.page.locator(`${VIEW}.chain-right`).waitFor({ timeout: 10_000 });

    const handle = app.page.locator('.charts-oi-resize-handle[aria-label="Resize chart and option-chain panels"]');
    await handle.focus();
    const start = Number(await handle.getAttribute("aria-valuenow"));
    assert.ok(start >= 360 && start <= 720, `starting width ${start}`);
    await app.page.keyboard.press("ArrowLeft");                    // chain on the right: left widens it
    assert.equal(Number(await handle.getAttribute("aria-valuenow")), Math.min(720, start + 24));
    for (let press = 0; press < 20; press += 1) await app.page.keyboard.press("ArrowLeft");
    assert.equal(await handle.getAttribute("aria-valuenow"), "720");
    for (let press = 0; press < 20; press += 1) await app.page.keyboard.press("ArrowRight");
    assert.equal(await handle.getAttribute("aria-valuenow"), "360");
    assert.equal(await app.page.evaluate(() => localStorage.getItem("chartsOiChainWidth")), "360");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the docked Watchlist table switches the chart and chain to the ticker clicked", async () => {
  const quotes = (read) => ({ json: { rows: (new URLSearchParams(read.search).get("symbols") || "").split(",").filter(Boolean)
    .map((symbol, index) => ({ symbol, lastPrice: 100 + index, change: 1, changePct: 1, volume: 1000, name: symbol })) } });
  const storage = await chartStorage({ chartsOiChainDockPanels: JSON.stringify(["watchlist"]) });
  await withApp({ view: "Charts & OI", storage, fakeReads: { ...CHAIN_READS, "GET /api/watchlist-quotes": quotes } }, async (app) => {
    await chainShown(app.page);
    const panel = app.page.locator(`${CHAIN} .charts-oi-chain-dock-panel[aria-label="Watchlist panel"]`);
    await panel.locator("td.is-symbol").first().waitFor({ timeout: 30_000 });
    const current = (await app.page.locator(".charts-oi-heading h2").innerText()).split(" ")[0];
    const target = current === "NVDA" ? "SPY" : "NVDA";
    await panel.locator("tr", { has: app.page.locator("td.is-symbol", { hasText: new RegExp(`^${target}$`) }) }).click();
    await app.page.locator(".charts-oi-heading h2", { hasText: `${target} — Charts & OI` }).waitFor({ timeout: 30_000 });
    await panel.locator("tr.is-active td.is-symbol", { hasText: new RegExp(`^${target}$`) }).waitFor({ timeout: 15_000 });
    assert.ok(app.reads.some((read) => read.path === "/api/oi-finder-chain" && new URLSearchParams(read.search).get("symbol") === target));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the chart opens without the chain and the Chain tab reveals it", async () => {
  await withApp(await options({ viewport: PHONE }), async (app) => {
    const tabs = app.page.locator('[data-testid="mobile-chart-section-tabs"]');
    await tabs.waitFor({ state: "visible", timeout: 90_000 });
    await sleep(1_500);
    assert.equal(await app.page.locator(CHAIN).count(), 0, "phones start chart-only");
    await tabs.locator("button", { hasText: "Chain" }).click();
    await chainShown(app.page);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
