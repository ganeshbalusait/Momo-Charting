// OI Level Scripts page - ThinkScript + TradingView Pine v6 (2026-11-04
// coverage), end to end in the real app on the options fixture chain (front
// ATM 100, call walls 110/105/108, put walls 90/95/92, expiries 2/9/16 DTE).

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { CHAIN_READS, sleep } from "./options-fixtures.mjs";

const PAGE = '[data-testid="oi-level-script-tos-view"]';
const PRE = `${PAGE} .oi-level-script-output pre`;
const TICKER = `${PAGE} input[aria-label="TOS OI level ticker"]`;
const SCOPE = `${PAGE} select[aria-label="OI script expiration scope"]`;
const options = (extra = {}) => ({ view: "OI Level Script TOS", fakeReads: CHAIN_READS, ...extra });

async function scriptReady(page) {
  await page.locator(PRE).waitFor({ state: "visible", timeout: 60_000 });
  return page.locator(PRE).innerText();
}

const symbolOfScript = (script) => /GetSymbol\(\) == "([A-Z.]+)"/.exec(script)?.[1];

e2eTest("the ThinkScript is built for the current ticker with every plot guarded by that ticker", async () => {
  await withApp(options(), async (app) => {
    const script = await scriptReady(app.page);
    const symbol = symbolOfScript(script);
    assert.ok(symbol, script.slice(0, 400));
    assert.ok(script.startsWith(`# ${symbol} OI + Volume Levels | `), script.slice(0, 120));
    assert.match(script, /3 expiries <= 16 DTE combined/);
    assert.equal((await app.page.locator(`${PAGE} .oi-level-script-atm strong`).innerText()), "$100.00");
    assert.ok(await app.page.locator(`${PAGE} .oi-level-script-levels article.is-call li`).count() >= 1);
    assert.ok(await app.page.locator(`${PAGE} .oi-level-script-levels article.is-put li`).count() >= 1);
    const plots = script.split("\n").filter((line) => /^plot (Call|Put)\d+ = /.test(line));
    assert.ok(plots.length >= 2);
    for (const line of plots) assert.match(line, /^plot (Call\d+ = if showCalls|Put\d+ = if showPuts) and isSymbol then [\d.]+ else Double\.NaN;$/);
    assert.match(script, /^plot ATM = if showATM and isSymbol then 100 else Double\.NaN;$/m);
    assert.match(script, /^ATM\.SetDefaultColor\(Color\.YELLOW\);$/m);
    assert.match(script, /^plot Call1 = if showCalls and isSymbol then 110 else Double\.NaN;$/m, "the biggest call wall is Call1");
    assert.match(script, /^plot Put1 = if showPuts and isSymbol then 90 else Double\.NaN;$/m);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Front expiry only re-scopes the script to the chart's expiry", async () => {
  await withApp(options(), async (app) => {
    await scriptReady(app.page);
    await app.page.locator(SCOPE).selectOption("expiry");
    await app.page.locator(`${PAGE} .oi-level-script-atm small`, { hasText: "· 2 DTE · matches Charts & OI" }).waitFor({ timeout: 10_000 });
    const script = await app.page.locator(PRE).innerText();
    assert.match(script.split("\n")[0], / 2 DTE$/);
    assert.match(script, /# Exact front-expiry levels matching the Charts & OI overlay\./);
  });
});

e2eTest("loading another ticker rebuilds the script for it; a malformed ticker cannot be loaded", async () => {
  await withApp(options(), async (app) => {
    const script = await scriptReady(app.page);
    const target = symbolOfScript(script) === "SPY" ? "NVDA" : "SPY";
    await app.page.locator(TICKER).fill(target.toLowerCase());
    await app.page.locator(TICKER).press("Enter");
    await app.page.waitForFunction(([selector, symbol]) => document.querySelector(selector)?.textContent.includes(`GetSymbol() == "${symbol}"`),
      [PRE, target], { timeout: 30_000 });
    await app.page.locator(TICKER).fill("1BAD");
    const load = app.page.locator(`${PAGE} .oi-level-script-controls button`, { hasText: /Load ticker|Reload ticker/ });
    assert.equal(await load.isDisabled(), true);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Copy ThinkScript reports a copy, or says plainly when the browser blocks it", async () => {
  await withApp(options(), async (app) => {
    await app.context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: new URL(app.page.url()).origin });
    const script = await scriptReady(app.page);
    await app.page.locator(`${PAGE} .oi-level-script-copy`).click();
    await app.page.locator(`${PAGE} .oi-level-script-status`, { hasText: "Copied — paste into a Thinkorswim study." }).waitFor({ timeout: 10_000 });
    assert.equal(await app.page.evaluate(() => navigator.clipboard.readText()), script);
    await app.page.evaluate(() => {
      navigator.clipboard.writeText = () => Promise.reject(new DOMException("blocked", "NotAllowedError"));
    });
    await app.page.locator(`${PAGE} .oi-level-script-copy`).click();
    await app.page.locator(`${PAGE} .oi-level-script-status`,
      { hasText: "Copy was blocked by the browser. Select the script text and copy it manually." }).waitFor({ timeout: 10_000 });
  });
});

e2eTest("TradingView: a two-ticker list builds one Pine script from exactly two chain reads", async () => {
  await withApp(options(), async (app) => {
    await scriptReady(app.page);
    await app.page.locator(`${PAGE} .oi-level-script-platform-tabs [role="tab"]`, { hasText: "TradingView Pine v6" }).click();
    await app.page.locator(`${PAGE} textarea[aria-label="TradingView OI script tickers"]`).fill("spy, aapl");
    const before = app.reads.length;
    await app.page.locator(`${PAGE} .oi-level-script-tv-actions button`, { hasText: "Build / refresh 2 tickers" }).click();
    await app.page.locator(`${PAGE} .oi-level-script-tv-builder header strong`, { hasText: "2/2 READY" }).waitFor({ timeout: 30_000 });
    const built = app.reads.slice(before).filter((read) => read.path === "/api/oi-finder-chain")
      .map((read) => new URLSearchParams(read.search).get("symbol")).sort();
    assert.deepEqual(built, ["AAPL", "SPY"]);
    assert.equal(await app.page.locator(`${PAGE} .oi-level-script-tv-symbols span.is-ready`).count(), 2);
    const pine = await app.page.locator(`${PAGE} .oi-level-script-output.is-tradingview pre`).innerText();
    assert.match(pine, /\/\/@version=6/);
    assert.ok(pine.includes("SPY") && pine.includes("AAPL"));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the script scrolls inside its box and the page never scrolls sideways", async () => {
  await withApp(options({ viewport: PHONE }), async (app) => {
    await scriptReady(app.page);
    await sleep(500);
    assert.equal(await overflowsSideways(app.page), false);
    const box = await app.page.locator(PRE).boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `script box at ${JSON.stringify(box)}`);
    assert.deepEqual(app.pageErrors, []);
  });
});
