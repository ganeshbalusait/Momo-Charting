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
    // 2026-11-20 "Nvda what is cyclical?": the card explains the word in plain sentences
    const cyclical = await p.getByTestId("long-term-lynch-cyclical-MU").innerText();
    assert.match(cyclical, /Cyclical means good years and bad years take turns: a chip maker earns a lot in a good year and far less in a bad one\./);
    assert.match(cyclical, /so here a low price is not a reason to buy\./);
    assert.ok(!/PEG|P\/E|boom|slump/.test(cyclical), "no trading words in the explanation");
    // the explanation sits right under the answer, above the tag it explains
    const order = await p.evaluate(() => {
      const top = (id) => document.querySelector(`[data-testid='${id}']`).getBoundingClientRect().top;
      return [top("long-term-lynch-cyclical-MU"), top("long-term-lynch-verdict-MU")];
    });
    assert.ok(order[0] < order[1], `the meaning of cyclical comes before the CYCLICAL tag: ${order}`);
    assert.match(await p.getByTestId("long-term-lynch-sheet-MU").innerText(), /0\.43, not used for this kind of business/);
    assert.equal(await p.getByTestId("long-term-lynch-cyclical-AAPL").count(), 0);
    // "fix all issue": the dividend joins the PEG, and slow inventory is a warning line
    assert.match(await p.getByTestId("long-term-lynch-sheet-COST").innerText(), /3\.25 with its 1\.0% yearly payout \(1 or less: at or under his fair price\)/);
    assert.match(await p.getByTestId("long-term-lynch-inventory-COST").innerText(), /18% slower than a year ago/);
    assert.equal(await p.getByTestId("long-term-lynch-inventory-AAPL").count(), 0);
    // the back-test result is one closed line (the top of the page stays short); a tap opens it
    const more = p.getByTestId("long-term-lynch-backtest");
    assert.equal(await more.evaluate((el) => el.open), false);
    assert.match(await more.locator("summary").innerText(), /CHEAP did not beat the quality group/);
    await more.locator("summary").click();
    assert.match(await more.innerText(), /CHEAP tag did NOT beat the quality group[\s\S]*counts the yearly payout to owners \(the dividend\) as part of the growth[\s\S]*This page's own choices, not Peter's/);
    await more.locator("summary").click();
    await p.getByTestId("long-term-lynch-tile-CYCLICAL").click();
    assert.deepEqual(await listed(p), ["MU 4/4"]);
    await p.getByTestId("long-term-lynch-tile-CYCLICAL").click();
    assert.equal(await p.getByTestId("long-term-table").count(), 0, "the Rule #1 table is replaced");
    // the star sheet: five lines, one per star
    const sheet = p.getByTestId("long-term-lynch-sheet-AAPL");
    assert.equal(await sheet.locator("li").count(), 5);
    assert.match(await sheet.innerText(), /Profit growth\s+\+15% a year[\s\S]*Borrowed\s+\$70 per \$100 of owners' money[\s\S]*0\.74 \(1 or less: at or under his fair price\)/);
    // 2026-11-20 audit: a card reads without trading shorthand
    const cards = await p.getByTestId("long-term-lynch-list").innerText();
    assert.ok(!/\bPEG\b|P\/E|× equity|\/yr|Stalwart|Earnings growth|Cash flow/.test(cards), "trading shorthand on a Peter card");
    assert.equal((await p.getByTestId("long-term-lynch-pays-AAPL").innerText()).trim(), "You pay $11.10 for every $1 it earns in a year.");
    assert.equal((await p.getByTestId("long-term-lynch-verdict-NVDA").innerText()).trim(), "ABOVE FAIR", "a score between 1 and 2 is above his fair price");
    // what the four tiles mean, and which choices are the page's own, can be opened on a phone too
    const tilesNote = p.getByTestId("long-term-lynch-tiles-note");
    await tilesNote.locator("summary").click();
    assert.match(await tilesNote.innerText(), /These four describe the price only\.[\s\S]*A CHEAP company can still fail another check/);
    await tilesNote.locator("summary").click();
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
    // the buy price and exit block stays inside the card on a phone
    const planBox = await p.getByTestId("long-term-lynch-plan-AAPL").boundingBox();
    const cardBox = await p.getByTestId("long-term-lynch-AAPL").boundingBox();
    assert.ok(planBox && cardBox && planBox.x >= cardBox.x && planBox.x + planBox.width <= cardBox.x + cardBox.width + 1,
      `buy price / exit block outside its card: ${JSON.stringify({ planBox, cardBox })}`);
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
      ["Peter would buy 1", "Quality 4", "Mag7 4", "Watchlist 1"],
    );
    assert.equal(await pressed(p, "long-term-lynch-scope-Best"), "true", "opens on Peter would buy");
    await p.getByTestId("long-term-lynch-scope-Quality").click();
    // 2026-11-20 "if Peter was there did he buy it or not? Simple question": one answer per card.
    const answer = async (sym) => [
      await p.getByTestId(`long-term-lynch-buy-${sym}`).getAttribute("data-answer"),
      (await p.getByTestId(`long-term-lynch-buy-${sym}`).innerText()).replace(/\s+/g, " ").trim(),
    ];
    assert.deepEqual(await answer("AAPL"), ["YES", "Would Peter buy it? YES Every check passes and the price is at or under his fair price for its growth."]);
    // NVDA: a good business at 45 times profit on 30% growth = 1.5; his price is $100 / 1.5 = $67
    assert.deepEqual(await answer("NVDA"), ["WAIT", "Would Peter buy it? NOT AT THIS PRICE A good business, but the price is above his fair price for its growth. His fair price works out to $66.66."]);
    assert.deepEqual(await answer("COST"), ["NO", "Would Peter buy it? NO It owes too much."]);
    assert.equal((await answer("MU"))[0], "CAREFUL");
    assert.match((await answer("MU"))[1], /ONLY WITH CARE Cyclical means good years and bad years take turns: a chip maker earns/);
    // 2026-11-20 "I need Peter which prize he will buy and I don't see exit also": on every card.
    const plan = async (sym) => [
      (await p.getByTestId(`long-term-lynch-buyprice-${sym}`).innerText()).replace(/\s+/g, " ").trim(),
      (await p.getByTestId(`long-term-lynch-exit-${sym}`).innerText()).replace(/\s+/g, " ").trim(),
    ];
    // AAPL: $100 at 11.1 times profit growing 15%: 100 x 15 / 11.1 = $135; a steady grower's exit is 30-50% above it
    assert.deepEqual(await plan("AAPL"), [
      "Today's $100.00 passes his price test. His fair price works out to $135; $67.50 or less he called a very good price. He never gave a dollar price; this is the page's sum of his rule at this year's profit.",
      "He had no set selling price, but he often sold a steady grower once it was up 30% to 50% from what he paid. From today's $100.00 that is about $130 to $150.",
    ]);
    // NVDA: 100 x 30 / 45 = $67; a fast grower: twice that
    assert.deepEqual(await plan("NVDA"), [
      "His fair price works out to $66.66; $33.33 or less he called a very good price. He never gave a dollar price; this is the page's sum of his rule at this year's profit. Today's $100.00 is above it.",
      "He would not own it at today's price. A price near $133, where the price is twice his fair level for this growth, he called very negative.",
    ]);
    assert.deepEqual(await plan("COST"), ["None. He would not buy it while it fails a check: it owes too much.", "None. He would not own it."]);
    assert.deepEqual(await plan("MU"), [
      "No set price. He bought this kind of business when its bad years were ending and its profit was low, not because the price looked low.",
      "He had no set selling price. He sold when the good years looked close to ending, for example when the price of what it sells, or its orders, start to fall. This page cannot check that for you.",
    ]);
    assert.equal(await p.locator("[data-testid^='long-term-lynch-plan-']").count(), 4, "a buy price and an exit on every card");
    // tap the card: the Why drawer opens under it; tap again: it closes
    const card = p.getByTestId("long-term-lynch-AAPL");
    assert.equal(await card.getAttribute("aria-expanded"), "false");
    await card.click();
    const drawer = p.getByTestId("long-term-drawer");
    await drawer.waitFor({ timeout: 20_000 });
    await drawer.getByText(/Ten years/i).first().waitFor({ timeout: 20_000 }); // the filings part loads after the simple box
    assert.equal(await card.getAttribute("aria-expanded"), "true");
    // 2026-11-20 "justification why ... a 10 kid can understand" + "Peter one don't mixed with
    // Warren": the details start with PETER's checklist in plain sentences, and nothing of Warren's.
    const peter = await p.getByTestId("long-term-simple-peter").innerText();
    assert.match(peter, /Would Peter buy it\? Yes\. Every check passes and the price is at or under his fair price for its growth\./);
    // "Did Peter use profit target?": AAPL grows 15% a year, a steady grower
    assert.match(await p.getByTestId("long-term-simple-peter-sell").innerText(),
      /When would he sell\? He had no set selling price, but he often sold a steady grower once it was up 30% to 50% from what he paid\. From today's \$100\.00 that is about \$130 to \$150\./);
    assert.match(peter, /Today's \$100\.00 passes his price test\. His fair price works out to \$135; \$67\.50 or less he called a very good price\./);
    assert.match(peter, /You pay \$11\.10 for every \$1 it earns in a year, and its profit grows 15% a year\. Divide the first by the second: 11\.10 ÷ 15 = 0\.74\. 1 or less means the price is at or under his fair price; half of that he called very good\./);
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
    // audit 11-20: a company off the quality list is never a YES and gets no dollar figure
    assert.equal(await p.getByTestId("long-term-lynch-buy-TSLA").getAttribute("data-answer"), "OFFLIST");
    assert.match((await p.getByTestId("long-term-lynch-buy-TSLA").innerText()).replace(/\s+/g, " "), /NOT ON THE LIST This page only scores quality companies/);
    assert.equal((await p.getByTestId("long-term-lynch-buyprice-TSLA").innerText()).trim(), "None worked out: it is not on the quality list.");
    // a card with no star sheet still has both lines ("every card")
    assert.equal((await p.getByTestId("long-term-lynch-buyprice-AMZN").innerText()).trim(), "Cannot be worked out: its profit did not grow over the ten years.");
    assert.equal((await p.getByTestId("long-term-lynch-exit-AMZN").innerText()).trim(), "None.");
    assert.equal(await p.getByTestId("long-term-lynch-lowq-AAPL").count(), 0);
    // 2026-11-20 "Why I don't see data for tsla": the card still answers, says why, and what you pay
    assert.equal((await p.getByTestId("long-term-lynch-nosheet-AMZN").innerText()).replace(/\s+/g, " ").trim(),
      "Would Peter buy it? CANNOT TELL Its profit did not grow over the ten years. You pay $30.00 for every $1 it earns in a year.");
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
e2eTest("Peter opens on Peter would buy: his YES answers, whatever Warren's rule says", async () => {
  // ZM: a YES from Peter while Warren's rule says AVOID. It is still on Peter's list.
  const list = () => [...rows(), row({ symbol: "ZM", name: "Zoom", signal: "AVOID", pe: 7.8, reason: "not a Rule #1 business" })];
  for (const viewport of [undefined, PHONE]) {
    await withApp({ view: VIEW, viewport, fakeReads: reads(list) }, async (app) => {
      const p = app.page;
      await p.getByTestId(viewport ? "long-term-card-AAPL" : "long-term-row-AAPL").waitFor({ timeout: 90_000 });
      await p.getByTestId("long-term-peter").click();
      await p.getByTestId("long-term-lynch-list").waitFor({ timeout: 20_000 });
      assert.equal(await pressed(p, "long-term-lynch-scope-Best"), "true", "opens on Peter would buy");
      assert.equal((await p.getByTestId("long-term-lynch-scope-Best").innerText()).replace(/\s+/g, " ").trim(), "Peter would buy 2");
      assert.deepEqual(await p.locator("[data-testid^='long-term-lynch-buy-']").evaluateAll((els) => els.map((el) => el.dataset.answer)), ["YES", "YES"]);
      assert.deepEqual(await listed(p), ["ZM 5/5", "AAPL 5/5"]);
      assert.match(await p.getByTestId("long-term-lynch-best-note").innerText(), /Peter would buy:[\s\S]*not a promise the price goes up/);
      // the card carries Peter's numbers only: no Rule #1 verdict on it
      const card = await p.getByTestId("long-term-lynch-ZM").innerText();
      assert.match(card, /You pay \$7\.80 for every \$1 it earns in a year\./);
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
    assert.match(warren, /Would Warren buy it\? Not yet\. It is a good business, but the price is too high today\./);
    // "profit target": this rule's only sell point is the fair price
    assert.match(await p.getByTestId("long-term-simple-warren-sell").innerText(),
      /When would he sell\? This rule sells when the price reaches the fair price, about \$200\. It has no other profit target/);
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
