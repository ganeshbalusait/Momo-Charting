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

// 2026-11-11 design change (changed on purpose): AI-written text is never shown. The test used to check that an "ai"
// reply was shown; now an "ai" field from any server is ignored and only the record lines reach the screen.
test("AI-written text is never shown, even when a server sends it; the record lines always stay", () => {
  const withAi = chatAnswer({ intent: "what_if", ticker: "SPY", day: "2026-11-09", lines: ["The 0.20-delta call ($753) cost $1.15 at the mid."],
    ai: { text: "  Hold SPY; we expect it to reach 600.  ", source: "openai" }, understoodBy: "AI" });
  assert.equal("ai" in withAi, false);
  assert.ok(!JSON.stringify(withAi).includes("reach 600"), "the AI's text is nowhere in what the screen draws");
  assert.equal(withAi.understoodByAi, true);
  assert.deepEqual(withAi.lines, [{ bullet: false, text: "The 0.20-delta call ($753) cost $1.15 at the mid." }]);
  for (const none of [{ lines: ["x"] }, { lines: ["x"], ai: null }, { lines: ["x"], ai: { text: "   " } }, { lines: ["x"], ai: "text" }, { lines: ["x"], ai: { text: 5 } }]) {
    assert.equal("ai" in chatAnswer(none), false);
    assert.equal(chatAnswer(none).understoodByAi, false);
  }
});

test("a definition from the glossary has its own heading", () => {
  assert.equal(chatAnswer({ intent: "define", lines: ["Theta: Theta is time decay."] }).kind, "Glossary");
});

test("the scanner answers have plain headings", () => {
  assert.deepEqual(["scanner_list", "scanner_ticker", "scanner_top"].map((intent) => chatAnswer({ intent, lines: ["x"] }).kind),
    ["Scanner list", "Scanner at that time", "Stayed on top"]);
});

test("the page's ticker goes with the first question; the conversation's own subject wins after that", async () => {
  const { chatSendContext } = await import("./botChat.js");
  assert.deepEqual(chatSendContext([], "NVDA"), { ticker: "NVDA", day: "", fromPage: true });
  assert.deepEqual(chatSendContext([], " nvda "), { ticker: "NVDA", day: "", fromPage: true });
  assert.equal(chatSendContext([], ""), null);
  assert.equal(chatSendContext(null, undefined), null);
  // An answer about no ticker (help, the bot) does not end the page's say.
  assert.deepEqual(chatSendContext([{ id: 1, subject: null }], "NVDA"), { ticker: "NVDA", day: "", fromPage: true });
  const own = [{ id: 1, subject: { ticker: "SPY", day: "2026-11-09", side: "bear" } }];
  assert.deepEqual(chatSendContext(own, "NVDA"), { ticker: "SPY", day: "2026-11-09", side: "bear" });
  assert.deepEqual(chatSendContext(own, ""), { ticker: "SPY", day: "2026-11-09", side: "bear" });
});

test("starters follow the ticker on screen; with none the window offers scanner questions and the Ask tab keeps its list", async () => {
  const { CHAT_DOCK_STARTERS, chatPageTicker, chatPlaceholder, chatStarters } = await import("./botChat.js");
  assert.deepEqual(chatStarters("NVDA", true), ["see NVDA", "why was NVDA missed", "NVDA at 9:30 in the scanner"]);
  assert.deepEqual(chatStarters("tsla"), ["see TSLA", "why was TSLA missed", "TSLA at 9:30 in the scanner"]);
  assert.deepEqual(chatStarters("", true), ["scanner at 9:30 give me the list of tickers", "which tickers stayed on top", "what is the bot doing"]);
  assert.equal(chatStarters("", true), CHAT_DOCK_STARTERS);
  assert.equal(chatStarters("", false), CHAT_STARTERS);
  assert.ok(CHAT_STARTERS.some((q) => /scanner/.test(q)));
  for (const q of [...CHAT_DOCK_STARTERS, ...chatStarters("NVDA")]) assert.equal(chatQuestion(q), q);
  assert.equal(chatPlaceholder("NVDA"), "For example: NVDA at 9:30 in the scanner");
  assert.match(chatPlaceholder(""), /scanner at 9:30/);
  assert.equal(chatPageTicker(null), "");
  assert.equal(chatPageTicker("  brk.b "), "BRK.B");
});

