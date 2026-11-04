// Premarket page -> "SCANNER HISTORY · LAST 30 DAYS", end to end: Show N days,
// the Table view's day navigation and counts, that morning's archived briefing,
// the Calendar and List views, and the empty-archive sentence. The archive is a
// fixture shaped like premarket_scanner_history.load_history.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const CARD = "[data-testid='premarket-scanner-history']";
const TOGGLE = `${CARD} .scanner-history-controls button.signal-explain-close`;

const entry = (date, symbol, clock, strength = "STRONG") => ({
  symbol, firstSeenAt: `${date}T${clock}:00-05:00`, lastSeenAt: `${date}T${clock}:00-05:00`,
  row: { symbol, changePct: 3.2, lastPrice: 120.5, strength, score: 4, signalAt: `${date}T07:00:00-05:00`, latestSignalAt: `${date}T07:00:00-05:00`,
    signals48: [], signals920: ["CALL2H"], signalsCyanHigher: ["CALLD"], fires: [], fireDates: {}, forming: false },
});
const DAYS = [
  { date: "2026-11-03", rows: [entry("2026-11-03", "NVDA", "06:40"), entry("2026-11-03", "AAPL", "07:15", "MODERATE")],
    briefing: { lines: ["**NVDA** gapping up after earnings"], generatedAt: "2026-11-03T08:00:00-05:00" } },
  { date: "2026-11-02", rows: [entry("2026-11-02", "TSLA", "06:05")], briefing: null },
  { date: "2026-10-30", rows: [entry("2026-10-30", "META", "07:45"), entry("2026-10-30", "MSFT", "08:10")], briefing: null },
];
const archive = (days) => ({ json: { days, retentionDays: 30 } });

async function open(page, label) {
  const toggle = page.locator(TOGGLE);
  await toggle.waitFor({ timeout: 90_000 });
  await page.locator(TOGGLE, { hasText: label }).waitFor({ timeout: 30_000 });
  await toggle.click();
}

e2eTest("Show 3 days opens the Table view on the newest day; the day arrows step and stop at the ends", async () => {
  await withApp({ view: "Premarket Scanner", storage: { scannerHistoryView: "table" },
    fakeReads: { "GET /api/premarket-scanner/history": archive(DAYS) } }, async (app) => {
    const { page } = app;
    await open(page, /^Show 3 days$/);
    const nav = page.locator(`${CARD} .scanner-day-nav`);
    await nav.waitFor({ timeout: 10_000 });
    const previous = nav.locator("button.scanner-day-navbtn[aria-label='Previous day']");
    const next = nav.locator("button.scanner-day-navbtn[aria-label='Next day']");
    assert.equal((await nav.locator(".scanner-day-label").innerText()).trim(), "Nov 3, 2026");
    assert.equal((await nav.locator(".scanner-day-count").innerText()).trim(), "2 matches · 2 tickers");
    assert.equal(await next.isDisabled(), true);
    assert.equal(await previous.isEnabled(), true);
    assert.match(await page.locator(`${CARD} [data-testid='scanner-history-briefing']`).innerText(), /NVDA gapping up after earnings/);

    await previous.click();
    assert.equal((await nav.locator(".scanner-day-label").innerText()).trim(), "Nov 2, 2026");
    assert.equal((await nav.locator(".scanner-day-count").innerText()).trim(), "1 match · 1 ticker");
    assert.equal(await page.locator(`${CARD} [data-testid='scanner-history-briefing']`).count(), 0);
    await previous.click();
    assert.equal((await nav.locator(".scanner-day-label").innerText()).trim(), "Oct 30, 2026");
    assert.equal((await nav.locator(".scanner-day-count").innerText()).trim(), "2 matches · 2 tickers");
    assert.equal(await previous.isDisabled(), true);
    assert.equal(await next.isEnabled(), true);
    await page.locator(TOGGLE, { hasText: /^Hide$/ }).click();
    await nav.waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the Calendar and List tabs switch the view", async () => {
  await withApp({ view: "Premarket Scanner", storage: { scannerHistoryView: "table" },
    fakeReads: { "GET /api/premarket-scanner/history": archive(DAYS) } }, async (app) => {
    const { page } = app;
    await open(page, /^Show 3 days$/);
    const tabs = page.locator(`${CARD} .scanner-history-viewtabs[role='tablist'][aria-label='History view'] button.scanner-history-viewtab[role='tab']`);
    assert.deepEqual((await tabs.allInnerTexts()).map((t) => t.trim()), ["Table", "Calendar", "List"]);
    await tabs.filter({ hasText: "Calendar" }).click();
    assert.equal(await tabs.filter({ hasText: "Calendar" }).getAttribute("aria-selected"), "true");
    const day = page.locator(`${CARD} .scanner-history-day-view`);
    await day.waitFor({ timeout: 5_000 });
    assert.deepEqual((await day.locator(".scanner-day-card-symbol").allInnerTexts()).map((t) => t.trim()).sort(), ["AAPL", "NVDA"]);
    await tabs.filter({ hasText: "List" }).click();
    await page.locator(`${CARD} .scanner-history-day`).first().waitFor({ timeout: 5_000 });
    assert.deepEqual((await page.locator(`${CARD} .scanner-history-date`).allInnerTexts()).map((t) => t.trim()), ["2026-11-03", "2026-11-02", "2026-10-30"]);
    assert.equal(await day.count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("an empty archive says history starts with the first morning a row appears", async () => {
  await withApp({ view: "Premarket Scanner", fakeReads: { "GET /api/premarket-scanner/history": archive([]) } }, async (app) => {
    const { page } = app;
    await open(page, /^Show 0 days$/);
    const empty = page.locator(`${CARD} .scanner-history-empty`);
    await empty.waitFor({ timeout: 5_000 });
    assert.match(await empty.innerText(), /No archived days yet/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the history card and its day nav fit", async () => {
  await withApp({ view: "Premarket Scanner", viewport: PHONE, storage: { scannerHistoryView: "table" },
    fakeReads: { "GET /api/premarket-scanner/history": archive(DAYS) } }, async (app) => {
    const { page } = app;
    await open(page, /^Show 3 days$/);
    const nav = page.locator(`${CARD} .scanner-day-nav`);
    await nav.waitFor({ timeout: 10_000 });
    await nav.scrollIntoViewIfNeeded();
    const box = await nav.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `day nav at ${JSON.stringify(box)} is cut off`);
    assert.equal(await overflowsSideways(page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
