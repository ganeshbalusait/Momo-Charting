// Stock Paper Trading, end to end (2026-11-04 coverage pass): status banner,
// risk settings, the paper-account switch, open positions with per-row close
// and Close All, and the trade journal. Every write would reach Alpaca or
// change the owner's settings, so each is answered by the harness (fakeWrites)
// or stopped by it - never allowed through. Specs needing a known state answer
// GET /api/dashboard with the live dashboard plus their own changes.

import assert from "node:assert/strict";
import test from "node:test";

import { BASE_URL, PHONE, e2eTest, marketHoursBlock, overflowsSideways, withApp } from "./harness.mjs";

const VIEW = "Paper Trading";
const BANNER = "section.paper-bot-banner";
const RISK = ".config-stack.risk-config-grid";
const writesTo = (app, path) => app.writes.filter((write) => write.path === path);
const body = (write) => JSON.parse(write.body || "{}");
const ok = (json = {}) => ({ json });

let liveDashboard = null;
async function readLiveDashboard() {
  liveDashboard ??= fetch(`${BASE_URL}/api/dashboard?compact=true`).then(async (response) => {
    if (!response.ok) throw new Error(`live dashboard read failed: HTTP ${response.status}`);
    return response.json();
  });
  return structuredClone(await liveDashboard);
}

function canned(edit) {
  let answer = null;
  return async () => {
    answer ??= readLiveDashboard().then(edit);
    return { json: await answer };
  };
}

const POSITIONS = [
  { symbol: "AAPL", qty: 5, avg_entry_price: 200, current_price: 204, unrealized_pl: 20, unrealized_plpc: 2, setup_name: "EMA + VWAP + ORB", llm_advice: "Hold: trend intact" },
  { symbol: "NVDA", qty: 3, avg_entry_price: 190, current_price: 186, unrealized_pl: -12, unrealized_plpc: -2.1, setup_name: "ORB" },
];

function known(dashboard, { positions = [], botState = "Stopped", block = "" } = {}) {
  dashboard.botState = botState;
  dashboard.riskConfig = { ...(dashboard.riskConfig || {}), dailyTradeAmount: 3000, tradeAmount: 300, stopLossPercent: 2, stopLossAmount: 6, firstProfitTargetPercent: 3 };
  dashboard.openPositions = positions;
  dashboard.status = { ...(dashboard.status || {}), openPositions: positions.length, accountLabel: "E2E Stock Paper", activeAccountId: "default-paper" };
  dashboard.scheduler = { ...(dashboard.scheduler || {}), entryBlockStatus: block, entryBlockMessage: block ? "Daily trade capital reached." : "" };
  dashboard.accounts = [
    { id: "default-paper", label: "Stock Paper", tradeLabel: "Stock Paper", stockTradingDisabled: false },
    { id: "paper2", label: "Paper 2", tradeLabel: "Paper 2", stockTradingDisabled: false },
    { id: "paper3", label: "OPTION TRADE", tradeLabel: "Mag7 Options", stockTradingDisabled: true },
  ];
  return dashboard;
}

const riskInput = (page, label) => page.locator(`${RISK} label`, { hasText: label }).locator("input");

async function openKnown(app) {
  await app.page.locator(`${BANNER} .paper-bot-banner-meta small`, { hasText: /^E2E Stock Paper$/ }).waitFor({ timeout: 60_000 });
  await app.page.waitForFunction(() => document.querySelector(".config-stack.risk-config-grid input")?.value === "3000", null, { timeout: 15_000 });
}

