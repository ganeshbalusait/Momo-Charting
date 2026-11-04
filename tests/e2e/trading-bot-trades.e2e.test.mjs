// Bot page -> Trades tab, end to end (2026-11-04 coverage pass): ticker
// search, outcome buttons, the ROI sort cycle, Compare contracts, AI at entry,
// the 2xEM chip and "Show 100 more". Canned Bot answers
// (trading-bot.fixtures.mjs) so every count is known; read-only page.

import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";
import { AI_READ, AI_REPORT, BOT_READ, LEARNING_READ, TEAM, botAnswer } from "./trading-bot.fixtures.mjs";

const reads = {
  [BOT_READ]: (read) => ({ json: botAnswer(read.search) }),
  [LEARNING_READ]: { json: TEAM },
  [AI_READ]: { json: AI_REPORT },
};
const CARD = ".bot-card";
const rows = (page) => page.locator(`${CARD} tbody tr`);
const shownHint = (page) => page.locator(`${CARD} .bot-card-head > span.bot-hint`);

async function openTrades(app) {
  await app.page.locator("[data-testid='bot-view']").waitFor({ timeout: 60_000 });
  await app.page.locator(`${CARD} .bot-card-head h3`, { hasText: /^Trades/ }).waitFor({ timeout: 30_000 });
  await rows(app.page).first().waitFor({ timeout: 30_000 });
}

function cellTexts(page, header) {
  return page.evaluate(([selector, title]) => {
    const table = document.querySelector(selector);
    const heads = [...table.querySelectorAll("thead th")].map((th) => th.textContent.replace(/[▲▼]/g, "").trim());
    const index = heads.findIndex((h) => h === title || h.startsWith(title));
    return [...table.querySelectorAll("tbody tr")].map((tr) => tr.children[index]?.textContent.trim());
  }, [`${CARD} table.bot-table`, header]);
}

e2eTest("typing a ticker leaves only its trades and the count follows", async () => {
  await withApp({ view: "Bot", fakeReads: reads, storage: { "bot.tab": "trades" } }, async (app) => {
    await openTrades(app);
    assert.match(await shownHint(app.page).innerText(), /^4 shown/);
    await app.page.locator("input.bot-search[aria-label='Filter by ticker']").fill("nvda");
    await app.page.waitForFunction(() => document.querySelectorAll(".bot-card tbody tr").length === 1);
    assert.deepEqual((await cellTexts(app.page, "Ticker")).map((t) => t.replace(/[✓✗] 2×EM$/, "")), ["NVDA"]);
    assert.match(await shownHint(app.page).innerText(), /^1 shown/);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Closed wins shows only winning closed trades and marks itself pressed", async () => {
  await withApp({ view: "Bot", fakeReads: reads, storage: { "bot.tab": "trades" } }, async (app) => {
    await openTrades(app);
    const outcome = app.page.locator("[role='group'][aria-label='Trade outcome']");
    await outcome.locator("button", { hasText: /^Closed wins$/ }).click();
    assert.equal(await outcome.locator("button", { hasText: /^Closed wins$/ }).getAttribute("aria-pressed"), "true");
    assert.equal(await outcome.locator("button", { hasText: /^All outcomes$/ }).getAttribute("aria-pressed"), "false");
    await app.page.waitForFunction(() => document.querySelectorAll(".bot-card tbody tr").length === 2);
    assert.deepEqual(await app.page.locator(`${CARD} span.bot-status`).allInnerTexts(), ["win", "win"]);
    await outcome.locator("button", { hasText: /^Open P\/L$/ }).click();
    await app.page.waitForFunction(() => document.querySelectorAll(".bot-card tbody tr").length === 1);
    assert.deepEqual(await app.page.locator(`${CARD} span.bot-status`).allInnerTexts(), ["open"]);
  });
});

e2eTest("the ROI header cycles high-to-low, low-to-high, then off", async () => {
  await withApp({ view: "Bot", fakeReads: reads, storage: { "bot.tab": "trades" } }, async (app) => {
    await openTrades(app);
    const roi = app.page.locator(`${CARD} th.bot-sortable`, { hasText: /^ROI \(strategy exit\)/ });
    assert.equal(await roi.getAttribute("aria-sort"), "none");
    await roi.click();
    assert.equal(await roi.getAttribute("aria-sort"), "descending");
    const high = await cellTexts(app.page, "ROI (strategy exit)");
    await roi.click();
    assert.equal(await roi.getAttribute("aria-sort"), "ascending");
    const low = await cellTexts(app.page, "ROI (strategy exit)");
    assert.deepEqual(low, [...high].reverse());
    await roi.click();
    assert.equal(await roi.getAttribute("aria-sort"), "none");
    assert.equal(await roi.locator(".bot-sort-arrow").innerText(), "");
  });
});

e2eTest("Compare contracts adds the ENTRY / 0DTE / Hold max columns", async () => {
  await withApp({ view: "Bot", fakeReads: reads, storage: { "bot.tab": "trades" } }, async (app) => {
    await openTrades(app);
    const heads = async () => (await app.page.locator(`${CARD} thead th`).allInnerTexts()).map((h) => h.replace(/[▲▼]/g, "").trim());
    const before = await heads();
    for (const added of ["ENTRY", "ENTRY %", "0DTE", "0DTE %", "Hold max"]) assert.ok(!before.includes(added), added);
    await app.page.locator(`${CARD} label.bot-hint input[type='checkbox']`).check();
    const after = await heads();
    for (const added of ["ENTRY", "ENTRY %", "0DTE", "0DTE %", "Hold max"]) assert.ok(after.includes(added), `${added} missing: ${after.join(" | ")}`);
    assert.equal(after.length, before.length + 5);
  });
});

e2eTest("AI at entry shows only a frozen pre-entry probability, and the 2xEM chip reads the saved verdict", async () => {
  await withApp({ view: "Bot", fakeReads: reads, storage: { "bot.tab": "trades" } }, async (app) => {
    await openTrades(app);
    const tickers = await cellTexts(app.page, "Ticker");
    const ai = await cellTexts(app.page, "AI at entry");
    const byTicker = tickers.map((ticker, i) => [ticker, ai[i]]);
    assert.ok(byTicker.some(([ticker, text]) => ticker.startsWith("NVDA") && text === "61% · main / strategy"), JSON.stringify(byTicker));
    assert.equal(ai.filter((text) => text === "Not recorded").length, 3);
    const aaplChips = tickers.filter((ticker) => ticker.startsWith("AAPL")).map((ticker) => ticker.replace(/^AAPL/, ""));
    assert.deepEqual(aaplChips.sort(), ["✓ 2×EM", "✗ 2×EM"]);
    for (const text of ai) assert.ok(!/NaN|undefined|\[object/.test(text), text);
  });
});

e2eTest("on 1M the first 100 trades show and 'Show 100 more' reveals the rest", async () => {
  await withApp({ view: "Bot", fakeReads: reads, storage: { "bot.tab": "trades" } }, async (app) => {
    await openTrades(app);
    await app.page.locator("[role='group'][aria-label='Range'] button", { hasText: /^1M$/ }).click();
    const more = app.page.locator("button.bot-refresh", { hasText: /^Show 100 more/ });
    await more.waitFor({ timeout: 30_000 });
    assert.equal((await more.innerText()).trim(), "Show 100 more · 100 of 105 displayed");
    assert.equal(await rows(app.page).count(), 100);
    await more.click();
    await app.page.waitForFunction(() => document.querySelectorAll(".bot-card tbody tr").length === 105);
    assert.equal(await more.count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});
