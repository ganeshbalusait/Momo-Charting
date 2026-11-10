// Bot page -> Ask (2026-11-10): the no-AI chatbot. The trader types a question about a ticker or the bot and
// the answer comes from the app's records. The answers are canned here; the spec checks what is sent and shown.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const CHAT_WRITE = "POST /api/bot-chat";
const CHAT = "[data-testid='bot-chat']";
const ASK = { "bot.view": "ask" };
const DESK_READ = "GET /api/momx-scanner/bot-desk";

const REPLIES = {
  "SPY if I take how much it went up": { intent: "what_if", ticker: "SPY", day: "2026-11-09", suggest: ["why SPY failed", "why was SPY missed"], lines: [
    "SPY on 2026-11-09: Level 2 call signal at 9:38 AM with the stock at $743.91.",
    "The 0.20-delta call ($753) cost $1.15 at the mid.",
    "Its best price of the day was $3.70: +220% from the entry.",
    "Three contracts at the mid would have been +$482 at the Bot's exit.",
    "Paper 3 (Rules bot): no order on it."] },
  "why SPY failed": { intent: "why_failed", ticker: "SPY", day: "2026-11-09", suggest: ["see SPY"], lines: [
    "On the Bot's record this one did not fail: the 0.20-delta contract was +139% at the exit (mid prices).",
    "Not ideal at the signal, all the same:", "- A high OI wall was just ahead at $745 (0.15% away)."] },
};

function answers(extra = {}) {
  return { [CHAT_WRITE]: (write) => {
    const question = JSON.parse(write.body).question;
    return extra[question] || { json: REPLIES[question] || { intent: "help", ticker: null, day: null, suggest: ["see SPY"],
      lines: ["I did not find a ticker I have records for in that question."] } };
  } };
}

const texts = async (locator) => (await locator.allTextContents()).map((s) => s.trim());

