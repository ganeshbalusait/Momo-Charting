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
  // COST also pays a 1% dividend (PEG 52 / (15 + 1) = 3.25) and its inventory turns 18% slower.
  row({ symbol: "COST", name: "Costco", pe: 52, debtToEquity: 1.2, dividendYield: 0.01, inventoryTurnChange: -0.18 }),
  row({ symbol: "NVDA", name: "Nvidia Corp", pe: 45, epsGrowth: 0.3 }),
  // MU: PEG 9.1 / 21 = 0.43 would read CHEAP, but the server marks it a chip maker (cyclical).
  row({ symbol: "MU", name: "Micron", pe: 9.1, epsGrowth: 0.21, cyclical: "chips" }),
  row({}),
  // Mag7 members that are not quality companies: TSLA can be scored, AMZN's earnings did not grow.
  row({ symbol: "TSLA", name: "Tesla", signal: "AVOID", quality: "LOW", pe: 30, epsGrowth: 0.3 }),
  row({ symbol: "AMZN", name: "Amazon", signal: "AVOID", quality: "LOW", pe: 30, epsGrowth: -0.05 }),
  row({ symbol: "SPY", name: "SPDR S&P 500", signal: "ETF", quality: null, pe: null, epsGrowth: null }),
];
const LISTS = { Mag7: ["AAPL", "NVDA", "TSLA", "AMZN", "SPY"], Watchlist: ["COST", "HD", "TSLA"] };
const ALL = ["AAPL 5/5", "MU 4/4", "NVDA 4/5", "COST 3/5"];

