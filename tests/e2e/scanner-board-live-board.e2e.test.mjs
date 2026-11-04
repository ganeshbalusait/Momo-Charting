// MomX Scanner live board, end to end in the real app (2026-11-04 coverage):
// the stored list paints, every scanner read through the proxy answers 200
// JSON, SCAN queues ONE rebuild and comes back, "Scan matches only" and the
// header sort do what the trader expects, and the phone layout scrolls inside
// the table rather than sideways.
//
// The first test reads the LIVE board (structure only). The rest answer the
// board with the deterministic fixture in scanner-board-fixtures.mjs.

import assert from "node:assert/strict";
import test from "node:test";

import { PHONE, e2eTest, marketHoursBlock, overflowsSideways, withApp } from "./harness.mjs";
import { BOARD_STORAGE, boardFor, boardReads, boardReady, rowSymbols, writesTo } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const REBUILD = "/api/momx-scanner/rebuild";

e2eTest("the stored list paints from the live worker and every scanner read answers 200 JSON", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE }, async (app) => {
    const answers = [];
    app.page.on("response", (response) => {
      const url = new URL(response.url());
      if (url.pathname.startsWith("/api/momx-scanner")) {
        answers.push({ path: url.pathname, status: response.status(), type: response.headers()["content-type"] || "" });
      }
    });
    await boardReady(app.page);
    assert.equal((await app.page.locator(".momx-listbtn-name").innerText()).trim(), "Mag7");
    // The board request named the stored list.
    assert.ok(app.reads.some((read) => read.path === "/api/momx-scanner" && /list=Mag7/.test(read.search)), "board read for Mag7");
    // Structure, not values: the three stats and a header row with the bands.
    assert.deepEqual(await app.page.locator(".momx-toolbar-stats .momx-stat-label").allInnerTexts(), ["Matches", "Tickers", "Updated"]);
    for (const band of ["RVOL", "SQZ", "SKIT"]) {
      assert.ok((await app.page.locator("tr.momx-group-row th").allInnerTexts()).includes(band), band + " band");
    }
    await app.page.waitForTimeout(20_000);          // one full 15 s poll cycle
    const bad = answers.filter((answer) => answer.status !== 200 || !answer.type.includes("json"));
    assert.ok(answers.length >= 2, "the board was polled");
    assert.deepEqual(bad, [], "every /api/momx-scanner* answer is 200 JSON (the proxy never shows a 5xx)");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("SCAN shows SCANNING, sends exactly one rebuild for the list on screen, then comes back", async () => {
  const fakeWrites = { [`POST ${REBUILD}`]: { json: { ok: true, list: "Mag7", queued: true, generatedAt: "2026-11-04T09:00:00+00:00" } } };
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads(), fakeWrites }, async (app) => {
    await boardReady(app.page);
    const scan = app.page.locator(".momx-toolbar > button.momx-btn.is-primary");
    await app.page.locator(".momx-toolbar > button.momx-btn.is-primary:enabled").waitFor({ timeout: 30_000 });
    assert.equal((await scan.innerText()).trim(), "SCAN");
    await scan.click();
    await app.page.waitForFunction(() => {
      const button = document.querySelector(".momx-toolbar > button.momx-btn.is-primary");
      return button && button.textContent.trim() === "SCANNING" && button.disabled;
    }, null, { timeout: 5_000 });
    // The fixture's generatedAt differs from the rebuild's baseline, so the
    // first 3 s re-read counts as the new scan landing.
    await app.page.waitForFunction(() => {
      const button = document.querySelector(".momx-toolbar > button.momx-btn.is-primary");
      return button && button.textContent.trim() === "SCAN";
    }, null, { timeout: 20_000 });
    const sent = writesTo(app, REBUILD);
    assert.equal(sent.length, 1);
    assert.deepEqual(JSON.parse(sent[0].body), { list: "Mag7" });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("unticking Scan matches only shows the whole list; ticking it back shows the matches", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    const toggle = app.page.locator("label.momx-toggle", { hasText: "Scan matches only" }).locator("input");
    assert.equal(await toggle.isChecked(), true, "ticked by default");
    assert.deepEqual((await rowSymbols(app.page)).sort(), ["AAPL", "NVDA"]);
    assert.equal(await app.page.locator("tr.momx-row.is-muted").count(), 0);
    const stats = await app.page.locator(".momx-toolbar-stats .momx-stat .momx-stat-value").allInnerTexts();
    assert.equal(stats[0].trim(), "2", "Matches");
    assert.equal(stats[1].trim(), "3", "Tickers");
    await toggle.uncheck();
    await app.page.locator("tr.momx-row.is-muted").first().waitFor({ timeout: 5_000 });
    assert.deepEqual((await rowSymbols(app.page)).sort(), ["AAPL", "NVDA", "SPY"]);
    await toggle.check();
    await app.page.locator("tr.momx-row.is-muted").first().waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual((await rowSymbols(app.page)).sort(), ["AAPL", "NVDA"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("clicking % Chg sorts high-to-low, again low-to-high, and the arrow follows", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    await app.page.locator("label.momx-toggle", { hasText: "Scan matches only" }).locator("input").uncheck();
    await app.page.locator("tr.momx-row.is-muted").first().waitFor({ timeout: 5_000 });
    const header = app.page.locator("table.momx-table:not(.momx-history-table) th.momx-th[data-col='pctChange']");
    assert.equal(await header.getAttribute("aria-sort"), "none");
    await header.click();
    await app.page.waitForFunction(() => document.querySelector("th.momx-th[data-col='pctChange']")?.getAttribute("aria-sort") === "descending");
    assert.deepEqual(await rowSymbols(app.page), ["NVDA", "AAPL", "SPY"]);
    assert.equal((await header.locator(".momx-sort-arrow").innerText()).trim(), "▼");
    await header.click();
    await app.page.waitForFunction(() => document.querySelector("th.momx-th[data-col='pctChange']")?.getAttribute("aria-sort") === "ascending");
    assert.deepEqual(await rowSymbols(app.page), ["SPY", "AAPL", "NVDA"]);
    assert.equal((await header.locator(".momx-sort-arrow").innerText()).trim(), "▲");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a worker that is still warming shows the building message, not an error", async () => {
  const reads = {
    ...boardReads(),
    "GET /api/momx-scanner": { json: { generatedAt: null, list: "Mag7", direction: "bull", universe: ["SPY"], universeCount: 1, rows: [], errors: {}, warming: true, message: "MomX worker unavailable: fixture" } },
  };
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: reads }, async (app) => {
    const empty = app.page.locator("td.momx-empty .momx-empty-inner");
    await empty.waitFor({ timeout: 60_000 });
    assert.match(await empty.innerText(), /^Building this watchlist from live bars/);
    assert.equal(await app.page.locator(".momx-banner.is-error").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

// KNOWN BUG (found 2026-11-04 by the coverage pass): the lightning badge's
// aria-label and click handler in MomxRow read `row.symbol || entry.symbol`,
// and `entry` does not exist in MomxRow (MomxScannerPanel.jsx ~2838/2841). A
// row that carries signals but an empty symbol throws "entry is not defined"
// while rendering and takes the board down. Kept as a known failure until the
// panel is fixed; it should pass (and lose `todo`) once it reads row.symbol only.
test("a row with signals but no ticker name does not crash the board", {
  todo: "MomxRow reads undeclared `entry` for a symbol-less row with fires (MomxScannerPanel.jsx ~2838)",
  skip: marketHoursBlock() || false,
  timeout: 180_000,
}, async () => {
  const broken = boardFor("Mag7");
  broken.rows.push({ ...broken.rows[1], symbol: "", scanPass: true, scanReasons: ["rvol:1h"] });
  const reads = { ...boardReads(), "GET /api/momx-scanner": { json: broken } };
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: reads }, async (app) => {
    await app.page.locator("[data-testid='momx-scanner-panel']").first().waitFor({ timeout: 60_000 });
    await app.page.waitForTimeout(3_000);
    assert.deepEqual(app.pageErrors, []);
    assert.ok(await app.page.locator("tr.momx-row").count() >= 2, "the named rows still show");
  });
});

// The scanner's ONE scroll container is .momx-scanner-view (both axes - see
// the four-incident note above .momx-table-scroll in index.css).
e2eTest("on a phone the wide board pans inside the scanner view, never the page sideways", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: BOARD_STORAGE, fakeReads: boardReads() }, async (app) => {
    await boardReady(app.page);
    assert.equal(await overflowsSideways(app.page), false, "the page itself does not scroll sideways");
    const view = await app.page.locator("[data-testid='momx-scanner-view']").evaluate((node) => ({
      pans: node.scrollWidth > node.clientWidth, width: node.getBoundingClientRect().width,
    }));
    assert.ok(view.pans, "the 30-column board is wider than a phone and pans inside the view");
    assert.ok(view.width <= PHONE.width + 1, `the scanner view is ${view.width}px wide`);
    const box = await app.page.locator(".momx-listbtn").boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, "the list button fits on screen");
    assert.equal((await app.page.locator(".momx-stat-value").first().innerText()).trim(), "2");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone, TOP appears after scrolling a long board and brings it back to the top", async () => {
  // The live Watchlist (hundreds of names) with every row shown is tall
  // enough to scroll on any day; only structure is asserted.
  await withApp({ view: VIEW, viewport: PHONE, storage: { ...BOARD_STORAGE, "momx.scanner.activeList": "Watchlist" } }, async (app) => {
    await boardReady(app.page);
    await app.page.locator("label.momx-toggle", { hasText: "Scan matches only" }).locator("input").uncheck();
    await app.page.waitForFunction(() => document.querySelectorAll("tr.momx-row").length > 40, null, { timeout: 60_000 });
    const view = app.page.locator("[data-testid='momx-scanner-view']");
    assert.equal(await app.page.locator("[data-testid='momx-to-top']").count(), 0, "no TOP while at the top");
    await view.evaluate((node) => { node.scrollTop = 900; node.dispatchEvent(new Event("scroll")); });
    const top = app.page.locator("[data-testid='momx-to-top']");
    await top.waitFor({ timeout: 5_000 });
    const box = await top.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width && box.y + box.height <= PHONE.height, "TOP is on screen");
    await top.click();
    assert.equal(await view.evaluate((node) => node.scrollTop), 0);
    await top.waitFor({ state: "detached", timeout: 5_000 });
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
