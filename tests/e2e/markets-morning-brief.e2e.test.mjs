// Premarket Scanner -> MORNING BRIEFING card and Scanner History's per-day
// brief (2026-11-04 coverage), end to end in the real app.
//
// The live brief exists only 05:30-09:35 ET (or as today's archive), and these
// specs run after the close, so the two reads are answered with fixtures
// (fakeReads) from the very first poll. Nothing here writes.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const VIEW = "Premarket Scanner";
const CARD = '[data-testid="morning-briefing"]';
const generatedAt = new Date(Date.now() - 20 * 60_000).toISOString();

const READY = {
  status: "READY",
  generatedAt,
  lines: [
    "SPY +0.4%, QQQ +0.6% premarket - tape is firm.",
    "Watchlist gainers: **NVDA** +5.2% (UPGRADE: raises PT).",
    "An odd ** marker stays literal",
    "Watch **AAPL** at the open - strongest signals.",
  ],
};
const WAITING = { status: "WAITING", lines: [], message: "The briefing builds weekday mornings 5:30-9:35 AM ET." };

const historyDay = (date, lines, rows = []) => ({
  date,
  rows,
  briefing: { lines, generatedAt: `${date}T13:05:00+00:00` },
});
const HISTORY = {
  retentionDays: 30,
  days: [
    historyDay("2026-11-03", ["Day two: **AAPL** -2.5% on guidance."]),
    historyDay("2026-11-02", ["Day one: **NVDA** +4.0% after the upgrade."], [
      { symbol: "NVDA", firstSeenAt: 1793620800, lastSeenAt: 1793621400, row: { symbol: "NVDA", strength: "STRONG", score: 88 } },
    ]),
    { date: "2026-10-30", rows: [{ symbol: "MSFT", firstSeenAt: 1793361600, lastSeenAt: 1793361600, row: { symbol: "MSFT" } }], briefing: { lines: [] } },
  ],
};

const reads = (briefing, history = HISTORY) => ({
  "GET /api/morning-briefing": { json: briefing },
  "GET /api/premarket-scanner/history": { json: history },
});

async function briefingRead(app) {
  for (let waited = 0; waited < 30_000 && !app.reads.some((read) => read.path === "/api/morning-briefing"); waited += 200) {
    await app.page.waitForTimeout(200);
  }
  assert.ok(app.reads.some((read) => read.path === "/api/morning-briefing"), "the app never asked for the brief");
}

e2eTest("a READY brief shows every line with bold tickers and the Updated time", async () => {
  await withApp({ view: VIEW, fakeReads: reads(READY) }, async (app) => {
    const card = app.page.locator(CARD);
    await card.waitFor({ state: "visible", timeout: 60_000 });
    const toolbar = card.locator(".table-toolbar");
    assert.match(await toolbar.innerText(), /MORNING BRIEFING/);
    assert.match(await toolbar.innerText(), /Updated \S/);
    const items = card.locator("ul.morning-brief-list li");
    assert.equal(await items.count(), READY.lines.length);
    assert.deepEqual(await card.locator("b.morning-briefing-ticker").allInnerTexts(), ["NVDA", "AAPL"]);
    assert.equal((await items.nth(1).innerText()).trim(), "Watchlist gainers: NVDA +5.2% (UPGRADE: raises PT).");
    assert.equal((await items.nth(2).innerText()).trim(), "An odd ** marker stays literal", "an unmatched ** must not swallow the line");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("WAITING hides the card cleanly and the premarket table still renders", async () => {
  await withApp({ view: VIEW, fakeReads: reads(WAITING) }, async (app) => {
    await app.page.locator('[data-testid="premarket-scanner-view"] .premarket-scanner-card').waitFor({ timeout: 60_000 });
    await briefingRead(app);
    await app.page.waitForTimeout(500);
    assert.equal(await app.page.locator(CARD).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("after a backend restart the archived brief still shows", async () => {
  const archived = { status: "READY", archived: true, lines: ["Watchlist gainers: **NVDA** +5.2%."], generatedAt };
  await withApp({ view: VIEW, fakeReads: reads(archived) }, async (app) => {
    const card = app.page.locator(CARD);
    await card.waitFor({ state: "visible", timeout: 60_000 });
    assert.deepEqual(await card.locator("b.morning-briefing-ticker").allInnerTexts(), ["NVDA"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Scanner History shows each day's brief and Previous day switches to that day's", async () => {
  await withApp({ view: VIEW, fakeReads: reads(WAITING), storage: { scannerHistoryView: "table" } }, async (app) => {
    const history = app.page.locator('[data-testid="premarket-scanner-history"]');
    const toggle = history.locator(".signal-explain-close");
    await toggle.waitFor({ timeout: 60_000 });
    await app.page.waitForFunction(() => /Show 3 days/.test(document.querySelector('[data-testid="premarket-scanner-history"] .signal-explain-close')?.textContent || ""), null, { timeout: 30_000 });
    await toggle.click();
    assert.equal(await history.locator('[role="tablist"][aria-label="History view"]').count(), 1);
    const brief = history.locator('[data-testid="scanner-history-briefing"]');
    await brief.waitFor({ timeout: 15_000 });
    assert.match(await brief.locator(".scanner-history-briefing-head").innerText(), /MORNING BRIEFING/);
    assert.deepEqual(await brief.locator("b.morning-briefing-ticker").allInnerTexts(), ["AAPL"], "the newest day opens first");
    await history.locator('button.scanner-day-navbtn[aria-label="Previous day"]').first().click();
    await app.page.waitForFunction(() => /Day one/.test(document.querySelector('[data-testid="scanner-history-briefing"]')?.textContent || ""));
    assert.deepEqual(await brief.locator("b.morning-briefing-ticker").allInnerTexts(), ["NVDA"]);
    await history.locator('button.scanner-day-navbtn[aria-label="Previous day"]').first().click();
    await app.page.waitForFunction(() => !document.querySelector('[data-testid="scanner-history-briefing"]'));
    assert.equal(await brief.count(), 0, "a day whose brief has no lines shows no brief block");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the brief is reached from the bottom bar and fits the screen", async () => {
  await withApp({ view: "Settings", viewport: PHONE, fakeReads: reads(READY) }, async (app) => {
    const tab = app.page.locator('[data-testid="mobile-primary-premarket-scanner"]');
    await tab.waitFor({ state: "visible", timeout: 60_000 });
    await tab.click();
    const card = app.page.locator(CARD);
    await card.waitFor({ state: "visible", timeout: 30_000 });
    await card.scrollIntoViewIfNeeded();
    const box = await card.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `card at ${JSON.stringify(box)} is cut off`);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a pop-out chart window never polls the brief", async () => {
  await withApp({ path: "/?popout=chart&symbol=SPY&timeframe=5m", fakeReads: reads(READY) }, async (app) => {
    await app.page.locator("#root").waitFor({ timeout: 60_000 });
    await app.page.waitForTimeout(8_000);
    assert.equal(app.reads.filter((read) => read.path === "/api/morning-briefing").length, 0);
    assert.equal(await app.page.locator(CARD).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});
