// Premarket page, end to end: the four overview cards (MATCHES / STRONG /
// LIVE TAPES / FEED), the table or its empty sentence, a ticker opening its
// 5-minute chart, and the 5-second poll that must stop when he leaves the page.
// The scanner answer is a fixture so each card has a known value; the FEED card
// is checked for all three of its states.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const VIEW = "[data-testid='premarket-scanner-view']";
const CARDS = `${VIEW} .scanner-overview-grid[aria-label='Premarket scanner overview'] .scanner-overview-card`;
const PILLS = "[data-testid='premarket-scanner-symbol']";

function row(symbol, changePct, strength, score) {
  return {
    symbol, changePct, strength, score, lastPrice: 100 + changePct, signalAt: "2026-11-03T07:00:00-05:00",
    latestSignalAt: "2026-11-03T07:00:00-05:00", firstSeenAt: "2026-11-03T07:02:11-05:00", signals48: [], signals920: [],
    signalsCyanHigher: ["CALLD"], signalsYellowHigher: [], signalsMacdHigher: [], higherSignalTimes: { CALLD: "2026-11-03T05:00:00-05:00" },
    fires: [], fireDates: {}, forming: false, catalyst: null, tapeNewestAt: "2026-11-03T07:25:00-05:00", tapeAgeSeconds: 300, tapeCoversWindow: true,
  };
}

function scanner({ rows = [row("NVDA", 8, "STRONG", 4), row("AAPL", 5, "MODERATE", 2)], gapNote = "", message } = {}) {
  return {
    json: {
      status: "READY", date: "2026-11-03", timezone: "America/New_York", windowLabel: "12:00 AM - 9:30 AM ET",
      symbols: ["AAPL", "AMZN", "AVGO", "GOOGL", "TSLA", "META", "MSFT", "NVDA", "NFLX"], rows, matchCount: rows.length,
      readySymbols: ["AAPL", "AMZN", "AVGO", "GOOGL", "MSFT", "NVDA", "NFLX"], pendingSymbols: ["META"], staleSymbols: ["TSLA"],
      laggingSymbols: [], premarketGapNote: gapNote, premarketGapState: "", generatedAt: "2026-11-03T07:30:00-05:00",
      message: message ?? `${rows.length} of 7 live tickers match in this premarket window. No data yet today for TSLA.`,
    },
  };
}

async function cards(page) {
  await page.locator(CARDS).nth(3).waitFor({ timeout: 90_000 });
  const out = {};
  for (const card of await page.locator(CARDS).all()) {
    const label = (await card.locator(".scanner-overview-heading b").innerText()).trim();
    out[label] = { value: (await card.locator("strong").innerText()).trim(), note: (await card.locator("small").innerText()).trim(), cls: await card.getAttribute("class") };
  }
  return out;
}

e2eTest("the four cards read the scanner's answer, and FEED says LIVE, GAP or BACKUP in the matching tone", async () => {
  const feeds = [
    { gapNote: "", value: "LIVE", tone: "tone-green" },
    { gapNote: "Tradier returned nothing for 04:00-07:00 ET (401 token not approved)", value: "GAP", tone: "tone-hot" },
    { gapNote: "04:00-07:00 ET filled from the Alpaca backup (SIP, 15 min delayed)", value: "BACKUP", tone: "tone-amber" },
  ];
  for (const feed of feeds) {
    await withApp({ view: "Premarket Scanner", fakeReads: { "GET /api/premarket-scanner": scanner({ gapNote: feed.gapNote }) } }, async (app) => {
      const shown = await cards(app.page);
      assert.deepEqual(Object.keys(shown), ["MATCHES", "STRONG", "LIVE TAPES", "FEED"]);
      assert.equal(shown.MATCHES.value, "2");
      assert.match(shown.MATCHES.cls, /\btone-cyan\b/);
      assert.equal(shown.STRONG.value, "1");
      assert.equal(shown["LIVE TAPES"].value, "7/9");
      assert.match(shown["LIVE TAPES"].note, /No data yet today: TSLA/);
      assert.match(shown["LIVE TAPES"].cls, /\btone-hot\b/);
      assert.equal(shown.FEED.value, feed.value);
      assert.match(shown.FEED.cls, new RegExp(`\\b${feed.tone}\\b`));
      if (feed.value === "GAP") assert.match(shown.FEED.note, /No data 04:00-07:00 ET/);
      assert.deepEqual(app.pageErrors, []);
    });
  }
});

