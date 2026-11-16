// The ticker report in a chat answer: sections, the two views, and the two-part ("more") answer.
import assert from "node:assert/strict";
import test from "node:test";

import { chatAnswer, chatHome } from "./botChat.js";
import { MORE_FAILED, MORE_WAIT, SECTIONS_OPEN, chatHeadline, chatMergeMore, chatMoreBody, dropVwap, rulesText } from "./chatReport.js";

const report = {
  intent: "report", ticker: "NVDA",
  lines: ["NVDA report"],
  views: [{ key: "long", met: 3, total: 5 }, { key: "short", title: "Short term", met: 5, total: 8 }],
  sections: [
    { key: "price", title: "Price", lines: ["$190.10 as of 15:59 ET"], view: "short", asOf: "as of 15:59 ET", links: [{ label: "Open the chart", view: "Charts & OI", ticker: "NVDA" }] },
    { key: "levels", title: "Chart levels", lines: ["21 EMA $188.40, VWAP $188.10, 50 EMA $185.00", "VWAP $188.10"], view: "short" },
    { key: "news", title: "News", lines: ["2 headlines so far"], view: "short", pending: true },
    { key: "scanner", title: "On the scanner", lines: ["Mag7 calls 09:31-15:55"], summary: "On Mag7 all day", view: "short" },
    { key: "analysts", title: "Analysts", lines: ["Buy"], view: "long", pending: true },
    { key: "bad" },
    { key: "price", title: "Duplicate" },
  ],
  more: "tok_123",
};

test("sections keep the server's order, drop broken or repeated ones, and default the summary to the first line", () => {
  const a = chatAnswer(report);
  assert.deepEqual(a.sections.map((s) => s.key), ["price", "levels", "news", "scanner", "analysts"]);
  assert.equal(a.sections[3].summary, "On Mag7 all day");
  assert.equal(a.sections[0].summary, "$190.10 as of 15:59 ET");
  assert.equal(a.sections[0].links[0].ticker, "NVDA");
  assert.equal(SECTIONS_OPEN, 3);
});

test("no VWAP on the cards: the VWAP part of a line goes, a VWAP-only line goes", () => {
  const a = chatAnswer(report);
  assert.deepEqual(a.sections[1].lines, ["21 EMA $188.40, 50 EMA $185.00"]);
  assert.equal(dropVwap("Above VWAP"), "");
  assert.equal(dropVwap("Price $10 · VWAP $9.80 · 9 EMA $9.90"), "Price $10, 9 EMA $9.90");
  const home = chatHome({ cards: [{ kind: "page_ticker", title: "Keep looking at NVDA", lines: ["$190.10", "VWAP $188.40"] }], chips: ["spy vwap on 5m", "NVDA price"] });
  assert.deepEqual(home.cards[0].lines, ["$190.10"]);
  assert.deepEqual(home.chips, ["NVDA price"]);
  const table = chatAnswer({ table: { kind: "rows", columns: [{ key: "level", label: "Level" }, { key: "vwap", label: "VWAP" }], rows: [{ level: "x", vwap: "1" }] } }).table;
  assert.deepEqual(table.columns.map((c) => c.key), ["level"]);
});

test("the views: short term first, each with its own rules count and its own sections", () => {
  const a = chatAnswer(report);
  assert.deepEqual(a.views.map((v) => [v.key, v.title, v.rules]), [["short", "Short term", "5 of 8 rules"], ["long", "Long term", "3 of 5 rules"]]);
  assert.deepEqual(a.views[0].sections.map((s) => s.key), ["price", "levels", "news", "scanner"]);
  assert.deepEqual(a.views[1].sections.map((s) => s.key), ["analysts"]);
  assert.equal(rulesText({ met: 1, total: 1 }), "1 of 1 rule");
  assert.equal(rulesText({ met: 9, total: 8 }), "");
  assert.equal(rulesText({}), "");
  assert.deepEqual(chatAnswer({ lines: ["x"] }).views, [], "an answer with no report has no views");
});

test("a 'more' token: waiting at once, one follow-up body, and the token is spent after merging", () => {
  const a = chatAnswer(report);
  assert.equal(a.more, "tok_123");
  assert.equal(a.moreState, "waiting");
  assert.deepEqual(chatMoreBody(a.more), { more: "tok_123" });
  assert.equal(chatAnswer({ ...report, more: "bad token <script>" }).more, "");
  assert.equal(MORE_WAIT, "getting latest news...");
});

