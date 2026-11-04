// MomX Scanner -> History -> FIND ("which tickers ever hit this?"), end to end.
// The archive page and the search answer are fixtures: a real search scans
// 8-25 MB day files on the worker, which a test has no business doing. What is
// real is the dialog, the condition the browser builds and sends, and the
// results / error screens.

import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";

const BASE = { "momx.scanner.activeList": "Mag7", "momx.scanner.activeView": "history", "momx-auto-cards-on": "0" };
const DIALOG = "[role='dialog'][aria-label='Search the archive']";
const SEARCH = `${DIALOG} .momx-filters-foot .momx-btn.is-primary`;
const TODAY = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());

const archive = () => ({
  json: { list: "Mag7", days: [TODAY], retentionDays: 30, date: TODAY, rows: [], totalRows: 0, returnedRows: 0, offset: 0,
    pageSize: 400, hasMore: false, truncated: false, timeIndex: [], sessions: [] },
});
const found = (read) => {
  const params = new URLSearchParams(read.search);
  const condition = { section: params.get("section"), timeframe: params.get("timeframe"), min: Number(params.get("min")),
    bg: (params.get("bg") || "").split(",").filter(Boolean), fg: (params.get("fg") || "").split(",").filter(Boolean) };
  return {
    json: {
      board: "Mag7", condition, days: [TODAY], totalResults: 2, returnedResults: 2, tickers: 2, scannedDays: 1, scannedSnapshots: 340, truncated: false,
      results: [
        { date: TODAY, symbol: "NVDA", first: "09:35", last: "10:20", firstAt: `${TODAY}T09:35:00-05:00`, hits: 6, peak: 4.8, lastValue: 3.1, industry: "Semiconductors", pctChange: 3.4 },
        { date: TODAY, symbol: "AAPL", first: "09:50", last: "09:55", firstAt: `${TODAY}T09:50:00-05:00`, hits: 2, peak: 3.2, lastValue: 3.2, industry: "Big Tech", pctChange: 1.1 },
      ],
    },
  };
};

async function openFind(page) {
  await page.locator(".momx-history-bar").waitFor({ timeout: 90_000 });
  await page.locator("[data-testid='momx-history-find']").click();
  await page.locator(DIALOG).waitFor({ state: "visible", timeout: 10_000 });
}

e2eTest("FIND builds the condition, refuses an empty minimum, shows the results and goes back to History", async () => {
  await withApp({ view: "MomX Scanner", storage: { ...BASE, "momx.scanner.direction": "bull" },
    fakeReads: { "GET /api/momx-scanner/history": archive, "GET /api/momx-scanner/history-query": found } }, async (app) => {
    const { page } = app;
    await openFind(page);
    const dialog = page.locator(DIALOG);
    assert.equal(await dialog.locator("select.momx-filters-mode[aria-label='Column to search']").inputValue(), "rvol");
    assert.equal(await page.locator(SEARCH).isEnabled(), true);              // default RVOL 1h >= 3 is runnable
    const minimum = dialog.locator("input[aria-label='Minimum reading']");
    await minimum.fill("");
    assert.equal(await page.locator(SEARCH).isDisabled(), true);
    assert.match(await dialog.locator(".momx-filters-sentence").innerText(), /Enter a number to search above/);
    await minimum.fill("3");
    await dialog.locator("label.momx-filters-chip", { hasText: /^5m$/ }).locator("input[name='momx-find-timeframe']").check();
    assert.match(await dialog.locator(".momx-filters-sentence").innerText(), /RVOL 5m ≥ 3, buyers winning — on this day/);
    assert.match(await dialog.locator("label.momx-filters-check").first().innerText(), /Buyers winning/);
    await page.locator(SEARCH).click();

    await page.locator("table.momx-table.momx-find-table").waitFor({ timeout: 10_000 });
    const asked = app.reads.filter((read) => read.path === "/api/momx-scanner/history-query").map((read) => new URLSearchParams(read.search));
    assert.equal(asked.length, 1);
    const params = asked[0];
    assert.deepEqual(
      { list: params.get("list"), section: params.get("section"), timeframe: params.get("timeframe"), min: params.get("min"),
        bg: params.get("bg"), fg: params.get("fg"), date: params.get("date") },
      { list: "Mag7", section: "rvol", timeframe: "5m", min: "3", bg: "cyan,green", fg: "cyan,green,dark_green", date: TODAY },
    );
    assert.match(await page.locator(".momx-find-headline").innerText(), /\S/);
    assert.match(await page.locator(".momx-find-sentence").innerText(), /RVOL 5m ≥ 3/);
    assert.deepEqual((await page.locator(".momx-find-table button.momx-find-symbol").allInnerTexts()).map((t) => t.trim()), ["NVDA", "AAPL"]);
    assert.equal(await page.locator(DIALOG).count(), 0);
    await page.locator(".momx-history-bar button.momx-btn", { hasText: "Back to history" }).click();
    await page.locator("[data-testid='momx-history-find']").waitFor({ timeout: 10_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a search the server cannot apply says why inside the dialog, never an empty result", async () => {
  await withApp({ view: "MomX Scanner", storage: { ...BASE, "momx.scanner.direction": "bull" },
    fakeReads: {
      "GET /api/momx-scanner/history": archive,
      "GET /api/momx-scanner/history-query": { status: 400, json: { error: "section must be one of rvol, sqz, skittles, got 'volume'" } },
    } }, async (app) => {
    const { page } = app;
    await openFind(page);
    await page.locator(SEARCH).click();
    const warn = page.locator(`${DIALOG} .momx-filters-warn.is-loud[role='alert']`);
    await warn.waitFor({ state: "visible", timeout: 10_000 });
    assert.match(await warn.innerText(), /The search did not run\. section must be one of/);
    assert.equal(await page.locator("table.momx-find-table").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on the BEAR board FIND's box reads 'Sellers winning'", async () => {
  await withApp({ view: "MomX Scanner", storage: { ...BASE, "momx.scanner.direction": "bear" },
    fakeReads: { "GET /api/momx-scanner/history": archive, "GET /api/momx-scanner/history-query": found } }, async (app) => {
    const { page } = app;
    await openFind(page);
    assert.match(await page.locator(`${DIALOG} label.momx-filters-check`).first().innerText(), /Sellers winning/);
    await page.locator(SEARCH).click();
    await page.locator("table.momx-find-table").waitFor({ timeout: 10_000 });
    const params = new URLSearchParams(app.reads.find((read) => read.path === "/api/momx-scanner/history-query").search);
    assert.equal(params.get("dir"), "bear");
    assert.equal(params.get("bg"), "magenta,red");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the FIND dialog fits on screen", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE, storage: { ...BASE, "momx.scanner.direction": "bull" },
    fakeReads: { "GET /api/momx-scanner/history": archive } }, async (app) => {
    const { page } = app;
    await openFind(page);
    const box = await page.locator(DIALOG).boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `FIND at ${JSON.stringify(box)} is cut off`);
    assert.deepEqual(app.pageErrors, []);
  });
});
