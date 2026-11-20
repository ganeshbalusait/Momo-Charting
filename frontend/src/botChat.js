// Ask the app (2026-11-10): the screen side of the no-AI chatbot
// (POST /api/bot-chat, momx/chatbot.py). Pure, so node:test can run it.

import { cleanLink, cleanLinks, linkAllowed } from "./appLinks.js";
import { chatMore, chatReasons, chatSections, chatTechnical, chatViews, chatWhy, dropVwapLines, placeHeadlines, replyHeadlines } from "./chatReport.js";

export const CHAT_URL = "/api/bot-chat";
export const CHAT_MAX = 300;

// When a question gets no reply at all (2026-11-19, "Why AMD is down today" on the phone: "Could not reach the app",
// while the app was up). The chat only reads, so asking again is safe: it asks once more by itself, and if that
// fails too it finds out WHY and says so, instead of one message for every cause.
export const CHAT_SIGNED_OUT = "Your sign-in to the app ran out. Reload this page (pull down), sign in, then ask again.";
export const CHAT_OFFLINE = "Your phone or PC is not reaching the app. Check Wi-Fi or mobile data, then ask again.";
export const CHAT_DROPPED = "The app is up, but the answer did not come back. Ask again.";
export const CHAT_RETRY_WAIT_MS = 1200;

/** Why nothing came back: a light sign-in read that is not allowed to follow the tunnel's login redirect. */
export async function chatFailureReason(fetchImpl) {
  try {
    const r = await fetchImpl("/api/auth/status", { credentials: "include", cache: "no-store", redirect: "manual" });
    if (r?.type === "opaqueredirect" || r?.status === 401 || r?.status === 403 || (r?.status >= 300 && r?.status < 400)) return CHAT_SIGNED_OUT;
    return CHAT_DROPPED;
  } catch {
    return CHAT_OFFLINE;
  }
}

/**
 * Send one chat question. A reply of any status is returned as it is. When the request itself fails (no reply), it
 * waits a moment and asks ONCE more; if that fails too it throws an Error whose message is the plain reason.
 */
export async function chatSend(fetchImpl, url, init, { waitMs = CHAT_RETRY_WAIT_MS, sleep = (ms) => new Promise((done) => setTimeout(done, ms)) } = {}) {
  try {
    return await fetchImpl(url, init);
  } catch {
    await sleep(waitMs);
    try {
      return await fetchImpl(url, init);
    } catch {
      throw new Error(await chatFailureReason(fetchImpl));
    }
  }
}
export const CHAT_STARTERS = [
  "SPY if I take how much it went up",
  "why AAPL failed",
  "why was NVDA missed",
  "see TSLA yesterday",
  "what is the bot doing",
  "scanner at 9:30 give me the list of tickers",
];
/** The chat window on every page opens with these when no ticker is on screen. */
export const CHAT_DOCK_STARTERS = [
  "scanner at 9:30 give me the list of tickers",
  "which tickers stayed on top",
  "what is the bot doing",
];
/** Anyone who is not the owner asks about the scanner only; the bot, the paper accounts and the trades stay the owner's. */
export const CHAT_SCANNER_STARTERS = [
  "scanner at 9:30 give me the list of tickers",
  "which tickers stayed on top",
];

/** The ticker on screen as the chat window uses it. "" = this page has none. */
export function chatPageTicker(raw) {
  return String(raw || "").trim().toUpperCase().slice(0, 12);
}

/** The page on screen as the chat uses it. "" = not known. */
export function chatPageName(raw) {
  return String(raw || "").trim().slice(0, 40);
}

