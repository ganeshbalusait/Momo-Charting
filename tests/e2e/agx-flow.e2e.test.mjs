// AGX Flow (2026-11-17): the trader opens the flow page and reads the unusual
// option volume board, at desktop and phone width. The board read is a fixture,
// so Schwab is never asked.
import assert from "node:assert/strict";
import { PHONE, e2eTest, overflowsSideways, scrollsToTheEnd, withApp } from "./harness.mjs";

const BOARD = {
  asOf: "2026-11-17T10:00:00-05:00",
  tickers: [
    { ticker: "NVDA", spot: 180, callPremium: 1_500_000, putPremium: 400_000, callVolume: 6900, putVolume: 3450, unusualCount: 2, cached: true, pending: false, error: null, fetchedAt: "2026-11-17T10:00:00-05:00" },
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
  // 2026-11-17 "give me mid prize also": bid 1.90 / ask 2.10 -> mid 2.00 on the row.
  assert.match(await rows.first().innerText(), /1\.90[\s\S]*2\.00[\s\S]*2\.10|1\.90 \/ 2\.10[\s\S]*2\.00/);
  assert.match(await rows.first().innerText(), /Mid 2\.00|2\.00/);
  assert.match(await rows.nth(1).innerText(), /170 Put/);
  assert.match(await view.innerText(), /not a direction signal/);
  // 2026-11-17 "who is dominating call or put?": NVDA has $1.5M calls vs $400K puts -> calls lead 79%.
  const dom = view.locator('[data-testid="dominance"]');
  assert.match(await dom.innerText(), /Calls vs puts today, option dollars[\s\S]*Calls lead · 79%[\s\S]*Calls \$1\.5M · Puts \$400K/);
  assert.equal(await dom.getAttribute("data-leader"), "calls");
  assert.match(await view.locator(".agx-flow-chip").first().innerText(), /Calls lead · 79%/);
  // 2026-11-17 "No need spy, qqq and small stocks and etf": no SPY/QQQ button, one grey line says what was left out.
  assert.equal(await view.getByRole("button", { name: /SPY/ }).count(), 0);
  // 2026-11-17 "Only mag7 seperate and watchlist": two board buttons, no My tickers.
  assert.deepEqual(await view.getByRole("group", { name: "Board" }).getByRole("button").allInnerTexts(), ["Mag7", "Watchlist"]);
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
// watchlist (without the Mag7 names, which have their own board), and the strip shows only the 12 tickers with the most option dollars.
for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`AGX Flow Watchlist button reads the whole watchlist at ${label}`, async () => {
    const watchlist = Array.from({ length: 30 }, (_, i) => `W${String.fromCharCode(65 + (i % 26))}${i >= 26 ? "X" : ""}`);
    const many = { ...BOARD, tickers: watchlist.map((ticker, i) => ({ ...BOARD.tickers[0], ticker, callPremium: i * 1000, putPremium: 0 })) };
    const reads = { "GET /api/agx-flow": { json: many }, "GET /api/watchlist": { json: { tickers: ["NVDA", ...watchlist, "TSLA"] } } };
    await withApp({ view: "AGX Flow", ...(viewport ? { viewport } : {}), fakeReads: reads, storage: { agxFlowPrefs: JSON.stringify({ board: "watchlist" }) } }, async (app) => {
      const view = app.page.locator('[data-testid="agx-flow-view"]');
      await view.waitFor({ timeout: 60_000 });
      await view.locator('[data-testid="agx-flow-row"]').first().waitFor({ timeout: 30_000 });
      const read = app.reads.find((r) => r.path === "/api/agx-flow");
      assert.equal(decodeURIComponent(read.search), `?symbols=${watchlist.join(",")}`);   // separate boards: no Mag7 names on Watchlist
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

// 2026-11-17 "Add search ticker also": type any ticker, AGX Flow shows only that one, says plainly why when there is
// nothing (no options, ETF), and "Back to board" returns to the board.
for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  const phone = Boolean(viewport);
  e2eTest(`AGX Flow search any ticker at ${label}`, async () => {
    const pltr = {
      ...BOARD,
      tickers: [{ ...BOARD.tickers[0], ticker: "PLTR" }],
      rows: BOARD.rows.map((r) => ({ ...r, ticker: "PLTR" })),
      excluded: [],
    };
    const lidr = { ...BOARD, rows: [], excluded: [],
      tickers: [{ ticker: "LIDR", spot: null, callPremium: 0, putPremium: 0, callVolume: 0, putVolume: 0, unusualCount: 0, cached: false, pending: false, error: "Schwab has no options 0 to 45 days out for LIDR.", fetchedAt: null }] };
    const reads = {
      "GET /api/agx-flow": (read) => {
        const s = decodeURIComponent(read.search || "");
        if (s === "?symbols=PLTR") return { json: pltr };
        if (s === "?symbols=LIDR") return { json: lidr };
        if (s === "?symbols=QQQ") return { status: 400, json: { error: "AGX Flow shows stocks only, and every ticker given is an ETF." } };
        return { json: BOARD };
      },
    };
    await withApp({ view: "AGX Flow", ...(viewport ? { viewport } : {}), fakeReads: reads }, async (app) => {
      const { page } = app;
      const view = page.locator('[data-testid="agx-flow-view"]');
      const rows = view.locator('[data-testid="agx-flow-row"]');
      await rows.first().waitFor({ timeout: 60_000 });
      const box = view.getByRole("searchbox", { name: "Search any ticker" });
      const go = view.getByRole("button", { name: "Search", exact: true });
      const tap = (el) => (phone ? el.tap() : el.click());
      const searches = () => app.reads.filter((r) => r.path === "/api/agx-flow").map((r) => decodeURIComponent(r.search));
      // A ticker that is on neither board.
      await box.fill("$pltr");
      await tap(go);
      await view.getByTestId("search-showing").getByText("Showing PLTR only").waitFor({ timeout: 15_000 });
      for (let i = 0; i < 50 && !(await rows.first().innerText().catch(() => "")).includes("PLTR"); i += 1) await page.waitForTimeout(100);
      assert.ok(searches().includes("?symbols=PLTR"), searches().join("\n"));
      assert.match(await rows.first().innerText(), /PLTR[\s\S]*185 Call/);
      assert.equal(await rows.count(), 2);
      for (const name of ["Mag7", "Watchlist"]) {
        assert.equal(await view.getByRole("button", { name, exact: true }).getAttribute("aria-pressed"), "false");
      }
      // No options: the reason in words, not an empty board.
      await box.fill("lidr");
      await box.press("Enter");
      await view.getByTestId("search-note").getByText("Schwab has no options 0 to 45 days out for LIDR.").waitFor({ timeout: 15_000 });
      assert.doesNotMatch(await view.innerText(), /No unusual contracts/);
      // An ETF: the server's stocks-only answer shows.
      await box.fill("QQQ");
      await tap(go);
      await view.getByText("AGX Flow shows stocks only, and every ticker given is an ETF.").waitFor({ timeout: 15_000 });
      if (phone) assert.equal(await overflowsSideways(page), false);
      // Back to board: the Mag7 board again.
      await tap(view.getByRole("button", { name: "✕ Back to board" }));
      await view.getByRole("button", { name: "Mag7", exact: true }).and(view.locator("[aria-pressed='true']")).waitFor({ timeout: 15_000 });
      for (let i = 0; i < 50 && !(await rows.first().innerText().catch(() => "")).includes("NVDA"); i += 1) await page.waitForTimeout(100);
      assert.match(await rows.first().innerText(), /NVDA/);
      assert.match(searches().at(-1), /AAPL,MSFT,NVDA/);
      assert.equal(await view.getByTestId("search-showing").count(), 0);
      assert.equal(await view.locator(".agx-flow-status.is-error").count(), 0);
      assert.deepEqual(app.pageErrors, []);
      assert.deepEqual(app.writes, []);
    });
  });
}

// 2026-11-17 "All six": Expiry and Order buttons ask the server for that window and order, a tapped contract opens its
// facts card, and the phone alert switch saves for this account. Desktop and phone width.
for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  const phone = Boolean(viewport);
  e2eTest(`AGX Flow expiry, hot order, contract card and phone alert at ${label}`, async () => {
    const hotBoard = { ...BOARD, order: "hot", rows: [{ ...BOARD.rows[0], newVolume: 800, newPremium: 160_000 }] };
    const reads = {
      "GET /api/agx-flow": (read) => ({ json: decodeURIComponent(read.search).includes("order=hot") ? hotBoard : BOARD }),
      "GET /api/agx-flow/alert": { json: { on: false, signedIn: true, hasPhone: true, minPremium: 1_000_000, windowStart: 555, windowEnd: 930 } },
    };
    const writes = { "POST /api/agx-flow/alert": (write) => ({ json: { on: JSON.parse(write.body).on } }) };
    await withApp({ view: "AGX Flow", ...(viewport ? { viewport } : {}), fakeReads: reads, fakeWrites: writes }, async (app) => {
      const { page } = app;
      const view = page.locator('[data-testid="agx-flow-view"]');
      const rows = view.locator('[data-testid="agx-flow-row"]');
      await rows.first().waitFor({ timeout: 60_000 });
      const tap = (el) => (phone ? el.tap() : el.click());
      const searches = () => app.reads.filter((r) => r.path === "/api/agx-flow").map((r) => decodeURIComponent(r.search));
      const waitFor = async (text) => {
        for (let i = 0; i < 80 && !searches().some((s) => s.includes(text)); i += 1) await page.waitForTimeout(100);
        assert.ok(searches().some((s) => s.includes(text)), searches().join("\n"));
      };
      // Expiry: This week asks for it and the bar says so.
      await tap(view.getByRole("group", { name: "Expiry" }).getByRole("button", { name: "This week" }));
      await waitFor("expiry=week");
      await view.getByText(/Calls vs puts today, option dollars · This week/).waitFor({ timeout: 10_000 });
      // Order: Hot right now asks for it and says what it means.
      await tap(view.getByRole("group", { name: "Order" }).getByRole("button", { name: "Hot right now" }));
      await waitFor("expiry=week&order=hot");
      await view.getByTestId("hot-note").waitFor({ timeout: 10_000 });
      for (let i = 0; i < 50 && (await rows.count()) !== 1; i += 1) await page.waitForTimeout(100);
      assert.equal(await rows.count(), 1);
      assert.match(await rows.first().innerText(), /\+800/);
      // Tap a contract: the facts card, then Close.
      await tap(rows.first().locator(phone ? ".agx-flow-card-body" : "td").first());
      const sheet = page.getByTestId("contract-sheet");
      await sheet.waitFor({ timeout: 10_000 });
      const text = await sheet.innerText();
      assert.match(text, /NVDA[\s\S]*185 Call/);
      assert.match(text, /Premium today[\s\S]*\$1,200,000/);
      assert.match(text, /Volume today[\s\S]*6,000/);
      assert.match(text, /Break-even at expiry[\s\S]*187\.00/);                 // 185 + mid 2.00
      assert.match(text, /Stock move to break-even[\s\S]*\+3\.9%/);              // 187 / 180
      assert.doesNotMatch(text, /up to 31 days out/);                            // 3 days out: the Options page has it
      if (phone) assert.equal(await overflowsSideways(page), false);
      await page.keyboard.press("Escape");
      await sheet.waitFor({ state: "detached", timeout: 5_000 });
      // Phone alert switch: Off -> On, saved for this account.
      const sw = view.getByRole("switch", { name: "Phone alert for $1M+ unusual" });
      await sw.scrollIntoViewIfNeeded();
      assert.equal(await sw.getAttribute("aria-checked"), "false");
      await tap(sw);
      await view.locator('[role="switch"][aria-checked="true"]').waitFor({ timeout: 10_000 });
      const saved = app.writes.filter((w) => w.path === "/api/agx-flow/alert");
      assert.equal(saved.length, 1);
      assert.deepEqual(JSON.parse(saved[0].body), { on: true });
      // Back to defaults so the saved prefs do not leak: All / Biggest $.
      await tap(view.getByRole("group", { name: "Expiry" }).getByRole("button", { name: "All" }));
      await tap(view.getByRole("group", { name: "Order" }).getByRole("button", { name: "Biggest $" }));
      assert.deepEqual(app.pageErrors, []);
    });
  });
}
