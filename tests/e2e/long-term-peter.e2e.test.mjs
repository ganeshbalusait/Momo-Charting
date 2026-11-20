// Long Term page, Peter view (2026-11-19, "add one more next to MOS like Peter
// then show me his star sheet and tickers", quality stocks only). The Peter
// button sits after 50% MOS / 30% MOS and swaps the Rule #1 table for Peter
// Lynch's star sheet. Every read is a fixture; nothing reaches the backend.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const VIEW = "Long Term";

function row(over) {
  return {
    symbol: "AAPL", name: "Apple Inc", signal: "WAIT", leaps: false, price: 100, quoteTimeMs: Date.now(), refPrice: 100,
    buyBelow: 80, leapsBelow: 90, buyZonePct: -0.25, safetyBuy: 80, sticker: 200, buffett: 240, fairValue: 200,
    payback: 7.5, roic: 0.55, roe: 1.1, financial: false, big5: 5, big5Na: 0, quality: "HIGH", pe: 11.1,
    debtToEquity: 0.7, salesGrowth: 0.12, epsGrowth: 0.15, fcfGrowth: 0.11, equityGrowth: 0.2, marketCap: 1.5e12,
    filedEnd: "2026-09-26", years: 10, currency: "USD", young: false, reason: "price above safety buy", setup: null,
    setupPass: false, ...over,
  };
}

// AAPL: PEG 11.1 / 15 = 0.74, all five stars. NVDA: 45 / 30 = 1.5, loses the price star.
// COST: 52 / 15 = 3.47 and heavy debt, two stars lost. HD: not a quality company, never listed.
const rows = () => [
  row({ symbol: "HD", name: "Home Depot", signal: "AVOID", quality: "LOW", reason: "debt 3.6× FCF" }),
  row({ symbol: "COST", name: "Costco", pe: 52, debtToEquity: 1.2 }),
  row({ symbol: "NVDA", name: "Nvidia Corp", pe: 45, epsGrowth: 0.3 }),
  row({}),
];

const reads = () => ({
  "GET /api/long-term": (read) => ({ json: {
    meta: {
      universe: 4, scored: 4, etfs: [], noFilings: [], refreshedAt: "2026-11-19T03:00:00-05:00", refreshing: null,
      deferredUntil: null, mos: Number(new URLSearchParams(read.search).get("mos") || 50),
    },
    rows: rows(),
  } }),
  "GET /api/long-term/analyst": { status: 404, json: { error: "no analyst data" } },
});

const listed = (p) => p.locator("[data-testid^='long-term-lynch-stars-']")
  .evaluateAll((els) => els.map((el) => `${el.dataset.testid.replace("long-term-lynch-stars-", "")} ${el.dataset.stars}`));
const tile = async (p, t) => (await p.getByTestId(`long-term-lynch-tile-${t}`).locator("b").innerText()).trim();
const pressed = (p, id) => p.getByTestId(id).getAttribute("aria-pressed");

e2eTest("Peter, next to the MOS buttons, shows the star sheet for the quality companies only", async () => {
  await withApp({ view: VIEW, fakeReads: reads() }, async (app) => {
    const p = app.page;
    await p.getByTestId("long-term-row-AAPL").waitFor({ timeout: 90_000 });
    // third button of the same switch, right after the two MOS buttons
    assert.deepEqual(
      await p.locator(".long-term-switch button").evaluateAll((els) => els.map((el) => el.innerText.trim())),
      ["50% MOS", "30% MOS", "Peter"],
    );
    await p.getByTestId("long-term-peter").click();
    await p.getByTestId("long-term-lynch-list").waitFor({ timeout: 20_000 });
    assert.equal(await pressed(p, "long-term-peter"), "true");
    assert.equal(await pressed(p, "long-term-mos-50"), "false");
    assert.deepEqual(await listed(p), ["AAPL 5/5", "NVDA 4/5", "COST 3/5"], "most stars first; HD is not a quality company");
    assert.deepEqual([await tile(p, "CHEAP"), await tile(p, "FAIR"), await tile(p, "PRICEY")], ["1", "1", "1"]);
    assert.equal(await p.getByTestId("long-term-table").count(), 0, "the Rule #1 table is replaced");
    // the star sheet: five lines, one per star
    const sheet = p.getByTestId("long-term-lynch-sheet-AAPL");
    assert.equal(await sheet.locator("li").count(), 5);
    assert.match(await sheet.innerText(), /Earnings growth\s+\+15%\/yr[\s\S]*Debt\s+0\.70× equity[\s\S]*PEG 0\.74/);
    assert.equal((await p.getByTestId("long-term-lynch-verdict-AAPL").innerText()).trim(), "CHEAP");
    assert.equal(await p.getByTestId("long-term-lynch-sheet-COST").locator("li.is-fail").count(), 2);
    assert.match(await p.getByTestId("long-term-lynch-legend").innerText(), /star sheet/i);
    // a tile and the search narrow the list
    await p.getByTestId("long-term-lynch-tile-PRICEY").click();
    assert.deepEqual(await listed(p), ["COST 3/5"]);
    await p.getByTestId("long-term-lynch-tile-PRICEY").click();
    await p.getByTestId("long-term-search").fill("nvid");
    assert.deepEqual(await listed(p), ["NVDA 4/5"]);
    await p.getByTestId("long-term-search").fill("");
    // a MOS button goes back to the Rule #1 table
    await p.getByTestId("long-term-mos-50").click();
    await p.getByTestId("long-term-row-HD").waitFor({ timeout: 20_000 });
    assert.equal(await p.getByTestId("long-term-lynch-list").count(), 0);
    assert.equal(await pressed(p, "long-term-peter"), "false");
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the Peter list fits the screen and the footer stays under it", async () => {
  await withApp({ view: VIEW, viewport: PHONE, fakeReads: reads() }, async (app) => {
    const p = app.page;
    await p.getByTestId("long-term-card-AAPL").waitFor({ timeout: 90_000 });
    const button = p.getByTestId("long-term-peter");
    const box = await button.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `Peter button at ${JSON.stringify(box)}`);
    await button.click();
    await p.getByTestId("long-term-lynch-list").waitFor({ timeout: 20_000 });
    assert.deepEqual(await listed(p), ["AAPL 5/5", "NVDA 4/5", "COST 3/5"]);
    assert.equal(await overflowsSideways(p), false);
    const edges = await p.evaluate(() => {
      const list = document.querySelector("[data-testid='long-term-lynch-list']").getBoundingClientRect();
      const footer = document.querySelector("[data-testid='long-term-footer']").getBoundingClientRect();
      const card = document.querySelector("[data-testid='long-term-lynch-AAPL']").getBoundingClientRect();
      return { listBottom: list.bottom, footerTop: footer.top, cardLeft: card.left, cardRight: card.right };
    });
    assert.ok(edges.footerTop >= edges.listBottom - 1, `footer over the cards: ${JSON.stringify(edges)}`);
    assert.ok(edges.cardLeft >= 0 && edges.cardRight <= PHONE.width + 1, `card cut off: ${JSON.stringify(edges)}`);
    assert.deepEqual(app.pageErrors, []);
  });
});
