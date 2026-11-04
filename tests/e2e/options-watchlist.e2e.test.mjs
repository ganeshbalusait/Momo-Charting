// My Watchlist page (2026-11-04 coverage), end to end in the real app.
//
// The list is a fixture: GET /api/dashboard answers from a small in-test list
// and every add / rename / delete is answered by the harness from the SAME
// list, so the 5 s dashboard poll and the write always agree and nothing
// reaches the trader's real watchlist.txt.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { sleep } from "./options-fixtures.mjs";

const PANEL = "section.watchlist-panel:not(.mag7-watchlist-panel)";
const ADD = `${PANEL} input[placeholder="Add ticker, e.g. AAPL"]`;
const SEARCH = `${PANEL} input[placeholder="Search ticker, e.g. AAPL"]`;
const CARDS = `${PANEL} .watchlist-row-card`;

function fakeWatchlist(initial = ["AAPL", "NVDA", "SPY", "META"], { delayDeleteMs = 0, addError = null } = {}) {
  const list = [...initial];
  const reply = (status = 200) => ({ status, json: { watchlist: [...list] } });
  return {
    list,
    options: {
      view: "Watchlist",
      fakeReads: { "GET /api/dashboard": () => ({ json: { watchlist: [...list] } }) },
      fakeWrites: {
        "POST /api/watchlist": (write) => {
          if (addError) return { status: 400, json: { error: addError } };
          const ticker = JSON.parse(write.body || "{}").ticker;
          if (ticker && !list.includes(ticker)) list.push(ticker);
          return reply(201);
        },
        "PUT /api/watchlist/NVDA": (write) => {
          list[list.indexOf("NVDA")] = JSON.parse(write.body || "{}").ticker;
          return reply();
        },
        "DELETE /api/watchlist/META": async () => {
          if (delayDeleteMs) await sleep(delayDeleteMs);
          list.splice(list.indexOf("META"), 1);
          return reply();
        },
      },
    },
  };
}

const cardSymbols = (page) => page.locator(`${CARDS} strong`).allInnerTexts();

async function ready(page, count) {
  await page.locator(`${PANEL} .watchlist-panel-head h2`).waitFor({ state: "visible", timeout: 60_000 });
  await page.waitForFunction(([selector, wanted]) => document.querySelectorAll(selector).length === wanted, [CARDS, count], { timeout: 30_000 });
}