e2eTest("LIVE: the stock page opens with its banner, risk settings, portfolio, positions and journal", async () => {
  await withApp({ view: VIEW }, async (app) => {
    const banner = app.page.locator(BANNER, { hasText: "PAPER TRADE BOT STATUS" });
    await banner.waitFor({ timeout: 60_000 });
    assert.match(await banner.getAttribute("class"), /\btone-(blue|gold|green|negative)\b/);
    for (const title of ["RISK SETTINGS", "YOUR PORTFOLIO", "OPEN POSITIONS", "TRADE JOURNAL"]) {
      await app.page.locator(".table-toolbar > span", { hasText: new RegExp(`^${title}$`) }).first().waitFor({ timeout: 15_000 });
    }
    assert.equal(await app.page.locator("section.portfolio-card .portfolio-balance-grid > div").count(), 3);
    const tables = await app.page.locator("section.data-card.scanner-table").allInnerTexts();
    for (const text of tables) assert.doesNotMatch(text, /\[object Object\]|undefined|NaN/);
    const journal = app.page.locator("section.data-card.scanner-table", { has: app.page.locator(".table-toolbar span", { hasText: /^TRADE JOURNAL$/ }) });
    assert.ok((await journal.locator("tbody tr").count()) > 0 || /No paper trades recorded yet\./.test(await journal.innerText()));
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the banner reads green with the symbols it manages, gold at the daily cap", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/dashboard": canned((d) => known(d, { positions: POSITIONS, botState: "Running" })) } }, async (app) => {
    await openKnown(app);
    const banner = app.page.locator(BANNER);
    assert.match(await banner.getAttribute("class"), /\btone-green\b/);
    assert.equal((await banner.locator("b").innerText()).trim(), "Bot is running and actively managing paper positions.");
    assert.match(await banner.locator("small").first().innerText(), /2 open positions .*: AAPL, NVDA\.$/);
  });
  await withApp({ view: VIEW, fakeReads: { "GET /api/dashboard": canned((d) => known(d, { botState: "Running", block: "daily_cap" })) } }, async (app) => {
    await openKnown(app);
    assert.match(await app.page.locator(BANNER).getAttribute("class"), /\btone-gold\b/);
    assert.equal((await app.page.locator(`${BANNER} small`).first().innerText()).trim(), "Daily trade capital reached.");
  });
});

e2eTest("Daily Trade $ fills the per-trade amount and stop $, and Save sends the numbers once", async () => {
  await withApp({
    view: VIEW,
    fakeReads: { "GET /api/dashboard": canned((d) => known(d)) },
    fakeWrites: { "POST /api/risk-settings": ok() },
  }, async (app) => {
    await openKnown(app);
    await riskInput(app.page, "Daily Trade $ Amount").fill("5000");
    assert.equal(await riskInput(app.page, "Per New Trade Amount").inputValue(), "500");
    assert.equal(await riskInput(app.page, "Stop Loss $").inputValue(), "10.00");
    await riskInput(app.page, "Stop Loss $").fill("15");
    assert.equal(await riskInput(app.page, "Stop Loss %").inputValue(), "3.00");
    await app.page.locator(`${RISK} button.config-save-button`, { hasText: /^Save Risk Settings$/ }).click();
    await app.page.locator(`${RISK} button.config-save-button:enabled`, { hasText: /^Save Risk Settings$/ }).waitFor({ timeout: 10_000 });
    const sent = writesTo(app, "/api/risk-settings");
    assert.equal(sent.length, 1);
    const values = body(sent[0]);
    assert.deepEqual([values.dailyTradeAmount, values.tradeAmount, values.stopLossPercent, values.stopLossAmount], [5000, 500, 3, 15]);
    assert.equal(typeof values.firstProfitTargetPercent, "number");
    assert.equal(app.writes.length, 1);
    assert.deepEqual(app.pageErrors, []);
  });
});

// FAILS TODAY: App.jsx syncs "1st Profit Target %" from the STOP percent
// (setFirstProfitTargetPercent(syncedStopPercent)), so the box shows 2 for a saved
// 3% target and the next Save overwrites the target with the stop value.
test("1st Profit Target % shows the saved target, not the stop %", {
  skip: marketHoursBlock() || false,
  todo: "App.jsx riskConfig sync sets firstProfitTargetPercent from stopLossPercent",
  timeout: 180_000,
}, async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/dashboard": canned((d) => known(d)) } }, async (app) => {
    await openKnown(app);
    assert.equal(await riskInput(app.page, "1st Profit Target %").inputValue(), "3");
  });
});

