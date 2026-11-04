// Bot page -> Paper 3 / Paper 5, the post-trade learning team and the AI
// model tab, end to end (2026-11-04 coverage pass). The paper / learning / AI
// reads are canned (trading-bot.fixtures.mjs) except where a spec says LIVE;
// the page only reads, and every spec asserts nothing was written.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import {
  AI_READ, AI_REPORT, BOT_READ, LEARNING_READ, MIRRORS, MIRROR_READ, TEAM, TEAM_NOT_STARTED, botAnswer,
} from "./trading-bot.fixtures.mjs";

const PAPER_SELECT = "select.bot-paper-select[aria-label='Alpaca paper account']";
const LEARNING = "section.bot-jevbook-note[aria-label='Post-trade learning team']";

function canned(extra = {}) {
  return {
    [BOT_READ]: (read) => ({ json: botAnswer(read.search) }),
    [LEARNING_READ]: { json: TEAM },
    [AI_READ]: { json: AI_REPORT },
    [MIRROR_READ]: { json: MIRRORS },
    ...extra,
  };
}

async function openBot(app) {
  await app.page.locator("[data-testid='bot-view']").waitFor({ timeout: 60_000 });
  await app.page.locator(PAPER_SELECT).waitFor({ timeout: 30_000 });
}

const mirrorReads = (app) => app.reads.filter((read) => read.path === "/api/momx-scanner/paper-mirror").length;

