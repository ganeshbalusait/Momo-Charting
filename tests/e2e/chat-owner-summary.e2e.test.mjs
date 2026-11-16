// The owner's AI summary in the chat (2026-11-11 evening), end to end at desktop and phone width.
// The owner sees one labelled box under the answer once the "more" part lands; a regular user never does, even when a
// faked server sends the field. Every chat reply is faked (POST /api/bot-chat), so no AI and no data route is reached;
// the only thing the screen may write is the chat's own read.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const CHAT_WRITE = "POST /api/bot-chat";
const STATUS = "GET /api/auth/status";
const BUTTON = "[data-testid='chat-dock-button']";
const CHAT = "[data-testid='dock-chat']";
const INPUT = `${CHAT} input.chat-input`;
const ANSWER = `${CHAT} [data-testid='chat-answer']`;
const BOX = "[data-testid='chat-ai-summary']";
const LABEL = "AI summary, can be wrong. Only you see this.";
const TEXT = "NVDA is -3.20% today at 170.00 while SPY is +0.40% and QQQ is +0.60%.";
const WIDTHS = [["desktop", undefined], ["phone width", PHONE]];
const member = { json: { bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
  user: { id: "e2e-user-1", email: "e2e.member@example.com", displayName: "Member", role: "user", isAdmin: false, isActive: true, hasPassword: true, mustChangePassword: false } } };
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const FAST = { intent: "rules", ticker: "NVDA", lines: ["I can't say whether to buy or sell. Here is what the app has.", "What the app's rules show on NVDA. More follows in a moment.",
  "This is what the app's rules show, not advice."], links: [], suggest: [], more: "summary_tok_abcdefghij" };
const MORE = { more: { done: true, lines: ["What the app's rules show on NVDA, with the chart numbers filled in."], articles: [], reasons: [],
  summary: { text: TEXT, label: LABEL, asOf: "written 14:30 ET from the app's facts" } } };

/** A faked chat server: the rules answer at once, then (after a moment) the more part with a summary in it. */
function chatServer() {
  const moreAsked = [];
  return { moreAsked, writes: { [CHAT_WRITE]: async (write) => {
    const body = JSON.parse(write.body || "{}");
    if (body.home) return { json: { home: { cards: [], chips: [] } } };
    if (body.more) {
      moreAsked.push(body.more);
      await wait(800);
      return { json: MORE };
    }
    return { json: FAST };
  } } };
}

async function ask(page, question, tap) {
  const button = page.locator(BUTTON);
  await button.waitFor({ timeout: 90_000 });
  if (!(await page.locator(CHAT).isVisible().catch(() => false))) await (tap ? button.tap() : button.click());
  await page.locator(INPUT).waitFor({ timeout: 15_000 });
  await page.locator(INPUT).fill(question);
  await page.locator(INPUT).press("Enter");
  await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
  return page.locator(ANSWER).first();
}

for (const [label, viewport] of WIDTHS) {
  const tap = viewport === PHONE;
  const size = viewport ? { viewport } : {};

  e2eTest(`the owner at ${label} sees the labelled AI summary box under the answer once the more part lands`, async () => {
    const server = chatServer();
    await withApp({ view: "Release Notes", ...size, fakeWrites: server.writes }, async (app) => {
      const { page } = app;
      const answer = await ask(page, "is NVDA a buy", tap);
      assert.equal(await answer.locator(BOX).count(), 0, "no box before the more part");
      const box = answer.locator(BOX);
      await box.waitFor({ timeout: 15_000 });
      assert.equal((await box.locator("[data-testid='chat-ai-summary-label']").innerText()).trim(), LABEL);
      assert.match(await box.innerText(), /NVDA is -3\.20% today at 170\.00/);
      // Set apart from the app's facts: its own dashed edge, and its text in italics.
      const style = await box.evaluate((el) => ({ border: getComputedStyle(el).borderTopStyle, italic: getComputedStyle(el.querySelector("p")).fontStyle }));
      assert.deepEqual(style, { border: "dashed", italic: "italic" });
      // It sits under the app's own lines, inside the answer, and fits the screen.
      const lineBox = await answer.locator(".chat-line").last().boundingBox();
      const summaryBox = await box.boundingBox();
      assert.ok(summaryBox.y >= lineBox.y + lineBox.height - 1, "the box is under the answer's lines");
      assert.ok(await box.evaluate((el) => { const b = el.getBoundingClientRect(); return b.left >= -1 && b.right <= window.innerWidth + 1; }));
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(server.moreAsked, [FAST.more], "the follow-up is asked once");
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });

  e2eTest(`a regular user at ${label} never sees the box, even when a summary field arrives`, async () => {
    const server = chatServer();
    await withApp({ view: "Release Notes", ...size, fakeReads: { [STATUS]: member, "GET /api/momx-scanner/rule-record": { json: {} } }, fakeWrites: server.writes }, async (app) => {
      const { page } = app;
      const answer = await ask(page, "is NVDA a buy", tap);
      await answer.getByText(/with the chart numbers filled in/).waitFor({ timeout: 15_000 });    // the more part has landed
      await wait(500);
      assert.equal(await page.locator(BOX).count(), 0);
      const all = await page.locator(CHAT).innerText();
      assert.doesNotMatch(all, /AI summary/);
      assert.ok(!all.includes(TEXT), "the summary text is not shown anywhere");
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });
}
