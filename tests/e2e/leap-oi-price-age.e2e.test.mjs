// LEAP OI Scanner: an old option price says when it was read (2026-11-20). The NVDA card at 5:39 PM ET showed
// Bid 1.90 / Mid 1.95 / delta 0.13 from a 12:53 PM read, before NVDA fell 7% into the close, with nothing saying so.
// The board is the REAL server's reply (captured from tests/test_momx_leap_oi_scanner_api.py, Schwab faked), with its
// read time moved: 5 hours old shows "Prices from <time> ET" on the big cards (and the phone's cards); a read made a
// minute ago shows no note. Desktop and phone width.

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
const AGE = "[data-testid='price-age']";
const WIDTHS = [["desktop", undefined], ["phone width", PHONE]];

let real = null;

before(() => {
  if (marketHoursBlock()) return;
  const out = path.join(mkdtempSync(path.join(tmpdir(), "agx-leap-age-")), "board.json");
  execFileSync(path.join(REPO, ".venv", "Scripts", "python.exe"),
    ["-m", "pytest", "tests/test_momx_leap_oi_scanner_api.py", "-q", "-p", "no:cacheprovider", "-k", "background_then_shows"],
    { cwd: REPO, encoding: "utf8", timeout: 240_000, env: { ...process.env, LEAP_OI_CAPTURE: out, PYTHONDONTWRITEBYTECODE: "1" } });
  real = JSON.parse(readFileSync(out, "utf8"));
}, { timeout: 260_000 });

// The same board with every row's prices read at `iso`.
function readAt(board, iso) {
  const stamp = (row) => (row ? { ...row, readAt: iso } : row);
  return {
    ...board,
    biggestCall: stamp(board.biggestCall), biggestPut: stamp(board.biggestPut),
    rows: (board.rows || []).map(stamp), entries: (board.entries || []).map(stamp),
    byTicker: (board.byTicker || []).map((t) => ({ ...t, call: stamp(t.call), put: stamp(t.put), entryCall: stamp(t.entryCall), entryPut: stamp(t.entryPut) })),
  };
}

const etTime = (date) => date.toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" });

for (const [label, viewport] of WIDTHS) {
  const size = viewport ? { viewport } : {};
  const phone = viewport === PHONE;

  e2eTest(`LEAP OI Scanner at ${label}: prices read 5 hours ago say their read time`, async () => {
    const old = new Date(Date.now() - 5 * 3600 * 1000);
    const reads = {
      "GET /api/leap-oi-scanner": { json: readAt(real, old.toISOString()) },
      "GET /api/watchlist": { json: { tickers: [] } },
      "GET /api/watchlist-quotes": { json: { rows: [] } },
    };
    await withApp({ view: "LEAP OI Scanner", storage: { leapOiPhoneView: "cards" }, ...size, fakeReads: reads }, async (app) => {
      const { page } = app;
      const view = page.locator(VIEW);
      const hero = view.locator(".leapoi-hero").first();
      await hero.waitFor({ timeout: 90_000 });
      const note = hero.locator(AGE);
      await note.waitFor({ timeout: 20_000 });
      const text = await note.innerText();
      assert.match(text, new RegExp(`^Prices from ${etTime(old).replace(/\s/g, "\\s")} ET: the bid, ask, mid and delta may have moved since\\.$`), text);
      if (phone) {
        const card = view.locator("[data-testid='leap-oi-row-card']").first();
        await card.waitFor({ timeout: 30_000 });
        assert.match(await card.locator(AGE).innerText(), /^Prices from /);
        assert.equal(await overflowsSideways(page), false, "nothing is cut off sideways");
      }
      assert.deepEqual(app.pageErrors, []);
    });
  });

  e2eTest(`LEAP OI Scanner at ${label}: prices read a minute ago show no note`, async () => {
    const reads = {
      "GET /api/leap-oi-scanner": { json: readAt(real, new Date(Date.now() - 60 * 1000).toISOString()) },
      "GET /api/watchlist": { json: { tickers: [] } },
      "GET /api/watchlist-quotes": { json: { rows: [] } },
    };
    await withApp({ view: "LEAP OI Scanner", storage: { leapOiPhoneView: "cards" }, ...size, fakeReads: reads }, async (app) => {
      const { page } = app;
      const view = page.locator(VIEW);
      await view.locator(".leapoi-hero").first().waitFor({ timeout: 90_000 });
      assert.equal(await view.locator(AGE).count(), 0);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}
