// Earnings Calendar -> "Import a Discord earnings screenshot": choose, find
// watchlist tickers (local OCR), review, add, clear - end to end in the real
// app (2026-11-04 coverage). All three POSTs are answered by the harness, so
// no OCR runs and the trader's saved imports are never touched; the calendar
// read is a fixture too, so the follow-up refresh spends no Finnhub call.

import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { e2eTest, withApp } from "./harness.mjs";

const VIEW = "Earnings Calendar";
const IMPORT = 'section.earnings-image-import[aria-label="Import earnings screenshot"]';
const FIXTURES = mkdtempSync(path.join(tmpdir(), "agx-e2e-earnings-"));
const PNG = path.join(FIXTURES, "weekly-earnings.png");
const TEXT = path.join(FIXTURES, "not-an-image.txt");
// A 1x1 transparent PNG.
writeFileSync(PNG, Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==", "base64"));
writeFileSync(TEXT, "earnings: NVDA tuesday");

const calendar = (manualImportCount = 0) => ({
  json: {
    live: true, source: "Finnhub earnings calendar", informationOnly: true, watchlistCount: 3, horizonDays: 45,
    startDate: "2026-11-04", endDate: "2026-12-19", refreshedAt: new Date().toISOString(),
    rows: [{ symbol: "NVDA", date: "2026-11-19", timingCode: "amc", timing: "After market close", epsEstimate: 1.2, daysUntil: 15 }],
    upcomingCount: 1, manualImportCount, errors: [],
  },
});
const CANDIDATES = {
  json: {
    source: "Local Windows OCR", weekStart: "2026-11-02", matchCount: 2,
    message: "Review the date and timing before adding these watchlist-only entries.",
    candidates: [
      { key: "NVDA-2026-11-03-bmo", symbol: "NVDA", date: "2026-11-03", timingCode: "bmo", timing: "Before market open" },
      { key: "AAPL-2026-11-06-amc", symbol: "AAPL", date: "2026-11-06", timingCode: "amc", timing: "After market close" },
    ],
  },
};
const isMonday = (iso) => /^\d{4}-\d{2}-\d{2}$/.test(iso) && new Date(`${iso}T12:00:00Z`).getUTCDay() === 1;
const OCR = "POST /api/earnings-calendar/ocr";
const SAVE = "POST /api/earnings-calendar/manual-import";
const CLEAR = "POST /api/earnings-calendar/manual-imports/clear";

async function chooseScreenshot(app, file = PNG) {
  const section = app.page.locator(IMPORT);
  await section.waitFor({ state: "visible", timeout: 60_000 });
  await section.locator('.earnings-image-controls input[type="file"]').setInputFiles(file);
  return section;
}

async function reviewRows(app) {
  const section = await chooseScreenshot(app);
  await section.locator(".earnings-image-dropzone.is-ready").waitFor();
  await section.locator(".earnings-image-controls button", { hasText: "Find watchlist tickers" }).click();
  await section.locator(".earnings-image-review").waitFor({ timeout: 15_000 });
  return section;
}

e2eTest("choosing a PNG gets it ready for local OCR", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/earnings-calendar": calendar() } }, async (app) => {
    const section = await chooseScreenshot(app);
    const dropzone = section.locator('.earnings-image-dropzone[role="button"]');
    await section.locator(".earnings-image-dropzone.is-ready").waitFor({ timeout: 10_000 });
    assert.match(await dropzone.innerText(), /Screenshot added[\s\S]*weekly-earnings\.png/);
    assert.equal(await section.locator('img[alt="Selected earnings screenshot preview"]').count(), 1);
    assert.equal((await section.locator("p.earnings-image-message").innerText()).trim(),
      "Image added. Choose the earnings week, then find your watchlist tickers.");
    assert.ok(isMonday(await section.locator('.earnings-image-controls label input[type="date"]').inputValue()), "the week defaults to a Monday");
    assert.equal(app.writes.length, 0, "choosing a file sends nothing");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("choosing a non-image says so and sends nothing", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/earnings-calendar": calendar() } }, async (app) => {
    const section = await chooseScreenshot(app, TEXT);
    await section.locator("p.earnings-image-error").waitFor({ timeout: 10_000 });
    assert.equal((await section.locator("p.earnings-image-error").innerText()).trim(), "Use a PNG, JPG, or WebP screenshot.");
    assert.equal(await section.locator(".earnings-image-dropzone.is-ready").count(), 0);
    assert.equal(await section.locator(".earnings-image-controls button", { hasText: "Find watchlist tickers" }).isDisabled(), true);
    assert.equal(app.writes.length, 0);
  });
});

