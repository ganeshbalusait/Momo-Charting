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

// ---- LEAP to buy (2026-11-06) ------------------------------------------------
// The LEAPS row (AAPL) also reads /api/long-term/AAPL/leap; the WAIT and AVOID
// rows never do. The fixture is the backend's payload shape, camelCase.

const CONTRACT = {
  symbol: "AAPL  280121C00080000", expiry: "2028-01-21", dte: 441, strike: 80, bid: 31.9, ask: 32.4, mid: 32.15,
  delta: 0.76, openInterest: 1234, volume: 56, breakeven: 112.4, breakevenPct: 0.124, intrinsic: 20, extrinsic: 12.4,
  cost: 3240, wideSpread: false,
};
const leap = (over = {}) => ({ json: {
  symbol: "AAPL", name: "Apple Inc", leaps: true, price: 100, leapsBelow: 160, contract: CONTRACT, reason: "",
  expiries: ["2027-11-19", "2028-01-21"], fetchedAt: "2026-11-06T14:00:00Z", refreshedAt: "2026-11-06T03:12:00-05:00", ...over,
} });
const leapReads = (answer = () => leap()) => ({ ...reads(), "GET /api/long-term/AAPL/leap": answer });
const LINE = /Jan 2028 \$80 C · ask \$32\.40 · BE \$112 \(\+12%\)/;
const leapPaths = (app) => app.reads.filter((r) => /\/leap$/.test(r.path)).map((r) => r.path);
const leapText = (p, sym, re) => p.waitForFunction(
  ([s, src]) => new RegExp(src).test(document.querySelector(`[data-testid='long-term-leap-${s}']`)?.textContent || ""),
  [sym, re.source],
  { timeout: 15_000 },
);

