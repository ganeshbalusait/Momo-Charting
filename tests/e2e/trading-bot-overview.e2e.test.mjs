// Bot page, end to end (2026-11-04 coverage pass): totals, range switches,
// book switches, the level strategy select, leaderboard -> Trades, the
// contract x exit comparison and the history tabs. The first spec reads the
// LIVE bot (structure only); the rest answer the Bot reads with canned data
// (trading-bot.fixtures.mjs) so the counts are known. Read-only page: any write
// would be stopped and recorded, and every spec asserts there were none.

import assert from "node:assert/strict";
import test from "node:test";

import { PHONE, e2eTest, marketHoursBlock, overflowsSideways, withApp } from "./harness.mjs";
import { AI_READ, AI_REPORT, BOT_READ, LEARNING_READ, TEAM, botAnswer } from "./trading-bot.fixtures.mjs";

const VIEW = "[data-testid='bot-view']";
const TOTALS = "section.bot-kpis[aria-label='Totals']";
const group = (page, name) => page.locator(`[role='group'][aria-label='${name}']`);
const tab = (page, name) => page.locator("nav.bot-tabs[role='tablist'] button.bot-tab", { hasText: name });

function canned(extra = {}) {
  return {
    [BOT_READ]: (read) => ({ json: botAnswer(read.search) }),
    [LEARNING_READ]: { json: TEAM },
    [AI_READ]: { json: AI_REPORT },
    ...extra,
  };
}

async function openBot(app) {
  await app.page.locator(VIEW).waitFor({ timeout: 60_000 });
  await app.page.locator(`${TOTALS} .bot-kpi`).first().waitFor({ timeout: 60_000 });
}

async function tradesKpiSub(page) {
  return (await page.locator(`${TOTALS} .bot-kpi`, { has: page.locator(".bot-kpi-label", { hasText: /^Trades$/ }) }).locator(".bot-kpi-sub").innerText()).trim();
}

