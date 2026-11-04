// MomX Scanner -> the momentum strip (NEW / LOST / RVOL chips), end to end.
// The board is the live Mag7 board; the strip's feed is a fixture naming one of
// the board's own tickers, so tapping its chip must leave exactly that row.
// The Clear button's POST is answered by the harness and never reaches the worker.

import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";

const SCANNER = {
  "momx.scanner.activeList": "Mag7",
  "momx.scanner.direction": "bull",
  "momx.scanner.activeView": "live",
  "momx-auto-cards-on": "0",
};
const ROWS = "section[data-testid='momx-scanner-view'] .momx-table-scroll tr.momx-row";
const STRIP = ".momx-momentum-strip[role='list'][aria-label='Momentum events, newest first']";
const CLEAR = "POST /api/momx-scanner/momentum/clear";

async function wholeBoard(page) {
  await page.locator("[data-testid='momx-scanner-panel']").first().waitFor({ timeout: 90_000 });
  const matchesOnly = page.locator("label.momx-toggle", { hasText: "Scan matches only" }).locator("input");
  if (await matchesOnly.isChecked()) await matchesOnly.uncheck();
  await page.locator(ROWS).nth(1).waitFor({ timeout: 120_000 });
}

const symbolsOnScreen = async (page) =>
  (await page.locator(`${ROWS} .momx-symbol`).allInnerTexts()).map((text) => text.trim()).filter(Boolean);

function strip(feed) {
  return {
    "GET /api/momx-scanner/momentum": () => ({
      json: {
        list: "Mag7",
        direction: "bull",
        generatedAt: new Date().toISOString(),
        events: feed.cleared || !feed.symbol ? [] : [
          { type: "new_match", symbol: feed.symbol, pctChange: 4.2, scanReasons: ["rvol:5m"], at: new Date().toISOString() },
          { type: "rvol_spike", symbol: feed.symbol, timeframe: "5m", value: 3.4, pctChange: 4.2, at: new Date(Date.now() - 60_000).toISOString() },
        ],
      },
    }),
  };
}

async function chipsArrive(page, feed) {
  assert.ok((await symbolsOnScreen(page)).includes("NVDA"), "the Mag7 board carries NVDA");
  feed.symbol = "NVDA";
  // The strip polls every 15 s; the next poll carries the fixture events.
  await page.locator(`${STRIP} .momx-mo-chip`).first().waitFor({ timeout: 25_000 });
  return feed.symbol;
}

e2eTest("tapping a chip shows only that ticker with a clear link; clear restores the board", async () => {
  const feed = {};
  await withApp({ view: "MomX Scanner", storage: SCANNER, fakeReads: strip(feed), fakeWrites: { [CLEAR]: { json: { events: [], cleared: true } } } }, async (app) => {
    const { page } = app;
    await wholeBoard(page);
    const everyone = await symbolsOnScreen(page);
    const symbol = await chipsArrive(page, feed);
    const chips = page.locator(`${STRIP} .momx-mo-chip`);
    assert.equal(await chips.count(), 2);
    const newChip = page.locator(`${STRIP} .momx-mo-chip.is-new-match`);
    assert.match(await newChip.innerText(), new RegExp(`NEW\\s*${symbol}\\s*\\+4\\.20%`));
    assert.match(await page.locator(`${STRIP} .momx-mo-chip.is-rvol-spike`).innerText(), new RegExp(`RVOL 5m\\s*3\\.4\\s*${symbol}`));

    await newChip.click();
    await page.locator(`${STRIP} .momx-mo-chip.is-focus`).first().waitFor({ timeout: 5_000 });
    const note = page.locator(".momx-banner.is-notice.momx-focus-note");
    await note.waitFor({ state: "visible", timeout: 5_000 });
    assert.match(await note.innerText(), new RegExp(`Showing only ${symbol}`));
    assert.deepEqual(await symbolsOnScreen(page), [symbol]);

    await note.locator("button.momx-chip-clear").click();
    await note.waitFor({ state: "detached", timeout: 5_000 });
    assert.deepEqual((await symbolsOnScreen(page)).sort(), [...everyone].sort());
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Clear empties the strip once, asks the worker for this list, and old chips do not come back", async () => {
  const feed = {};
  await withApp({
    view: "MomX Scanner",
    storage: SCANNER,
    fakeReads: strip(feed),
    fakeWrites: { [CLEAR]: () => { feed.cleared = true; return { json: { events: [], cleared: true } }; } },
  }, async (app) => {
    const { page } = app;
    await wholeBoard(page);
    await chipsArrive(page, feed);
    await page.locator(`${STRIP} button.momx-mo-clear`).click();
    await page.locator(STRIP).waitFor({ state: "detached", timeout: 5_000 });
    const clears = app.writes.filter((write) => write.path === "/api/momx-scanner/momentum/clear");
    assert.equal(clears.length, 1);
    assert.deepEqual(JSON.parse(clears[0].body), { list: "Mag7" });
    await page.waitForTimeout(16_000);                        // one more strip poll
    assert.equal(await page.locator(STRIP).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the strip's chips fit and focus the board", async () => {
  const feed = {};
  await withApp({ view: "MomX Scanner", viewport: PHONE, storage: SCANNER, fakeReads: strip(feed) }, async (app) => {
    const { page } = app;
    await wholeBoard(page);
    const symbol = await chipsArrive(page, feed);
    const chip = page.locator(`${STRIP} .momx-mo-chip.is-new-match`);
    const box = await chip.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `chip at ${JSON.stringify(box)} is cut off`);
    await chip.tap();
    await page.locator(".momx-focus-note").waitFor({ timeout: 5_000 });
    assert.deepEqual(await symbolsOnScreen(page), [symbol]);
    assert.deepEqual(app.pageErrors, []);
  });
});
