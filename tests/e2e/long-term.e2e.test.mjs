// Long Term page (Buffett / Rule #1 value screen), end to end (2026-11-06).
// Every read is answered with a fixture (fakeReads); nothing reaches the
// backend. Desktop: tiles, tile filter, search, the Why drawer, the 30% MOS
// switch. Phone: cards, no sideways scroll, tap opens the drawer. Fail-safe:
// a row with no price time is shown as "No price" and never counts as BUY.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const VIEW = "Long Term";

function row(over) {
  return {
    symbol: "AAPL", name: "Apple Inc", signal: "BUY", leaps: true, price: 100, quoteTimeMs: Date.now(), refPrice: 100,
    buyBelow: 120, leapsBelow: 160, buyZonePct: 0.167, safetyBuy: 120, sticker: 200, buffett: 240, fairValue: 200,
    payback: 7.5, roic: 0.55, roe: 1.1, financial: false, big5: 5, big5Na: 0, quality: "HIGH", pe: 11.1,
    debtToEquity: 0.7, salesGrowth: 0.12, epsGrowth: 0.15, fcfGrowth: 0.11, equityGrowth: 0.2, marketCap: 1.5e12,
    filedEnd: "2026-09-26", years: 10, currency: "USD", young: false, reason: "", ...over,
  };
}

const rows = () => [
  row({}),
  row({
    symbol: "NVDA", name: "Nvidia Corp", signal: "WAIT", leaps: false, price: 198, refPrice: 198, buyBelow: 68, leapsBelow: 108,
    buyZonePct: -1.9, safetyBuy: 68, reason: "price above safety buy",
  }),
  row({
    symbol: "HD", name: "Home Depot", signal: "AVOID", leaps: false, price: 356, refPrice: 356, buyBelow: null, leapsBelow: null,
    buyZonePct: null, safetyBuy: null, big5: 2, reason: "debt 3.6× FCF",
  }),
];

// The page answers for whatever MOS the browser asked for (meta.mos must match).
const page = (list = rows) => (read) => {
  const mos = Number(new URLSearchParams(read.search).get("mos") || 50);
  return { json: {
    meta: {
      universe: 5, scored: 3, etfs: ["SPY"], noFilings: ["EA"], refreshedAt: "2026-11-06T03:12:00-05:00",
      refreshing: null, deferredUntil: null, mos,
    },
    rows: list(),
  } };
};

const detail = (symbol, name) => ({ json: {
  symbol, name,
  checks: [
    { key: "roic_now", label: "ROIC now", value: 0.55, threshold: "≥ 15%", passed: true, detail: "NI TTM / (equity + debt)" },
    { key: "debt", label: "Debt payable", value: 0.7, threshold: "≤ 3× FCF", passed: true, detail: "0.7× FCF" },
  ],
  years: [
    { end: "2025-09-27", revenue: 416e9, netIncome: 112e9, eps: 7.46, equity: 74e9, fcf: 108e9, roic: 0.62 },
    { end: "2026-09-26", revenue: 479e9, netIncome: 132e9, eps: 8.98, equity: 112e9, fcf: 136e9, roic: 0.69 },
  ],
  unit: "USD",
  explain: [
    "Sticker EPS $8.98 growing 15%/yr for 10 years → $36.3",
    "Historical P/E median 27 → future P/E 27",
    "Discounted 15%/yr → sticker $242",
    "Safety Buy $121 at 50% margin",
  ],
  crossChecks: [{ label: "ROIC vs Finnhub", ok: true, detail: "69% vs 70%" }],
  sources: [{ label: "EDGAR CIK", value: "320193" }],
  listingFactor: 1,
} });

const reads = (list) => ({
  "GET /api/long-term": page(list),
  "GET /api/long-term/AAPL": () => detail("AAPL", "Apple Inc"),
  "GET /api/long-term/NVDA": () => detail("NVDA", "Nvidia Corp"),
});

const count = (p, sym) => p.getByTestId(`long-term-row-${sym}`).count();
const tileCount = async (p, tile) => (await p.getByTestId(`long-term-tile-${tile}`).locator("b").innerText()).trim();
const ready = (p) => p.getByTestId("long-term-row-AAPL").waitFor({ timeout: 90_000 });
const rowCount = (n) => document.querySelectorAll("[data-testid^='long-term-row-']").length === n;

