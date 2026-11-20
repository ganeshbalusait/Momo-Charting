// LEAP OI Scanner: the stock's current price on every card (2026-11-19, "Add current prices also"). The board is the
// REAL server's reply (captured from tests/test_momx_leap_oi_scanner_api.py, Schwab faked); the live quotes are faked
// at /api/watchlist-quotes. Checked at desktop and phone width: each card shows the current price with today's change,
// "From price" uses that price, and when no quote comes back the card says the price is from the chain read.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { before } from "node:test";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { PHONE, e2eTest, marketHoursBlock, overflowsSideways, withApp } from "./harness.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const VIEW = "[data-testid='leap-oi-view']";
const WIDTHS = [["desktop", undefined], ["phone width", PHONE]];

let real = null;

before(() => {
  if (marketHoursBlock()) return;
  const out = path.join(mkdtempSync(path.join(tmpdir(), "agx-leap-price-")), "board.json");
  execFileSync(path.join(REPO, ".venv", "Scripts", "python.exe"),
    ["-m", "pytest", "tests/test_momx_leap_oi_scanner_api.py", "-q", "-p", "no:cacheprovider", "-k", "background_then_shows"],
    { cwd: REPO, encoding: "utf8", timeout: 240_000, env: { ...process.env, LEAP_OI_CAPTURE: out, PYTHONDONTWRITEBYTECODE: "1" } });
  real = JSON.parse(readFileSync(out, "utf8"));
}, { timeout: 260_000 });

const tickersOf = (board) => [...new Set([board.biggestCall, board.biggestPut, ...(board.entries || []), ...(board.rows || []), ...(board.byTicker || [])]
  .filter(Boolean).map((r) => r.ticker))];

for (const [label, viewport] of WIDTHS) {
  const phone = viewport === PHONE;
  const size = viewport ? { viewport } : {};
  const cardSel = phone ? "[data-testid='leap-oi-row-card']" : "[data-testid='leap-oi-row']";

  e2eTest(`LEAP OI Scanner at ${label}: every card shows the current stock price and From price uses it`, async () => {
    const asked = [];
    // Each ticker's live price is 10% above the price at the chain read, up 1.5% on the day.
    const quoteFor = (ticker) => {
      const spot = real.byTicker.find((t) => t.ticker === ticker)?.spot ?? 100;
      return { symbol: ticker, lastPrice: Math.round(spot * 110) / 100, changePct: 1.5 };
    };
    const reads = {
      "GET /api/leap-oi-scanner": { json: real },
      "GET /api/watchlist": { json: { tickers: [] } },
      "GET /api/watchlist-quotes": (read) => {
        const symbols = (new URLSearchParams(read.search).get("symbols") || "").split(",").filter(Boolean);
        asked.push(symbols);
        return { json: { rows: symbols.map(quoteFor) } };
      },
    };
    await withApp({ view: "LEAP OI Scanner", ...size, fakeReads: reads }, async (app) => {
      const { page } = app;
      const view = page.locator(VIEW);
      await view.locator(cardSel).first().waitFor({ timeout: 90_000 });
      const row = real.rows[0];
      const live = quoteFor(row.ticker).lastPrice;
      const first = view.locator(cardSel).first();
      const stock = first.locator("[data-testid='leap-oi-stock']");
      for (let i = 0; i < 50 && !(await stock.innerText()).includes("+1.5%"); i += 1) await page.waitForTimeout(100);
      assert.equal((await stock.innerText()).trim(), `${live.toFixed(2)} (+1.5%)`);
      assert.match(await stock.getAttribute("class"), /is-up/);
      const away = Math.round((row.strike / live - 1) * 1000) / 10;
      assert.ok((await first.innerText()).includes(`${away > 0 ? "+" : ""}${away.toFixed(1)}%`), `From price ${away}% missing`);
      // One quote request for every ticker on screen, not one per card.
      assert.deepEqual([...asked[0]].sort(), tickersOf(real).sort());
      assert.match(await view.locator(".leapoi-hero").first().innerText(), /Stock \d+\.\d\d \(\+1\.5%\)/);
      assert.equal(await view.locator(`${cardSel} [data-testid='leap-oi-stock']`).count(), await view.locator(cardSel).count());
      if (phone) {
        assert.match(await first.innerText(), /Stock price/);
        assert.equal(await overflowsSideways(page), false);
      }
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });

  e2eTest(`LEAP OI Scanner at ${label}: with no live quote the card shows the price at the chain read`, async () => {
    const reads = {
      "GET /api/leap-oi-scanner": { json: real },
      "GET /api/watchlist": { json: { tickers: [] } },
      "GET /api/watchlist-quotes": { status: 503, json: { error: "quotes down" } },
    };
    await withApp({ view: "LEAP OI Scanner", ...size, fakeReads: reads }, async (app) => {
      const view = app.page.locator(VIEW);
      await view.locator(cardSel).first().waitFor({ timeout: 90_000 });
      const row = real.rows[0];
      const stock = view.locator(cardSel).first().locator("[data-testid='leap-oi-stock']");
      assert.equal((await stock.innerText()).trim(), `${Number(row.spot).toFixed(2)} at read`);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });
}