e2eTest("review, edit and add only the ticked rows", async () => {
  await withApp({
    view: VIEW,
    fakeReads: { "GET /api/earnings-calendar": calendar() },
    fakeWrites: { [OCR]: CANDIDATES, [SAVE]: { json: { imported: 1, skipped: 0, manualImportCount: 1 } } },
  }, async (app) => {
    const section = await reviewRows(app);
    const ocr = app.writes.filter((write) => write.path === "/api/earnings-calendar/ocr");
    assert.equal(ocr.length, 1);
    const sent = JSON.parse(ocr[0].body);
    assert.match(sent.imageData, /^data:image\/png;base64,/);
    assert.ok(isMonday(sent.weekStart), `weekStart ${sent.weekStart} is not a Monday`);
    assert.match(await section.locator("p.earnings-image-message").innerText(), /^2 watchlist tickers found locally/);
    assert.equal(await section.locator(".earnings-image-review-grid article").count(), 2);

    await section.locator('input[aria-label="NVDA earnings date"]').fill("2026-11-04");
    await section.locator('select[aria-label="NVDA earnings timing"]').selectOption("amc");
    await section.locator(".earnings-image-review-grid article", { hasText: "AAPL" }).locator('input[type="checkbox"]').uncheck();
    const readsBefore = app.reads.filter((read) => read.search.includes("force=true")).length;
    await section.locator(".earnings-image-review > button", { hasText: "Add selected to calendar" }).click();
    await section.locator("p.earnings-image-message", { hasText: "added to the calendar" }).waitFor({ timeout: 10_000 });
    assert.equal((await section.locator("p.earnings-image-message").innerText()).trim(), "1 reviewed earnings release added to the calendar.");

    const saves = app.writes.filter((write) => write.path === "/api/earnings-calendar/manual-import");
    assert.equal(saves.length, 1);
    const rows = JSON.parse(saves[0].body).rows;
    assert.equal(rows.length, 1, "the unticked AAPL row must not be sent");
    assert.deepEqual([rows[0].symbol, rows[0].date, rows[0].timingCode], ["NVDA", "2026-11-04", "amc"]);
    assert.equal(await section.locator(".earnings-image-review").count(), 0);
    assert.ok(app.reads.filter((read) => read.path === "/api/earnings-calendar" && read.search.includes("force=true")).length > readsBefore,
      "the calendar reloads after an import");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a refused import shows the server's reason and the button comes back", async () => {
  const reason = "Select at least one valid watchlist earnings row.";
  await withApp({
    view: VIEW,
    fakeReads: { "GET /api/earnings-calendar": calendar() },
    fakeWrites: { [OCR]: CANDIDATES, [SAVE]: { status: 400, json: { error: reason } } },
  }, async (app) => {
    const section = await reviewRows(app);
    const add = section.locator(".earnings-image-review > button");
    await add.click();
    await section.locator("p.earnings-image-error").waitFor({ timeout: 10_000 });
    assert.equal((await section.locator("p.earnings-image-error").innerText()).trim(), reason);
    await section.locator(".earnings-image-review > button:enabled").waitFor();
    assert.equal((await add.innerText()).trim(), "Add selected to calendar");
    assert.equal(await section.locator(".earnings-image-review-grid article").count(), 2, "the review rows stay for another try");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Clear N manual imports sends one clear and reports how many went", async () => {
  await withApp({
    view: VIEW,
    fakeReads: { "GET /api/earnings-calendar": calendar(2) },
    fakeWrites: { [CLEAR]: { json: { removed: 2, manualImportCount: 0 } } },
  }, async (app) => {
    const section = app.page.locator(IMPORT);
    const clear = section.locator("button.earnings-image-clear");
    await clear.waitFor({ state: "visible", timeout: 60_000 });
    assert.equal((await clear.innerText()).trim(), "Clear 2 manual imports");
    await clear.click();
    await section.locator("p.earnings-image-message", { hasText: "cleared" }).waitFor({ timeout: 10_000 });
    assert.equal((await section.locator("p.earnings-image-message").innerText()).trim(), "2 manual screenshot imports cleared.");
    assert.equal(app.writes.filter((write) => write.path === "/api/earnings-calendar/manual-imports/clear").length, 1);
    assert.deepEqual(app.pageErrors, []);
  });
});