test("the second part fills pending sections and puts headlines and reasons into the news section", () => {
  const a = chatAnswer(report);
  const merged = chatMergeMore(a, {
    sections: [{ key: "analysts", title: "Analysts", lines: ["Buy, 60 analysts"], analyst: { buy: 1 } }, { key: "extra", title: "Extra", lines: ["late part"] }],
    headlines: [
      { title: "Nvidia beats", source: "Reuters", url: "https://example.com/a" },
      { title: "Nvidia beats", source: "Reuters", url: "https://example.com/a" },
      { title: "Bad link", url: "javascript:alert(1)" },
    ],
    reasons: ["Earnings beat lifted chip names", "Above VWAP all day"],
  });
  assert.equal(merged.more, "", "the token is spent");
  assert.equal(merged.moreState, "done");
  const news = merged.sections.find((s) => s.key === "news");
  assert.equal(news.pending, false);
  assert.deepEqual(news.headlines.map((h) => [h.title, h.source, h.url]), [["Nvidia beats", "Reuters", "https://example.com/a"], ["Bad link", "", ""]]);
  assert.deepEqual(news.reasons, ["Earnings beat lifted chip names"]);
  const analysts = merged.sections.find((s) => s.key === "analysts");
  assert.equal(analysts.pending, false);
  assert.equal(analysts.view, "long", "a filled section stays in its view");
  assert.deepEqual(analysts.analyst, { buy: 1 });
  assert.equal(merged.sections.at(-1).key, "extra");
  // The views show the merged sections, not the ones from before the second part.
  assert.deepEqual(merged.views[1].sections.find((x) => x.key === "analysts").analyst, { buy: 1 });
  assert.equal(merged.views[0].sections.find((x) => x.key === "news").headlines.length, 2);
});

test("a plain news answer gets the headlines and reasons on the answer itself", () => {
  const a = chatAnswer({ intent: "news", ticker: "NVDA", lines: ["2 headlines"], more: "n1" });
  const merged = chatMergeMore(a, { headlines: [{ title: "Chips rally", url: "https://example.com/b" }], reasons: ["Sector-wide rally"] });
  assert.deepEqual(merged.headlines, [{ title: "Chips rally", source: "example.com", url: "https://example.com/b", time: "" }]);
  assert.deepEqual(merged.reasons, ["Sector-wide rally"]);
});

test("a failed second part: one plain line, pending sections stop loading, nothing asked again", () => {
  const merged = chatMergeMore(chatAnswer(report), null);
  assert.equal(merged.moreState, "failed");
  assert.equal(merged.more, "");
  assert.ok(merged.sections.filter((s) => s.key === "news" || s.key === "analysts").every((s) => !s.pending && s.failed));
  assert.match(MORE_FAILED, /could not be loaded/);
  assert.equal(chatMergeMore(null, {}), null);
});

test("headline links are http(s) only and name their source", () => {
  assert.equal(chatHeadline({ title: "x", url: "file:///c:/x" }).url, "");
  assert.equal(chatHeadline({ title: "x", url: "https://www.cnbc.com/a" }).source, "cnbc.com");
  assert.equal(chatHeadline({ url: "https://a.com" }), null);
});

// 2026-11-11 review: the screen read an invented reply shape, so on the real server the second part added nothing.
// These run replies captured from POST /api/bot-chat (tests/api_harness.py, fake AI) through the screen's own code.
import { readFileSync } from "node:fs";
import { CHECKING_LINE, chatMoreReply, chatReason, chatReasons, chatTechnical, placeHeadlines, technicalData } from "./chatReport.js";
import { technicalCard } from "./miniEventChart.js";

const captured = JSON.parse(readFileSync(new URL("./chatReport.serverReply.json", import.meta.url), "utf8"));