e2eTest("the table lists the rows in the scanner's %Chg order, or says why it is empty", async () => {
  await withApp({ view: "Premarket Scanner", fakeReads: { "GET /api/premarket-scanner": scanner() } }, async (app) => {
    const { page } = app;
    await page.locator(PILLS).nth(1).waitFor({ timeout: 90_000 });
    assert.deepEqual((await page.locator(PILLS).allInnerTexts()).map((t) => t.trim()), ["NVDA", "AAPL"]);
    assert.equal(await page.locator(`${PILLS}[aria-label='Open AAPL on the 5-minute chart']`).count(), 1);
    assert.match(await page.locator(".premarket-scanner-card .table-toolbar").first().innerText(), /PREMARKET SCANNER/);
    assert.deepEqual(app.pageErrors, []);
  });
  const quiet = "0 of 7 live tickers match in this premarket window.";
  await withApp({ view: "Premarket Scanner", fakeReads: { "GET /api/premarket-scanner": scanner({ rows: [], message: quiet }) } }, async (app) => {
    const { page } = app;
    const empty = page.locator(".premarket-scanner-card .no-setups");
    await empty.waitFor({ timeout: 90_000 });
    assert.equal((await empty.innerText()).trim(), quiet);
    assert.equal(await page.locator(PILLS).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a ticker pill opens Charts & OI", async () => {
  await withApp({ view: "Premarket Scanner", fakeReads: { "GET /api/premarket-scanner": scanner({ rows: [row("AAPL", 5, "STRONG", 4)] }) } }, async (app) => {
    const { page } = app;
    const pill = page.locator(`${PILLS}[aria-label='Open AAPL on the 5-minute chart']`);
    await pill.waitFor({ timeout: 90_000 });
    await pill.click();
    await page.locator("button.nav-item[title='Charts & OI'][aria-current='page']").waitFor({ timeout: 15_000 });
    assert.equal(await page.locator(VIEW).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("inside its window the page polls every 5 s, and stops when he switches page", async () => {
  await withApp({ view: "Premarket Scanner", fakeReads: { "GET /api/premarket-scanner": scanner() } }, async (app) => {
    const { page } = app;
    // A weekday 07:30 ET: the scanner's own window, where the poll is 5 s.
    await page.clock.setFixedTime(new Date("2026-11-03T12:30:00Z"));
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.locator(CARDS).first().waitFor({ timeout: 90_000 });
    const polls = () => app.reads.filter((read) => read.path === "/api/premarket-scanner").length;
    const start = polls();
    await page.waitForTimeout(11_500);
    assert.ok(polls() - start >= 2, `only ${polls() - start} polls in 11.5 s`);
    await page.locator("button.nav-item[title='MomX Scanner']").click();
    await page.locator("[data-testid='momx-scanner-view']").waitFor({ timeout: 30_000 });
    const left = polls();
    await page.waitForTimeout(11_000);
    assert.equal(polls(), left, "no premarket poll after leaving the page");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the Premarket page fits without sideways scroll", async () => {
  await withApp({ view: "Premarket Scanner", viewport: PHONE, fakeReads: { "GET /api/premarket-scanner": scanner() } }, async (app) => {
    const { page } = app;
    await page.locator(CARDS).nth(3).waitFor({ timeout: 90_000 });
    assert.equal(await overflowsSideways(page), false);
    for (const card of await page.locator(CARDS).all()) {
      const box = await card.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `card at ${JSON.stringify(box)} is cut off`);
    }
    assert.equal(await page.locator("[data-testid='mobile-primary-premarket-scanner']").count(), 1);
    assert.deepEqual(app.pageErrors, []);
  });
});
