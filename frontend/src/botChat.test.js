import assert from "node:assert/strict";
import test from "node:test";

import { CHAT_MAX, CHAT_STARTERS, CHAT_URL, chatAnswer, chatAppend, chatQuestion } from "./botChat.js";
import { readDeskView } from "./botDesk.js";

test("a question is sent as one trimmed line of at most 300 characters; blanks send nothing", () => {
  assert.equal(chatQuestion("  see   aapl \n today "), "see aapl today");
  assert.equal(chatQuestion("   "), "");
  assert.equal(chatQuestion(null), "");
  assert.equal(chatQuestion("x".repeat(500)).length, CHAT_MAX);
  assert.equal(CHAT_URL, "/api/bot-chat");
  assert.ok(CHAT_STARTERS.length >= 4 && CHAT_STARTERS.every((q) => chatQuestion(q) === q));
});

test("an answer shows its kind, what it is about, plain lines and bullet lines apart", () => {
  const a = chatAnswer({ intent: "why_failed", ticker: "AAPL", day: "2026-11-10", suggest: ["see AAPL", "", 7, "why was AAPL missed"],
    lines: ["AAPL on 2026-11-10: Level 2 call signal at 9:38 AM.", "What was against it at the signal:", "- Price was on the wrong side of VWAP.", "  ", 5] });
  assert.equal(a.kind, "Why it failed");
  assert.equal(a.about, "AAPL · 2026-11-10");
  assert.deepEqual(a.lines, [
    { bullet: false, text: "AAPL on 2026-11-10: Level 2 call signal at 9:38 AM." },
    { bullet: false, text: "What was against it at the signal:" },
    { bullet: true, text: "Price was on the wrong side of VWAP." }]);
  assert.deepEqual(a.suggest, ["see AAPL", "why was AAPL missed"]);
});

test("every kind of answer has a plain heading, and a broken reply still reads", () => {
  assert.deepEqual(["what_if", "why_missed", "show", "status", "help", "error", "nonsense"].map((intent) => chatAnswer({ intent, lines: ["x"] }).kind),
    ["How far it went", "Why it was missed", "On file", "The bot now", "What I can answer", "Could not answer", "What I can answer"]);
  for (const broken of [null, undefined, "text", {}, { lines: "no" }, { lines: [] }]) {
    const a = chatAnswer(broken);
    assert.deepEqual(a.lines, [{ bullet: false, text: "I have no answer for that." }]);
    assert.deepEqual(a.suggest, []);
    assert.equal(a.about, "");
  }
  assert.equal(chatAnswer({ intent: "status", lines: ["ok"], day: "2026-11-10" }).about, "2026-11-10");
});

test("the conversation keeps the last 30 turns", () => {
  let turns = [];
  for (let i = 0; i < 35; i += 1) turns = chatAppend(turns, { id: i });
  assert.equal(turns.length, 30);
  assert.deepEqual([turns[0].id, turns[29].id], [5, 34]);
  assert.deepEqual(chatAppend(null, { id: 1 }), [{ id: 1 }]);
});

test("the Ask view is remembered like Research; anything else opens the Scorecard", () => {
  assert.equal(readDeskView("ask"), "ask");
  assert.equal(readDeskView("research"), "research");
  assert.equal(readDeskView("chat"), "desk");
});

test("the conversation remembers the newest ticker and day, and sends nothing before there is one", async () => {
  const { chatContext, chatSubject } = await import("./botChat.js");
  assert.equal(chatContext([]), null);
  assert.equal(chatContext(null), null);
  assert.deepEqual(chatSubject({ ticker: "NVDA", day: "2026-11-09", lines: ["x"] }), { ticker: "NVDA", day: "2026-11-09" });
  assert.equal(chatSubject({ intent: "status", ticker: null, day: "2026-11-10" }), null);
  assert.equal(chatSubject("broken"), null);
  const turns = [{ id: 1, subject: { ticker: "SPY", day: "2026-11-09" } }, { id: 2, subject: null }, { id: 3 },
    { id: 4, subject: { ticker: "NVDA", day: "2026-11-09" } }, { id: 5, subject: null }];
  assert.deepEqual(chatContext(turns), { ticker: "NVDA", day: "2026-11-09" });     // a help / status answer in between does not erase it
  assert.deepEqual(chatContext(turns.slice(0, 3)), { ticker: "SPY", day: "2026-11-09" });
  assert.deepEqual(chatContext([{ id: 1, subject: { ticker: "AMD" } }]), { ticker: "AMD", day: "" });
});

test("the side (calls or puts) of the last answer is remembered with the ticker", async () => {
  const { chatContext, chatSubject } = await import("./botChat.js");
  assert.deepEqual(chatSubject({ ticker: "NVDA", day: "2026-11-09", side: "bull" }), { ticker: "NVDA", day: "2026-11-09", side: "bull" });
  assert.deepEqual(chatSubject({ ticker: "NVDA", day: "2026-11-09", side: "up" }), { ticker: "NVDA", day: "2026-11-09" });
  assert.deepEqual(chatContext([{ subject: { ticker: "NVDA", day: "2026-11-09", side: "bear" } }]), { ticker: "NVDA", day: "2026-11-09", side: "bear" });
});

test("an AI reply is shown only when the server sent one; the record lines always stay", () => {
  const withAi = chatAnswer({ intent: "what_if", ticker: "SPY", day: "2026-11-09", lines: ["The 0.20-delta call ($753) cost $1.15 at the mid."],
    ai: { text: "  Profit: +$482 at the Bot's exit.  ", source: "openai" }, understoodBy: "AI" });
  assert.equal(withAi.ai, "Profit: +$482 at the Bot's exit.");
  assert.equal(withAi.understoodByAi, true);
  assert.deepEqual(withAi.lines, [{ bullet: false, text: "The 0.20-delta call ($753) cost $1.15 at the mid." }]);
  for (const none of [{ lines: ["x"] }, { lines: ["x"], ai: null }, { lines: ["x"], ai: { text: "   " } }, { lines: ["x"], ai: "text" }, { lines: ["x"], ai: { text: 5 } }]) {
    assert.equal(chatAnswer(none).ai, "");
    assert.equal(chatAnswer(none).understoodByAi, false);
  }
});

test("when the AI is not being asked the answer says why, and never next to an AI reply", () => {
  assert.equal(chatAnswer({ lines: ["x"], aiNote: " the AI service is over its free limit " }).aiNote, "the AI service is over its free limit");
  assert.equal(chatAnswer({ lines: ["x"], aiNote: "ignored", ai: { text: "Up 140%." } }).aiNote, "");
  assert.equal(chatAnswer({ lines: ["x"], aiNote: 5 }).aiNote, "");
});