async function until(check, message, timeout = 10_000) {
  const deadline = Date.now() + timeout;
  while (!check()) {
    if (Date.now() > deadline) throw new Error(message);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

e2eTest("Paper 3 replaces the contract / exit switches with the account's own totals and orders", async () => {
  const slow = async () => { await new Promise((resolve) => setTimeout(resolve, 1_500)); return { json: MIRRORS }; };
  await withApp({ view: "Bot", fakeReads: canned({ [MIRROR_READ]: slow }) }, async (app) => {
    await openBot(app);
    await app.page.locator(PAPER_SELECT).selectOption("paper3");
    await app.page.locator("p.bot-hint", { hasText: "Loading the paper3 paper trades..." }).waitFor({ timeout: 10_000 });
    const heading = app.page.locator(".bot-page h3", { hasText: /^Paper 3 · / });
    await heading.waitFor({ timeout: 30_000 });
    assert.equal((await heading.innerText()).trim(), "Paper 3 · 2 orders · calls");
    assert.equal(await app.page.locator("[role='group'][aria-label='Contract']").count(), 0);
    assert.equal(await app.page.locator("[role='group'][aria-label='Exit comparison']").count(), 0);
    const kpis = (await app.page.locator("section.bot-kpis[aria-label='Paper totals'] .bot-kpi-label").allInnerTexts()).map((s) => s.trim());
    assert.deepEqual(kpis, ["Realized P/L · paper", "Closed ROI", "Open P/L · midpoint estimate", "Closed win rate", "Orders"]);
    assert.ok(app.reads.some((read) => read.path === "/api/momx-scanner/paper-mirror" && read.search === "?days=1"));
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("switching Paper 3 to Paper 5 never shows Paper 3's orders under the Paper 5 heading", async () => {
  await withApp({ view: "Bot", fakeReads: canned() }, async (app) => {
    await openBot(app);
    await app.page.locator(PAPER_SELECT).selectOption("paper3");
    await app.page.locator(".bot-page h3", { hasText: /^Paper 3 · / }).waitFor({ timeout: 30_000 });
    await app.page.locator(PAPER_SELECT).selectOption("paper5");
    await app.page.locator(".bot-page h3", { hasText: /^Paper 5 · / }).waitFor({ timeout: 30_000 });
    const tickers = await app.page.evaluate(() => {
      const table = [...document.querySelectorAll(".bot-scroll table.bot-table")].find((t) => t.querySelector("th")?.textContent === "Day");
      const index = [...table.querySelectorAll("thead th")].findIndex((th) => th.textContent === "Ticker");
      return [...table.querySelectorAll("tbody tr")].map((tr) => tr.children[index]?.textContent);
    });
    assert.deepEqual(tickers, ["SPY", "SPY"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a dead scanner worker shows 'unavailable' with a Retry that asks again", async () => {
  const workerDown = { json: { warming: true, rows: [], events: [], lists: [], message: "MomX worker unavailable: refused" } };
  await withApp({ view: "Bot", fakeReads: canned({ [MIRROR_READ]: workerDown }) }, async (app) => {
    await openBot(app);
    await app.page.locator(PAPER_SELECT).selectOption("paper3");
    const alert = app.page.locator("div[role='alert']", { has: app.page.locator("p.bot-error") });
    await alert.waitFor({ timeout: 30_000 });
    assert.equal((await alert.locator("p.bot-error").innerText()).trim(), "Could not load the paper trades (paper3 is unavailable from the paper service).");
    assert.equal(await app.page.locator("section.bot-kpis[aria-label='Paper totals']").count(), 0, "no $0 totals for a missing account");
    const before = mirrorReads(app);
    await alert.locator("button.bot-refresh", { hasText: "Retry paper history" }).click();
    await until(() => mirrorReads(app) > before, "Retry did not ask again");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Refresh paper asks for the paper history exactly once more", async () => {
  await withApp({ view: "Bot", fakeReads: canned() }, async (app) => {
    await openBot(app);
    await app.page.locator(PAPER_SELECT).selectOption("paper5");
    await app.page.locator(".bot-page h3", { hasText: /^Paper 5 · / }).waitFor({ timeout: 30_000 });
    const before = mirrorReads(app);
    await app.page.locator("button.bot-refresh[title='Refresh']", { hasText: "Refresh paper" }).click();
    await until(() => mirrorReads(app) > before, "Refresh paper did not ask again");
    await app.page.locator(".bot-page h3", { hasText: /^Paper 5 · / }).waitFor({ timeout: 30_000 });
    await new Promise((resolve) => setTimeout(resolve, 1_000));   // room for a duplicate request to show up
    assert.equal(mirrorReads(app), before + 1);
    assert.deepEqual(app.writes, []);
  });
});

e2eTest("on a phone the paper order table scrolls inside its card, not the page", async () => {
  await withApp({ view: "Bot", viewport: PHONE, fakeReads: canned() }, async (app) => {
    await openBot(app);
    await app.page.locator(PAPER_SELECT).selectOption("paper3");
    await app.page.locator(".bot-page h3", { hasText: /^Paper 3 · / }).waitFor({ timeout: 30_000 });
    await app.page.locator(".bot-scroll table.bot-table").first().waitFor({ timeout: 15_000 });
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("LIVE: the learning team panel settles on a real status within 20 s", async () => {
  await withApp({ view: "Bot", fakeReads: { [BOT_READ]: (read) => ({ json: botAnswer(read.search) }) } }, async (app) => {
    await openBot(app);
    const section = app.page.locator(LEARNING);
    await section.locator("b", { hasText: "Trade first · learn afterward" }).waitFor({ timeout: 30_000 });
    await section.locator("p", { hasText: /Reviews (ON|OFF) · \d+ trades reviewed|Review status unavailable/ }).first().waitFor({ timeout: 20_000 });
    assert.equal(await section.locator("p", { hasText: "Loading review status…" }).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the learning details open without errors and the experiment rule reads 09:05", async () => {
  await withApp({ view: "Bot", fakeReads: canned() }, async (app) => {
    await openBot(app);
    const section = app.page.locator(LEARNING);
    await section.locator("p", { hasText: "Reviews ON · 12 trades reviewed · 1 queued · 0 failed" }).waitFor({ timeout: 20_000 });
    for (const summary of ["Specialist scoreboard", "Latest agent discussion", "Experiment evidence · exp-1"]) {
      await section.locator("details > summary", { hasText: summary }).click();
    }
    assert.match(await section.locator("details", { has: app.page.locator("summary", { hasText: "Specialist scoreboard" }) }).innerText(), /execution: 4 calls · 2\/3 correct \(67%\)/);
    assert.match(await section.locator("details", { has: app.page.locator("summary", { hasText: "Experiment evidence" }) }).innerText(), /Candidate: Entries from 09:05 ET\./);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the learning panel shows in All trades and in Paper 3, saying OFF before the team starts", async () => {
  await withApp({ view: "Bot", fakeReads: canned({ [LEARNING_READ]: { json: TEAM_NOT_STARTED } }) }, async (app) => {
    await openBot(app);
    const off = app.page.locator(`${LEARNING} p`, { hasText: "Reviews OFF · 0 trades reviewed · 0 queued · 0 failed" });
    await off.waitFor({ timeout: 20_000 });
    await app.page.locator(PAPER_SELECT).selectOption("paper3");
    await app.page.locator(".bot-page h3", { hasText: /^Paper 3 · / }).waitFor({ timeout: 30_000 });
    await off.waitFor({ timeout: 20_000 });
    assert.equal(await app.page.locator(LEARNING).count(), 1);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the AI model tab opens with its score chart and folds on a tap", async () => {
  await withApp({ view: "Bot", fakeReads: canned(), storage: { "bot.tab": "ai" } }, async (app) => {
    await openBot(app);
    const card = app.page.locator("section.bot-jevbook-note[aria-label='AI model score']");
    await card.waitFor({ timeout: 30_000 });
    const header = card.locator("button.bot-refresh").first();
    assert.match(await header.innerText(), /^AI model \(shadow\) · score 0\.570 · edge · bull 0\.580 · bear 0\.520/);
    const chart = card.locator("svg[role='img'][aria-label='AI score chart']");
    await chart.waitFor({ state: "visible", timeout: 15_000 });
    assert.match(await card.innerText(), /Score by test day so far/);
    await header.click();
    await chart.waitFor({ state: "detached", timeout: 10_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("AI pick counts no more trades than All trades, and with no trained model the AI tab is empty", async () => {
  await withApp({ view: "Bot", fakeReads: canned({ [AI_READ]: { json: { latest: {}, history: [], paper: {} } } }) }, async (app) => {
    await openBot(app);
    const tradesValue = async () => Number((await app.page.locator("section.bot-kpis[aria-label='Totals'] .bot-kpi", {
      has: app.page.locator(".bot-kpi-label", { hasText: /^Trades$/ }),
    }).locator(".bot-kpi-value").innerText()).trim());
    await app.page.locator("section.bot-kpis[aria-label='Totals'] .bot-kpi").first().waitFor({ timeout: 30_000 });
    const all = await tradesValue();
    await app.page.locator("[role='group'][aria-label='Which bot'] button", { hasText: "AI pick" }).click();
    await app.page.locator("p.bot-jevbook-note", { hasText: "AI pick" }).waitFor({ timeout: 10_000 });
    const picked = await tradesValue();
    assert.equal(all, 4);
    assert.equal(picked, 1);
    await app.page.locator("nav.bot-tabs button.bot-tab", { hasText: "AI model" }).click();
    await app.page.locator("button.bot-tab[aria-selected='true']", { hasText: "AI model" }).waitFor({ timeout: 10_000 });
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    assert.equal(await app.page.locator("section[aria-label='AI model score']").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});
