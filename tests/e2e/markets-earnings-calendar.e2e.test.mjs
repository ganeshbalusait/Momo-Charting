// Earnings Calendar page end to end in the real app (2026-11-04 coverage).
// The calendar read is answered with a fixture (a fixed week, Wednesday
// 2026-11-04), so lanes, week navigation and the empty/error states are exact
// and 'Refresh calendar' never spends a Finnhub call.

import assert from "node:assert/strict";
import test from "node:test";

import { PHONE, e2eTest, marketHoursBlock, overflowsSideways, withApp } from "./harness.mjs";

const VIEW = "Earnings Calendar";
const PAGE = 'section.earnings-workspace[aria-label="Watchlist earnings calendar"]';
const release = (symbol, date, timingCode, timing, epsEstimate, daysUntil) => ({
  symbol, date, timingCode, timing, epsEstimate, daysUntil, quarter: 4, year: 2026,
  source: "Finnhub earnings calendar", manualImport: false,
});
const ROWS = [
  release("AAPL", "2026-11-05", "bmo", "Before market open", 2.1, 1),
  release("NVDA", "2026-11-05", "amc", "After market close", 1.23, 1),
  release("MSFT", "2026-11-06", "tbd", "Time not confirmed", null, 2),
];
const calendar = (overrides = {}) => ({
  live: true, source: "Finnhub earnings calendar", informationOnly: true, watchlistCount: 3, horizonDays: 45,
  startDate: "2026-11-04", endDate: "2026-12-19", refreshedAt: new Date().toISOString(), rows: ROWS,
  upcomingCount: ROWS.length, manualImportCount: 0, errors: [], ...overrides,
});
const fakes = (payload = calendar(), delayForce = 0) => ({
  "GET /api/earnings-calendar": async (read) => {
    if (delayForce && read.search.includes("force=true")) await new Promise((resolve) => setTimeout(resolve, delayForce));
    return { json: payload };
  },
});

async function open(app) {
  await app.page.locator(PAGE).waitFor({ state: "visible", timeout: 60_000 });
  await app.page.locator(`${PAGE} .earnings-command-actions button`, { hasText: "Refresh calendar" }).waitFor({ timeout: 30_000 });
  return app.page.locator(PAGE);
}
const weekLabel = (page) => page.locator(`${PAGE} .earnings-week-toolbar b`).innerText();