const reads = (list = rows) => ({
  "GET /api/long-term": (read) => ({ json: {
    meta: {
      universe: 7, scored: 6, etfs: ["SPY"], noFilings: [], lists: LISTS, refreshedAt: "2026-11-19T03:00:00-05:00", refreshing: null,
      deferredUntil: null, mos: Number(new URLSearchParams(read.search).get("mos") || 50),
    },
    rows: list(),
  } }),
  "GET /api/long-term/analyst": { status: 404, json: { error: "no analyst data" } },
  "GET /api/long-term/AAPL": { json: {
    symbol: "AAPL", name: "Apple Inc",
    checks: [{ key: "roic_now", label: "ROIC now", value: 0.55, threshold: "≥ 15%", passed: true, detail: "NI TTM / (equity + debt)" }],
    years: [{ end: "2026-09-26", revenue: 479e9, netIncome: 132e9, eps: 8.98, equity: 112e9, fcf: 136e9, roic: 0.69 }],
    unit: "USD", explain: ["Sticker EPS $8.98 growing 15%/yr for 10 years → $36.3"], crossChecks: [], sources: [], listingFactor: 1,
  } },
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
    await p.getByTestId("long-term-lynch-scope-Quality").click();
    assert.equal(await pressed(p, "long-term-peter"), "true");
    assert.equal(await pressed(p, "long-term-mos-50"), "false");
    assert.deepEqual(await listed(p), ALL, "most stars first; HD is not a quality company");
    assert.deepEqual(
      [await tile(p, "CHEAP"), await tile(p, "FAIR"), await tile(p, "PRICEY"), await tile(p, "CYCLICAL")],
      ["1", "1", "1", "1"],
    );
    // 2026-11-19 "add a cyclical warning label": a chip maker at PEG 0.43 is CYCLICAL, never CHEAP
    assert.equal((await p.getByTestId("long-term-lynch-verdict-MU").innerText()).trim(), "CYCLICAL");
    assert.match(await p.getByTestId("long-term-lynch-cyclical-MU").innerText(), /Cyclical \(chips\).*not a bargain sign/);
    assert.match(await p.getByTestId("long-term-lynch-sheet-MU").innerText(), /PEG 0\.43, not judged/);
    assert.equal(await p.getByTestId("long-term-lynch-cyclical-AAPL").count(), 0);
    // "fix all issue": the dividend joins the PEG, and slow inventory is a warning line
    assert.match(await p.getByTestId("long-term-lynch-sheet-COST").innerText(), /PEG 3\.25 with the 1\.0% dividend/);
    assert.match(await p.getByTestId("long-term-lynch-inventory-COST").innerText(), /18% slower than a year ago/);
    assert.equal(await p.getByTestId("long-term-lynch-inventory-AAPL").count(), 0);
    // the back-test result is one closed line (the top of the page stays short); a tap opens it
    const more = p.getByTestId("long-term-lynch-backtest");
    assert.equal(await more.evaluate((el) => el.open), false);
    assert.match(await more.locator("summary").innerText(), /CHEAP did not beat the quality group/);
    await more.locator("summary").click();
    assert.match(await more.innerText(), /CHEAP tag did NOT beat the quality group[\s\S]*counts the dividend as growth/);
    await more.locator("summary").click();
    await p.getByTestId("long-term-lynch-tile-CYCLICAL").click();
    assert.deepEqual(await listed(p), ["MU 4/4"]);
    await p.getByTestId("long-term-lynch-tile-CYCLICAL").click();
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
    await p.getByTestId("long-term-lynch-scope-Quality").click();
    assert.deepEqual(await listed(p), ALL);
    assert.equal(await overflowsSideways(p), false);
    const warn = await p.getByTestId("long-term-lynch-cyclical-MU").boundingBox();
    assert.ok(warn && warn.x >= 0 && warn.x + warn.width <= PHONE.width + 1, `cyclical warning cut off: ${JSON.stringify(warn)}`);
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

// 2026-11-19 "in each card I don't see detail when I click" + "add mag7 tickers and watchlist
// tickers separate also".
e2eTest("a tap on a Peter card opens its details, and Mag7 / Watchlist are their own groups", async () => {
  await withApp({ view: VIEW, fakeReads: reads() }, async (app) => {
    const p = app.page;
    await p.getByTestId("long-term-row-AAPL").waitFor({ timeout: 90_000 });
    await p.getByTestId("long-term-peter").click();
    await p.getByTestId("long-term-lynch-list").waitFor({ timeout: 20_000 });
    // the three groups, with how many cards each holds
    assert.deepEqual(
      await p.locator(".long-term-lynch-scope button").evaluateAll((els) => els.map((el) => el.innerText.replace(/\s+/g, " ").trim())),
      ["Peter's best 1", "Quality 4", "Mag7 4", "Watchlist 1"],
    );
    assert.equal(await pressed(p, "long-term-lynch-scope-Best"), "true", "opens on Peter's best");
    await p.getByTestId("long-term-lynch-scope-Quality").click();
    // tap the card: the Why drawer opens under it; tap again: it closes
    const card = p.getByTestId("long-term-lynch-AAPL");
    assert.equal(await card.getAttribute("aria-expanded"), "false");
    await card.click();
    const drawer = p.getByTestId("long-term-drawer");
    await drawer.waitFor({ timeout: 20_000 });
    assert.match(await drawer.innerText(), /Ten years/i);
    assert.equal(await card.getAttribute("aria-expanded"), "true");
    // 2026-11-20 "justification why ... a 10 kid can understand" + "Peter one don't mixed with
    // Warren": the details start with PETER's checklist in plain sentences, and nothing of Warren's.
    const peter = await p.getByTestId("long-term-simple-peter").innerText();
    assert.match(peter, /Peter would like this\. It is growing fast and the price is low for that growth\./);
    assert.match(peter, /You pay \$11\.10 for every \$1 it earns in a year, and it grows 15% a year\. 11\.1 ÷ 15 = 0\.74\./);
    const simple = await p.getByTestId("long-term-simple").innerText();
    assert.ok(!/\b(ROIC|EPS|PEG|FCF|P\/E)\b/.test(simple), "no trading words in the simple box");
    assert.match(simple, /This is Peter Lynch's checklist[\s\S]*does not tell you what to do with your money\./);
    assert.equal(await p.getByTestId("long-term-simple-warren").count(), 0, "no Warren side in the Peter view");
    const whole = await drawer.innerText();
    assert.ok(!/Warren|Rule #1|Safety Buy|safety buy|Sticker|LEAP/i.test(whole), `Warren's rule in the Peter details: ${whole.slice(0, 300)}`);
    assert.match(whole, /Ten years/i, "the ten-year table stays");
    assert.ok(!/Rule #1/.test(await p.getByTestId("long-term-panel").innerText()), "no Rule #1 text anywhere on the Peter page");
    // it is the first thing in the details
    assert.equal(await drawer.evaluate((el) => el.querySelector("header + *")?.getAttribute("data-testid")), "long-term-simple");
    assert.ok(app.reads.some((r) => r.path === "/api/long-term/AAPL"), "the details were read");
    assert.equal((await p.getByTestId("long-term-lynch-details-AAPL").innerText()).trim(), "Hide details");
    await p.getByTestId("long-term-lynch-details-AAPL").click();
    assert.equal(await p.getByTestId("long-term-drawer").count(), 0);
    // Mag7: every Mag7 company. TSLA is marked not quality; AMZN says why it has no sheet; the ETF is left out.
    await p.getByTestId("long-term-lynch-scope-Mag7").click();
    assert.deepEqual(await listed(p), ["AAPL 5/5", "TSLA 5/5", "NVDA 4/5"]);
    assert.equal(await p.getByTestId("long-term-lynch-lowq-TSLA").count(), 1);
    assert.equal(await p.getByTestId("long-term-lynch-lowq-AAPL").count(), 0);
    assert.match(await p.getByTestId("long-term-lynch-nosheet-AMZN").innerText(), /No star sheet: earnings did not grow/);
    assert.equal(await p.getByTestId("long-term-lynch-SPY").count(), 0);
    // Watchlist: its quality companies only (HD and TSLA are on it, but not quality)
    await p.getByTestId("long-term-lynch-scope-Watchlist").click();
    assert.deepEqual(await listed(p), ["COST 3/5"]);
    await p.getByTestId("long-term-lynch-scope-Quality").click();
    assert.deepEqual(await listed(p), ALL);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the groups fit and a tapped card shows its details without sideways scroll", async () => {
  await withApp({ view: VIEW, viewport: PHONE, fakeReads: reads() }, async (app) => {
    const p = app.page;
    await p.getByTestId("long-term-card-AAPL").waitFor({ timeout: 90_000 });
    await p.getByTestId("long-term-peter").click();
    await p.getByTestId("long-term-lynch-list").waitFor({ timeout: 20_000 });
    for (const scope of ["Best", "Quality", "Mag7", "Watchlist"]) {
      const box = await p.getByTestId(`long-term-lynch-scope-${scope}`).boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `${scope} button at ${JSON.stringify(box)}`);
    }
    await p.getByTestId("long-term-lynch-scope-Mag7").click();
    await p.getByTestId("long-term-lynch-nosheet-AMZN").waitFor({ timeout: 10_000 });
    await p.getByTestId("long-term-lynch-AAPL").click();
    const drawer = p.getByTestId("long-term-drawer");
    await drawer.waitFor({ timeout: 20_000 });
    const box = await drawer.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `drawer at ${JSON.stringify(box)}`);
    // the simple-words box: Peter's side only, nothing cut off
    const side = await p.getByTestId("long-term-simple-peter").boundingBox();
    assert.ok(side && side.x >= 0 && side.x + side.width <= PHONE.width + 1, `simple box cut off: ${JSON.stringify(side)}`);
    assert.equal(await p.getByTestId("long-term-simple-warren").count(), 0);
    assert.equal(await overflowsSideways(p), false);
    assert.deepEqual(app.pageErrors, []);
  });
});

// 2026-11-20 "Confused here which one buy?" then "Peter one don't mixed with Warren".
e2eTest("Peter opens on Peter's best: every star and CHEAP, whatever Warren's rule says", async () => {
  // ZM: full marks from Peter while Warren's rule says AVOID. It is still one of Peter's best.
  const list = () => [...rows(), row({ symbol: "ZM", name: "Zoom", signal: "AVOID", pe: 7.8, reason: "not a Rule #1 business" })];
  for (const viewport of [undefined, PHONE]) {
    await withApp({ view: VIEW, viewport, fakeReads: reads(list) }, async (app) => {
      const p = app.page;
      await p.getByTestId(viewport ? "long-term-card-AAPL" : "long-term-row-AAPL").waitFor({ timeout: 90_000 });
      await p.getByTestId("long-term-peter").click();
      await p.getByTestId("long-term-lynch-list").waitFor({ timeout: 20_000 });
      assert.equal(await pressed(p, "long-term-lynch-scope-Best"), "true", "opens on Peter's best");
      assert.equal((await p.getByTestId("long-term-lynch-scope-Best").innerText()).replace(/\s+/g, " ").trim(), "Peter's best 2");
      assert.deepEqual(await listed(p), ["ZM 5/5", "AAPL 5/5"]);
      assert.match(await p.getByTestId("long-term-lynch-best-note").innerText(), /Peter's best:[\s\S]*not a promise the price goes up/);
      // the card carries Peter's numbers only: no Rule #1 verdict on it
      const card = await p.getByTestId("long-term-lynch-ZM").innerText();
      assert.match(card, /PEG\s*0\.52[\s\S]*P\/E\s*7\.8[\s\S]*Growth\s*15%\/yr/);
      assert.ok(!/Rule #1|AVOID|Warren/.test(await p.getByTestId("long-term-panel").innerText()), "nothing of Warren's on the Peter page");
      const group = await p.locator(".long-term-lynch-scope").boundingBox();
      assert.ok(group && group.x >= 0 && group.x + group.width <= (viewport ? PHONE.width : 4000) + 1, `group buttons at ${JSON.stringify(group)}`);
      await p.getByTestId("long-term-lynch-scope-Quality").click();
      assert.deepEqual(await listed(p), ["ZM 5/5", "AAPL 5/5", "MU 4/4", "NVDA 4/5", "COST 3/5"]);
      assert.equal(await p.getByTestId("long-term-lynch-best-note").count(), 0);
      if (viewport) assert.equal(await overflowsSideways(p), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }
});

e2eTest("the Rule #1 table's details explain Warren's checklist only", async () => {
  await withApp({ view: VIEW, fakeReads: reads() }, async (app) => {
    const p = app.page;
    await p.getByTestId("long-term-row-AAPL").waitFor({ timeout: 90_000 });
    await p.getByTestId("long-term-row-AAPL").click();
    const drawer = p.getByTestId("long-term-drawer");
    await drawer.waitFor({ timeout: 20_000 });
    const warren = await p.getByTestId("long-term-simple-warren").innerText();
    assert.match(warren, /Warren would wait\. It is a good business, but the price is too high today\./);
    assert.match(warren, /For every \$100 put into the business, it earns \$55 a year\. Warren wants \$15 or more\./);
    assert.match(warren, /Warren only buys at a big discount: \$80 or less\. Today it costs \$100, so it is not on sale yet\./);
    assert.match(warren, /What his rule says: do nothing now\. Look again if the price drops to \$80 or less\./);
    assert.equal(await p.getByTestId("long-term-simple-peter").count(), 0, "no Peter side in the Rule #1 details");
    assert.match(await p.getByTestId("long-term-simple").innerText(), /This is Warren Buffett's checklist/);
    await drawer.getByText(/ROIC now/).first().waitFor({ timeout: 20_000 }); // the filings part loads after the box
    assert.match(await drawer.innerText(), /Checklist[\s\S]*ROIC now[\s\S]*Valuation/i, "the Rule #1 checklist and valuation stay");
    assert.deepEqual(app.pageErrors, []);
  });
});
