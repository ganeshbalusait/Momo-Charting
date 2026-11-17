// Ask chat: open interest 1 to 12 months out, with delta (2026-11-16). The trader asked "Nvda check the leap which
// one had more OI" on his phone and got the Long Term page. The answer on screen is the REAL server's: before the
// specs run, the integration test asks the real /api/bot-chat on scratch files (Schwab faked, no AI allowed) and saves
// what came back, and the browser plays that reply. Checked at desktop and phone width: the biggest call and put with
// their delta, the cards (calls and puts both), the whole table with its Delta column, nothing cut off.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { before } from "node:test";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { PHONE, e2eTest, marketHoursBlock, overflowsSideways, withApp } from "./harness.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const CHAT_WRITE = "POST /api/bot-chat";
const BUTTON = "[data-testid='chat-dock-button']";
const CHAT = "[data-testid='dock-chat']";
const ANSWER = `${CHAT} [data-testid='chat-answer']`;
const QUESTION = "Nvda check the leap which one had more OI";
const WIDTHS = [["desktop", undefined], ["phone width", PHONE]];

let real = null;

before(() => {
  if (marketHoursBlock()) return;
  const out = path.join(mkdtempSync(path.join(tmpdir(), "agx-chat-long-oi-")), "reply.json");
  execFileSync(path.join(REPO, ".venv", "Scripts", "python.exe"),
    ["-m", "pytest", "tests/test_momx_chat_long_oi_api.py", "-q", "-p", "no:cacheprovider", "-k", "capture_the_real_reply"],
    { cwd: REPO, encoding: "utf8", timeout: 240_000, env: { ...process.env, CHAT_LONG_OI_CAPTURE: out, PYTHONDONTWRITEBYTECODE: "1" } });
  real = JSON.parse(readFileSync(out, "utf8"));
}, { timeout: 260_000 });

for (const [label, viewport] of WIDTHS) {
  const tap = viewport === PHONE;
  const size = viewport ? { viewport } : {};

  e2eTest(`a LEAP open-interest question at ${label}: the biggest call and put with delta, cards and the whole table`, async () => {
    const asked = [];
    const writes = { [CHAT_WRITE]: (write) => {
      const body = JSON.parse(write.body);
      if (body.home) return { json: { cards: [] } };
      if (body.more) return { status: 503, json: { error: "busy" } };
      asked.push(body.question);
      return { json: real };
    } };
    await withApp({ view: "Release Notes", ...size, fakeWrites: writes }, async (app) => {
      const { page } = app;
      const button = page.locator(BUTTON);
      await button.waitFor({ timeout: 90_000 });
      if (!(await page.locator(CHAT).isVisible().catch(() => false))) await (tap ? button.tap() : button.click());
      const input = page.locator(`${CHAT} input.chat-input`);
      await input.waitFor({ timeout: 15_000 });
      await input.fill(QUESTION);
      await input.press("Enter");
      const answer = page.locator(ANSWER).last();
      await answer.waitFor({ timeout: 15_000 });
      const text = await answer.innerText();
      assert.deepEqual(asked, [QUESTION]);
      assert.match(text, /NVDA strikes with the most open interest, expiries 3 to 12 months out/);
      assert.match(text, /Most call OI: 185 call, .* OI 108,750, delta 0\.31/);
      assert.match(text, /Most put OI: 140 put, .* OI 97,510, delta -0\.32/);
      assert.doesNotMatch(text, /Bearish signal|Technical analysis/);   // not the Long Term view it gave before
      // The cards: headed by the contract, calls and puts both, each with its delta.
      const cards = answer.locator("[data-testid='chat-row-card']");
      assert.equal(await cards.count(), 5);
      const heads = await cards.locator(".chat-row-head b").allInnerTexts();
      assert.deepEqual(heads.slice(0, 2), ["185 Call", "140 Put"]);
      assert.match(await cards.first().innerText(), /Delta\s*0\.31/);
      // The whole table opens as a sheet with a Delta column.
      await answer.locator("[data-testid='chat-table-open']").click();
      const sheet = page.locator("[data-testid='chat-table-sheet']");
      await sheet.waitFor({ timeout: 10_000 });
      assert.deepEqual(await sheet.locator("thead th").allInnerTexts(), ["Contract", "Expiry", "Days", "OI", "Delta", "Bid", "Ask"]);
      // The faked chain has 3 calls and 2 puts at delta 0.10 or more, 3 to 12 months out: every one is in the sheet,
      // and the 0.08-delta 250 call is not (2026-11-16: "Don't give high oi delta 0.02 like that").
      assert.equal(await sheet.locator("tbody tr").count(), real.table.rows.length);
      assert.equal(real.table.rows.length, 5);
      assert.doesNotMatch(await sheet.innerText(), /250 Call/);
      await sheet.locator("[data-testid='chat-sheet-close']").click();
      const box = await answer.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= page.viewportSize().width + 1, "the answer is cut off at the side");
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });
}