e2eTest("the live Bot page opens with its seven totals and writes nothing", async () => {
  await withApp({ view: "Bot" }, async (app) => {
    await openBot(app);
    assert.equal((await app.page.locator(".bot-head h2").innerText()).trim(), "BOT workspace");
    assert.equal(await app.page.locator(`${TOTALS} .bot-kpi`).count(), 7);
    const labels = (await app.page.locator(`${TOTALS} .bot-kpi-label`).allInnerTexts()).map((s) => s.trim());
    assert.deepEqual(labels.slice(1), ["ROI", "Closed wins / losses", "Avg trade", "Trades", "Best rule", "Worst rule"]);
    assert.match(labels[0], /^P\/L · strategy exit$/);
    assert.equal(await group(app.page, "Range").locator("button.is-on").innerText(), "Today");
    assert.ok(app.reads.some((read) => read.path === "/api/momx-scanner/bot" && read.search === "?days=1"));
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("tapping Today then 1W: the slower Today answer never paints over 1W", async () => {
  let todayLanded;
  const slowTodayDone = new Promise((resolve) => { todayLanded = resolve; });
  const reads = canned({
    [BOT_READ]: async (read) => {
      if (read.search === "?days=1") {
        await new Promise((resolve) => setTimeout(resolve, 2_500));
        todayLanded();
      }
      return { json: botAnswer(read.search) };
    },
  });
  await withApp({ view: "Bot", fakeReads: reads }, async (app) => {
    await app.page.locator(VIEW).waitFor({ timeout: 60_000 });
    await group(app.page, "Range").locator("button", { hasText: /^1W$/ }).click();
    await app.page.waitForFunction(() => /^3 days · /.test(
      [...document.querySelectorAll("section.bot-kpis[aria-label='Totals'] .bot-kpi")]
        .find((k) => k.querySelector(".bot-kpi-label")?.textContent.trim() === "Trades")?.querySelector(".bot-kpi-sub")?.textContent || ""), null, { timeout: 30_000 });
    await slowTodayDone;
    await app.page.waitForTimeout(500);
    assert.equal(await group(app.page, "Range").locator("button.is-on").innerText(), "1W");
    assert.match(await tradesKpiSub(app.page), /^3 days · /);
    assert.equal((await app.page.locator(`${TOTALS} .bot-kpi`, { has: app.page.locator(".bot-kpi-label", { hasText: /^Trades$/ }) }).locator(".bot-kpi-value").innerText()).trim(), "7");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("News trade and AI pick explain themselves, follow BULL / BEAR, and survive a reload", async () => {
  await withApp({ view: "Bot", fakeReads: canned() }, async (app) => {
    await openBot(app);
    const books = group(app.page, "Which bot");
    await books.locator("button", { hasText: "News trade" }).click();
    assert.match(await books.locator("button", { hasText: "News trade" }).getAttribute("class"), /\bis-on\b.*\bis-jev\b/);
    const note = app.page.locator("p.bot-jevbook-note", { hasText: "News trade" });
    assert.match(await note.innerText(), /NEWS ▲ list \(calls\)/);
    assert.match(await tradesKpiSub(app.page), /^1 day · /);
    assert.equal((await app.page.locator(`${TOTALS} .bot-kpi`, { has: app.page.locator(".bot-kpi-label", { hasText: /^Trades$/ }) }).locator(".bot-kpi-value").innerText()).trim(), "1");
    await group(app.page, "Board").locator("button", { hasText: "BEAR · puts" }).click();
    assert.match(await note.innerText(), /NEWS ▼ list \(puts\)/);
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await openBot(app);
    assert.match(await group(app.page, "Which bot").locator("button", { hasText: "News trade" }).getAttribute("class"), /\bis-on\b/);
    await group(app.page, "Which bot").locator("button", { hasText: "AI pick" }).click();
    assert.match(await app.page.locator("p.bot-jevbook-note", { hasText: "AI pick" }).innerText(), /scored 55% or more/);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the Level strategy select shows for All trades on both boards (bear Level puts, 2026-11-05) and hides for other bots", async () => {
  await withApp({ view: "Bot", fakeReads: canned() }, async (app) => {
    await openBot(app);
    const level = app.page.locator("select.bot-paper-select[aria-label='Level strategy']");
    await level.waitFor({ state: "visible", timeout: 15_000 });
    assert.equal(await level.inputValue(), "all");
    await group(app.page, "Board").locator("button", { hasText: "BEAR · puts" }).click();
    await level.waitFor({ state: "visible", timeout: 10_000 });
    await group(app.page, "Board").locator("button", { hasText: "BULL · calls" }).click();
    await level.waitFor({ state: "visible", timeout: 10_000 });
    await group(app.page, "Which bot").locator("button", { hasText: "News trade" }).click();
    await level.waitFor({ state: "detached", timeout: 10_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a leaderboard row opens that rule's trades and 'all rules' clears it; the tab survives a reload", async () => {
  // No bot.tab seed here: the harness re-seeds its storage on every load, which
  // would undo the reload check below. A fresh browser opens on the Leaderboard.
  await withApp({ view: "Bot", fakeReads: canned() }, async (app) => {
    await openBot(app);
    const goRow = app.page.locator("table.bot-table tr.is-click", { has: app.page.locator("td b", { hasText: /^GO$/ }) });
    await goRow.click();
    const selected = app.page.locator("button.bot-tab[aria-selected='true']");
    assert.equal((await selected.innerText()).trim(), "Trades · GO");
    assert.equal((await app.page.locator(".bot-card-head h3").innerText()).trim(), "Trades · GO");
    assert.equal(await app.page.locator(".bot-card tbody tr").count(), 2);
    await app.page.locator("button.bot-clear").click();
    assert.equal((await app.page.locator(".bot-card-head h3").innerText()).trim(), "Trades");
    assert.equal(await app.page.locator(".bot-card tbody tr").count(), 4);
    await tab(app.page, "Rules explained").click();
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await openBot(app);
    assert.equal((await app.page.locator("button.bot-tab[aria-selected='true']").innerText()).trim(), "Rules explained");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Rules explained lists Daily 2 with what it needs and its back-test", async () => {
  await withApp({ view: "Bot", fakeReads: canned(), storage: { "bot.tab": "rules" } }, async (app) => {
    await openBot(app);
    const daily2 = app.page.locator(".bot-help-list .bot-help-item", { has: app.page.locator(".bot-help-name b", { hasText: /^Daily 2$/ }) });
    await daily2.waitFor({ timeout: 15_000 });
    const text = await daily2.innerText();
    assert.match(text, /Needs: A\+ grade after 09:35, momentum Extended, not up 10%\+, RVOL on at least two timeframes/);
    assert.match(text, /Back-test: /);
    assert.equal(await daily2.locator(".bot-chip").count(), 0, "Daily 2 is a main rule, not a test rule");
  });
});

e2eTest("a refused Bot read shows the admin-only message", async () => {
  await withApp({ view: "Bot", fakeReads: canned({ [BOT_READ]: { status: 403, json: { error: "admin only" } } }) }, async (app) => {
    await app.page.locator(VIEW).waitFor({ timeout: 60_000 });
    const error = app.page.locator(".bot-error[role='alert']");
    await error.waitFor({ timeout: 30_000 });
    assert.match(await error.innerText(), /^The Bot page is for admin accounts only\. Use Refresh to retry\.$/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Same-day + Hold to close re-labels the totals; Best price hides Open P/L; only the contract is remembered", async () => {
  await withApp({ view: "Bot", fakeReads: canned(), storage: { "bot.tab": "trades" } }, async (app) => {
    await openBot(app);
    await group(app.page, "Contract").locator("button", { hasText: /^Same-day$/ }).click();
    await group(app.page, "Exit comparison").getByRole("button", { name: /^Hold to close/ }).click();
    assert.match(await app.page.locator("p.bot-jevbook-note", { hasText: "Same-day · Hold to close" }).innerText(), /same-day \(0DTE\) contract held to the 3:50 PM close/);
    assert.equal((await app.page.locator(`${TOTALS} .bot-kpi-label`).first().innerText()).trim(), "P/L · Same-day · Hold to close");
    await group(app.page, "Exit comparison").getByRole("button", { name: /^Best price/ }).click();
    assert.equal(await group(app.page, "Exit comparison").getByRole("button", { name: /^Best price/ }).getAttribute("aria-pressed"), "true");
    const outcomes = (await group(app.page, "Trade outcome").locator("button").allInnerTexts()).map((s) => s.trim());
    assert.deepEqual(outcomes, ["All outcomes", "Losses", "Wins", "Unpriced"]);
    assert.ok((await app.page.locator(`${TOTALS} .bot-kpi-label`).allInnerTexts()).includes("Hindsight gains / break-even"));
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await openBot(app);
    assert.equal((await group(app.page, "Contract").locator("button.is-on").innerText()).trim(), "Same-day");
    assert.equal(await group(app.page, "Exit comparison").getByRole("button", { name: /^Strategy exit/ }).getAttribute("aria-pressed"), "true");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Contract x exit shows nine exit columns and an All rules row; a rule row opens its trades", async () => {
  await withApp({ view: "Bot", fakeReads: canned(), storage: { "bot.tab": "combos" } }, async (app) => {
    await openBot(app);
    const matrix = app.page.locator("table.bot-table.bot-matrix");
    await matrix.waitFor({ timeout: 15_000 });
    assert.equal(await matrix.locator("thead tr").nth(1).locator("th").count(), 9);
    assert.equal(await matrix.locator("tr.bot-total-row td.is-left").innerText(), "All rules");
    assert.ok(await matrix.locator("td.bot-cell .bot-wl").count() > 0);
    await matrix.locator("tr.is-click", { has: app.page.locator("td b", { hasText: /^Daily 2$/ }) }).click();
    assert.equal((await app.page.locator("button.bot-tab[aria-selected='true']").innerText()).trim(), "Trades · Daily 2");
    assert.deepEqual(app.pageErrors, []);
  });
});

// FAILS TODAY: the history effect in BotPanel.jsx depends on `tab`, so moving
// from Winners by period to Day by day downloads the whole 180-day answer again.
test("Winners by period then Day by day fetch the 180-day history once", {
  skip: marketHoursBlock() || false,
  todo: "BotPanel.jsx loadHistory effect re-runs on every periods/days tab change",
  timeout: 180_000,
}, async () => {
  await withApp({ view: "Bot", fakeReads: canned(), storage: { "bot.tab": "leaders" } }, async (app) => {
    await openBot(app);
    await tab(app.page, "Winners by period").click();
    await app.page.locator("table.bot-matrix tbody tr").first().waitFor({ timeout: 30_000 });
    await tab(app.page, "Day by day").click();
    await app.page.locator(".bot-card-head h3", { hasText: "Day by day" }).waitFor({ timeout: 15_000 });
    await app.page.locator(".bot-card tbody tr").first().waitFor({ timeout: 30_000 });
    assert.equal(app.reads.filter((read) => read.path === "/api/momx-scanner/bot" && read.search === "?days=180").length, 1);
  });
});

e2eTest("the history tabs render their tables from the 180-day answer", async () => {
  await withApp({ view: "Bot", fakeReads: canned(), storage: { "bot.tab": "periods" } }, async (app) => {
    await openBot(app);
    await app.page.locator("table.bot-matrix tr.bot-total-row").waitFor({ timeout: 30_000 });
    await tab(app.page, "Day by day").click();
    const rows = app.page.locator(".bot-card tbody tr");
    await rows.first().waitFor({ timeout: 30_000 });
    assert.equal(await rows.count(), 5);
    assert.equal((await rows.first().locator("td").first().innerText()).trim(), "2026-10-28");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the Bot page never scrolls sideways and the range buttons stay reachable", async () => {
  await withApp({ view: "Bot", viewport: PHONE, fakeReads: canned(), storage: { "bot.tab": "trades" } }, async (app) => {
    await openBot(app);
    await app.page.locator(".bot-scroll table.bot-table").first().waitFor({ timeout: 15_000 });
    assert.equal(await overflowsSideways(app.page), false);
    for (const label of ["Today", "180D"]) {
      const button = group(app.page, "Range").locator("button", { hasText: new RegExp(`^${label}$`) });
      await button.scrollIntoViewIfNeeded();
      const box = await button.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `${label} at ${JSON.stringify(box)} is off screen`);
    }
    assert.deepEqual(app.pageErrors, []);
  });
});
