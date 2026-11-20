// LEAP OI Scanner: today's trades on each card, so the trader does not have to open AGX Flow to know if a LEAP was
// bought or sold (2026-11-20, "If I see leap oi scanner like INTC I don't know anything like buyers or seller ... then I
// have to search agx flow ... I feel little confusion"). Also the beginner "What is this page?" box. The board is the
// real server's reply (captured from tests/test_momx_leap_oi_scanner_api.py); the flow numbers are added to its rows
// in the shape momx/leap_oi_scanner.with_flow writes (tests/test_momx_leap_flow.py). Desktop and phone width.

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

let board = null;

before(() => {
  if (marketHoursBlock()) return;
  const out = path.join(mkdtempSync(path.join(tmpdir(), "agx-leap-flow-")), "board.json");
  execFileSync(path.join(REPO, ".venv", "Scripts", "python.exe"),
    ["-m", "pytest", "tests/test_momx_leap_oi_scanner_api.py", "-q", "-p", "no:cacheprovider", "-k", "background_then_shows"],
    { cwd: REPO, encoding: "utf8", timeout: 240_000, env: { ...process.env, LEAP_OI_CAPTURE: out, PYTHONDONTWRITEBYTECODE: "1" } });
  const real = JSON.parse(readFileSync(out, "utf8"));
  const key = (r) => `${r.ticker}|${r.side}|${r.expiry}|${r.strike}`;
  const first = key(real.rows[0]);
  const second = real.rows[1] ? key(real.rows[1]) : null;
  const flow = (r) => (key(r) === first ? { flow: { volume: 1200, premium: 273600, fill: "ask", unusual: false, at: "2026-11-19T16:04:00-05:00" } }
    : key(r) === second ? { flow: { volume: 800, premium: 2_500_000, fill: "bid", unusual: true, at: "2026-11-19T16:04:00-05:00" } } : {});
  const add = (r) => (r ? { ...r, ...flow(r) } : r);
  board = { ...real, rows: real.rows.map(add), entries: (real.entries || []).map(add), biggestCall: add(real.biggestCall), biggestPut: add(real.biggestPut) };
}, { timeout: 260_000 });

for (const [label, viewport] of WIDTHS) {
  const phone = viewport === PHONE;
  const size = viewport ? { viewport } : {};
  const cardSel = phone ? "[data-testid='leap-oi-row-card']" : "[data-testid='leap-oi-row']";

  e2eTest(`LEAP OI Scanner at ${label}: each card says what traded today, bought or sold, with a beginner guide`, async () => {
    const reads = {
      "GET /api/leap-oi-scanner": { json: board },
      "GET /api/watchlist": { json: { tickers: [] } },
      "GET /api/watchlist-quotes": { json: { rows: [] } },
    };
    await withApp({ view: "LEAP OI Scanner", ...size, fakeReads: reads }, async (app) => {
      const { page } = app;
      const view = page.locator(VIEW);
      await view.locator(cardSel).first().waitFor({ timeout: 90_000 });
      const flows = view.locator(`${cardSel} [data-testid='leap-oi-flow']`);
      assert.equal(await flows.count(), await view.locator(cardSel).count(), "every card has a Today's trades line");
      const first = flows.nth(0);
      assert.match(await first.innerText(), /1,200 contracts · \$274K[\s\S]*Bought/);
      assert.match(await first.getAttribute("title"), /paid the asking price/);
      if (board.rows[1]) {
        assert.match(await flows.nth(1).innerText(), /800 contracts · \$2\.5M[\s\S]*Sold[\s\S]*Unusual/);
      }
      if (board.rows[2]) assert.match(await flows.nth(2).innerText(), /No trades seen today/);
      if (phone) assert.match(await view.locator(cardSel).first().innerText(), /Today's trades \(bought or sold\)/);
      // The headline card says it too.
      assert.match(await view.locator(".leapoi-hero").first().innerText(), /Today's trades:/);
      // Beginner guide: open on the first visit, explains the words, says it is not advice.
      const guide = view.getByTestId("beginner-guide");
      assert.equal(await guide.getAttribute("open"), "");
      const text = await guide.innerText();
      assert.match(text, /What is this page\?[\s\S]*Open interest[\s\S]*Delta/);
      assert.match(text, /not advice[\s\S]*paper trading/);
      await (phone ? guide.locator("summary").tap() : guide.locator("summary").click());
      for (let i = 0; i < 30 && (await guide.getAttribute("open")) !== null; i += 1) await page.waitForTimeout(100);
      assert.equal(await guide.getAttribute("open"), null, "the guide closes");
      assert.equal(await page.evaluate(() => localStorage.getItem("beginnerGuideSeen:leapOi")), "1");
      if (phone) assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 300_000 });
}