test("the window knows when the Ask tab is on screen, and a double goodbye counts once", async () => {
  const { markPageChat, pageChatShowing, subscribePageChat } = await import("./botChat.js");
  let told = 0;
  const stop = subscribePageChat(() => { told += 1; });
  assert.equal(pageChatShowing(), false);
  const gone = markPageChat();
  assert.equal(pageChatShowing(), true);
  const second = markPageChat();
  gone();
  gone();
  assert.equal(pageChatShowing(), true);
  second();
  assert.equal(pageChatShowing(), false);
  assert.equal(told, 4);
  stop();
  markPageChat()();
  assert.equal(told, 4);
});

// Changed on purpose (2026-11-11): there is no AI reply any more, so an "ai" field no longer hides the note; it is ignored.
test("when the AI is not being asked the answer says why; an AI reply field changes nothing", () => {
  assert.equal(chatAnswer({ lines: ["x"], aiNote: " the AI service is over its free limit " }).aiNote, "the AI service is over its free limit");
  const sent = chatAnswer({ lines: ["x"], aiNote: "the AI service did not answer", ai: { text: "Up 140%." } });
  assert.equal(sent.aiNote, "the AI service did not answer");
  assert.ok(!JSON.stringify(sent).includes("Up 140%"));
  assert.equal(chatAnswer({ lines: ["x"], aiNote: 5 }).aiNote, "");
});

test("a regular user is offered scanner questions only, with and without a ticker on screen", async () => {
  const { CHAT_SCANNER_STARTERS, chatDockNote, chatDockTitle, chatPlaceholder, chatRefused, chatStarters } = await import("./botChat.js");
  assert.deepEqual(chatStarters("", true, false), ["scanner at 9:30 give me the list of tickers", "which tickers stayed on top"]);
  assert.equal(chatStarters("", true, false), CHAT_SCANNER_STARTERS);
  assert.deepEqual(chatStarters(" nvda ", true, false), ["NVDA at 9:30 in the scanner", "how long was NVDA in the top", "scanner at 9:30 give me the list of tickers", "which tickers stayed on top"]);
  assert.equal(chatDockNote(false), "Answers come from the app's saved market data. It only reads.");
  const shown = [...chatStarters("", true, false), ...chatStarters("NVDA", true, false), chatPlaceholder(""), chatPlaceholder("NVDA"), chatDockNote(false), chatDockTitle(false), chatRefused(false)];
  for (const words of shown) assert.doesNotMatch(words, /\b(bot|paper|scorecard|trades?|profit|loss|missed|failed)\b/i);
  for (const q of [...chatStarters("", true, false), ...chatStarters("NVDA", true, false)]) assert.equal(chatQuestion(q), q);
});

test("the owner's screen is unchanged, and a caller that says nothing is the owner's Ask tab", async () => {
  const { CHAT_DOCK_STARTERS, chatDockNote, chatDockTitle, chatRefused, chatStarters } = await import("./botChat.js");
  assert.equal(chatStarters("", true, true), CHAT_DOCK_STARTERS);
  assert.equal(chatStarters("", false), CHAT_STARTERS);
  assert.deepEqual(chatStarters("NVDA", true, true), ["see NVDA", "why was NVDA missed", "NVDA at 9:30 in the scanner"]);
  assert.equal(chatDockTitle(true), "Ask about a ticker, the scanner or the bot");
  assert.equal(chatDockNote(true), "Answers come from the app's records. It only reads; it cannot place or change a trade.");
  assert.equal(chatRefused(true), "Only the owner can ask about the bot's trades.");
});