e2eTest("the LEAPS row shows the contract to buy, the drawer explains it, and no other row asks for one", async () => {
  await withApp({ view: VIEW, fakeReads: leapReads() }, async (app) => {
    const p = app.page;
    await ready(p);
    await leapText(p, "AAPL", /Jan 2028/);
    assert.match(await p.getByTestId("long-term-leap-AAPL").innerText(), LINE);
    assert.equal(await p.getByTestId("long-term-leap-NVDA").count(), 0, "a WAIT row carries no contract line");
    assert.equal(await p.getByTestId("long-term-leap-HD").count(), 0, "an AVOID row carries no contract line");
    await p.getByTestId("long-term-row-AAPL").click();
    const section = p.getByTestId("long-term-drawer-leap");
    await section.waitFor({ timeout: 10_000 });
    const text = await section.innerText();
    assert.match(text, /LEAP to buy/i); // innerText carries the CSS uppercase
    assert.match(text, /Jan 21, 2028 \(441 days\) · \$80 call · delta 0\.76/);
    assert.match(text, /Ask \$32\.40 · bid \$31\.90 · mid \$32\.15 · \$3,240 per contract/);
    assert.match(text, /Breakeven \$112\.40 · \+12% from \$100/);
    assert.match(text, /In the money \$20\.00 · time value \$12\.40/);
    assert.match(text, /Open interest 1,234 · volume 56/);
    assert.match(text, /under the LEAPS level \$160\.00/);
    assert.deepEqual(leapPaths(app), ["/api/long-term/AAPL/leap"], "only the flagged row reads the chain, once");
    assert.ok(app.reads.some((r) => r.path === "/api/long-term/AAPL/leap" && /mos=50/.test(r.search)));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the LEAPS card carries the contract line and nothing scrolls sideways", async () => {
  await withApp({ view: VIEW, viewport: PHONE, fakeReads: leapReads() }, async (app) => {
    const p = app.page;
    const card = p.getByTestId("long-term-card-AAPL");
    await card.waitFor({ timeout: 90_000 });
    await leapText(p, "AAPL", /Jan 2028/);
    assert.match(await card.getByTestId("long-term-leap-AAPL").innerText(), LINE);
    assert.equal(await p.getByTestId("long-term-leap-NVDA").count(), 0);
    assert.equal(await overflowsSideways(p), false);
    await card.click();
    const section = p.getByTestId("long-term-drawer-leap");
    await section.waitFor({ timeout: 10_000 });
    assert.match(await section.innerText(), /Breakeven \$112\.40/);
    assert.equal(await overflowsSideways(p), false);
    assert.deepEqual(leapPaths(app), ["/api/long-term/AAPL/leap"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("fail-safe: a leap answer with no contract says why, never a price, and the WAIT row is never asked", async () => {
  const reason = "no in-the-money call with a live quote";
  await withApp({ view: VIEW, fakeReads: leapReads(() => leap({ contract: null, reason })) }, async (app) => {
    const p = app.page;
    await ready(p);
    await leapText(p, "AAPL", /No contract/);
    const line = (await p.getByTestId("long-term-leap-AAPL").innerText()).trim();
    assert.equal(line, `No contract: ${reason}`);
    await p.getByTestId("long-term-row-AAPL").click();
    const section = p.getByTestId("long-term-drawer-leap");
    await section.waitFor({ timeout: 10_000 });
    const lines = (await section.locator(".long-term-lines").innerText()).trim();
    assert.equal(lines, `No contract: ${reason}`);
    assert.ok(!/\$/.test(lines) && !/\$/.test(line), "no price is shown without a contract");
    assert.deepEqual(leapPaths(app), ["/api/long-term/AAPL/leap"], "NVDA (WAIT) and HD (AVOID) never read /leap");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a 'chain unavailable' answer is not final: it says why, keeps a Retry, and the retry shows the contract", async () => {
  // Schwab blocked at the first read (the server answers 200 + contract null and does not
  // cache it); the page must ask again instead of holding the miss until the nightly refresh.
  let calls = 0;
  const answer = () => (calls++ === 0 ? leap({ contract: null, reason: "chain unavailable" }) : leap());
  await withApp({ view: VIEW, fakeReads: leapReads(answer) }, async (app) => {
    const p = app.page;
    await ready(p);
    await leapText(p, "AAPL", /No contract: chain unavailable/);
    const line = p.getByTestId("long-term-leap-AAPL");
    assert.ok(!/\$/.test(await line.innerText()), "no price without a contract");
    assert.equal(await line.getByText("Retry").count(), 1, "a transient miss carries a Retry");
    await p.getByTestId("long-term-row-AAPL").click();
    const section = p.getByTestId("long-term-drawer-leap");
    await section.waitFor({ timeout: 10_000 });
    assert.match(await section.innerText(), /No contract: chain unavailable/);
    assert.match(await section.innerText(), /retries on its own/);
    await line.getByText("Retry").click();
    await leapText(p, "AAPL", /Jan 2028/);
    assert.match(await line.innerText(), LINE);
    assert.equal(await line.getByText("Retry").count(), 0, "a contract carries no Retry");
    assert.match(await section.innerText(), /Breakeven \$112\.40/);
    assert.deepEqual(leapPaths(app), ["/api/long-term/AAPL/leap", "/api/long-term/AAPL/leap"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a final 'no contract' verdict carries no Retry and is read once", async () => {
  const reason = "no expiry 12-25 months out";
  await withApp({ view: VIEW, fakeReads: leapReads(() => leap({ contract: null, reason })) }, async (app) => {
    const p = app.page;
    await ready(p);
    await leapText(p, "AAPL", /No contract/);
    const line = p.getByTestId("long-term-leap-AAPL");
    assert.equal((await line.innerText()).trim(), `No contract: ${reason}`);
    assert.equal(await line.getByText("Retry").count(), 0);
    assert.deepEqual(leapPaths(app), ["/api/long-term/AAPL/leap"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("flagged rows past the first 12 offer 'Find contract' instead of 'finding…', and the link reads the chain", async () => {
  // 15 LEAPS rows: T01..T12 are read on their own; T13..T15 wait for the trader.
  const syms = Array.from({ length: 15 }, (_, i) => `T${String(i + 1).padStart(2, "0")}`);
  const many = () => syms.map((s, i) => row({ symbol: s, name: `${s} Corp`, signal: "WAIT", buyZonePct: -0.01 * (i + 1), reason: "price above safety buy" }));
  const fakes = { ...reads(many) };
  for (const s of syms) fakes[`GET /api/long-term/${s}/leap`] = () => leap({ symbol: s, name: `${s} Corp` });
  await withApp({ view: VIEW, fakeReads: fakes }, async (app) => {
    const p = app.page;
    await p.getByTestId("long-term-row-T01").waitFor({ timeout: 90_000 });
    await leapText(p, "T12", /Jan 2028/);
    await leapText(p, "T13", /Find contract/);
    for (const s of ["T13", "T14", "T15"]) {
      const text = (await p.getByTestId(`long-term-leap-${s}`).innerText()).trim();
      assert.equal(text, "Find contract", `${s} is past the cap`);
    }
    assert.equal(leapPaths(app).length, 12, "only the first 12 are read on their own");
    assert.ok(!leapPaths(app).some((path) => /T1[345]/.test(path)));
    await p.getByTestId("long-term-leap-T14").getByText("Find contract").click();
    await leapText(p, "T14", /Jan 2028/);
    assert.match(await p.getByTestId("long-term-leap-T14").innerText(), LINE);
    assert.equal(await p.getByTestId("long-term-drawer").count(), 0, "the link did not open the row");
    assert.ok(leapPaths(app).includes("/api/long-term/T14/leap"));
    assert.equal(leapPaths(app).length, 13);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a failed leap read shows 'contract not available' with a Retry that reads again", async () => {
  let calls = 0;
  const answer = () => (calls++ === 0 ? { status: 503, json: { error: "chain down" } } : leap());
  await withApp({ view: VIEW, fakeReads: leapReads(answer) }, async (app) => {
    const p = app.page;
    await ready(p);
    await leapText(p, "AAPL", /contract not available/);
    const line = p.getByTestId("long-term-leap-AAPL");
    assert.ok(!/\$/.test(await line.innerText()));
    await line.getByText("Retry").click();
    await leapText(p, "AAPL", /Jan 2028/);
    assert.match(await line.innerText(), LINE);
    assert.equal(await p.getByTestId("long-term-drawer").count(), 0, "Retry did not open the row");
    assert.deepEqual(leapPaths(app), ["/api/long-term/AAPL/leap", "/api/long-term/AAPL/leap"]);
    assert.deepEqual(app.pageErrors, []);
  });
});
