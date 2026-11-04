// Option Paper Trading, end to end (2026-11-04 coverage pass): the boards,
// the settings cards, the bot Start / Pause / Stop buttons, the position and
// order buttons and the order ticket. EVERY write here would reach Alpaca or
// change the owner's settings, so each one is answered by the harness
// (fakeWrites) or stopped by it - never allowed through. Specs that need a
// known state answer GET /api/dashboard with the live dashboard plus their own
// changes ("canned"), read once from the running app.

import assert from "node:assert/strict";
import test from "node:test";

import { BASE_URL, PHONE, e2eTest, marketHoursBlock, overflowsSideways, withApp } from "./harness.mjs";

const VIEW = "Option Paper Trading";
const BANNER = "section.paper-bot-banner";
const CONTRACT = "NVDA261120C00200000";
const ok = (json = {}) => ({ json });
const delayed = (ms, reply) => async () => { await new Promise((resolve) => setTimeout(resolve, ms)); return reply; };
const writesTo = (app, path) => app.writes.filter((write) => write.path === path);
const body = (write) => JSON.parse(write.body || "{}");

async function until(check, message, timeout = 10_000) {
  const deadline = Date.now() + timeout;
  while (!check()) {
    if (Date.now() > deadline) throw new Error(message());
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

let liveDashboard = null;
async function readLiveDashboard() {
  liveDashboard ??= fetch(`${BASE_URL}/api/dashboard?compact=true`).then(async (response) => {
    if (!response.ok) throw new Error(`live dashboard read failed: HTTP ${response.status}`);
    return response.json();
  });
  return structuredClone(await liveDashboard);
}

/** GET /api/dashboard answered with the live dashboard changed by `edit` (same answer every poll). */
function canned(edit) {
  let answer = null;
  return async () => {
    answer ??= readLiveDashboard().then(edit);
    return { json: await answer };
  };
}

const ACCOUNTS = [
  { id: "paper3", label: "OPTION TRADE", tradeLabel: "Mag7 Options", usage: "option_mag7", paper: true,
    status: { connectionStatus: "Connected", tradeableBuyingPower: 4000, accountEquity: 10000, dailyPnL: 12 } },
  { id: "paper5", label: "WATCHLIST OPTIONS", tradeLabel: "Watchlist Options", usage: "option_watchlist", paper: true,
    status: { connectionStatus: "Error", statusMessage: "401 unauthorized", tradeableBuyingPower: 0, accountEquity: 0 } },
];

function known(dashboard, { positions = [], state = "Paused" } = {}) {
  dashboard.optionBot = { ...(dashboard.optionBot || {}), state, executionMode: "automatic", watchlistSource: "option", message: "Option bot is paused." };
  dashboard.optionRiskConfig = {
    dailyTradeAmount: "500", tradeAmount: "150", contractQuantity: "", stopLossPercent: "20", firstProfitTargetPercent: "100",
    firstProfitTargetCons: "80%", firstProfitTargetSellMode: "percentage", firstProfitTargetSellValue: "80", runnerLockStepPercent: "50",
  };
  dashboard.optionAccounts = ACCOUNTS;
  dashboard.optionPositions = positions;
  dashboard.optionBrokerOrders = [];
  return dashboard;
}

const POSITION = {
  contract: CONTRACT, symbol: "NVDA", underlying_symbol: "NVDA", account_profile_id: "paper3", quantity: 2, remaining_quantity: 2,
  entry_price: 1.0, current_mid: 1.1, runner_stop: 0.8, take_profit_1: 2.0, status: "position_open", pnl: 20,
  selected_option_expiry: "2026-11-20", selected_option_strike: 200,
};

// The canned dashboard has landed once its accounts show (Watchlist Options is
// the canned broken account) - the page's own defaults never render that.
async function openKnown(app, state = "Paused") {
  await app.page.locator(".option-ticket-grid > div", { has: app.page.locator("span", { hasText: /^Watchlist Options$/ }) })
    .locator("b", { hasText: /^UNAVAILABLE$/ }).waitFor({ timeout: 60_000 });
  await app.page.locator(`${BANNER} b`, { hasText: `| ${state}` }).waitFor({ timeout: 30_000 });
}

const field = (page, placeholder) => page.locator(`input[placeholder='${placeholder}']`);
const labelled = (page, label) => page.locator("section.option-layout-grid-three label", { hasText: label }).locator("select");

e2eTest("LIVE: the option page opens with its boards and the supervisor's locked authority", async () => {
  await withApp({ view: VIEW }, async (app) => {
    const banner = app.page.locator(BANNER, { hasText: "OPTION PAPER BOT" });
    await banner.waitFor({ timeout: 60_000 });
    await app.page.locator("[data-testid='option-account-performance']").waitFor({ timeout: 30_000 });
    for (const title of ["OPTION SCAN COVERAGE", "OPTION BLOCKING REASONS", "OPTION ACCOUNT BOOKS", "ALPACA OPTION ORDERS", "OPTION TRADES TAKEN"]) {
      await app.page.locator(".table-toolbar span", { hasText: new RegExp(`^${title}$`) }).first().waitFor({ timeout: 15_000 });
    }
    const card = app.page.locator("section.option-supervisor-card");
    assert.match(await card.locator(".option-supervisor-hero").innerText(), /Can place orders: No \| Can override rules: No/);
    const panels = (await card.locator(".option-supervisor-panel > span").allInnerTexts()).map((s) => s.trim());
    assert.deepEqual(panels, ["Taken Review", "Skipped / Blocked", "Unusual Conditions", "Catalyst Summary · Information only", "Bad Trade Review", "Suggested Tuning"]);
    for (const panel of await card.locator(".option-supervisor-panel").all()) assert.ok((await panel.locator("p").count()) >= 1);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("LIVE: three dashboard polls land with no page error and no write", async () => {
  await withApp({ view: VIEW }, async (app) => {
    await app.page.locator(BANNER).waitFor({ timeout: 60_000 });
    const polls = () => app.reads.filter((read) => read.path === "/api/dashboard").length;
    const start = polls();
    await until(() => polls() >= start + 3, () => `only ${polls() - start} polls in 25 s`, 25_000);
    assert.equal(await app.page.locator(BANNER).count(), 1);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Save Option Risk Settings sends the typed values once and shows Saving... meanwhile", async () => {
  await withApp({
    view: VIEW,
    fakeReads: { "GET /api/dashboard": canned((d) => known(d)) },
    fakeWrites: { "POST /api/option-risk-settings": delayed(1_200, ok()) },
  }, async (app) => {
    await openKnown(app);
    await field(app.page, "Leave empty or set amount").fill("600");
    await field(app.page, "Optional. Leave empty if using contract count.").fill("");
    await field(app.page, "Example: 2 cons").fill("2");
    await field(app.page, "Example: 20, 30, 50. Empty = EMA20 stop").fill("30");
    await field(app.page, "Example: 100").fill("100");
    await labelled(app.page, "Contracts To Sell At Target 1").selectOption("contracts");
    await field(app.page, "Example: 4").fill("4");
    await field(app.page, "Default 50. Example: 50, 100, 150").fill("50");
    const save = app.page.locator("button.config-save-button", { hasText: /^(Save Option Risk Settings|Saving\.\.\.)$/ });
    await save.click();
    await app.page.locator("button.config-save-button", { hasText: /^Saving\.\.\.$/ }).waitFor({ timeout: 5_000 });
    await app.page.locator("button.config-save-button", { hasText: /^Save Option Risk Settings$/ }).waitFor({ timeout: 10_000 });
    const sent = writesTo(app, "/api/option-risk-settings");
    assert.equal(sent.length, 1);
    assert.deepEqual(body(sent[0]), {
      dailyTradeAmount: "600", tradeAmount: "", contractQuantity: "2", stopLossPercent: "30", firstProfitTargetPercent: "100",
      firstProfitTargetCons: "4 cons", firstProfitTargetSellMode: "contracts", firstProfitTargetSellValue: "4", runnerLockStepPercent: "50",
    });
    assert.equal(app.writes.length, 1);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("choosing 'MomX scanner (BULL board)' saves at once, with no Save click", async () => {
  await withApp({
    view: VIEW,
    fakeReads: { "GET /api/dashboard": canned((d) => known(d)) },
    fakeWrites: { "POST /api/option-bot-settings": ok() },
  }, async (app) => {
    await openKnown(app);
    await labelled(app.page, "Option Bot Watchlist").selectOption("momx");
    await until(() => writesTo(app, "/api/option-bot-settings").length > 0, () => "no save was sent");
    const sent = writesTo(app, "/api/option-bot-settings");
    assert.equal(sent.length, 1);
    assert.equal(body(sent[0]).watchlistSource, "momx");
    assert.equal(body(sent[0]).contractPolicy, "only_long_call");
  });
});

e2eTest("Approval Mode Human makes the ticket's Routing read 'Human approval required'", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/dashboard": canned((d) => known(d)) } }, async (app) => {
    await openKnown(app);
    const routing = app.page.locator(".option-ticket-grid > div", { has: app.page.locator("span", { hasText: /^Routing$/ }) }).locator("b");
    assert.equal((await routing.innerText()).trim(), "Auto paper engine");
    await labelled(app.page, "Approval Mode").selectOption("human");
    assert.equal((await routing.innerText()).trim(), "Human approval required");
    assert.deepEqual(app.writes, [], "changing the select alone saves nothing");
  });
});

e2eTest("a refused save shows the server's reason", async () => {
  const reason = "Administrator access is required.";
  await withApp({
    view: VIEW,
    fakeReads: { "GET /api/dashboard": canned((d) => known(d)) },
    fakeWrites: { "POST /api/option-risk-settings": { status: 403, json: { error: reason } } },
  }, async (app) => {
    await openKnown(app);
    await app.page.locator("button.config-save-button", { hasText: /^Save Option Risk Settings$/ }).click();
    await app.page.locator(".live-error", { hasText: reason }).waitFor({ timeout: 10_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Start / Pause / Stop send Running / Paused / Stopped and the banner follows", async () => {
  const reply = (state) => ok({ optionBot: { state, message: `Option bot is ${state.toLowerCase()}.` } });
  await withApp({
    view: VIEW,
    fakeReads: { "GET /api/dashboard": canned((d) => known(d, { state: "Stopped" })) },
    fakeWrites: { "POST /api/option-bot-control": (write) => reply(body(write).state) },
  }, async (app) => {
    await openKnown(app, "Stopped");
    const buttons = app.page.locator(".override-stack.option-override-stack button");
    await buttons.filter({ hasText: /^Start Option Bot$/ }).click();
    await app.page.locator(`${BANNER} b`, { hasText: "AUTO-PLAN | Running" }).waitFor({ timeout: 10_000 });
    await buttons.filter({ hasText: /^Pause Option Bot$/ }).click();
    await app.page.locator(`${BANNER} b`, { hasText: "PAUSED | Paused" }).waitFor({ timeout: 10_000 });
    await buttons.filter({ hasText: /^Stop Option Bot$/ }).click();
    await app.page.locator(`${BANNER} b`, { hasText: "MANUAL | Stopped" }).waitFor({ timeout: 10_000 });
    assert.deepEqual(writesTo(app, "/api/option-bot-control").map((w) => body(w).state), ["Running", "Paused", "Stopped"]);
    assert.equal(app.writes.length, 3);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("an unanswered Start is stopped by the harness and its reason is shown", async () => {
  await withApp({ view: VIEW }, async (app) => {
    await app.page.locator(BANNER).waitFor({ timeout: 60_000 });
    await app.page.locator(".override-stack.option-override-stack button", { hasText: /^Start Option Bot$/ }).click();
    await app.page.locator(".live-error", { hasText: "Blocked by the end-to-end harness: writes are not sent." }).waitFor({ timeout: 10_000 });
    assert.equal(writesTo(app, "/api/option-bot-control").length, 1);
    assert.equal(app.writes.length, 1, "the button reached only its own route");
  });
});

// FAILS TODAY: the banner always says "orders disabled" (App.jsx), while the
// engine it controls sends Alpaca paper option orders (api_server
// set_option_bot_state: "...sending Alpaca paper option orders...").
test("the option banner never claims orders are disabled while the engine sends paper orders", {
  skip: marketHoursBlock() || false,
  todo: "App.jsx paper-bot-banner-meta hard-codes '| orders disabled'",
  timeout: 180_000,
}, async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/dashboard": canned((d) => known(d, { state: "Running" })) } }, async (app) => {
    await openKnown(app, "Running");
    assert.doesNotMatch(await app.page.locator(`${BANNER} .paper-bot-banner-meta`).innerText(), /orders disabled/);
  });
});

e2eTest("with no open positions or stale buys the three action buttons are disabled", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/dashboard": canned((d) => known(d)) } }, async (app) => {
    await openKnown(app);
    for (const label of [/^Manage Stops Now$/, /^Close All Option Positions$/, /^Cancel Stale Buy Orders$/]) {
      assert.equal(await app.page.locator("button.close-all-button", { hasText: label }).isDisabled(), true, String(label));
    }
    assert.deepEqual(app.writes, []);
  });
});

e2eTest("a disconnected account shows UNAVAILABLE in the account books", async () => {
  await withApp({ view: VIEW, fakeReads: { "GET /api/dashboard": canned((d) => known(d)) } }, async (app) => {
    await openKnown(app);
    const books = app.page.locator("section.data-card", { has: app.page.locator(".table-toolbar span", { hasText: /^OPTION ACCOUNT BOOKS$/ }) });
    const cell = (label) => books.locator(".option-ticket-grid > div", { has: app.page.locator("span", { hasText: new RegExp(`^${label}$`) }) }).locator("b");
    assert.equal((await cell("Watchlist Options").innerText()).trim(), "UNAVAILABLE");
    assert.notEqual((await cell("Mag7 Options").innerText()).trim(), "UNAVAILABLE");
  });
});

e2eTest("closing one contract sends one POST with its symbol and shows '...' while it runs", async () => {
  await withApp({
    view: VIEW,
    fakeReads: { "GET /api/dashboard": canned((d) => known(d, { positions: [POSITION] })) },
    fakeWrites: { "POST /api/close-option-position": delayed(1_200, ok({ result: { status: "submitted" } })) },
  }, async (app) => {
    await openKnown(app);
    const close = app.page.locator(`button.close-position-button[aria-label='Close ${CONTRACT}']`);
    await close.waitFor({ timeout: 30_000 });
    await close.click();
    await app.page.locator(`button.close-position-button[aria-label='Close ${CONTRACT}']`, { hasText: "..." }).waitFor({ timeout: 5_000 });
    await app.page.locator(`button.close-position-button[aria-label='Close ${CONTRACT}']`, { hasText: /^x$/ }).waitFor({ timeout: 10_000 });
    const sent = writesTo(app, "/api/close-option-position");
    assert.equal(sent.length, 1);
    assert.deepEqual(body(sent[0]), { optionSymbol: CONTRACT });
    assert.equal(app.writes.length, 1);
  });
});

e2eTest("Manage Stops Now and Close All each send exactly one write to their own route", async () => {
  await withApp({
    view: VIEW,
    fakeReads: { "GET /api/dashboard": canned((d) => known(d, { positions: [POSITION] })) },
    fakeWrites: { "POST /api/manage-option-positions": ok(), "POST /api/close-all-option-positions": ok() },
  }, async (app) => {
    await openKnown(app);
    const manage = app.page.locator("button.close-all-button", { hasText: /^Manage Stops Now$/ });
    await app.page.locator(`button.close-position-button[aria-label='Close ${CONTRACT}']`).waitFor({ timeout: 30_000 });
    await manage.click();
    await app.page.locator("button.close-all-button:enabled", { hasText: /^Manage Stops Now$/ }).waitFor({ timeout: 10_000 });
    await app.page.locator("button.close-all-button", { hasText: /^Close All Option Positions$/ }).click();
    await app.page.locator("button.close-all-button:enabled", { hasText: /^Close All Option Positions$/ }).waitFor({ timeout: 10_000 });
    assert.deepEqual(app.writes.map((w) => w.path), ["/api/manage-option-positions", "/api/close-all-option-positions"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the Log ... To Option Journal button sends one ticket for the previewed underlying", async () => {
  await withApp({
    view: VIEW,
    fakeReads: { "GET /api/dashboard": canned((d) => known(d)) },
    fakeWrites: { "POST /api/option-paper-trades": { status: 201, json: {} } },
  }, async (app) => {
    await openKnown(app);
    const log = app.page.locator("button.config-save-button", { hasText: /^Log .+ To Option Journal$/ });
    await log.waitFor({ timeout: 30_000 });
    assert.equal(await log.isEnabled(), true);
    const label = (await log.innerText()).trim().replace(/^Log /, "").replace(/ To Option Journal$/, "");
    await log.click();
    await app.page.locator("button.config-save-button:enabled", { hasText: /^Log .+ To Option Journal$/ }).waitFor({ timeout: 10_000 });
    const sent = writesTo(app, "/api/option-paper-trades");
    assert.equal(sent.length, 1);
    const ticket = body(sent[0]);
    assert.match(ticket.optionSymbol, /^[A-Z.]+ CALL \| Next Weekly \| .+$/);
    assert.equal(ticket.optionSymbol, label);
    assert.equal(ticket.underlyingSymbol, ticket.optionSymbol.split(" ")[0]);
    assert.equal(ticket.quantity, 1);
    assert.equal(ticket.notes, "Submitted from option ticket preview while Auto Mode is selected.");
  });
});

e2eTest("on a phone the three settings cards stack inside the screen", async () => {
  await withApp({ view: VIEW, viewport: PHONE, fakeReads: { "GET /api/dashboard": canned((d) => known(d)) } }, async (app) => {
    await openKnown(app);
    const cards = app.page.locator("section.option-layout-grid.option-layout-grid-three > section.data-card");
    assert.equal(await cards.count(), 3);
    let previousBottom = -Infinity;
    for (const card of await cards.all()) {
      await card.scrollIntoViewIfNeeded();
      const box = await card.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `card at ${JSON.stringify(box)} is cut off`);
      const top = box.y + (await app.page.evaluate(() => window.scrollY));
      assert.ok(top >= previousBottom - 1, "cards overlap instead of stacking");
      previousBottom = top + box.height;
    }
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