e2eTest("the tiles count BUY, LEAPS, WAIT and AVOID, and a tile or the search narrows the table", async () => {
  await withApp({ view: VIEW, fakeReads: reads() }, async (app) => {
    const p = app.page;
    await ready(p);
    assert.deepEqual(
      [await tileCount(p, "BUY"), await tileCount(p, "LEAPS"), await tileCount(p, "WAIT"), await tileCount(p, "AVOID"), await tileCount(p, "NODATA")],
      ["1", "1", "1", "1", "0"],
    );
    assert.equal(await count(p, "AAPL") + await count(p, "NVDA") + await count(p, "HD"), 3);
    await p.getByTestId("long-term-tile-BUY").click();
    await p.waitForFunction(rowCount, 1);
    assert.equal(await count(p, "AAPL"), 1);
    await p.getByTestId("long-term-tile-BUY").click(); // a second click clears the tile
    await p.waitForFunction(rowCount, 3);
    await p.getByTestId("long-term-search").fill("NV");
    await p.waitForFunction(rowCount, 1);
    assert.equal(await count(p, "NVDA"), 1);
    assert.match(await p.getByTestId("long-term-footer").innerText(), /SPY/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("clicking a row opens the Why drawer with the valuation in words", async () => {
  await withApp({ view: VIEW, fakeReads: reads() }, async (app) => {
    const p = app.page;
    await ready(p);
    await p.getByTestId("long-term-row-AAPL").click();
    const drawer = p.getByTestId("long-term-drawer");
    await drawer.waitFor({ timeout: 10_000 });
    await drawer.getByText(/Sticker/).first().waitFor({ timeout: 10_000 });
    assert.match(await drawer.innerText(), /Sticker/);
    assert.ok(app.reads.some((r) => r.path === "/api/long-term/AAPL"));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the 30% safety switch re-reads the page with mos=30 and still shows rows", async () => {
  await withApp({ view: VIEW, fakeReads: reads() }, async (app) => {
    const p = app.page;
    await ready(p);
    assert.ok(app.reads.some((r) => r.path === "/api/long-term" && /mos=50/.test(r.search)));
    await p.getByTestId("long-term-mos-30").click();
    await p.waitForFunction(() => document.querySelector("[data-testid='long-term-mos-30']")?.getAttribute("aria-pressed") === "true");
    await ready(p);
    assert.ok(app.reads.some((r) => r.path === "/api/long-term" && /mos=30/.test(r.search)));
    assert.ok(await count(p, "AAPL") >= 1);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone each ticker is a card, nothing scrolls sideways, and a tap opens the drawer", async () => {
  await withApp({ view: VIEW, viewport: PHONE, fakeReads: reads() }, async (app) => {
    const p = app.page;
    const card = p.getByTestId("long-term-card-AAPL");
    await card.waitFor({ timeout: 90_000 });
    assert.equal(await card.isVisible(), true);
    assert.equal(await card.locator(".long-term-pill.is-buy").first().isVisible(), true, "the BUY pill shows");
    assert.equal(await overflowsSideways(p), false);
    await card.click();
    const drawer = p.getByTestId("long-term-drawer");
    await drawer.waitFor({ timeout: 10_000 });
    await drawer.getByText(/Sticker/).first().waitFor({ timeout: 10_000 });
    assert.equal(await overflowsSideways(p), false);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a row with no price time shows No price and never counts as BUY", async () => {
  const list = () => [row({ quoteTimeMs: null })];
  await withApp({ view: VIEW, fakeReads: reads(list) }, async (app) => {
    const p = app.page;
    await ready(p);
    assert.match(await p.getByTestId("long-term-row-AAPL").innerText(), /No price/);
    assert.equal(await tileCount(p, "BUY"), "0");
    assert.equal(await tileCount(p, "LEAPS"), "0");
    assert.equal(await tileCount(p, "NODATA"), "1");
    assert.deepEqual(app.pageErrors, []);
  });
});
