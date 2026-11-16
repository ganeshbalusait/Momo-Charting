// The "why did it move" card (2026-11-14). His words: "Why spy went down this morning?" got a circular answer; he wants
// one like Google's ("a crisp clear answer", "a proper reason") and a Positive / Negative / Neutral tag on every news link.
// The "more" part sends `why`; these check the screen's side of that contract: what is kept, what is dropped, and how
// the card is laid out (answer on top, the numbers under a closed "Show the numbers").
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { chatAnswer } from "./botChat.js";
import {
  CHECKING_LINE, MORE_WAIT, TONE_LABELS, WHY_LABELS, WHY_MAX_REASONS, WHY_WAIT, chatHeadline, chatMergeMore, chatTone, chatWhy,
  chatWhySource, dragText, whyFactLines, whyLayout,
} from "./chatReport.js";

const FAST = {
  intent: "why_move", ticker: "SPY", day: "2026-11-13", more: "why_tok_abcdefghij",
  lines: [
    "This morning (9:30 AM-12:00 PM ET, Fri Nov 13): SPY -1.36% from the open, -0.98% from Thursday's close.",
    "Regular session: SPY -0.39% to 754.25 (Fri Nov 13, 4:00 PM ET)",
    "The app's saved headlines, this morning:",
    "9:58 AM ET, Benzinga: Nasdaq futures fall after Nvidia CEO resigns",
    CHECKING_LINE,
  ],
  headlines: [{ title: "Nasdaq futures fall after Nvidia CEO resigns", source: "Benzinga", atText: "9:58 AM ET", url: "https://www.benzinga.com/a", tone: "negative" }],
};

const WHY = {
  answer: "SPY fell 1.36% this morning (9:30 AM-12:00 PM ET, Fri Nov 13). The biggest drags among its largest stocks were NVDA -5.89%, AVGO -3.83% and GOOGL -3.00%.",
  by: "code", note: WHY_LABELS.code,
  window: { label: "This morning", text: "9:30 AM-12:00 PM ET, Fri Nov 13" },
  reasons: [
    { title: "Executive change", text: "\"Nasdaq futures fall after Nvidia CEO resigns\"",
      sources: [{ source: "Benzinga", title: "Nasdaq futures fall after Nvidia CEO resigns", atText: "9:58 AM", url: "https://www.benzinga.com/a", tone: "negative" }] },
    { title: "Treasury yields", text: "\"Stock Market Midday: Nasdaq Plunges as Treasury Yields Hit 5%\"",
      sources: [{ source: "IBD", title: "Stock Market Midday", atText: "12:15 PM", url: "http://insecure.example.com/b", tone: "negative" },
        { source: "Yahoo", title: "Rebound", atText: "10:06 AM", url: "javascript:alert(1)", tone: "maybe" }] },
  ],
  drags: [{ symbol: "NVDA", pct: -5.89 }, { symbol: "AVGO", pct: -3.83 }, { symbol: "bad sym", pct: 1 }, { symbol: "GOOGL", pct: "x" }],
};

test("why_move answers have their own heading, not 'What I can answer'", () => {
  assert.equal(chatAnswer({ intent: "why_move", lines: ["x"] }).kind, "Why it moved");
  assert.notEqual(chatAnswer({ intent: "why_move", lines: ["x"] }).kind, "What I can answer");
  assert.equal(chatAnswer({ intent: "why_move", lines: ["x"] }).intent, "why_move");
});

test("tone: only the server's three words are kept, and a headline carries its tone", () => {
  assert.deepEqual(["positive", "Negative ", "neutral", "bullish", "", null, 3].map(chatTone), ["positive", "negative", "neutral", "", "", "", ""]);
  assert.deepEqual(TONE_LABELS, { positive: "Positive", negative: "Negative", neutral: "Neutral" });
  assert.equal(chatHeadline(FAST.headlines[0]).tone, "negative");
  assert.equal("tone" in chatHeadline({ title: "No tone sent" }), false, "the screen never makes one up");
});

test("chatWhy keeps the contract's fields, https links only, at most three reasons, tones the server's", () => {
  const why = chatWhy(WHY, false);
  assert.equal(why.answer, WHY.answer);
  assert.equal(why.by, "code");
  assert.equal(why.note, "From the headlines and prices below. Not confirmed.");
  assert.deepEqual(why.window, { label: "This morning", text: "9:30 AM-12:00 PM ET, Fri Nov 13" });
  assert.equal(why.reasons.length, 2);
  assert.deepEqual(why.reasons[0], { title: "Executive change", text: "\"Nasdaq futures fall after Nvidia CEO resigns\"",
    sources: [{ source: "Benzinga", title: "Nasdaq futures fall after Nvidia CEO resigns", time: "9:58 AM", url: "https://www.benzinga.com/a", tone: "negative" }] });
  assert.equal(why.reasons[1].sources[0].url, null, "http is not linked");
  assert.equal(why.reasons[1].sources[1].url, null, "javascript: is never a link");
  assert.equal(why.reasons[1].sources[1].tone, "", "an unknown tone word shows no tag");
  assert.deepEqual(why.drags, [{ symbol: "NVDA", pct: -5.89 }, { symbol: "AVGO", pct: -3.83 }]);
  const many = chatWhy({ ...WHY, reasons: Array.from({ length: 6 }, (_, i) => ({ title: `R${i}`, text: "t", sources: [] })) });
  assert.equal(many.reasons.length, WHY_MAX_REASONS);
  assert.equal(WHY_MAX_REASONS, 3);
});

