// MomX Scanner -> History tab, end to end: the day picker and its arrows,
// jump-to-time, the session links, ticker search across days and the remembered
// tab. The archive is a fixture shaped exactly like momx/history.py's
// history_response (today + yesterday in ET), so every step has a known answer
// and no 8-25 MB day file is read on the live worker.

import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";

const BASE = { "momx.scanner.activeList": "Mag7", "momx.scanner.direction": "bull", "momx-auto-cards-on": "0" };
const HISTORY_TAB = "button.momx-segbtn[role='tab']";
const DAY = "[data-testid='momx-history-day']";
const ROWS = "table.momx-table.momx-history-table tr.momx-row.momx-history-row";

const etDay = (ms) => new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date(ms));
const TODAY = etDay(Date.now());
const YESTERDAY = (() => {
  const [y, m, d] = TODAY.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d - 1)).toISOString().slice(0, 10);
})();
const TOTAL = 1203;                                            // a busy day: three pages of 400

function snapshot(day, clock, symbol, extra = {}) {
  const at = `${day}T${clock}:00-05:00`;
  return {
    symbol, at, isArrival: true, changed: [], firstSeenAt: at, lastSeenAt: at, hits: 1, truncated: false, peakRvol: 3.2,
    row: {
      symbol, scanPass: true, pctChange: 2.4, last: 101.25, prevClose: 98.88, industry: "Semiconductors", scanReasons: ["rvol:5m"],
      rvol: { "5m": { value: 3.2, bg: "cyan", fg: "black" }, "1h": { value: 1.1, bg: "black", fg: "green" } },
      sqz: { "2h": { value: 1, bg: "red", fg: "white" } }, skittles: { "2h": { value: 61, bg: "green", fg: "black" } },
      highLow: { value: 0.6, bg: "green" }, color: { bg: "green" }, sparkline: [100, 100.5, 101.25], quoteTrend: [],
    },
    ...extra,
  };
}

function archive(read) {
  const params = new URLSearchParams(read.search);
  const symbol = (params.get("symbol") || "").toUpperCase();
  const base = { list: "Mag7", days: [TODAY, YESTERDAY], retentionDays: 30 };
  if (symbol) {
    const rows = symbol === "AAPL"
      ? [snapshot(TODAY, "09:45", "AAPL", { date: TODAY }), snapshot(YESTERDAY, "10:05", "AAPL", { date: YESTERDAY })]
      : [];
    return { json: { ...base, date: params.get("date") || null, symbol, rows, totalRows: rows.length, returnedRows: rows.length,
      offset: 0, pageSize: 400, hasMore: false, truncated: false } };
  }
  const date = params.get("date") || TODAY;
  if (date !== TODAY && date !== YESTERDAY) return { json: { ...base, date: null, rows: [] } };
  const offset = Number(params.get("offset") || 0);
  const rows = [snapshot(date, "09:31", "NVDA"), snapshot(date, "09:45", "AAPL"), snapshot(date, "12:10", "NVDA", { isArrival: false, changed: ["rvol.5m"] })];
  return {
    json: {
      ...base, date, rows, totalRows: TOTAL, returnedRows: rows.length, offset, pageSize: 400, hasMore: offset + 400 < TOTAL, truncated: true,
      timeIndex: [["09:31", 0, 1], ["09:45", 1, 1], ["12:10", TOTAL - 1, 1]],
      sessions: [["Overnight", 0], ["Pre", 0], ["Open", 2], ["Morning", 0], ["Midday", 1], ["Power", 0], ["After", 0]],
    },
  };
}

const historyReads = (app) => app.reads.filter((read) => read.path === "/api/momx-scanner/history").map((read) => new URLSearchParams(read.search));

async function openHistory(page) {
  const tab = page.locator(HISTORY_TAB, { hasText: "History" });
  await tab.waitFor({ timeout: 90_000 });
  if ((await tab.getAttribute("aria-selected")) !== "true") await tab.click();
  await page.locator(".momx-history-bar").waitFor({ timeout: 30_000 });
  await page.locator(ROWS).first().waitFor({ timeout: 30_000 });
}