test("captured server reply: the route's {more: {...}} wrapper is unwrapped and its lines are added", () => {
  for (const [question, { fast, more }] of Object.entries(captured)) {
    assert.ok(more && typeof more.more === "object", `${question}: the route wraps the second part in "more"`);
    const a = chatAnswer(fast);
    assert.ok(a.more, `${question}: the fast part carries a token`);
    const merged = chatMergeMore(a, more);
    assert.equal(merged.moreState, "done");
    const texts = merged.lines.map((l) => l.text);
    assert.ok(more.more.lines.every((line) => texts.includes(line.replace(/^- /, ""))), `${question}: the second part's lines are on the answer`);
    assert.deepEqual(chatMergeMore(a, more.more).lines, merged.lines, "wrapped or unwrapped, the same answer");
  }
  assert.equal(chatMoreReply(null), null);
  assert.deepEqual(chatMoreReply({ lines: ["x"] }), { lines: ["x"] });
});

test("captured server reply: the server's articles show as headline buttons with their time", () => {
  const { fast, more } = captured["latest news on nvda"];
  const a = chatAnswer(fast);
  assert.deepEqual(a.headlines, [{ title: "Chipmaker announces a thing", source: "Wire", url: "https://example.test/a", time: "Wed Nov 11 15:30 ET" }]);
  const merged = chatMergeMore(a, more);
  assert.equal(merged.headlines.length, 1, "the same article from the second part is not shown twice");
});

// Changed on purpose (2026-11-11 design change): reasons are plain lines the server built from a fixed category and the
// saved headline. A reason object (an AI sentence) is no longer turned into a line; it is dropped.
test("captured server reply: reasons are the server's plain lines, category then headline, source and time", () => {
  const { fast, more } = captured["why is nvda up today"];
  const reason = more.more.reasons[0];
  assert.equal(reason, 'Product or business news: "Chipmaker announces a thing" (Wire, 15:30 ET)');
  assert.equal(chatReason(reason), reason);
  assert.deepEqual(chatReasons([reason, "plain line", { text: "A chip announcement was reported.", headline: "x" }, { text: "" }, 7]), [reason, "plain line"]);
  // The server already wrote the reason into its lines, so it is shown once, there, and not again underneath.
  const merged = chatMergeMore(chatAnswer(fast), more);
  assert.ok(merged.lines.some((l) => l.bullet && l.text === chatReason(reason)));
  assert.deepEqual(merged.reasons, []);
  // A reason the lines do not carry is shown under "What the news points to".
  const quiet = chatMergeMore(chatAnswer(fast), { more: { ...more.more, lines: [] } });
  assert.deepEqual(quiet.reasons, [chatReason(reason)]);
  assert.equal(quiet.headlines.length, 1);
});

test("captured server reply: the full report gets its Technical card, filled sections, and news on the 'why' part", () => {
  const { fast, more } = captured["nvda full report"];
  const a = chatAnswer(fast);
  assert.ok(a.sections.length >= 8);
  assert.equal(a.headlines.length, 0, "the report's articles sit on its 'why' section, not under the answer");
  assert.equal(a.sections.find((s) => s.key === "why").headlines.length, 1);
  const fastCard = technicalCard(a.technical);
  assert.deepEqual(fastCard.pills.map((p) => p.key), ["short", "mid", "long"], "the server's three views show as the card's pills");
  const merged = chatMergeMore(a, more);
  const card = technicalCard(merged.technical);
  assert.deepEqual(card.pills.map((p) => [p.key, p.verdict.verdict, p.verdict.strength]),
    more.more.views.map((v) => [v.view, v.verdict.toLowerCase(), v.strength]));
  assert.ok(!/advice/i.test(card.pills[0].verdict.word), "the not-advice label is never the verdict word");
  assert.ok(card.daily.bars.length > 20 && card.weekly.bars.length > 20, "the mini chart has daily and weekly bars");
  assert.ok(card.daily.markers.length > 0, "the events are marked");
  assert.equal(card.daily.resistance.label, "Call wall (High OI board)");
  const tech = merged.sections.find((s) => s.key === "technicals");
  assert.ok(tech.lines.some((l) => /21 EMA/.test(l)), "the chart numbers fill the technicals section");
  const why = merged.sections.find((s) => s.key === "why");
  assert.equal(why.headlines.length, 1);
  assert.equal(why.links.length, 1, "a filled section keeps its links when the second part sends none");
  assert.deepEqual(why.reasons, [], "the reason is already in the section's lines");
});