test("Esc goes to the chat only when nothing else is waiting for it", async () => {
  const { CHAT_ESC_OWNERS, chatTakesEsc } = await import("./botChat.js");
  const inside = { name: "chat input" };
  const outside = { name: "toolbar button" };
  const panel = { getBoundingClientRect: () => ({ left: 100, top: 200, width: 40, height: 60 }), contains: (node) => node === inside };
  const screen = ({ atCentre = inside, focus = outside, open = null } = {}) => ({
    activeElement: focus,
    elementFromPoint: (x, y) => (x === 120 && y === 230 ? atCentre : null),
    querySelector: (selector) => (selector === CHAT_ESC_OWNERS ? open : null),
  });
  const menu = { name: "saved grids menu" };
  assert.equal(chatTakesEsc(panel, screen()), true, "nothing else open");
  assert.equal(chatTakesEsc(panel, screen({ focus: inside })), true);
  assert.equal(chatTakesEsc(panel, screen({ open: menu })), false, "a menu that does not reach the middle of the window keeps its Esc");
  assert.equal(chatTakesEsc(panel, screen({ open: menu, focus: inside })), true, "typing in the chat: Esc is the chat's");
  assert.equal(chatTakesEsc(panel, screen({ atCentre: outside })), false, "something covers the window");
  assert.equal(chatTakesEsc(panel, screen({ atCentre: outside, focus: inside })), false, "covered, even with the cursor left in it");
  assert.equal(chatTakesEsc(null, screen()), false);
  // Native <details> have no Esc of their own, and some stay open on the page for good, so they must not hold the key.
  assert.match(CHAT_ESC_OWNERS, /\[role='menu'\]/);
  assert.match(CHAT_ESC_OWNERS, /\.global-price-alert-popover/);
  assert.doesNotMatch(CHAT_ESC_OWNERS, /details/);
});

test("once the ticker on screen changes, what was said about the old one no longer goes along", async () => {
  const { chatSendContext } = await import("./botChat.js");
  const nvda = { id: 1, pageTicker: "NVDA", subject: { ticker: "NVDA", day: "2026-11-09", side: "bull" } };
  const onPage = { ticker: "SPY", day: "", fromPage: true };
  assert.deepEqual(chatSendContext([nvda], "NVDA"), { ticker: "NVDA", day: "2026-11-09", side: "bull" });  // same chart: carries on
  assert.deepEqual(chatSendContext([nvda], "SPY"), onPage);                                                // the chart moved
  assert.deepEqual(chatSendContext([nvda], " spy "), onPage);
  // A help answer right after the move has no ticker: the next question must not fall back to NVDA.
  assert.deepEqual(chatSendContext([nvda, { id: 2, pageTicker: "SPY", subject: null }], "SPY"), onPage);
  // Asking about another ticker while the chart stays put carries on with that ticker, until the chart moves.
  const tsla = { id: 2, pageTicker: "NVDA", subject: { ticker: "TSLA", day: "2026-11-09" } };
  assert.deepEqual(chatSendContext([nvda, tsla], "NVDA"), { ticker: "TSLA", day: "2026-11-09" });
  assert.deepEqual(chatSendContext([nvda, tsla], "SPY"), onPage);
  // A page with no ticker does not end the conversation; coming back from it to a chart starts from that chart.
  assert.deepEqual(chatSendContext([nvda], ""), { ticker: "NVDA", day: "2026-11-09", side: "bull" });
  assert.deepEqual(chatSendContext([nvda, { id: 2, pageTicker: "", subject: null }], "NVDA"), { ticker: "NVDA", day: "", fromPage: true });
  // Moving there and back: only the newest stretch on this ticker counts.
  const spy = { id: 2, pageTicker: "SPY", subject: { ticker: "SPY", day: "2026-11-09" } };
  assert.deepEqual(chatSendContext([nvda, spy], "NVDA"), { ticker: "NVDA", day: "", fromPage: true });
});

const LINK = { label: "Open NVDA on Mag7 History, 09:30", view: "MomX Scanner", list: "Mag7", side: "bull", tab: "history", day: "2026-11-10", ticker: "NVDA", time: "09:30" };

