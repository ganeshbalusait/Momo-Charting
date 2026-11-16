// The owner's AI summary on screen (2026-11-11 evening): owner only, under its fixed label, in a box apart from the facts.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { chatAnswer } from "./botChat.js";
import { OWNER_SUMMARY_LABEL, chatMergeMore, chatOwnerSummary } from "./chatReport.js";

const LABEL = "AI summary, can be wrong. Only you see this.";
const sent = { text: "NVDA is -3.20% today at 170.00 while SPY is +0.40%.", label: LABEL, asOf: "written 14:30 ET from the app's facts" };
const fast = { intent: "rules", ticker: "NVDA", lines: ["What the app's rules show on NVDA. More follows in a moment."], more: "tok_abcdefghijklmnopqrstu" };

test("the owner gets the summary with its fixed label from the more part", () => {
  const merged = chatMergeMore(chatAnswer(fast, true), { more: { done: true, lines: [], summary: sent } }, true);
  assert.deepEqual(merged.ownerSummary, { text: sent.text, label: LABEL, asOf: sent.asOf });
  assert.equal(OWNER_SUMMARY_LABEL, LABEL);
});

test("a regular user's screen shows nothing even if a summary field arrived", () => {
  const merged = chatMergeMore(chatAnswer(fast, false), { more: { done: true, lines: [], summary: sent } }, false);
  assert.equal(merged.ownerSummary, null);
  assert.equal(chatOwnerSummary(sent, false), null);
  assert.equal(chatOwnerSummary(sent), null);                          // not the owner unless said so
  assert.equal(chatOwnerSummary(sent, "true"), null);
});

test("a first answer never carries one, even if the server sent the field", () => {
  assert.equal(chatAnswer({ ...fast, summary: sent }, true).ownerSummary, null);
});

test("the label is always the fixed one, and an empty or odd summary is dropped", () => {
  assert.equal(chatOwnerSummary({ text: "x", label: "Facts from the app" }, true).label, LABEL);
  assert.equal(chatOwnerSummary({ text: "   " }, true), null);
  assert.equal(chatOwnerSummary("text", true), null);
  assert.equal(chatOwnerSummary({ text: "a".repeat(900) }, true).text.length, 700);
});

test("AI down: the more part has no summary and the answer has no box", () => {
  const merged = chatMergeMore(chatAnswer(fast, true), { more: { done: true, lines: [] } }, true);
  assert.equal(merged.ownerSummary, null);
  assert.equal(merged.moreState, "done");
  assert.equal(chatMergeMore(chatAnswer(fast, true), null, true).ownerSummary, null);
});

const report = readFileSync(new URL("./ChatReport.jsx", import.meta.url), "utf8");
const chat = readFileSync(new URL("./BotChat.jsx", import.meta.url), "utf8");
const css = readFileSync(new URL("./chatSearch.css", import.meta.url), "utf8");

test("the box renders the label above the text in its own testable element", () => {
  assert.match(report, /export function OwnerSummary\(\{ summary \}\)/);
  assert.match(report, /className="chat-ai-summary" data-testid="chat-ai-summary"/);
  assert.match(report, /data-testid="chat-ai-summary-label">\{summary\.label\}<\/b>\s*<p>\{summary\.text\}<\/p>/);
});

// Updated 2026-11-14: the answer card moved into its own Answer component, and a "why" answer's AI write-up (the WhyCard)
// takes the summary's place, so the box shows for the owner only and only when the answer has no why card.
test("the chat renders the box for the owner only", () => {
  assert.match(chat, /\{isOwner && !why \? <OwnerSummary summary=\{answer\.ownerSummary\} \/> : null\}/);
});

test("the box is styled apart from the app's facts", () => {
  assert.match(css, /\.chat-ai-summary \{[^}]*border: 1px dashed/);
  assert.match(css, /\.chat-ai-summary p \{[^}]*font-style: italic/);
});