e2eTest("Ask: a starter question, a typed question and a follow-up each send one request and show the answer", async () => {
  await withApp({ view: "Bot", storage: ASK, fakeWrites: answers() }, async (app) => {
    const chat = app.page.locator(CHAT);
    await chat.waitFor({ timeout: 60_000 });
    assert.equal((await app.page.locator("[role='group'][aria-label='Page'] button[aria-pressed='true']").textContent()).trim(), "Ask");
    assert.match(await chat.locator(".chat-note").textContent(), /any reply with a number that is not in the records is thrown away\. It only reads; it cannot place or change a trade\./);
    assert.deepEqual((await texts(chat.locator(".chat-empty .chat-chip"))).slice(0, 2), ["SPY if I take how much it went up", "why AAPL failed"]);
    assert.equal(await chat.locator("button.chat-send").isDisabled(), true);              // nothing typed yet

    await chat.locator(".chat-empty .chat-chip", { hasText: "SPY if I take how much it went up" }).click();
    const first = chat.locator("[data-testid='chat-answer']").first();
    await first.waitFor({ timeout: 15_000 });
    assert.equal((await first.locator(".chat-kind").textContent()).trim(), "How far it went · SPY · 2026-11-09");
    assert.deepEqual((await texts(first.locator(".chat-line"))).slice(1, 3), ["The 0.20-delta call ($753) cost $1.15 at the mid.", "Its best price of the day was $3.70: +220% from the entry."]);
    assert.deepEqual(JSON.parse(app.writes[0].body), { question: "SPY if I take how much it went up" });   // nothing to continue yet

    await first.locator(".chat-chip", { hasText: "why SPY failed" }).click();           // a follow-up the answer offered
    const second = chat.locator("[data-testid='chat-answer']").nth(1);
    await second.waitFor({ timeout: 15_000 });
    assert.equal((await second.locator(".chat-kind").textContent()).trim(), "Why it failed · SPY · 2026-11-09");
    assert.deepEqual(await texts(second.locator(".chat-line.is-bullet")), ["A high OI wall was just ahead at $745 (0.15% away)."]);

    await chat.locator("input.chat-input").fill("   tell me   a joke ");
    await chat.locator("input.chat-input").press("Enter");
    const third = chat.locator("[data-testid='chat-answer']").nth(2);
    await third.waitFor({ timeout: 15_000 });
    assert.equal((await third.locator(".chat-kind").textContent()).trim(), "What I can answer");
    assert.equal(await chat.locator("input.chat-input").inputValue(), "");
    assert.deepEqual(app.writes.map((w) => JSON.parse(w.body).question), ["SPY if I take how much it went up", "why SPY failed", "tell me a joke"]);
    // From the second question on, the ticker and day the conversation is about go along (2026-11-10:
    // he asked "NVDA if I take how much it went up" and then just "Profit or loss").
    assert.deepEqual(JSON.parse(app.writes[1].body).context, { ticker: "SPY", day: "2026-11-09" });
    assert.deepEqual(JSON.parse(app.writes[2].body).context, { ticker: "SPY", day: "2026-11-09" });
    await chat.locator("input.chat-input").fill("Profit or loss");
    await chat.locator("input.chat-input").press("Enter");
    await chat.locator("[data-testid='chat-answer']").nth(3).waitFor({ timeout: 15_000 });
    assert.deepEqual(JSON.parse(app.writes[3].body), { question: "Profit or loss", context: { ticker: "SPY", day: "2026-11-09" } });   // the help answer did not erase it
    assert.deepEqual(await texts(chat.locator(".chat-q")).then((q) => q.map((s) => s.replace(/^You/, ""))), ["SPY if I take how much it went up", "why SPY failed", "tell me a joke", "Profit or loss"]);
    assert.ok(app.writes.every((w) => w.path === "/api/bot-chat"));
    assert.ok(!app.reads.some((r) => r.path === "/api/momx-scanner/bot-desk"), "the Ask view must not load the Scorecard");
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Ask: a refused or failed answer says so plainly, and the tab switches back to the Scorecard", async () => {
  const refuse = { "see SPY": { status: 403, json: { error: "admin only" } }, "why was NVDA missed": { status: 500, json: {} } };
  await withApp({ view: "Bot", storage: ASK, fakeWrites: answers(refuse), fakeReads: { [DESK_READ]: { json: { available: false, accounts: {}, limits: {} } } } }, async (app) => {
    const chat = app.page.locator(CHAT);
    await chat.waitFor({ timeout: 60_000 });
    await chat.locator("input.chat-input").fill("see SPY");
    await chat.locator("button.chat-send").click();
    await chat.locator("[data-testid='chat-answer']", { hasText: "Only the owner can ask about the bot's trades." }).waitFor({ timeout: 15_000 });
    await chat.locator("input.chat-input").fill("why was NVDA missed");
    await chat.locator("button.chat-send").click();
    await chat.locator("[data-testid='chat-answer']", { hasText: "The app could not answer that. Nothing was changed." }).waitFor({ timeout: 15_000 });
    assert.deepEqual(await texts(chat.locator(".chat-kind")), ["Could not answer", "Could not answer"]);
    await app.page.locator("[role='group'][aria-label='Page'] button", { hasText: "Scorecard" }).click();
    await app.page.locator("[data-testid='bot-desk']").waitFor({ timeout: 15_000 });
    assert.equal(await app.page.locator(CHAT).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone width: the Ask view fits, the question box stays reachable and an answer wraps", async () => {
  await withApp({ view: "Bot", viewport: PHONE, storage: ASK, fakeWrites: answers() }, async (app) => {
    const chat = app.page.locator(CHAT);
    await chat.waitFor({ timeout: 60_000 });
    await chat.locator("input.chat-input").fill("SPY if I take how much it went up");
    await chat.locator("button.chat-send").tap();
    const answer = chat.locator("[data-testid='chat-answer']").first();
    await answer.waitFor({ timeout: 15_000 });
    assert.equal(await overflowsSideways(app.page), false);
    for (const target of [answer, chat.locator("form.chat-form"), chat.locator("button.chat-send")]) {
      const box = await target.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, "cut off on a phone");
    }
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Ask: an AI reply shows on top with the record lines under it; without one the lines stand alone", async () => {
  const withAi = { ...REPLIES["SPY if I take how much it went up"], ai: { text: "Profit: three contracts would have made +$482 at the Bot's exit.", source: "openai" } };
  const understood = { ...REPLIES["why SPY failed"], understoodBy: "AI" };
  const extra = { "did SPY make money": { json: withAi }, "was that spy trade any good": { json: understood } };
  await withApp({ view: "Bot", storage: ASK, fakeWrites: answers(extra) }, async (app) => {
    const chat = app.page.locator(CHAT);
    await chat.waitFor({ timeout: 60_000 });
    await chat.locator("input.chat-input").fill("did SPY make money");
    await chat.locator("button.chat-send").click();
    const first = chat.locator("[data-testid='chat-answer']").first();
    await first.waitFor({ timeout: 15_000 });
    assert.equal((await first.locator("[data-testid='chat-ai']").textContent()).trim(), "AIProfit: three contracts would have made +$482 at the Bot's exit.");
    assert.equal((await first.locator(".chat-from").textContent()).trim(), "From the records");
    assert.equal(await first.locator(".chat-line").count(), 5);                                   // every record line is still shown
    // The AI's reply comes before the record lines.
    const aiBox = await first.locator("[data-testid='chat-ai']").boundingBox();
    const lineBox = await first.locator(".chat-line").first().boundingBox();
    assert.ok(aiBox && lineBox && aiBox.y < lineBox.y);

    await chat.locator("input.chat-input").fill("was that spy trade any good");
    await chat.locator("button.chat-send").click();
    const second = chat.locator("[data-testid='chat-answer']").nth(1);
    await second.waitFor({ timeout: 15_000 });
    assert.equal(await second.locator("[data-testid='chat-ai']").count(), 0);                     // understood by the AI, but no reply text
    assert.equal((await second.locator(".chat-from").textContent()).trim(), "The AI worked out what you were asking; the answer is from the records");

    await chat.locator("input.chat-input").fill("SPY if I take how much it went up");            // the plain canned answer: no AI at all
    await chat.locator("button.chat-send").click();
    const third = chat.locator("[data-testid='chat-answer']").nth(2);
    await third.waitFor({ timeout: 15_000 });
    assert.equal(await third.locator("[data-testid='chat-ai']").count(), 0);
    assert.equal(await third.locator(".chat-from").count(), 0);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