// Changed on purpose (2026-11-11): the AI's "general" text is no longer kept; definitions come as glossary lines.
test("an answer keeps its links, table and time; an AI general note and nothing else gets through", async () => {
  const a = chatAnswer({ intent: "scanner_ticker", lines: ["x"], links: [LINK, { view: "Nope" }, { label: "bad", view: "Sectors", ticker: "<b>" }], general: "  RVOL is relative volume.  ", asOf: " as of 22:15 ET " });
  assert.deepEqual(a.links, [LINK]);
  assert.equal("general" in a, false);
  assert.ok(!JSON.stringify(a).includes("RVOL is relative volume"));
  assert.equal(a.asOf, "as of 22:15 ET");
  assert.equal(a.table, null);
  for (const broken of [null, {}, { links: "x", general: 5, asOf: {}, table: "x" }]) {
    const none = chatAnswer(broken);
    assert.deepEqual([none.links, none.asOf, none.table], [[], "", null]);
  }
  assert.equal(chatAnswer({ intent: "scanner_day", lines: ["x"] }).kind, "Scanner that day");
  assert.equal(chatAnswer({ intent: "open_chart", lines: ["x"] }).kind, "Go to the chart");
  assert.equal(chatAnswer({ intent: "bot_why_missed", lines: ["x"] }).kind, "Why it was missed");
});

test("a regular user's answer never carries a link to an owner page, even if the server sent one", () => {
  const reply = { lines: ["x"], links: [{ label: "Bot", view: "Bot" }, { label: "Paper", view: "Paper Trading" }, { label: "Chart", view: "Charts & OI", ticker: "SPY" }],
    table: { kind: "rows", title: "t", columns: [{ key: "symbol", label: "Symbol" }], rows: [{ symbol: "SPY" }], link: { label: "Bot", view: "Bot" } } };
  assert.deepEqual(chatAnswer(reply, false).links, [{ label: "Chart", view: "Charts & OI", ticker: "SPY" }]);
  assert.equal(chatAnswer(reply, false).table.link, null);
  assert.equal(chatAnswer(reply, true).links.length, 3);
  assert.equal(chatAnswer(reply, true).table.link.view, "Bot");
});

test("a table of rows is limited and cleaned; a scanner day is only a checked reference", async () => {
  const { chatRowLink, chatTable } = await import("./botChat.js");
  const rows = Array.from({ length: 60 }, (_, i) => ({ symbol: `T${i}`, price: i + 0.5, junk: "x", bad: { a: 1 } }));
  const table = chatTable({ kind: "rows", title: " Top ", note: " n ", columns: [{ key: "symbol", label: "Symbol" }, { key: "price", label: "Price", align: "right" }, { key: "bad", label: "Bad", align: "sideways" }, { nokey: 1 }, null], rows: [...rows, null, 5],
    link: { label: "History", view: "MomX Scanner", tab: "history", time: "10:15" } });
  assert.equal(table.rows.length, 40);
  assert.deepEqual(table.columns.map((c) => c.align), ["left", "right", "left"]);
  assert.deepEqual(table.rows[1], { symbol: "T1", price: "1.5", bad: "" });
  assert.deepEqual([table.title, table.note], ["Top", "n"]);
  assert.deepEqual(chatRowLink(table, table.rows[1]), { label: "Open T1", view: "MomX Scanner", ticker: "T1", tab: "history", time: "10:15" });
  assert.equal(chatRowLink({ ...table, link: null }, table.rows[1]), null);
  assert.equal(chatRowLink(table, { symbol: "not a ticker!" }), null);
  for (const bad of [null, "x", {}, { kind: "html" }, { kind: "rows" }, { kind: "rows", columns: [], rows: [{}] }, { kind: "rows", columns: [{ key: "a", label: "A" }], rows: [] }]) assert.equal(chatTable(bad), null);

  const day = { kind: "scanner_day", title: "NVDA", ticker: "nvda", day: "2026-11-10", sections: [{ list: "Mag7", side: "bull" }, { list: "Watchlist", side: "bear" }, { list: "<x>", side: "bull" }, { list: "Mag7", side: "up" }, null], extra: "dropped" };
  assert.deepEqual(chatTable(day), { kind: "scanner_day", title: "NVDA", ticker: "NVDA", day: "2026-11-10", sections: [{ list: "Mag7", side: "bull" }, { list: "Watchlist", side: "bear" }] });
  assert.equal(chatTable({ ...day, ticker: "<b>" }), null);
  assert.equal(chatTable({ ...day, day: "soon" }), null);
  assert.equal(chatTable({ ...day, sections: [] }), null);
});

