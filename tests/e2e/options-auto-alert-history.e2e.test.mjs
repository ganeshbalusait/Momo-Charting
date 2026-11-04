// Auto Alert history by session - the calendar, the < > arrows and the
// ALERTS BY SESSION roll-up (2026-11-04 coverage), end to end in the real app.
// The live feed and the dated history route are fixtures: today has one NVDA
// confirm, an earlier session two (AAPL, SPY), and one quiet day in between.

import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";
import { expiryIn } from "./options-fixtures.mjs";

const DRAWER = '[data-testid="oi-auto-alert-drawer"]';
const FEED = `${DRAWER} .auto-alert-drawer-feed[aria-label="OI alert history"]`;
const DATE = `${FEED} input[type="date"][aria-label="Show alerts for this date"]`;
const EARLIER = `${FEED} [aria-label="Earlier session with alerts"]`;
const LATER = `${FEED} [aria-label="Later session with alerts"]`;

const TODAY = expiryIn(0);
const EARLIER_DAY = expiryIn(-6);
const QUIET_DAY = expiryIn(-3);

const confirm = (day, symbol, side, strike, hour) => ({
  id: `${symbol}:${side}:${day}:${hour}`, kind: "confirm", symbol, side, level: { strike }, price: strike + 1,
  at: `${day}T${String(hour).padStart(2, "0")}:00:00-05:00`, sessionDate: day,
  message: `${symbol} ${side} OI ${strike} CONFIRMED · 5m close ${strike + 1}`,
});
const TODAY_EVENTS = [confirm(TODAY, "NVDA", "CALL", 150, 10)];
const EARLIER_EVENTS = [confirm(EARLIER_DAY, "SPY", "PUT", 570, 11), confirm(EARLIER_DAY, "AAPL", "CALL", 230, 10)];
const summaryDay = (date, events) => ({
  date, symbolCount: new Set(events.map((event) => event.symbol)).size, alertCount: events.length,
  symbols: events.map((event) => ({ symbol: event.symbol, sides: [event.side], alerts: [{ side: event.side, strike: event.level.strike, at: event.at }] })),
});

const fakeReads = {
  "GET /api/oi-auto-alerts": { json: {
    enabled: true, includeMag7: false, mag7Symbols: [], manualSymbols: [], maxManualSymbols: 30, status: "Armed",
    message: "Waiting for the 9:15 AM ET OI-ladder build.", refreshing: false, lastError: "", pendingSymbols: [], rows: [],
    events: TODAY_EVENTS, serverTime: `${TODAY}T11:00:00-05:00`, session: { isTradingDay: true, regularHours: false },
    sessionAlerts: { days: [summaryDay(TODAY, TODAY_EVENTS), summaryDay(EARLIER_DAY, EARLIER_EVENTS)] },
  } },
  "GET /api/oi-auto-alerts/history": (read) => {
    const date = new URLSearchParams(read.search).get("date");
    const events = date === EARLIER_DAY ? EARLIER_EVENTS : [];
    return { json: { date, availableDates: [TODAY, EARLIER_DAY], symbols: [...new Set(events.map((e) => e.symbol))].sort(),
      symbolCount: new Set(events.map((e) => e.symbol)).size, alertCount: events.length, historyDays: 30, events } };
  },
  "GET /api/watchlist-quotes": { json: { rows: [] } },
};

const historyReads = (app) => app.reads.filter((read) => read.path === "/api/oi-auto-alerts/history")
  .map((read) => new URLSearchParams(read.search).get("date"));

async function ready(page) {
  await page.locator(FEED).waitFor({ state: "visible", timeout: 60_000 });
  await page.locator(`${FEED} header small`, { hasText: "1 alert · 1 ticker · today" }).waitFor({ timeout: 30_000 });
}

e2eTest("today is the default session: Later is disabled, Earlier steps to the last session with alerts", async () => {
  await withApp({ view: "Auto Alert", fakeReads }, async (app) => {
    await ready(app.page);
    assert.equal(await app.page.locator(DATE).inputValue(), TODAY);
    assert.equal(await app.page.locator(DATE).getAttribute("max"), TODAY, "no picking a date after the server's today");
    assert.equal(await app.page.locator(LATER).isDisabled(), true);
    assert.equal(historyReads(app).length, 0, "today comes from the live feed, not the history route");
    await app.page.locator(EARLIER).click();
    await app.page.locator(`${FEED} header small`, { hasText: "2 alerts · 2 tickers" }).waitFor({ timeout: 15_000 });
    assert.equal(await app.page.locator(DATE).inputValue(), EARLIER_DAY, "the arrow skips the quiet days in between");
    assert.deepEqual(historyReads(app), [EARLIER_DAY]);
    assert.deepEqual(await app.page.locator(`${FEED} > button.is-confirm b`).allInnerTexts(),
      EARLIER_EVENTS.map((event) => event.message.split(" · ")[0]));
    assert.equal(await app.page.locator(EARLIER).isDisabled(), true, "nothing older has alerts");
    await app.page.locator(LATER).click();
    await ready(app.page);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("picking a quiet date asks the server for that day and says it was quiet", async () => {
  await withApp({ view: "Auto Alert", fakeReads }, async (app) => {
    await ready(app.page);
    await app.page.locator(DATE).fill(QUIET_DAY);
    await app.page.locator(`${FEED} .auto-alert-history-empty`, { hasText: `No confirmed alerts on ${QUIET_DAY}.` }).waitFor({ timeout: 15_000 });
    assert.ok(historyReads(app).includes(QUIET_DAY));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("ALERTS BY SESSION: a date drives the history panel, a ticker opens its chart", async () => {
  await withApp({ view: "Auto Alert", fakeReads }, async (app) => {
    await ready(app.page);
    const pick = app.page.locator(`${DRAWER} .auto-alert-summary-pick`, { hasText: EARLIER_DAY });
    await pick.click();
    await app.page.locator(`${DRAWER} .auto-alert-summary-pick.is-active`, { hasText: EARLIER_DAY }).waitFor({ timeout: 5_000 });
    await app.page.locator(`${FEED} header small`, { hasText: "2 alerts · 2 tickers" }).waitFor({ timeout: 15_000 });
    assert.match(await app.page.locator(`${DRAWER} .auto-alert-summary-day`, { hasText: EARLIER_DAY }).locator("small").first().innerText(), /2 tickers · 2 alerts/);
    await app.page.locator(`${DRAWER} .auto-alert-summary-day`, { hasText: TODAY }).locator(".auto-alert-summary-symbols button", { hasText: "NVDA" }).click();
    await app.page.locator(".charts-oi-heading h2", { hasText: "NVDA" }).waitFor({ timeout: 60_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});