test("AI reasons under the code's answer say so; only that one fixed note is taken from the server (round 2, 2026-11-14)", () => {
  const reasonsOnly = { ...WHY, by: "ai", note: WHY_LABELS.aiReasons };
  assert.equal(WHY_LABELS.aiReasons, "Reasons written by AI from the headlines below; the answer is from the prices. Not confirmed.",
    "the same text as momx/chat_why_card.py NOTE_AI_REASONS");
  assert.equal(chatWhy(reasonsOnly, true).note, WHY_LABELS.aiReasons);
  assert.equal(chatWhy(reasonsOnly, false), null, "still the owner's only");
  assert.equal(chatWhy({ ...WHY, note: WHY_LABELS.aiReasons }, false).note, WHY_LABELS.code, "a code card never claims AI reasons");
});

test("an AI-written answer is the owner's only, and always carries the AI label", () => {
  const ai = { ...WHY, by: "ai", note: "Trust me." };
  assert.equal(chatWhy(ai, false), null, "a regular user's screen drops an AI answer even if one arrived");
  assert.equal(chatWhy(ai, true).note, "Written by AI from the headlines and prices below. Not confirmed.", "the screen's own label, not the server's note");
  for (const bad of [null, "x", {}, { ...WHY, by: "someone" }, { ...WHY, answer: "  " }]) assert.equal(chatWhy(bad, true), null);
});

test("sources: a source with neither name nor title is dropped", () => {
  assert.equal(chatWhySource({ url: "https://a.example.com" }), null);
  assert.equal(chatWhySource({ title: "t", url: "https://www.cnbc.com/x" }).source, "cnbc.com");
});

test("layout: 'Writing the answer...' on top while waiting, then the answer; the numbers closed under it", () => {
  const fast = chatAnswer(FAST, true);
  assert.equal(fast.moreState, "waiting");
  assert.deepEqual(whyLayout(fast), { top: "wait", collapse: true });
  assert.equal(WHY_WAIT, "Writing the answer...");
  const done = chatMergeMore(fast, { more: { done: true, lines: [], articles: [], reasons: [], why: WHY } }, true);
  assert.equal(done.why.answer, WHY.answer);
  assert.deepEqual(whyLayout(done), { top: "why", collapse: true });
  // A failed second part, or one without a card: the lines stay open, as before.
  assert.deepEqual(whyLayout(chatMergeMore(fast, null, true)), { top: "", collapse: false });
  assert.deepEqual(whyLayout(chatMergeMore(fast, { more: { done: true, lines: [] } }, true)), { top: "", collapse: false });
  // Any other answer is laid out as before, even while its own second part loads.
  assert.deepEqual(whyLayout(chatAnswer({ intent: "news", lines: ["x"], more: "tok_news_123" })), { top: "", collapse: false });
  assert.equal(MORE_WAIT, "getting latest news...");
});

test("the numbers part: no pending line, and no headline printed twice (the tagged row is shown instead)", () => {
  const fast = chatAnswer(FAST, true);
  const texts = whyFactLines(fast.lines, fast.headlines).map((l) => l.text);
  assert.deepEqual(texts, [FAST.lines[0], FAST.lines[1]]);
  assert.ok(!texts.includes(CHECKING_LINE));
  // Without structured headlines the text lines stay.
  assert.deepEqual(whyFactLines(fast.lines, []).map((l) => l.text), FAST.lines.slice(0, 4));
  assert.deepEqual(fast.headlines.map((h) => h.tone), ["negative"]);
});

test("a headlines heading with clock times in it goes when every line under it was a tagged copy (2026-11-14 review)", () => {
  const title = "Stocks slide as Nvidia CEO resigns";
  const lines = [{ text: "This morning (9:30 AM-12:00 PM ET, Fri Nov 13): SPY -1.36% from the open." },
    { text: "The app's saved headlines from Thu 4:00 PM to Fri 12:01 PM:" }, { text: `Fri Nov 13 10:00 ET, Wire: ${title}` }];
  assert.deepEqual(whyFactLines(lines, [{ title }]).map((l) => l.text), [lines[0].text]);
});

test("drags print the server's percent with its sign", () => {
  assert.deepEqual([{ symbol: "NVDA", pct: -5.89 }, { symbol: "META", pct: 0.6 }].map(dragText), ["NVDA -5.89%", "META +0.60%"]);
});

test("the card's markup: answer, Key reasons, source links in a new tab, tone tags, a closed toggle", () => {
  const report = readFileSync(new URL("./ChatReport.jsx", import.meta.url), "utf8");
  const chat = readFileSync(new URL("./BotChat.jsx", import.meta.url), "utf8");
  assert.match(report, /<h4 className="chat-why-head">Key reasons<\/h4>/);
  assert.match(report, /data-testid="chat-why-source-link" href=\{s\.url\} target="_blank" rel="noopener noreferrer"/);
  assert.match(report, /aria-expanded=\{open\}/);
  // The toggle points at its body only while the body is on the page (2026-11-14 review).
  assert.match(report, /aria-controls=\{open \? id : undefined\}/);
  assert.match(report, /useState\(false\)/, "the numbers start closed");
  assert.match(report, /"Hide the numbers" : "Show the numbers"/);
  assert.match(chat, /layout\.top === "wait" \? <WhyWait \/> : layout\.top === "why" \? <WhyCard why=\{why\} \/> : null\}\s*\{layout\.collapse \? <ChatNumbers>/,
    "the answer slot sits above the numbers");
});