test("the tray: the newest answer's links, then its questions; the page's questions while waiting or when it has none", async () => {
  const { chatTray } = await import("./botChat.js");
  const starters = ["a", "b"];
  assert.deepEqual(chatTray([], starters), { links: [], chips: starters });
  assert.deepEqual(chatTray(null, null), { links: [], chips: [] });
  const answered = { answer: chatAnswer({ lines: ["x"], links: [LINK], suggest: ["why NVDA"] }) };
  assert.deepEqual(chatTray([answered], starters), { links: [LINK], chips: ["why NVDA"] });
  assert.deepEqual(chatTray([answered, { answer: null }], starters), { links: [], chips: starters }, "busy");
  assert.deepEqual(chatTray([answered, { answer: chatAnswer({ intent: "error", lines: ["over the limit"] }) }], starters), { links: [], chips: starters }, "a reply with none");
});

test("the opening cards: empty ones left out, and an owner card never reaches a regular user", async () => {
  const { chatHome } = await import("./botChat.js");
  const cards = [
    { kind: "page_ticker", title: "Keep looking at NVDA", lines: ["$190.10", "", 5], asOf: "as of 15:59 ET", links: [{ label: "Chart", view: "Charts & OI", ticker: "NVDA" }, { view: "Bot", label: "Bot" }], question: " NVDA  price " },
    { kind: "scanner_top", title: "Top of the scanner", lines: [] },
    { kind: "walls", title: "", lines: ["x"] },
    { kind: "bot_trades", title: "Open positions", lines: ["2 open"], links: [{ label: "Bot", view: "Bot" }], question: "what is the bot doing" },
    { kind: "x", title: "Open bot trades", lines: ["2 open"] },
    null, 7];
  const owner = chatHome({ cards, chips: ["what is the bot doing", " a  b ", "", 5] }, true);
  assert.deepEqual(owner.cards.map((c) => c.kind), ["page_ticker", "bot_trades", "x"]);
  assert.equal(owner.cards[0].links.length, 2);
  assert.deepEqual(owner.chips, ["what is the bot doing", "a b", "5"]);
  const regular = chatHome({ cards, chips: ["what is the bot doing", "show paper trades", "NVDA price"] }, false);
  assert.deepEqual(regular.cards.map((c) => c.kind), ["page_ticker"]);
  assert.deepEqual(regular.cards[0], { kind: "page_ticker", title: "Keep looking at NVDA", lines: ["$190.10", "5"], asOf: "as of 15:59 ET", links: [{ label: "Chart", view: "Charts & OI", ticker: "NVDA" }], question: "NVDA price" });
  assert.deepEqual(regular.chips, ["NVDA price"]);
  for (const broken of [null, undefined, "x", {}, { cards: "x", chips: 5 }]) assert.deepEqual(chatHome(broken), { cards: [], chips: [], tickers: [] });
});