// Questions for the page on screen. A page not listed here keeps the plain starters below.
function pageStarters(page, t, isOwner) {
  const mine = (...owner) => (isOwner ? owner : []);
  switch (page) {
    case "Charts & OI":
      return t ? ["What level we have in chart", `${t} signals on 5m`, `${t} today scanner`, `call wall on ${t}`, ...mine(`why was ${t} missed`)] : null;
    case "Quick Options":
      return t ? ["Option chain", `call wall on ${t}`, `expected move ${t}`, `what is max pain for ${t}`, `${t} today scanner`] : null;
    case "MomX Scanner":
      return [...(t ? [`${t} today scanner`] : []), "scanner at 9:30 give me the list of tickers", "which tickers stayed on top today", "which sector is hot"];
    case "Premarket Scanner":
      return ["premarket today", "which sector is hot", "which tickers stayed on top today"];
    case "Sectors":
      return ["which sector is hot", "what was hot at noon"];
    case "News Feed":
      return [...(t ? [`any news on ${t}`] : []), "which sector is hot", "premarket today"];
    case "Earnings Calendar":
      return t ? [`when is ${t} earnings`] : null;
    case "Long Term":
      return t ? [`long term ${t}`, `any news on ${t}`, `when is ${t} earnings`] : null;
    default:
      return null;
  }
}

/** The questions offered before the first one is asked: for the page on screen, about its ticker when there is one. */
export function chatStarters(pageTicker, compact = false, isOwner = true, pageName = "") {
  const ticker = chatPageTicker(pageTicker);
  const forPage = pageStarters(chatPageName(pageName), ticker, isOwner);
  if (forPage) return forPage;
  if (!isOwner) return ticker ? [`${ticker} at 9:30 in the scanner`, `how long was ${ticker} in the top`, ...CHAT_SCANNER_STARTERS] : CHAT_SCANNER_STARTERS;
  if (ticker) return [`see ${ticker}`, `why was ${ticker} missed`, `${ticker} at 9:30 in the scanner`];
  return compact ? CHAT_DOCK_STARTERS : CHAT_STARTERS;
}

/** The grey hint inside the question box. */
export function chatPlaceholder(pageTicker) {
  const ticker = chatPageTicker(pageTicker);
  return ticker ? `For example: ${ticker} at 9:30 in the scanner` : "For example: scanner at 9:30 give me the list of tickers";
}

/** The round button's hover text. */
export function chatDockTitle(isOwner) {
  return isOwner ? "Ask about a ticker, the scanner or the bot" : "Ask about a ticker or the scanner";
}

/** The small line at the top of the chat window. */
export function chatDockNote(isOwner) {
  return isOwner ? "Answers come from the app's records. It only reads; it cannot place or change a trade." : "Answers come from the app's saved market data. It only reads.";
}

/** What the screen says when the server refuses a question. */
export function chatRefused(isOwner) {
  return isOwner ? "Only the owner can ask about the bot's trades." : "That is not open on this account. You can ask what the scanner showed.";
}

/** Things that close on their own Esc without covering the middle of the chat window (the Grids menu, the chart's alert menu, the Alert Center). */
export const CHAT_ESC_OWNERS = "[role='menu'], .global-price-alert-popover";

/** The chat's own layers on top of its window. While one is up, Esc closes that layer and leaves the window open. */
export const CHAT_LAYERS = ".chat-sheet-host, .chat-scanner-day, .chat-history, .chat-suggest";

/** Does Esc belong to the chat window? Not when something covers it. Yes when the cursor is in it. Otherwise only if no menu is open. */
export function chatTakesEsc(panel, doc) {
  if (doc.querySelector(CHAT_LAYERS)) return false;
  const box = panel?.getBoundingClientRect();
  if (!box || !panel.contains(doc.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2))) return false;
  return panel.contains(doc.activeElement) || !doc.querySelector(CHAT_ESC_OWNERS);
}

/** What may be sent: trimmed, one line, at most CHAT_MAX characters. "" = nothing to send. */
export function chatQuestion(text) {
  return String(text || "").replace(/\s+/g, " ").trim().slice(0, CHAT_MAX);
}

const KINDS = { what_if: "How far it went", why_failed: "Why it failed", why_missed: "Why it was missed", show: "On file",
  status: "The bot now", help: "What I can answer", error: "Could not answer",
  scanner_list: "Scanner list", scanner_ticker: "Scanner at that time", scanner_top: "Stayed on top",
  scanner_day: "Scanner that day", open_page: "Go to", open_chart: "Go to the chart", open_scanner: "Go to the scanner",
  chart_levels: "Chart levels", chart_signals: "Chart signals", chain: "Option chain", walls: "High OI walls",
  expected_move: "Expected move", max_pain: "Max pain", sectors: "Sectors", premarket: "Premarket", news: "News",
  earnings: "Earnings", long_term: "Long term", morning_brief: "Morning brief", quote: "Price", help_search: "From the help pages", define: "Glossary",
  search: "Search", refused: "Not on this account",
  // 2026-11-14: a "why did it move" answer had no heading of its own and showed "What I can answer".
  why_move: "Why it moved", latest_news: "News",
  bot_what_if: "How far it went", bot_why_failed: "Why it failed", bot_why_missed: "Why it was missed", bot_show: "On file", bot_status: "The bot now", bot_desk: "The bot now", paper: "On file" };

