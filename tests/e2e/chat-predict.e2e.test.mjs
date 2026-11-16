// Ask chat: "Can we predict the price?" (2026-11-15). The trader asked it on the SPY chart and got release notes.
// The answer on screen is the REAL server's: before the specs run, the integration test asks the real
// /api/bot-chat on scratch files (no AI allowed) and saves what came back, and the browser plays that reply.
// Checked at desktop and phone width: the plain "no one can predict" line, the options range, the walls, and
// no release note anywhere in the answer.

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
const QUESTION = "Can we predict the prize of NVDA?";
const WIDTHS = [["desktop", undefined], ["phone width", PHONE]];

let real = null;

before(() => {
  if (marketHoursBlock()) return;
  const out = path.join(mkdtempSync(path.join(tmpdir(), "agx-chat-predict-")), "reply.json");
  execFileSync(path.join(REPO, ".venv", "Scripts", "python.exe"),
    ["-m", "pytest", "tests/test_momx_chat_predict_api.py", "-q", "-p", "no:cacheprovider", "-k", "capture_the_real_reply"],
    { cwd: REPO, encoding: "utf8", timeout: 240_000, env: { ...process.env, CHAT_PREDICT_CAPTURE: out, PYTHONDONTWRITEBYTECODE: "1" } });
  real = JSON.parse(readFileSync(out, "utf8"));
}, { timeout: 260_000 });

for (const [label, viewport] of WIDTHS) {
  const tap = viewport === PHONE;
  const size = viewport ? { viewport } : {};

  e2eTest(`a price-prediction question at ${label}: the plain line, the options range and the walls, no release notes`, async () => {
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
      assert.match(text, /No one can predict a price, and this app does not try\./);
      assert.match(text, /NVDA expected move for .*\+\/-/);
      assert.match(text, /Biggest call wall/);
      assert.match(text, /Biggest put wall/);
      assert.doesNotMatch(text, /Release note/i);
      const box = await answer.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= page.viewportSize().width + 1, "the answer is cut off at the side");
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });
}
