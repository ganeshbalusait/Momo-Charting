// SPY and QQQ fire Setup rules like a stock (2026-11-05 "Yes, open them"):
// a SPY row the worker flags as an ETF still shows its GO tag in the Setup
// column; IWM with the same picture shows none. Reads are canned; no writes.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { BOARD_STORAGE, boardFor, boardReads, boardReady } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const GO = "[data-testid='momx-strategy-tag-go']";
const rowOf = (page, symbol) => page.locator("tr.momx-row").filter({ has: page.locator("[data-testid='momx-symbol-link']", { hasText: new RegExp(`^${symbol}$`) }) });

function reads() {
  const base = boardReads();
  const goAt = Math.floor(Date.now() / 1000) - 1800;
  const etfGo = (template, symbol) => ({
    ...template, symbol, etf: true, industry: "Index ETF", scanPass: true,
    grade: { ...(template.grade || {}), letter: "A+" },
    adx: { "30m": { plus: 30, minus: 10, adx: 30 } },
    m5: { ...(template.m5 || {}), gapGo: { goAt, gap: 1.2 } },
  });
  return {
    ...base,
    "GET /api/momx-scanner": (read) => {
      const params = new URLSearchParams(read.search);
      const payload = boardFor(params.get("list") || "Mag7", params.get("dir") === "bear" ? "bear" : "bull");
      if (params.get("dir") === "bear") return { json: payload };
      const template = payload.rows[0];
      const stocks = payload.rows.filter((r) => r.symbol !== "SPY");
      return { json: { ...payload, rows: [...stocks, etfGo(template, "SPY"), etfGo(template, "IWM")],
        rest: (payload.rest || []).filter((r) => r.symbol !== "SPY") } };
    },
  };
}

async function check(app) {
  await boardReady(app.page);
  const spy = rowOf(app.page, "SPY");
  await spy.locator(GO).waitFor({ timeout: 20_000 });
  assert.match((await spy.locator(GO).innerText()).trim(), /^GO \d{1,2}:\d{2}$/);
  assert.equal(await rowOf(app.page, "IWM").locator(GO).count(), 0, "other ETFs stay out");
  assert.deepEqual(app.writes, []);
  assert.deepEqual(app.pageErrors, []);
}

e2eTest("SPY (an ETF) shows its GO tag like a stock; IWM does not", async () => {
  await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: reads() }, check);
});

e2eTest("on a phone the SPY GO tag shows and the page does not widen", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: BOARD_STORAGE, fakeReads: reads() }, async (app) => {
    await check(app);
    assert.equal(await overflowsSideways(app.page), false);
  });
});