const TABLE_MAX_ROWS = 40;
const TABLE_MAX_COLUMNS = 24;
const text = (value, max = 200) => (typeof value === "string" ? value.trim().slice(0, max) : typeof value === "number" && Number.isFinite(value) ? String(value) : "");

/** A table as the screen may draw it. null = nothing usable. A scanner day is only a reference: the sheet reads the rows itself. */
export function chatTable(raw, isOwner = true) {
  if (!raw || typeof raw !== "object") return null;
  if (raw.kind === "scanner_day") {
    const place = cleanLink({ view: "MomX Scanner", ticker: raw.ticker, day: raw.day });
    if (!place?.ticker || !place.day) return null;
    const sections = (Array.isArray(raw.sections) ? raw.sections : [])
      .map((section) => cleanLink({ view: "MomX Scanner", list: section?.list, side: section?.side }))
      .filter((section) => section?.list && section.side)
      .map(({ list, side }) => ({ list, side }));
    return sections.length ? { kind: "scanner_day", title: text(raw.title) || `${place.ticker} on the scanner`, ticker: place.ticker, day: place.day, sections } : null;
  }
  if (raw.kind !== "rows" || !Array.isArray(raw.columns) || !Array.isArray(raw.rows)) return null;
  const columns = raw.columns
    .filter((column) => column && typeof column.key === "string" && column.key && typeof column.label === "string")
    .filter((column) => !/\bvwap\b/i.test(`${column.key} ${column.label}`))   // his rule: "Don't use vwap"
    .slice(0, TABLE_MAX_COLUMNS)
    .map(({ key, label, align }) => ({ key, label: label.trim().slice(0, 40), align: align === "right" || align === "center" ? align : "left" }));
  if (!columns.length) return null;
  const rows = raw.rows
    .filter((row) => row && typeof row === "object" && !Array.isArray(row))
    .slice(0, TABLE_MAX_ROWS)
    .map((row) => Object.fromEntries(columns.map(({ key }) => [key, text(row[key])])));
  if (!rows.length) return null;
  const link = cleanLink(raw.link);
  return { kind: "rows", title: text(raw.title), columns, rows, note: text(raw.note, 400), link: linkAllowed(link, isOwner) ? link : null };
}

const TICKER_COLUMN = /^(ticker|symbol)$/i;

/** The button on one row of a table: the table's own link, moved to that row's ticker. null = this table has none. */
export function chatRowLink(table, row) {
  if (!table?.link || !row) return null;
  const column = table.columns.find(({ key }) => TICKER_COLUMN.test(key));
  const ticker = column ? row[column.key] : "";
  return ticker ? cleanLink({ ...table.link, ticker, label: `Open ${ticker}` }) : null;
}

