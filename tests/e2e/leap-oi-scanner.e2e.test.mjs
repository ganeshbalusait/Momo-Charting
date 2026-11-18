// LEAP OI Scanner page (2026-11-17). The board on screen is the REAL server's reply: before the specs run, the
// integration test asks the real /api/leap-oi-scanner on scratch files (Schwab faked) and saves what came back; the
// browser plays it. Checked at desktop and phone width: the two headline cards, the ENTRY list, the whole list with
// delta 0.10 and up only, the filter chips sending the right request, Chart opening the chart, nothing cut off.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { before } from "node:test";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { PHONE, e2eTest, marketHoursBlock, overflowsSideways, scrollsToTheEnd, withApp } from "./harness.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const VIEW = "[data-testid='leap-oi-view']";
const WIDTHS = [["desktop", undefined], ["phone width", PHONE]];

let real = null;

before(() => {
  if (marketHoursBlock()) return;
  const out = path.join(mkdtempSync(path.join(tmpdir(), "agx-leap-oi-")), "board.json");
  execFileSync(path.join(REPO, ".venv", "Scripts", "python.exe"),
    ["-m", "pytest", "tests/test_momx_leap_oi_scanner_api.py", "-q", "-p", "no:cacheprovider", "-k", "background_then_shows"],
    { cwd: REPO, encoding: "utf8", timeout: 240_000, env: { ...process.env, LEAP_OI_CAPTURE: out, PYTHONDONTWRITEBYTECODE: "1" } });
  real = JSON.parse(readFileSync(out, "utf8"));
}, { timeout: 260_000 });

for (const [label, viewport] of WIDTHS) {
  const phone = viewport === PHONE;
  const size = viewport ? { viewport } : {};

  e2eTest(`LEAP OI Scanner at ${label}: headline cards, ENTRY list, whole list, chips, nothing cut off`, async () => {
    const asked = [];
    const reads = {
      "GET /api/leap-oi-scanner": (read) => { asked.push(read.search || ""); return { json: real }; },
      "GET /api/watchlist": { json: { tickers: ["PLTR", "AMD"] } },
    };
    await withApp({ view: "LEAP OI Scanner", ...size, fakeReads: reads }, async (app) => {
      const { page } = app;
      const view = page.locator(VIEW);
      await view.waitFor({ timeout: 90_000 });
      await view.locator(".leapoi-hero").first().waitFor({ timeout: 30_000 });
      const heroes = await view.locator(".leapoi-hero").allInnerTexts();
      assert.match(heroes[0], /Biggest call open interest[\s\S]*NVDA|AAPL/);
      assert.match(heroes[0], /200 Call/);
      assert.match(heroes[1], /140 Put/);
      const rowSel = phone ? "[data-testid='leap-oi-row-card']" : "[data-testid='leap-oi-row']";
      const entrySel = phone ? "[data-testid='leap-oi-entry-row-card']" : "[data-testid='leap-oi-entry-row']";
      assert.equal(await view.locator(rowSel).count(), real.rows.length);
      assert.ok((await view.locator(entrySel).count()) > 0);
      const allText = await view.innerText();
      assert.ok(!allText.includes("250 Call"), "the 0.08-delta lottery strike must not show");
      assert.match(allText, /ENTRY/);
      assert.match(allText, /coin flip/);
      // The first request carries Mag7 + the watchlist and the LEAP window.
      assert.match(decodeURIComponent(asked[0]), /symbols=AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,PLTR,AMD&from=3&to=12&side=both&min_delta=0\.10/);
      // Chips change the request.
      const chip = view.getByRole("button", { name: "ENTRY only" });
      await (phone ? chip.tap() : chip.click());
      await page.waitForFunction((n) => window.__leapAsked === undefined || true, asked.length);
      for (let i = 0; i < 50 && !asked.some((s) => s.includes("entry_only=1")); i += 1) await page.waitForTimeout(100);
      assert.ok(asked.some((s) => s.includes("entry_only=1")), asked.join("\n"));
      // By ticker (2026-11-17, "what about other tickers from watchlist"): one card per ticker, searchable.
      const lines = view.locator("[data-testid='leap-oi-ticker']");
      assert.equal(await lines.count(), real.byTicker.length);
      // Every ticker shows at once: no "Show all" tap (2026-11-17 "Still I don't see full loading").
      assert.equal(await view.getByRole("button", { name: /Show all/ }).count(), 0);
      assert.match(await view.innerText(), new RegExp(`All ${real.byTicker.length} tickers on the board`));
      assert.match(await lines.first().innerText(), /AAPL[\s\S]*Most call OI[\s\S]*200 Call[\s\S]*ENTRY call/);
      await view.getByRole("searchbox", { name: "Find a ticker" }).fill("nv");
      for (let i = 0; i < 30 && (await lines.count()) !== 1; i += 1) await page.waitForTimeout(100);
      assert.equal(await lines.count(), 1);
      assert.match(await lines.first().innerText(), /NVDA/);
      if (phone) assert.equal(await overflowsSideways(page), false);
      await view.getByRole("searchbox", { name: "Find a ticker" }).fill("");
      const end = await scrollsToTheEnd(page, ".leap-oi-page", "[data-testid='leap-oi-ticker']");
      assert.equal(end.moved, true, "the page did not scroll with the wheel");
      assert.ok(end.lastBottom <= end.areaBottom + 1, `last card cut off: ${JSON.stringify(end)}`);
      assert.deepEqual(app.pageErrors, []);
      // Chart opens the chart page for that ticker.
      const chart = view.getByRole("button", { name: "Chart ›" }).first();
      await (phone ? chart.tap() : chart.click());
      await view.waitFor({ state: "detached", timeout: 15_000 });
      assert.deepEqual(app.pageErrors, []);
    });
  });
}
