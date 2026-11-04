// Mag7 Watchlist page ("Mag7 Scanner" view key, 2026-11-04 coverage), end to
// end in the real app. The list and its leveraged mappings are a fixture:
// GET /api/dashboard and every add / rename / delete answer from the same
// in-test list, so nothing reaches the trader's saved Mag7 list.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { sleep } from "./options-fixtures.mjs";

const PANEL = "section.watchlist-panel.mag7-watchlist-panel";
const ROWS = `${PANEL} .mag7-watchlist-row`;
const MAPPINGS = { METU: "META", AMDL: "AMD" };

function fakeMag7(initial = ["METU", "AMDL", "NVDA"], { delayDeleteMs = 0, addError = null } = {}) {
  const list = [...initial];
  const payload = () => ({
    mag7OptionWatchlistSource: [...list],
    mag7OptionWatchlist: [...new Set(list.map((symbol) => MAPPINGS[symbol] || symbol))],
    optionBot: { symbolMappings: MAPPINGS, aliasMap: { META: ["METU"], AMD: ["AMDL"] } },
  });
  return {
    view: "Mag7 Scanner",
    fakeReads: { "GET /api/dashboard": () => ({ json: payload() }) },
    fakeWrites: {
      "POST /api/mag7-scanner-watchlist": (write) => {
        if (addError) return { status: 400, json: { error: addError } };
        list.push(JSON.parse(write.body || "{}").ticker);
        return { status: 201, json: payload() };
      },
      "PUT /api/mag7-scanner-watchlist/METU": (write) => {
        list[list.indexOf("METU")] = JSON.parse(write.body || "{}").ticker;
        return { json: payload() };
      },
      "DELETE /api/mag7-scanner-watchlist/NVDA": async () => {
        if (delayDeleteMs) await sleep(delayDeleteMs);
        list.splice(list.indexOf("NVDA"), 1);
        return { json: payload() };
      },
    },
  };
}

async function ready(page, count) {
  await page.locator(`${PANEL} h2`).waitFor({ state: "visible", timeout: 60_000 });
  await page.waitForFunction(([selector, wanted]) => document.querySelectorAll(selector).length === wanted, [ROWS, count], { timeout: 30_000 });
}

const labels = (page) => page.locator(`${ROWS} small`).allInnerTexts();

e2eTest("the Mag7 Watchlist shows each ticker with the underlying it scans", async () => {
  await withApp(fakeMag7(), async (app) => {
    await ready(app.page, 3);
    assert.equal(await app.page.locator(`${PANEL} h2`).innerText(), "Mag7 Watchlist");
    assert.equal(
      (await app.page.locator(`${PANEL} .watchlist-panel-head p`).innerText()).trim(),
      "3 signal tickers, 3 mapped underlyings scanned by the MAG7 scanner",
    );
    assert.deepEqual(await labels(app.page), ["METU -> META", "AMDL -> AMD", "Scan NVDA"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Add sends one POST and the new row appears", async () => {
  await withApp(fakeMag7(), async (app) => {
    await ready(app.page, 3);
    await app.page.locator(`${PANEL} input[placeholder="Add ticker, e.g. USO"]`).fill("uso");
    await app.page.locator(`${PANEL} .watchlist-add-button`, { hasText: "+ Add ticker" }).click();
    await ready(app.page, 4);
    const posts = app.writes.filter((write) => write.path === "/api/mag7-scanner-watchlist");
    assert.deepEqual(posts.map((write) => JSON.parse(write.body)), [{ ticker: "USO" }]);
    assert.equal((await labels(app.page)).at(-1), "Scan USO");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Edit -> Save sends one PUT for that ticker; Cancel sends nothing", async () => {
  await withApp(fakeMag7(), async (app) => {
    await ready(app.page, 3);
    const metu = app.page.locator(ROWS, { hasText: "METU" });
    await metu.locator(".watchlist-edit-btn", { hasText: "Edit" }).click();
    await app.page.locator(`${PANEL} .watchlist-inline-input`).fill("nvdl");
    await app.page.locator(`${PANEL} .watchlist-cancel-btn`).click();
    assert.equal(app.writes.length, 0);
    await metu.locator(".watchlist-edit-btn", { hasText: "Edit" }).click();
    await app.page.locator(`${PANEL} .watchlist-inline-input`).fill("nvdl");
    await app.page.locator(ROWS).locator(".watchlist-edit-btn", { hasText: "Save" }).click();
    await app.page.locator(ROWS, { hasText: "NVDL" }).waitFor({ timeout: 10_000 });
    assert.deepEqual(app.writes.map((write) => [write.method, write.path, JSON.parse(write.body)]),
      [["PUT", "/api/mag7-scanner-watchlist/METU", { ticker: "NVDL" }]]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Delete sends one DELETE and shows ... while pending", async () => {
  await withApp(fakeMag7(undefined, { delayDeleteMs: 1_500 }), async (app) => {
    await ready(app.page, 3);
    await app.page.locator(ROWS, { hasText: "NVDA" }).locator(".watchlist-delete-btn").click();
    await app.page.locator(ROWS, { hasText: "NVDA" }).locator(".watchlist-delete-btn", { hasText: "..." }).waitFor({ timeout: 1_400 });
    await ready(app.page, 2);
    assert.deepEqual(app.writes.map((write) => `${write.method} ${write.path}`), ["DELETE /api/mag7-scanner-watchlist/NVDA"]);
  });
});

e2eTest("search matches the mapped underlying, and a miss says No ticker found", async () => {
  await withApp(fakeMag7(), async (app) => {
    await ready(app.page, 3);
    const search = app.page.locator(`${PANEL} input[placeholder="Search ticker, e.g. META or METU"]`);
    await search.fill("meta");
    await search.press("Enter");
    await ready(app.page, 1);
    assert.deepEqual(await labels(app.page), ["METU -> META"], "META finds METU through its mapping");
    await search.fill("zzzzq");
    await search.press("Enter");
    await app.page.locator(`${PANEL} .watchlist-row-card strong`, { hasText: "No ticker found" }).waitFor({ timeout: 10_000 });
    await app.page.locator(`${PANEL} .watchlist-edit-btn`, { hasText: "Clear" }).click();
    await ready(app.page, 3);
  });
});

e2eTest("a refused add shows the server's reason", async () => {
  await withApp(fakeMag7(undefined, { addError: "ticker is required." }), async (app) => {
    await ready(app.page, 3);
    await app.page.locator(`${PANEL} input[placeholder="Add ticker, e.g. USO"]`).fill("X");
    await app.page.locator(`${PANEL} .watchlist-add-button`, { hasText: "+ Add ticker" }).click();
    await app.page.locator(".live-error", { hasText: "ticker is required." }).waitFor({ timeout: 10_000 });
  });
});

e2eTest("on a phone the Mag7 rows fit with no sideways scroll", async () => {
  await withApp({ ...fakeMag7(), viewport: PHONE }, async (app) => {
    await ready(app.page, 3);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