/** One answer as the screen shows it: a small heading, plain lines, bullet lines apart, follow-up questions, buttons to the page. */
export function chatAnswer(reply, isOwner = true) {
  const r = reply && typeof reply === "object" ? reply : {};
  // Plain answer lines are left as the records say them (a bot's saved reason can name VWAP); the cards drop VWAP.
  const all = Array.isArray(r.lines) ? r.lines.filter((x) => typeof x === "string" && x.trim()) : [];
  const lines = all.map((text) => (text.startsWith("- ") ? { bullet: true, text: text.slice(2) } : { bullet: false, text }));
  const about = [r.ticker, r.day].filter(Boolean).join(" · ");
  // The server's "articles" (with the older "headlines" name) go on the report's news part when it has one.
  const placed = placeHeadlines(chatSections(r.sections, isOwner, chatTable), replyHeadlines(r));
  const sections = placed.sections;
  // A report also sends its sections flat in `lines` ("Title:" then up to six of its lines); on screen the cards carry
  // them. Every line before the first section's title stays (the plain "no advice" line, the header), and so does a
  // later line that is neither a section title nor on a card (another tool's lines the plan joined on).
  const titles = new Set(sections.map((s) => `${s.title}:`));
  const onCards = new Set((Array.isArray(r.sections) ? r.sections : []).flatMap((s) => (Array.isArray(s?.lines) ? s.lines : [])));
  const flatStart = all.findIndex((text) => titles.has(text));
  const above = !sections.length ? lines : flatStart < 0 ? lines.slice(0, 1)
    : lines.filter((line, i) => i < flatStart || (!titles.has(all[i]) && !onCards.has(all[i])));
  return {
    kind: KINDS[r.intent] || KINDS.help,
    intent: typeof r.intent === "string" ? r.intent : "",
    about,
    // No AI-written text is shown (2026-11-11): a reply's "ai" or "general" field, from an older server, is ignored.
    // Why the AI could not help read the question, when the server said so (for example the free limit is used up).
    aiNote: typeof r.aiNote === "string" ? r.aiNote.trim() : "",
    understoodByAi: r.understoodBy === "AI",
    lines: above.length || sections.length ? above : [{ bullet: false, text: "I have no answer for that." }],
    suggest: (Array.isArray(r.suggest) ? r.suggest : []).filter((x) => typeof x === "string" && x.trim()).slice(0, 4),
    links: cleanLinks(r.links, isOwner),
    table: chatTable(r.table, isOwner),
    // The full ticker report: collapsible sections, the Short term / Long term views, and the slower second part.
    sections,
    views: chatViews(r.views, sections),
    // The server's views are one verdict per term (short / mid / long) and miniChart its small chart: one card.
    technical: chatTechnical(r.views, r.miniChart, sections.find((s) => s.key === "technicals")?.asOf),
    more: chatMore(r.more),
    moreState: chatMore(r.more) ? "waiting" : "",
    headlines: placed.headlines,
    reasons: chatReasons(r.reasons),
    asOf: typeof r.asOf === "string" ? r.asOf.trim() : "",
    // The owner's AI summary arrives only with the "more" part (chatMergeMore); a first answer never carries one.
    ownerSummary: null,
    // The "why did it move" card (answer, key reasons with tagged sources); it normally arrives with the "more" part.
    why: chatWhy(r.why, isOwner),
  };
}

/** What sits between the conversation and the question box, and never scrolls away: the newest answer's buttons, then
 * its follow-up questions. While waiting, and when an answer offers no questions, the page's own questions. */
export function chatTray(turns, starters) {
  const list = Array.isArray(turns) ? turns : [];
  const newest = list.length ? list[list.length - 1]?.answer : null;
  const chips = newest?.suggest?.length ? newest.suggest : (Array.isArray(starters) ? starters : []);
  return { links: newest?.links || [], chips };
}

const OWNER_CARD = /\b(bots?|paper|scorecard|trades?)\b/i;

/** The opening screen's cards and chips. A card with nothing to show is left out, and a regular user never sees an owner card. */
export function chatHome(raw, isOwner = true) {
  const r = raw && typeof raw === "object" ? raw : {};
  const cards = (Array.isArray(r.cards) ? r.cards : []).flatMap((card) => {
    if (!card || typeof card !== "object") return [];
    const title = text(card.title, 80);
    const kind = text(card.kind, 40);
    const lines = dropVwapLines((Array.isArray(card.lines) ? card.lines : []).map((line) => text(line, 160))).slice(0, 8);
    if (!title || !lines.length || (!isOwner && OWNER_CARD.test(`${kind.replace(/[_-]/g, " ")} ${title}`))) return [];
    return [{ kind, title, lines, asOf: text(card.asOf, 40), links: cleanLinks(card.links, isOwner, 3), question: chatQuestion(card.question) }];
  }).slice(0, 6);
  const chips = (Array.isArray(r.chips) ? r.chips : []).map(chatQuestion).filter((q) => q && (isOwner || !OWNER_CARD.test(q))).slice(0, 8);
  // Tickers the app has on its boards and watchlist, for the ask box's type-ahead. Strings only.
  const tickers = (Array.isArray(r.tickers) ? r.tickers : []).map((t) => text(t, 12).toUpperCase()).filter((t) => /^[A-Z][A-Z0-9.]{0,5}$/.test(t)).slice(0, 400);
  return { cards, chips: chips.filter((q) => !/\bvwap\b/i.test(q)), tickers };
}