test("the screen's ticker goes apart from the conversation's: page AAPL, conversation about ZETA", async () => {
  const { chatHomeBody, chatRequestBody } = await import("./botChat.js");
  const zeta = [{ id: 1, pageTicker: "AAPL", subject: { ticker: "ZETA", day: "2026-11-10" } }];
  assert.deepEqual(chatRequestBody("What level we have in chart", zeta, "aapl", "Charts & OI"),
    { question: "What level we have in chart", context: { ticker: "ZETA", day: "2026-11-10", pageTicker: "AAPL", pageName: "Charts & OI" } });
  assert.deepEqual(chatRequestBody("x", [], "", ""), { question: "x" });
  assert.deepEqual(chatRequestBody("x", [], "", "Release Notes"), { question: "x", context: { pageName: "Release Notes" } });
  assert.deepEqual(chatRequestBody("x", [], "SPY", ""), { question: "x", context: { ticker: "SPY", day: "", fromPage: true, pageTicker: "SPY" } });
  assert.deepEqual(chatHomeBody(" nvda ", "Charts & OI"), { home: true, context: { pageTicker: "NVDA", pageName: "Charts & OI" } });
  assert.deepEqual(chatHomeBody("", ""), { home: true, context: { pageTicker: "", pageName: "" } });
});

test("questions follow the page on screen; a regular user's never name the owner's things", async () => {
  const { chatStarters } = await import("./botChat.js");
  assert.deepEqual(chatStarters("NVDA", true, true, "Charts & OI"), ["What level we have in chart", "NVDA signals on 5m", "NVDA today scanner", "call wall on NVDA", "why was NVDA missed"]);
  assert.deepEqual(chatStarters("AAPL", true, false, "Quick Options")[0], "Option chain");
  assert.ok(chatStarters("", true, true, "MomX Scanner").some((q) => /scanner at 9:30/.test(q)));
  assert.deepEqual(chatStarters("", true, true, "Release Notes"), chatStarters("", true, true), "a page with no questions of its own keeps the plain ones");
  assert.deepEqual(chatStarters("", true, true, "Charts & OI"), chatStarters("", true, true), "no ticker yet on a chart");
  for (const page of ["Charts & OI", "Quick Options", "MomX Scanner", "Premarket Scanner", "Sectors", "News Feed", "Earnings Calendar", "Long Term", "Settings", ""]) {
    for (const ticker of ["NVDA", ""]) {
      for (const q of chatStarters(ticker, true, false, page)) {
        assert.doesNotMatch(q, /\b(bot|paper|scorecard|trades?|profit|loss|missed|failed)\b/i, `${page}: ${q}`);
        assert.equal(chatQuestion(q), q);
      }
    }
  }
});

function memoryStorage(seed = {}) {
  const data = new Map(Object.entries(seed));
  return { data, getItem: (k) => (data.has(k) ? data.get(k) : null), setItem: (k, v) => { data.set(k, v); } };
}

test("history is per account: another person on this browser never sees it", async () => {
  const { chatAccount, chatHistoryAdd, chatHistoryRead } = await import("./botChat.js");
  const storage = memoryStorage();
  chatHistoryAdd("a@x.com", "NVDA price", "t1", storage);
  chatHistoryAdd("b@x.com", "SPY price", "t2", storage);
  assert.deepEqual(chatHistoryRead("a@x.com", storage), [{ q: "NVDA price", at: "t1", n: 1 }]);
  assert.deepEqual(chatHistoryRead("b@x.com", storage), [{ q: "SPY price", at: "t2", n: 1 }]);
  assert.deepEqual(chatHistoryRead("", storage), [], "not knowing who it is shows nothing");
  assert.deepEqual(chatHistoryAdd("", "x", "t", storage), []);
  assert.equal(storage.data.size, 2, "nothing is kept without an account");
  assert.equal(chatAccount({ user: { id: "U-1", email: "x@y" } }), "u-1");
  assert.equal(chatAccount({ user: { email: "X@Y.com" } }), "x@y.com");
  for (const none of [null, {}, { user: null }, { user: {} }]) assert.equal(chatAccount(none), "");
});

