// AGX Flow (2026-11-17): the trader opens the flow page and reads the unusual
// option volume board, at desktop and phone width. The board read is a fixture,
// so Schwab is never asked.
import assert from "node:assert/strict";
import { PHONE, e2eTest, overflowsSideways, scrollsToTheEnd, withApp } from "./harness.mjs";

const BOARD = {
  asOf: "2026-11-17T10:00:00-05:00",
  tickers: [
    { ticker: "NVDA", spot: 180, callPremium: 1_500_000, putPremium: 400_000, callVolume: 6900, putVolume: 3450, unusualCount: 2, cached: true, pending: false, error: null },
    { ticker: "AAPL", spot: 230, callPremium: 0, putPremium: 0, callVolume: 0, putVolume: 0, unusualCount: 0, cached: false, pending: false, error: "Schwab did not answer for AAPL (RuntimeError). Trying again in a few minutes." },
  ],
  rows: [
    { ticker: "NVDA", spot: 180, side: "CALL", expiry: "2026-11-20", days: 3, strike: 185, volume: 6000, oi: 1200, volOi: 5, last: 2.15, bid: 1.9, ask: 2.1, premium: 1_200_000, delta: 0.35, iv: 35, fill: "ask", newVolume: 800 },
    { ticker: "NVDA", spot: 180, side: "PUT", expiry: "2026-11-27", days: 10, strike: 170, volume: 3000, oi: 400, volOi: 7.5, last: 1.0, bid: 1.0, ask: 1.2, premium: 330_000, delta: -0.3, iv: null, fill: "bid", newVolume: 0 },
  ],
  excluded: [{ ticker: "SPY", reason: "ETF" }, { ticker: "QQQ", reason: "ETF" }, { ticker: "NIO", reason: "under $10" }],
  minPrice: 10,
  note: "Volume and prices from Schwab's option chain. 'Bought/Sold' is a guess from where the last trade printed vs the bid/ask. Unusual = today's volume above open interest. Facts only, not a direction signal.",
};

const fakeReads = { "GET /api/agx-flow": { json: BOARD } };

async function checkBoard(app) {
  const view = app.page.locator('[data-testid="agx-flow-view"]');
  await view.waitFor({ timeout: 60_000 });
  const rows = view.locator('[data-testid="agx-flow-row"]');
  await rows.first().waitFor({ timeout: 30_000 });
  assert.equal(await rows.count(), 2);
  assert.match(await rows.first().innerText(), /185 Call/);
  assert.match(await rows.first().innerText(), /\$1\.2M/);
  assert.match(await rows.first().innerText(), /Bought/);
  assert.match(await rows.nth(1).innerText(), /170 Put/);
  assert.match(await view.innerText(), /not a direction signal/);
  // 2026-11-17 "No need spy, qqq and small stocks and etf": no SPY/QQQ button, one grey line says what was left out.
  assert.equal(await view.getByRole("button", { name: /SPY/ }).count(), 0);
  assert.equal((await view.locator('[data-testid="board-left-out"] summary').innerText()).trim(), "Left out: 2 ETFs · 1 stock under $10");
  const read = app.reads.find((r) => r.path === "/api/agx-flow");
  assert.ok(read, "the page read /api/agx-flow");
  assert.match(decodeURIComponent(read.search), /AAPL,MSFT,NVDA/);
  // Calls only hides the put.
  await view.getByRole("button", { name: "Calls", exact: true }).click();
  await assert.doesNotReject(async () => {
    for (let i = 0; i < 50 && (await rows.count()) !== 1; i += 1) await app.page.waitForTimeout(100);
  });
  assert.equal(await rows.count(), 1);
  assert.match(await rows.first().innerText(), /185 Call/);
  // $500K hides nothing more; $1M still keeps the $1.2M call.
  await view.getByRole("button", { name: "$1M", exact: true }).click();
  assert.equal(await rows.count(), 1);
  assert.deepEqual(app.pageErrors, []);
  assert.deepEqual(app.writes, []);
}

e2eTest("AGX Flow shows the unusual flow board at desktop width", async () => {
  await withApp({ view: "AGX Flow", fakeReads }, async (app) => {
    await checkBoard(app);
    assert.equal(await app.page.locator(".agx-flow-table").count(), 1);
  });
});