e2eTest("the Paper Account select switches with one POST and options-only accounts cannot be picked", async () => {
  await withApp({
    view: VIEW,
    fakeReads: { "GET /api/dashboard": canned((d) => known(d)) },
    fakeWrites: { "POST /api/account-select": ok() },
  }, async (app) => {
    await openKnown(app);
    const select = app.page.locator("label.filter-control.account-selector select");
    assert.equal(await select.inputValue(), "default-paper");
    const optionsOnly = select.locator("option", { hasText: "(options only)" });
    assert.equal(await optionsOnly.count(), 1);
    assert.equal(await optionsOnly.isDisabled(), true);
    assert.equal((await optionsOnly.innerText()).trim(), "Mag7 Options (options only)");
    await select.selectOption("paper2");
    const deadline = Date.now() + 10_000;
    while (!writesTo(app, "/api/account-select").length && Date.now() < deadline) await new Promise((r) => setTimeout(r, 100));
    assert.deepEqual(writesTo(app, "/api/account-select").map(body), [{ profileId: "paper2" }]);
  });
});

e2eTest("with no open positions Close All Positions is disabled and the table says so", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/dashboard": canned((d) => known(d)) } }, async (app) => {
    await openKnown(app);
    assert.equal(await app.page.locator("button.close-all-button", { hasText: /^Close All Positions$/ }).isDisabled(), true);
    const positions = app.page.locator("section.data-card.scanner-table", { has: app.page.locator(".table-toolbar span", { hasText: /^OPEN POSITIONS$/ }) });
    assert.match(await positions.innerText(), /No open paper positions\./);
    assert.deepEqual(app.writes, []);
  });
});

e2eTest("a row's close sends one POST for that symbol; Close All sends one too; the LLM column reads as text", async () => {
  await withApp({
    view: VIEW,
    fakeReads: { "GET /api/dashboard": canned((d) => known(d, { positions: POSITIONS })) },
    fakeWrites: { "POST /api/close-position": ok({ result: { message: "Close order submitted for AAPL." } }), "POST /api/close-all-positions": ok() },
  }, async (app) => {
    await openKnown(app);
    const positions = app.page.locator("section.data-card.scanner-table", { has: app.page.locator(".table-toolbar span", { hasText: /^OPEN POSITIONS$/ }) });
    await positions.locator(".symbol-pill", { hasText: /^AAPL$/ }).waitFor({ timeout: 15_000 });
    const text = await positions.innerText();
    assert.match(text, /Hold: trend intact/);
    assert.doesNotMatch(text, /\[object Object\]|undefined/);
    await app.page.locator("button.close-position-button[aria-label='Close AAPL']").click();
    await app.page.locator("button.close-position-button[aria-label='Close AAPL']:enabled").waitFor({ timeout: 10_000 });
    await app.page.locator("button.close-all-button", { hasText: /^Close All Positions$/ }).click();
    await app.page.locator("button.close-all-button:enabled", { hasText: /^Close All Positions$/ }).waitFor({ timeout: 10_000 });
    assert.deepEqual(app.writes.map((w) => [w.path, body(w)]), [["/api/close-position", { symbol: "AAPL" }], ["/api/close-all-positions", {}]]);
    assert.deepEqual(app.pageErrors, []);
  });
});

// FAILS TODAY: the stock bot's Start / Pause (POST /api/bot-control) lives only
// on the "Dashboard" view, which is not in the rail, while this page's banner
// tells him to "Start the bot".
test("the stock bot can be started or paused from Stock Paper Trading", {
  skip: marketHoursBlock() || false,
  todo: "the only Start/Pause Stock Bot control is on the unreachable Dashboard view",
  timeout: 180_000,
}, async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/dashboard": canned((d) => known(d)) } }, async (app) => {
    await openKnown(app);
    assert.ok(await app.page.locator("section.paper-trading-layout button", { hasText: /(Start|Pause) Stock Bot/ }).count() > 0);
  });
});

e2eTest("on a phone the risk grid and portfolio stack inside the screen", async () => {
  await withApp({ view: VIEW, viewport: PHONE, fakeReads: { "GET /api/dashboard": canned((d) => known(d, { positions: POSITIONS })) } }, async (app) => {
    await openKnown(app);
    for (const selector of [RISK, "section.portfolio-card", `${RISK} button.config-save-button`, "button.close-all-button"]) {
      const element = app.page.locator(selector).first();
      await element.scrollIntoViewIfNeeded();
      const box = await element.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `${selector} at ${JSON.stringify(box)} is cut off`);
    }
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