/** What a question sends: the conversation's subject and, apart from it, what is on screen. Questions naming "chart", "this" or "here" use the screen's. */
export function chatRequestBody(question, turns, pageTicker, pageName) {
  const context = { ...(chatSendContext(turns, pageTicker) || {}) };
  const ticker = chatPageTicker(pageTicker);
  const page = chatPageName(pageName);
  if (ticker) context.pageTicker = ticker;
  if (page) context.pageName = page;
  return Object.keys(context).length ? { question, context } : { question };
}

/** The one request the opening screen makes. */
export function chatHomeBody(pageTicker, pageName) {
  return { home: true, context: { pageTicker: chatPageTicker(pageTicker), pageName: chatPageName(pageName) } };
}

/** Which account this browser is signed in as, from /api/auth/status. "" = not known, and then no history is read or kept. */
export function chatAccount(status) {
  const user = status && typeof status === "object" ? status.user : null;
  return user ? String(user.id || user.email || "").trim().toLowerCase().slice(0, 120) : "";
}

export const CHAT_HISTORY_MAX = 30;
const historyKey = (account) => `agx.chat.history.${encodeURIComponent(account)}`;

function browserStorage() {
  try { return window.localStorage; } catch { return null; }
}

/** This account's earlier questions on this browser, newest first. Blocked or damaged storage reads as none. */
export function chatHistoryRead(account, storage = browserStorage()) {
  if (!account || !storage) return [];
  try {
    const list = JSON.parse(storage.getItem(historyKey(account)) || "[]");
    if (!Array.isArray(list)) return [];
    return list.filter((item) => item && typeof item.q === "string" && item.q)
      .map(({ q, at, n }) => ({ q: chatQuestion(q), at: typeof at === "string" ? at : "", n: Number.isInteger(n) && n > 0 ? Math.min(n, 9999) : 1 }))
      .slice(0, CHAT_HISTORY_MAX);
  } catch {
    return [];
  }
}

/** Remember a question: newest first, no repeats, at most 30, with how many times it was asked (`n`, for the type-ahead). Returns the new list. */
export function chatHistoryAdd(account, question, at = "", storage = browserStorage()) {
  const q = chatQuestion(question);
  if (!account || !q) return chatHistoryRead(account, storage);
  const same = q.toLowerCase();
  const earlier = chatHistoryRead(account, storage);
  const before = earlier.find((item) => item.q.toLowerCase() === same);
  const list = [{ q, at, n: (before?.n || 0) + 1 }, ...earlier.filter((item) => item.q.toLowerCase() !== same)].slice(0, CHAT_HISTORY_MAX);
  try { storage?.setItem(historyKey(account), JSON.stringify(list)); } catch { /* blocked or full: this session just shows it */ }
  return list;
}

/** Search on this device: every word typed must be in the question. */
export function chatHistorySearch(list, query) {
  const words = chatQuestion(query).toLowerCase().split(" ").filter(Boolean);
  const items = Array.isArray(list) ? list : [];
  return words.length ? items.filter(({ q }) => words.every((word) => q.toLowerCase().includes(word))) : items;
}

/** The conversation with one more turn; only the last 30 turns are kept on screen. */
export function chatAppend(turns, turn) {
  return [...(Array.isArray(turns) ? turns : []), turn].slice(-30);
}

/** What the conversation is about now: the ticker and day of the newest answer that had one. Sent with the
 * next question, so "profit or loss?" continues with the same ticker. null = nothing yet. */
export function chatContext(turns) {
  const list = Array.isArray(turns) ? turns : [];
  for (let i = list.length - 1; i >= 0; i -= 1) {
    const subject = list[i] && list[i].subject;
    if (subject && typeof subject.ticker === "string" && subject.ticker) {
      const about = { ticker: subject.ticker, day: typeof subject.day === "string" ? subject.day : "" };
      if (subject.side === "bull" || subject.side === "bear") about.side = subject.side;
      return about;
    }
  }
  return null;
}