test("chatTechnical: nothing to show is null; a bad view is left out", () => {
  assert.equal(chatTechnical([], null), null);
  assert.equal(chatTechnical([{ view: "week", verdict: "Bullish" }], {}), null);
  const t = chatTechnical([{ view: "short", verdict: "Bearish", strength: 3, reason: "r", label: "From the app's signals, not advice." }], null);
  assert.deepEqual(t.terms, { short: { verdict: "bearish", strength: 3, reason: "r" } });
  const p = placeHeadlines([], [{ title: "x" }]);
  assert.deepEqual(p.headlines, [{ title: "x" }]);
});

// F4 (2026-11-11): a real report showed its content twice (the fast reply's flat lines repeat every section card) and the
// "Checking Google News..." line never cleared after the second part arrived.
test("captured server reply: a report shows only its header line above the cards, never the sections again", () => {
  const { fast } = captured["nvda full report"];
  assert.ok(fast.lines.length > 40, "the server sends the report flat as well (for clients without cards)");
  const a = chatAnswer(fast);
  assert.deepEqual(a.lines, [{ bullet: false, text: fast.lines[0] }], "only the header line; the cards hold the rest");
  const flat = a.lines.map((l) => l.text);
  for (const s of a.sections) for (const line of s.lines) assert.ok(!flat.includes(line), `"${line}" is on its card, not repeated above`);
});

test("captured server reply: the second part clears the pending 'checking' line, in the lines and in the cards", () => {
  assert.equal(CHECKING_LINE, "Checking Google News and reading the headlines...", "the same text as momx/chat_why.py CHECKING_LINE");
  const texts = (lines) => lines.map((l) => (typeof l === "string" ? l : l.text));
  const { fast: whyFast, more: whyMore } = captured["why is nvda up today"];
  const why = chatAnswer(whyFast);
  assert.ok(texts(why.lines).includes(CHECKING_LINE), "while waiting the answer says it is checking");
  const done = chatMergeMore(why, whyMore);
  assert.ok(!texts(done.lines).includes(CHECKING_LINE), "the checking line is gone once the second part is in");
  assert.ok(texts(done.lines).includes(whyMore.more.lines[0]), "and the second part's lines are there");
  assert.ok(!texts(chatMergeMore(why, null).lines).includes(CHECKING_LINE), "a failed second part does not leave it either");

  const { fast, more } = captured["nvda full report"];
  const report = chatMergeMore(chatAnswer(fast), more);
  assert.deepEqual(texts(report.lines), more.more.lines, "the second part's header replaces the fast header ('follow in a moment')");
  for (const s of report.sections) assert.ok(!s.lines.includes(CHECKING_LINE), `${s.key}: no checking line left on a card`);
  // A second part that left the "why" card out: the fast card stays, without the checking line.
  const partial = chatMergeMore(chatAnswer(fast), { more: { ...more.more, sections: more.more.sections.filter((s) => s.key !== "why") } });
  const keptWhy = partial.sections.find((s) => s.key === "why");
  assert.ok(keptWhy && keptWhy.lines.length && !keptWhy.lines.includes(CHECKING_LINE));
  const failed = chatMergeMore(chatAnswer(fast), null);
  for (const s of failed.sections) assert.ok(!s.lines.includes(CHECKING_LINE), `${s.key}: a failed second part leaves no checking line`);
});

// The Long Term page (2026-11-11): GET /api/long-term/analyst now carries "technical". In the chat report's own shape
// (views + miniChart) the card read nothing and stayed hidden; technicalData reads both shapes.
test("technicalData: the Long Term route's technical shows its card in the chat report's shape and in the card's own", () => {
  const { views, miniChart } = captured["nvda full report"].more.more;
  assert.equal(technicalCard({ views, miniChart }), null, "the raw server shape is not the card's shape");
  const card = technicalCard(technicalData({ views, miniChart, asOf: "as of 15:58 ET" }));
  assert.deepEqual(card.pills.map((p) => [p.key, p.verdict.verdict]), views.map((v) => [v.view, v.verdict.toLowerCase()]));
  assert.ok(card.daily.bars.length > 20 && card.weekly.bars.length > 20);
  assert.equal(card.asOf, "as of 15:58 ET");
  assert.deepEqual(technicalCard(technicalData(views)).pills.length, views.length, "the views list alone");
  const own = { terms: { long: { verdict: "bullish", strength: 4, reason: "r" } } };
  assert.equal(technicalData(own), own, "the card's own shape passes as it is");
  for (const none of [null, undefined, "x", 5]) assert.equal(technicalData(none), null);
  assert.equal(technicalCard(technicalData({ views: [] })), null, "nothing to show hides the card");
});

