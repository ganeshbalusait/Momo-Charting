// Option chain tick streaming (2026-11-04 coverage), end to end in the real
// app on the fixture chain (options-fixtures.mjs).
//
// * The open chain leases ONLY its visible contracts (the lease GET is
//   answered in the browser - through the live gateway it would start the
//   hub loop documented in tests/test_cov_options_stream_api.py).
// * An option tick arriving on /api/live-market-stream lands in that row's
//   Mark cell. The SSE feed is a page-level stub that sends one Schwab-shaped
//   option packet for the ATM call of every subscribed ticker.

import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";
import { CHAIN_READS, chartStorage, expiryIn, occ, sleep } from "./options-fixtures.mjs";

const CHAIN = "#charts-oi-option-chain";
const OPEN = `${CHAIN} .charts-oi-expiry-accordion .charts-oi-expiry-item.is-open`;
const DEPTH = `${OPEN} select[aria-label="Strikes displayed above and below ATM"]`;

const options = async () => ({ view: "Charts & OI", storage: await chartStorage(), fakeReads: CHAIN_READS });
const leases = (app) => app.reads.filter((read) => read.path === "/api/live-option-stream")
  .map((read) => {
    const params = new URLSearchParams(read.search);
    return { symbol: params.get("symbol"), contracts: (params.get("contracts") || "").split(",").filter(Boolean) };
  });

async function openChainTab(page) {
  await page.locator(`${CHAIN} > header`, { hasText: "Spot $100" }).waitFor({ timeout: 90_000 });
  await page.locator(`${CHAIN} .charts-oi-chain-view-tabs [role="tab"]`, { hasText: /^Chain$/ }).click();
  await page.locator(OPEN).first().waitFor({ state: "visible", timeout: 30_000 });
}

async function waitForLeases(app, count) {
  for (let waited = 0; waited < 10_000 && leases(app).length < count; waited += 100) await sleep(100);
  return leases(app);
}

e2eTest("the open chain leases only its visible contracts, and a smaller window sends a smaller lease", async () => {
  await withApp(await options(), async (app) => {
    await openChainTab(app.page);
    const [first] = await waitForLeases(app, 1);
    assert.ok(first, "no lease was sent for the open chain");
    const front = expiryIn(2);
    const yymmdd = `${front.slice(2, 4)}${front.slice(5, 7)}${front.slice(8, 10)}`;
    assert.match(first.symbol, /^[A-Z][A-Z0-9.-]{0,9}$/);
    assert.equal(first.contracts.length, 42, "21 visible strikes x call + put of the one open expiry");
    assert.ok(first.contracts.length <= 300, "never near the streamer's 300-contract ceiling");
    assert.ok(first.contracts.every((contract) => contract.startsWith(first.symbol.padEnd(6, " ")) && contract.includes(yymmdd)),
      "only the open expiry's contracts, in Schwab's padded OCC form");
    assert.deepEqual(first.contracts, [...first.contracts].sort(), "a stable, sorted key");

    await app.page.locator(DEPTH).selectOption("5");
    const all = await waitForLeases(app, 2);
    assert.equal(all.at(-1).contracts.length, 22, "±5 strikes: 11 x 2");
    await sleep(1_500);
    assert.equal(leases(app).length, 2, "the 500 ms debounce sends one lease per change, not one per render");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a streamed option tick updates that contract's Mark cell", async () => {
  await withApp(await options(), async (app) => {
    await openChainTab(app.page);                                   // remembers the Chain tab for the reload
    const front = expiryIn(2);
    await app.page.route("**/api/live-market-stream**", (route) => {
      const symbols = (new URL(route.request().url()).searchParams.get("symbols") || "").split(",").filter(Boolean);
      const body = symbols.map((symbol, index) => `id: ${index + 1}\nevent: option\ndata: ${JSON.stringify({
        symbol: occ(symbol, front, "CALL", 100),
        underlying: symbol,
        receivedAt: new Date().toISOString(),
        data: { bid: 7.72, ask: 7.82, mark: 7.77, last: 7.75, totalVolume: 5000, openInterest: 500, delta: 0.55, gamma: 0.03 },
      })}\n\n`).join("");
      return route.fulfill({ status: 200, headers: { "content-type": "text/event-stream", "cache-control": "no-store" }, body });
    });
    await app.page.reload({ waitUntil: "domcontentloaded" });
    await app.page.locator(OPEN).first().waitFor({ state: "visible", timeout: 90_000 });
    const atmCallMark = app.page.locator(`${OPEN} tr.is-atm td.option-mark`).first();
    await app.page.waitForFunction(
      (selector) => document.querySelector(selector)?.textContent.trim() === "$7.77",
      `${OPEN} tr.is-atm td.option-mark`, { timeout: 30_000 },
    );
    assert.equal((await atmCallMark.innerText()).trim(), "$7.77");
    // Only that contract moved: the ATM put keeps the REST mark.
    assert.equal((await app.page.locator(`${OPEN} tr.is-atm td.option-mark`).nth(1).innerText()).trim(), "$4.10");
    assert.deepEqual(app.pageErrors, []);
  });
});