e2eTest("the page shows the four summary tiles, a Monday-Friday board and the footnote", async () => {
  await withApp({ view: VIEW, fakeReads: fakes() }, async (app) => {
    const page = await open(app);
    const tiles = page.locator('section.earnings-summary-grid[aria-label="Earnings calendar summary"] article');
    assert.equal(await tiles.count(), 4);
    assert.equal((await tiles.filter({ hasText: "Watchlist coverage" }).locator("b").innerText()).trim(), "3");
    assert.equal((await tiles.filter({ hasText: "Next report" }).locator("b").innerText()).trim(), "AAPL");
    assert.match(await tiles.filter({ hasText: "Next report" }).locator("small").innerText(), /Tomorrow$/);
    assert.equal(await page.locator("section.earnings-week-board").getAttribute("aria-label"), "Week beginning November 2, 2026");
    const days = page.locator("article.earnings-week-day");
    assert.equal(await days.count(), 5);
    assert.deepEqual((await days.locator(":scope > header b").allInnerTexts()).map((t) => t.trim()), ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]);
    assert.match(await page.locator("p.earnings-footnote").innerText(), /makes no Schwab\/TOS request/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("before-open and after-close releases land in their lanes with the EPS estimate", async () => {
  await withApp({ view: VIEW, fakeReads: fakes() }, async (app) => {
    const page = await open(app);
    const thursday = page.locator("article.earnings-week-day", { hasText: "Thursday" });
    const before = thursday.locator("section.earnings-lane.is-before-open article.earnings-release-card");
    const afterClose = thursday.locator("section.earnings-lane.is-after-close article.earnings-release-card");
    assert.deepEqual(await before.locator(".earnings-release-title b").allInnerTexts(), ["AAPL"]);
    assert.match(await before.innerText(), /EPS est\. \$2\.10/);
    assert.equal(await before.locator("small.earnings-timing").getAttribute("class"), "earnings-timing is-bmo");
    assert.deepEqual(await afterClose.locator(".earnings-release-title b").allInnerTexts(), ["NVDA"]);
    assert.match(await afterClose.innerText(), /EPS est\. \$1\.23/);
    const friday = page.locator("article.earnings-week-day", { hasText: "Friday" });
    const msft = friday.locator("section.earnings-lane.is-after-close article.earnings-release-card");
    assert.equal(await msft.locator("small.earnings-timing").getAttribute("class"), "earnings-timing is-tbd");
    assert.match(await msft.innerText(), /EPS est\. --/);
    assert.equal(await page.locator("article.earnings-week-day", { hasText: "Monday" }).locator("article.earnings-release-card").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Next and Previous week move the week by 7 days; This week is only enabled away from it", async () => {
  await withApp({ view: VIEW, fakeReads: fakes() }, async (app) => {
    const page = await open(app);
    const nav = page.locator(".earnings-week-navigation");
    assert.equal(await weekLabel(app.page), "November 2, 2026");
    assert.equal(await nav.locator("button", { hasText: "This week" }).isDisabled(), true);
    await nav.locator("button", { hasText: "Next week" }).click();
    assert.equal(await weekLabel(app.page), "November 9, 2026");
    assert.equal(await nav.locator("button", { hasText: "This week" }).isEnabled(), true);
    assert.equal(await page.locator("article.earnings-release-card").count(), 0);
    await nav.locator("button", { hasText: "Previous week" }).click();
    await nav.locator("button", { hasText: "Previous week" }).click();
    assert.equal(await weekLabel(app.page), "October 26, 2026");
    await nav.locator("button", { hasText: "This week" }).click();
    assert.equal(await weekLabel(app.page), "November 2, 2026");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("no releases shows the empty line; a provider error shows its reason", async () => {
  await withApp({ view: VIEW, fakeReads: fakes(calendar({ rows: [], upcomingCount: 0 })) }, async (app) => {
    const page = await open(app);
    await page.locator(".earnings-empty").waitFor();
    assert.match(await page.locator(".earnings-empty").innerText(), /No watchlist earnings are scheduled in the next 45 days/);
    assert.equal(await page.locator(".earnings-error").count(), 0);
  });
  const reason = "Set FINNHUB_API_KEY or FMP_API_KEY to load a live earnings calendar.";
  await withApp({ view: VIEW, fakeReads: fakes(calendar({ rows: [], live: false, errors: [reason], source: "Earnings calendar unavailable" })) }, async (app) => {
    const page = await open(app);
    await page.locator(".earnings-error").waitFor();
    assert.equal((await page.locator(".earnings-error").innerText()).trim(), reason);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Refresh calendar forces a fresh read and says Refreshing meanwhile", async () => {
  await withApp({ view: VIEW, fakeReads: fakes(calendar(), 1_500) }, async (app) => {
    const page = await open(app);
    await page.locator(".earnings-command-actions button").click();
    await app.page.waitForFunction((root) => document.querySelector(`${root} .earnings-command-actions button`)?.textContent.trim() === "Refreshing", PAGE);
    assert.equal(await page.locator(".earnings-command-actions button svg.is-spinning").count(), 1);
    await app.page.waitForFunction((root) => document.querySelector(`${root} .earnings-command-actions button`)?.textContent.trim() === "Refresh calendar", PAGE, { timeout: 10_000 });
    assert.ok(app.reads.some((read) => read.path === "/api/earnings-calendar" && read.search === "?days=45&force=true"));
    assert.deepEqual(app.pageErrors, []);
  });
});

// Suspected (2026-11-04, from the CSS): .earnings-workspace has no overflow of
// its own inside the 100dvh overflow:hidden .workspace (see the News Feed spec).
test("desktop: a real mouse wheel reaches Friday's column and the footnote", {
  skip: marketHoursBlock() || false,
  todo: "suspected: .earnings-workspace cannot scroll inside the overflow:hidden .workspace",
  timeout: 180_000,
}, async () => {
  await withApp({ view: VIEW, fakeReads: fakes() }, async (app) => {
    const page = await open(app);
    const box = await page.boundingBox();
    await app.page.mouse.move(box.x + box.width / 2, 450);
    for (let step = 0; step < 20; step += 1) await app.page.mouse.wheel(0, 600);
    await app.page.waitForTimeout(300);
    const footnote = await page.locator("p.earnings-footnote").boundingBox();
    assert.ok(footnote && footnote.y >= 0 && footnote.y + footnote.height <= 900, `footnote at ${JSON.stringify(footnote)}`);
  });
});

e2eTest("phone: the calendar fits the width", async () => {
  await withApp({ view: VIEW, viewport: PHONE, fakeReads: fakes() }, async (app) => {
    await open(app);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