e2eTest("the day picker lists the archived days with today marked, and the arrows step and stop at the ends", async () => {
  await withApp({ view: "MomX Scanner", storage: BASE, fakeReads: { "GET /api/momx-scanner/history": archive } }, async (app) => {
    const { page } = app;
    await openHistory(page);
    const picker = page.locator(DAY);
    const options = (await picker.locator("option").allInnerTexts()).map((t) => t.trim());
    assert.equal(options.length, 2);
    assert.match(options[0], / · today$/);
    assert.doesNotMatch(options[1], /today/);
    assert.equal(await picker.inputValue(), TODAY);
    const older = page.locator("button.scanner-day-navbtn[aria-label='Older day']");
    const newer = page.locator("button.scanner-day-navbtn[aria-label='Newer day']");
    assert.equal(await newer.isDisabled(), true);
    assert.equal(await older.isDisabled(), false);
    await older.click();
    await page.waitForFunction(([sel, day]) => document.querySelector(sel)?.value === day, [DAY, YESTERDAY], { timeout: 10_000 });
    assert.equal(await older.isDisabled(), true);
    assert.equal(await newer.isDisabled(), false);
    assert.ok(historyReads(app).some((params) => params.get("date") === YESTERDAY));
    assert.match(await page.locator(".momx-history-bar .scanner-day-count").innerText(), /1203 entries/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("typing 0945 reads 09:45 and Go moves the page there; 9999 is refused with a reason", async () => {
  await withApp({ view: "MomX Scanner", storage: BASE, fakeReads: { "GET /api/momx-scanner/history": archive } }, async (app) => {
    const { page } = app;
    await openHistory(page);
    const jump = page.locator("[data-testid='momx-history-jump']");
    await jump.click();
    await jump.pressSequentially("0945");
    assert.equal(await jump.inputValue(), "09:45");
    await page.locator("[data-testid='momx-history-jump-go'][aria-label='Go to this time']").click();
    // 09:45 sits at position 1 of 1203: the page that starts there is offset 1203 - 1 - 400.
    for (let i = 0; i < 40 && !historyReads(app).some((p) => p.get("offset") === "802"); i += 1) await page.waitForTimeout(250);
    assert.ok(historyReads(app).some((params) => params.get("offset") === "802"), "Go must move the pager to 09:45");
    assert.equal(await page.locator(".momx-history-jump-error").count(), 0);

    await jump.click();
    await jump.pressSequentially("9999");
    await page.locator("[data-testid='momx-history-jump-go']").click();
    const error = page.locator(".momx-history-jump-error[role='alert']");
    await error.waitFor({ state: "visible", timeout: 5_000 });
    assert.match(await error.innerText(), /Enter a time like 09:45/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a session link jumps the pager to the first row of that window", async () => {
  await withApp({ view: "MomX Scanner", storage: BASE, fakeReads: { "GET /api/momx-scanner/history": archive } }, async (app) => {
    const { page } = app;
    await openHistory(page);
    const links = page.locator("[data-testid='momx-history-sessions'] button.momx-session-link");
    assert.deepEqual((await links.allInnerTexts()).map((t) => t.replace(/\s+/g, " ").trim()), ["Open 2", "Midday 1"]);
    await links.filter({ hasText: "Open" }).click();
    for (let i = 0; i < 40 && !historyReads(app).some((p) => p.get("offset") === "803"); i += 1) await page.waitForTimeout(250);
    assert.ok(historyReads(app).some((params) => params.get("offset") === "803"), "Open starts at position 0 -> offset 803");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("typing a ticker searches every day ('All days'); Clear returns to the day", async () => {
  await withApp({ view: "MomX Scanner", storage: BASE, fakeReads: { "GET /api/momx-scanner/history": archive } }, async (app) => {
    const { page } = app;
    await openHistory(page);
    await page.locator("input.momx-history-search[aria-label='Search history for a ticker']").fill("aapl");
    await page.waitForFunction((sel) => document.querySelector(sel)?.value === "", DAY, { timeout: 10_000 });
    assert.equal((await page.locator(`${DAY} option`).first().innerText()).trim(), "All days");
    const searched = historyReads(app).filter((params) => params.get("symbol") === "AAPL");
    assert.ok(searched.length >= 1 && !searched[0].get("date"), "the search spans every day");
    await page.locator(ROWS).nth(1).waitFor({ timeout: 10_000 });
    assert.equal(await page.locator(ROWS).count(), 2);           // one AAPL row from each day
    await page.locator(".momx-history-bar button.momx-btn", { hasText: "Clear" }).click();
    await page.waitForFunction(([sel, day]) => document.querySelector(sel)?.value === day, [DAY, TODAY], { timeout: 10_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("History stays the open tab after a reload", async () => {
  await withApp({ view: "MomX Scanner", storage: BASE, fakeReads: { "GET /api/momx-scanner/history": archive } }, async (app) => {
    const { page } = app;
    await openHistory(page);
    await page.reload({ waitUntil: "domcontentloaded" });
    const tab = page.locator(HISTORY_TAB, { hasText: "History" });
    await tab.waitFor({ timeout: 90_000 });
    await page.locator(`${HISTORY_TAB}[aria-selected='true']`, { hasText: "History" }).waitFor({ timeout: 15_000 });
    await page.locator(".momx-history-bar").waitFor({ timeout: 15_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the History bar fits and the day picker is reachable", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE, storage: BASE, fakeReads: { "GET /api/momx-scanner/history": archive } }, async (app) => {
    const { page } = app;
    await openHistory(page);
    for (const selector of [DAY, "input.momx-history-search", "[data-testid='momx-history-find']"]) {
      await page.locator(selector).scrollIntoViewIfNeeded();
      const box = await page.locator(selector).boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `${selector} at ${JSON.stringify(box)} is cut off`);
    }
    assert.deepEqual(app.pageErrors, []);
  });
});