/** What goes along with a question: the conversation's own subject, or, until it has one, the ticker on screen.
 * Only the turns asked since the ticker on screen last changed count, so after moving the chart from NVDA to SPY
 * "why was it missed" is about SPY. A turn that named another ticker while the chart stayed put still carries on. */
export function chatSendContext(turns, pageTicker) {
  const ticker = chatPageTicker(pageTicker);
  const list = Array.isArray(turns) ? turns : [];
  let from = list.length;
  while (from > 0 && !(ticker && typeof list[from - 1]?.pageTicker === "string" && list[from - 1].pageTicker !== ticker)) from -= 1;
  const own = chatContext(list.slice(from));
  if (own) return own;
  return ticker ? { ticker, day: "", fromPage: true } : null;
}

// While the Bot page's own Ask tab is on screen the chat window stands aside, so there are never two chats at once.
let pageChats = 0;
const pageChatListeners = new Set();
const tellPageChatListeners = () => pageChatListeners.forEach((listener) => listener());

export const pageChatShowing = () => pageChats > 0;

export function subscribePageChat(listener) {
  pageChatListeners.add(listener);
  return () => pageChatListeners.delete(listener);
}

/** The Ask tab calls this when it appears; the function it returns says it has gone. */
export function markPageChat() {
  pageChats += 1;
  tellPageChatListeners();
  let gone = false;
  return () => {
    if (gone) return;
    gone = true;
    pageChats -= 1;
    tellPageChatListeners();
  };
}

/** The ticker / day an answer was about, kept on its turn. null when the answer had no ticker. */
export function chatSubject(reply) {
  const r = reply && typeof reply === "object" ? reply : {};
  if (typeof r.ticker !== "string" || !r.ticker) return null;
  const subject = { ticker: r.ticker, day: typeof r.day === "string" ? r.day : "" };
  if (r.side === "bull" || r.side === "bear") subject.side = r.side;       // calls or puts: a follow-up stays on the same side
  return subject;
}

// How one answer line is drawn (his report 2026-11-12: "looks like paragraph, very difficult to read").
// The server's lines stay word for word; this only splits a line into a label and its text so the screen can
// show rows instead of a wall of text. The screen prints label + ": " + text, so the words on screen are
// exactly the server's (the browser tests and copy-paste read them):
//   "18:30 ET, Benzinga: Headline..."      -> { kind: "news", label: "18:30 ET, Benzinga", text: "Headline..." }
//   "Market: QQQ -0.32% (19:55 ET)."       -> { kind: "row",  label: "Market", text: "QQQ -0.32% (19:55 ET)." }
//   "The app's saved headlines, last 24 hours:" -> { kind: "head", text: ... }
//   anything else                           -> { kind: "plain", text }
// tone is "up" / "down" when the line's first signed percent says so, for a green or red edge.
const NEWS_LINE = /^(\d{1,2}:\d{2}(?:\s?[AP]M)?\s+ET),\s+([^:]{2,40}):\s+(.+)$/;
const ROW_LINE = /^([A-Z][A-Za-z0-9 /&()'.,-]{1,34}):\s+(\S.*)$/;
const FIRST_PCT = /([+\-−])\d+(?:\.\d+)?%/;
export function chatLineParts(text, first = false) {
  const t = String(text ?? "").trim();
  const pct = t.match(FIRST_PCT);
  const tone = pct ? (pct[1] === "+" ? "up" : "down") : "";
  const news = t.match(NEWS_LINE);
  if (news) return { kind: "news", label: `${news[1]}, ${news[2].trim()}`, text: news[3], tone: "" };
  if (/:$/.test(t) && t.length <= 80) return { kind: "head", label: "", text: t.slice(0, -1), tone: "" };
  const row = t.match(ROW_LINE);
  if (row && !/^https?$/i.test(row[1])) return { kind: "row", label: row[1].trim(), text: row[2], tone };
  return { kind: first ? "lead" : "plain", label: "", text: t, tone };
}