test("history keeps the newest 30, no repeats, and survives blocked or damaged storage", async () => {
  const { CHAT_HISTORY_MAX, chatHistoryAdd, chatHistoryRead, chatHistorySearch } = await import("./botChat.js");
  const storage = memoryStorage();
  for (let i = 0; i < 35; i += 1) chatHistoryAdd("a", `q${i}`, "", storage);
  let list = chatHistoryRead("a", storage);
  assert.equal(list.length, CHAT_HISTORY_MAX);
  assert.equal(CHAT_HISTORY_MAX, 30);
  assert.deepEqual([list[0].q, list[29].q], ["q34", "q5"]);
  list = chatHistoryAdd("a", "Q20", "later", storage);
  assert.deepEqual(list[0], { q: "Q20", at: "later", n: 2 }, "asked again: counted, for the type-ahead's most-asked order");
  assert.equal(list.filter((item) => item.q.toLowerCase() === "q20").length, 1);

  const blocked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.deepEqual(chatHistoryRead("a", blocked), []);
  assert.deepEqual(chatHistoryAdd("a", "x", "t", blocked), [{ q: "x", at: "t", n: 1 }]);
  assert.deepEqual(chatHistoryRead("a", null), []);
  for (const junk of ["{not json", "5", '{"a":1}', '[null,7,{"q":5},{"at":"x"}]']) assert.deepEqual(chatHistoryRead("a", memoryStorage({ "agx.chat.history.a": junk })), []);

  const items = [{ q: "NVDA today scanner", at: "" }, { q: "call wall on SPY", at: "" }, { q: "nvda chart", at: "" }];
  assert.deepEqual(chatHistorySearch(items, "nvda").map((i) => i.q), ["NVDA today scanner", "nvda chart"]);
  assert.deepEqual(chatHistorySearch(items, " scanner   NVDA ").map((i) => i.q), ["NVDA today scanner"]);
  assert.equal(chatHistorySearch(items, "").length, 3);
  assert.deepEqual(chatHistorySearch(null, "x"), []);
});

test("while the chat's own layer is up, Esc is that layer's and the window stays open", async () => {
  const { CHAT_LAYERS, chatTakesEsc } = await import("./botChat.js");
  const inside = {};
  const panel = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 10, height: 10 }), contains: (n) => n === inside };
  const doc = (layer) => ({ activeElement: inside, elementFromPoint: () => inside, querySelector: (s) => (s === CHAT_LAYERS ? layer : null) });
  assert.equal(chatTakesEsc(panel, doc(null)), true);
  assert.equal(chatTakesEsc(panel, doc({})), false);
  for (const layer of ["chat-sheet-host", "chat-scanner-day", "chat-history", "chat-suggest"]) assert.match(CHAT_LAYERS, new RegExp(layer));
});

test("answer lines are split into labelled rows, not a paragraph", async () => {
  const { chatLineParts } = await import("./botChat.js");
  assert.deepEqual(chatLineParts("18:30 ET, Benzinga: DeSantis Asks: How Congress Members Outperform"),
    { kind: "news", label: "18:30 ET, Benzinga", text: "DeSantis Asks: How Congress Members Outperform", tone: "" });
  assert.deepEqual(chatLineParts("Market: QQQ -0.32% (19:55 ET)."), { kind: "row", label: "Market", text: "QQQ -0.32% (19:55 ET).", tone: "down" });
  assert.deepEqual(chatLineParts("After hours: NVDA +1.20% to 159.37"), { kind: "row", label: "After hours", text: "NVDA +1.20% to 159.37", tone: "up" });
  assert.deepEqual(chatLineParts("The app's saved headlines, last 24 hours:"), { kind: "head", label: "", text: "The app's saved headlines, last 24 hours", tone: "" });
  assert.equal(chatLineParts("SPY -0.35% today at 759.05 (scanner board, 19:55 ET).", true).kind, "lead");
  assert.equal(chatLineParts("SPY -0.35% today at 759.05 (scanner board, 19:55 ET).", true).tone, "down");
  assert.equal(chatLineParts("SPY is not on the Mag7 or Watchlist calls or puts boards now.").kind, "plain");
  assert.equal(chatLineParts("a sentence with a time 10:30 and more words: not a label because it is long and lower case").kind, "plain");
  assert.equal(chatLineParts(null).text, "");
});