e2eTest("AGX Flow shows cards and nothing runs off the side on a phone", async () => {
  await withApp({ view: "AGX Flow", viewport: PHONE, fakeReads }, async (app) => {
    await checkBoard(app);
    assert.ok((await app.page.locator(".agx-flow-card").count()) >= 1);
    assert.equal(await overflowsSideways(app.page), false);
    const box = await app.page.locator('[data-testid="agx-flow-row"]').first().boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, "the card fits the phone width");
    // A long list (60 rows) must scroll with a real wheel right to its last card.
    const long = { ...BOARD, rows: Array.from({ length: 60 }, (_, i) => ({ ...BOARD.rows[0], strike: 185 + i, premium: 5_000_000 - i })) };
    await app.page.route("**/api/agx-flow*", (route) => route.fulfill({ json: long }));
    await app.page.getByRole("button", { name: "$100K", exact: true }).click();
    await app.page.getByRole("button", { name: "Both", exact: true }).click();
    await app.page.reload();
    await app.page.locator('[data-testid="agx-flow-row"]').nth(59).waitFor({ state: "attached", timeout: 60_000 });
    const end = await scrollsToTheEnd(app.page, ".agx-flow-page", '[data-testid="agx-flow-row"]');
    assert.equal(end.moved, true, "the page did not scroll with the wheel");
    assert.ok(end.lastBottom <= end.areaBottom + 1, `last card cut off: ${JSON.stringify(end)}`);
  });
});

// 2026-11-17 "I see only mag7 tickets what about other tickets from watchlist": the Watchlist button reads the whole
// watchlist after Mag7 (no SPY/QQQ since 2026-11-17), and the strip shows only the 12 tickers with the most option dollars.
for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`AGX Flow Watchlist button reads the whole watchlist at ${label}`, async () => {
    const watchlist = Array.from({ length: 30 }, (_, i) => `W${String.fromCharCode(65 + (i % 26))}${i >= 26 ? "X" : ""}`);
    const many = { ...BOARD, tickers: watchlist.map((ticker, i) => ({ ...BOARD.tickers[0], ticker, callPremium: i * 1000, putPremium: 0 })) };
    const reads = { "GET /api/agx-flow": { json: many }, "GET /api/watchlist": { json: { tickers: watchlist } } };
    await withApp({ view: "AGX Flow", ...(viewport ? { viewport } : {}), fakeReads: reads, storage: { agxFlowPrefs: JSON.stringify({ board: "watchlist" }) } }, async (app) => {
      const view = app.page.locator('[data-testid="agx-flow-view"]');
      await view.waitFor({ timeout: 60_000 });
      await view.locator('[data-testid="agx-flow-row"]').first().waitFor({ timeout: 30_000 });
      const read = app.reads.find((r) => r.path === "/api/agx-flow");
      assert.equal(decodeURIComponent(read.search), `?symbols=AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,${watchlist.join(",")}`);
      assert.equal(await view.locator(".agx-flow-strip > *").count(), 12);
      assert.equal(await view.getByRole("button", { name: "Watchlist", exact: true }).getAttribute("aria-pressed"), "true");
      if (viewport) assert.equal(await overflowsSideways(app.page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

// 2026-11-17 "Not loading properly": every 5 minutes the board re-reads each ticker, and each card said "reading…"
// while still showing its numbers, with "Reading Schwab… (3 of 9)" on top. A ticker being re-read keeps its count;
// only a ticker with no numbers yet says "reading…", and the top line says when the board was read.
for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`AGX Flow keeps numbers on screen while a ticker is re-read, at ${label}`, async () => {
    const board = {
      ...BOARD,
      tickers: [
        { ...BOARD.tickers[0], pending: true, cached: false, fetchedAt: "2026-11-17T13:20:00-05:00" },
        { ticker: "AMD", spot: 0, callPremium: 0, putPremium: 0, callVolume: 0, putVolume: 0, unusualCount: 0, cached: false, pending: true, error: null, fetchedAt: null },
      ],
    };
    await withApp({ view: "AGX Flow", ...(viewport ? { viewport } : {}), fakeReads: { "GET /api/agx-flow": { json: board } } }, async (app) => {
      const view = app.page.locator('[data-testid="agx-flow-view"]');
      const chips = view.locator(".agx-flow-chip");
      await chips.first().waitFor({ timeout: 60_000 });
      const text = (await chips.allInnerTexts()).join("\n");
      assert.match(text, /NVDA\s*2 unusual/);
      assert.match(text, /AMD\s*reading…/);
      assert.match(await view.innerText(), /Reading Schwab… \(1 of 2\)/);
      // Once only re-reads are left, the line says when the board was read, not "Reading Schwab".
      const updating = { ...board, tickers: [board.tickers[0]] };
      await app.page.route("**/api/agx-flow*", (route) => route.fulfill({ json: updating }));
      await view.getByText(/Updated 1:20 PM ET · refreshing/).waitFor({ timeout: 20_000 });
      assert.doesNotMatch(await view.innerText(), /Reading Schwab/);
      if (viewport) assert.equal(await overflowsSideways(app.page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}