// 2026-11-11 review (rv4): "should i buy nvda" through the real route (captured in chatReport.adviceReply.json, fake AI).
// The server puts its plain "no advice" line before the report's header; the screen kept only line 0 and then the merge
// put the second part's header in its place, so the direct answer was gone once the chart numbers arrived.
import { FAST_HEADER, PENDING_LINES, READING_SAVED_LINE } from "./chatReport.js";

const advice = JSON.parse(readFileSync(new URL("./chatReport.adviceReply.json", import.meta.url), "utf8"))["should i buy nvda"];
const NO_ADVICE_LINE = "I can't say whether to buy or sell. Here is what the app has.";   // momx/chat_ai.py NO_ADVICE_LINE

test("captured 'should i buy nvda': the no-advice line stays on screen before and after the second part", () => {
  const texts = (lines) => lines.map((l) => (typeof l === "string" ? l : l.text));
  assert.equal(advice.fast.lines[0], NO_ADVICE_LINE, "the server sends the no-advice line first");
  const a = chatAnswer(advice.fast);
  assert.deepEqual(texts(a.lines), advice.fast.lines.slice(0, 2), "the no-advice line and the report header, not the flat sections");
  const m = chatMergeMore(a, advice.more);
  assert.equal(texts(m.lines)[0], NO_ADVICE_LINE, "still the first line once the second part is in");
  assert.ok(texts(m.lines).includes(advice.more.more.lines[0]), "the second part's header is shown");
  assert.ok(!texts(m.lines).some((t) => FAST_HEADER.test(t)), "the fast 'more follows in a moment' header is gone");
  assert.deepEqual(texts(chatMergeMore(a, null).lines), advice.fast.lines.slice(0, 2), "a failed second part keeps both lines");
});

test("chatAnswer keeps another tool's lines that come after the report's flat sections", () => {
  const fast = { ...advice.fast, lines: [...advice.fast.lines, "SPY +0.40% today (scanner board, 15:55 ET)."] };
  const shown = chatAnswer(fast).lines.map((l) => l.text);
  assert.ok(shown.includes("SPY +0.40% today (scanner board, 15:55 ET)."), "a joined tool's line is not dropped");
  for (const s of chatAnswer(fast).sections) assert.ok(!shown.includes(`${s.title}:`), `"${s.title}:" is a card, not a line`);
});

test("a past day's 'reading the saved headlines' line clears when the second part lands or fails", () => {
  assert.equal(READING_SAVED_LINE, "Reading that day's saved headlines...", "the same text as momx/chat_why.py READING_SAVED_LINE");
  assert.deepEqual(PENDING_LINES, [CHECKING_LINE, READING_SAVED_LINE], "the same list as momx/chat_why.py PENDING_LINES");
  const fast = { intent: "why_move", ticker: "NVDA", more: "tok123",
    lines: ["NVDA closed 172.59 on 2026-11-10, -3.88% from 179.55 at the 2026-11-09 close (saved daily bars).",
      "The app's saved headlines from 2026-11-10:", "- Headline A", READING_SAVED_LINE] };
  const texts = (lines) => lines.map((l) => l.text);
  const a = chatAnswer(fast);
  assert.ok(texts(a.lines).includes(READING_SAVED_LINE), "while waiting it says so");
  const done = chatMergeMore(a, { more: { lines: ["The headlines point to chip export news."], reasons: [] } });
  assert.deepEqual(texts(done.lines), ["NVDA closed 172.59 on 2026-11-10, -3.88% from 179.55 at the 2026-11-09 close (saved daily bars).",
    "The app's saved headlines from 2026-11-10:", "Headline A", "The headlines point to chip export news."]);
  assert.ok(!texts(chatMergeMore(a, null).lines).includes(READING_SAVED_LINE), "a failed second part does not leave it either");
  const card = chatMergeMore({ ...a, sections: [{ key: "why", title: "Why it moved", lines: ["x", READING_SAVED_LINE], summary: "x", headlines: [], reasons: [], links: [], pending: false }] }, null);
  assert.deepEqual(card.sections[0].lines, ["x"], "nor on a card");
});