e2eTest("My Watchlist lists the saved tickers with their count", async () => {
  const { options } = fakeWatchlist();
  await withApp(options, async (app) => {
    await ready(app.page, 4);
    assert.equal(await app.page.locator(`${PANEL} .watchlist-panel-head h2`).innerText(), "My Watchlist");
    assert.equal((await app.page.locator(`${PANEL} .watchlist-panel-head p`).innerText()).trim(), "4 symbols scanned by Alpaca");
    assert.deepEqual(await cardSymbols(app.page), ["AAPL", "NVDA", "SPY", "META"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("+ Add ticker sends one POST, clears the box and shows the new card", async () => {
  const fake = fakeWatchlist();
  await withApp(fake.options, async (app) => {
    await ready(app.page, 4);
    const addButton = app.page.locator(`${PANEL} .watchlist-add-button`, { hasText: "+ Add ticker" });
    assert.equal(await addButton.isDisabled(), true, "nothing to add yet");
    await app.page.locator(ADD).fill("pltr");
    assert.equal(await app.page.locator(ADD).inputValue(), "PLTR", "typed tickers are upper-cased");
    await addButton.click();
    await ready(app.page, 5);
    const posts = app.writes.filter((write) => write.method === "POST" && write.path === "/api/watchlist");
    assert.equal(posts.length, 1);
    assert.deepEqual(JSON.parse(posts[0].body), { ticker: "PLTR" });
    assert.equal(await app.page.locator(ADD).inputValue(), "");
    assert.ok((await cardSymbols(app.page)).includes("PLTR"));
    await sleep(6_000);                                            // the next dashboard poll keeps it
    assert.ok((await cardSymbols(app.page)).includes("PLTR"));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("search filters on Search/Enter, says plainly when nothing matches, and Clear restores", async () => {
  const { options } = fakeWatchlist();
  await withApp(options, async (app) => {
    await ready(app.page, 4);
    const helper = app.page.locator(`${PANEL} p.watchlist-helper`).first();
    await app.page.locator(SEARCH).fill("aa");
    assert.equal(await app.page.locator(CARDS).count(), 4, "typing alone does not filter (commit on press)");
    await app.page.locator(SEARCH).press("Enter");
    await ready(app.page, 1);
    assert.equal(await helper.innerText(), "Showing 1 of 4 tickers matching AA.");
    await app.page.locator(SEARCH).fill("zzzzq");
    await app.page.locator(`${PANEL} .watchlist-add-button`, { hasText: "Search" }).click();
    await app.page.locator(`${PANEL} .watchlist-grid-cards p.watchlist-helper`, { hasText: "No ticker in your watchlist matches ZZZZQ." }).waitFor({ timeout: 10_000 });
    await app.page.locator(`${PANEL} .watchlist-edit-btn`, { hasText: "Clear" }).click();
    await ready(app.page, 4);
    assert.equal(await helper.innerText(), "Every change is saved locally and used on the next scan.");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Edit -> Save renames in place with one PUT; Cancel sends nothing", async () => {
  const { options } = fakeWatchlist();
  await withApp(options, async (app) => {
    await ready(app.page, 4);
    const nvda = app.page.locator(CARDS, { hasText: "NVDA" });
    await nvda.locator(".watchlist-edit-btn", { hasText: "Edit" }).click();
    await app.page.locator(`${PANEL} .watchlist-inline-input`).fill("amd");
    await app.page.locator(`${PANEL} .watchlist-cancel-btn`).click();
    assert.equal(app.writes.length, 0, "Cancel must not save");
    await nvda.locator(".watchlist-edit-btn", { hasText: "Edit" }).click();
    await app.page.locator(`${PANEL} .watchlist-inline-input`).fill("nvdl");
    await app.page.locator(CARDS).locator(".watchlist-edit-btn", { hasText: "Save" }).click();
    await app.page.waitForFunction((selector) => [...document.querySelectorAll(`${selector} strong`)].some((node) => node.textContent === "NVDL"), CARDS);
    const puts = app.writes.filter((write) => write.method === "PUT");
    assert.deepEqual(puts.map((write) => [write.path, JSON.parse(write.body)]), [["/api/watchlist/NVDA", { ticker: "NVDL" }]]);
    assert.deepEqual(await cardSymbols(app.page), ["AAPL", "NVDL", "SPY", "META"], "the renamed ticker keeps its place");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Delete sends one DELETE, shows ... while it is pending, then the card is gone", async () => {
  const { options } = fakeWatchlist(undefined, { delayDeleteMs: 1_500 });
  await withApp(options, async (app) => {
    await ready(app.page, 4);
    const button = app.page.locator(CARDS, { hasText: "META" }).locator(".watchlist-delete-btn");
    await button.click();
    await app.page.locator(CARDS, { hasText: "META" }).locator(".watchlist-delete-btn", { hasText: "..." }).waitFor({ timeout: 1_400 });
    await ready(app.page, 3);
    assert.deepEqual(app.writes.map((write) => `${write.method} ${write.path}`), ["DELETE /api/watchlist/META"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a refused add shows the server's reason", async () => {
  const { options } = fakeWatchlist(undefined, { addError: "ticker is required." });
  await withApp(options, async (app) => {
    await ready(app.page, 4);
    await app.page.locator(ADD).fill("X");
    await app.page.locator(`${PANEL} .watchlist-add-button`, { hasText: "+ Add ticker" }).click();
    await app.page.locator(".live-error", { hasText: "ticker is required." }).waitFor({ timeout: 10_000 });
    assert.equal(await app.page.locator(CARDS).count(), 4);
  });
});

e2eTest("x closes the page back to Charts & OI", async () => {
  const { options } = fakeWatchlist();
  await withApp(options, async (app) => {
    await ready(app.page, 4);
    await app.page.locator(`${PANEL} .watchlist-close`).click();
    await app.page.locator('[data-testid="charts-and-oi-view"]').waitFor({ timeout: 60_000 });
    assert.equal(await app.page.locator(".command-context h1").innerText(), "Charts & OI");
  });
});

e2eTest("on a phone the cards fit and the page never scrolls sideways", async () => {
  const { options } = fakeWatchlist();
  await withApp({ ...options, viewport: PHONE }, async (app) => {
    await ready(app.page, 4);
    for (const card of await app.page.locator(CARDS).all()) {
      const box = await card.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `card at ${JSON.stringify(box)}`);
    }
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
