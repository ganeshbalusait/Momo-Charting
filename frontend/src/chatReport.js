// The ticker report in a chat answer (2026-11-11): sections shown as collapsible cards, the Short term / Long term
// views with their rules counts, and the two-part answer (the fast part at once, then ONE follow-up read with the
// "more" token that adds the latest headlines and what the news points to). Pure, so node:test can run it.
// Numbers here are only ever the server's own (tool output with its time); nothing is worked out on screen.

import { cleanLinks } from "./appLinks.js";

const str = (value, max = 200) => (typeof value === "string" ? value.trim().slice(0, max) : typeof value === "number" && Number.isFinite(value) ? String(value) : "");
const num = (value) => (typeof value === "number" && Number.isFinite(value) ? value : null);

// His rule (2026-11-11): "Don't use vwap". A piece of a line that names VWAP is left out; a line that is only VWAP goes.
const VWAP = /\bvwap\b/i;
export function dropVwap(line) {
  const text = str(line, 400);
  if (!VWAP.test(text)) return text;
  return text.split(/\s*(?:[,;|·]|\s-\s)\s*/).filter((part) => part && !VWAP.test(part)).join(", ");
}
export const dropVwapLines = (lines, max = 400) => (Array.isArray(lines) ? lines : []).map((line) => dropVwap(str(line, max))).filter(Boolean);

// His ask (2026-11-14): "For the news link I don't know news is positive or negative give me that tag also". The
// server's code decides each headline's tone for the ticker asked about (or the market); the screen only shows it.
export const TONES = ["positive", "negative", "neutral"];
export const TONE_LABELS = { positive: "Positive", negative: "Negative", neutral: "Neutral" };
/** The server's tone word, or "" when it sent none (the screen never guesses one). */
export const chatTone = (raw) => (typeof raw === "string" && TONES.includes(raw.trim().toLowerCase()) ? raw.trim().toLowerCase() : "");

/** An outside article as a button may open it: http(s) only, with the source's name. null = unusable. */
export function chatHeadline(raw) {
  if (!raw || typeof raw !== "object") return null;
  const title = str(raw.title, 240);
  let url = "";
  try {
    const parsed = new URL(String(raw.url || raw.link || ""));
    if (parsed.protocol === "https:" || parsed.protocol === "http:") url = parsed.href;
  } catch { url = ""; }
  if (!title) return null;
  let source = str(raw.source, 60);
  if (!source && url) source = new URL(url).hostname.replace(/^www\./, "");
  const tone = chatTone(raw.tone);
  // The server's articles say their time as "atText" ("Wed Nov 11 15:30 ET"); "at" is the raw ISO stamp.
  return { title, source, url, time: str(raw.atText || raw.time || raw.at || raw.publishedAt, 40), ...(tone ? { tone } : {}) };
}

/** The server's article list ("articles") and the older "headlines" name, as one list. */
export const replyHeadlines = (reply) => chatHeadlines([
  ...(Array.isArray(reply?.headlines) ? reply.headlines : []),
  ...(Array.isArray(reply?.articles) ? reply.articles : []),
]);

export function chatHeadlines(raw, max = 12) {
  const seen = new Set();
  return (Array.isArray(raw) ? raw : []).map(chatHeadline).filter((h) => {
    const key = (h?.url || h?.title || "").toLowerCase();
    if (!h || seen.has(key)) return false;
    seen.add(key);
    return true;
  }).slice(0, max);
}

/**
 * One reason as a plain line. Since 2026-11-11 the server sends each reason as a finished line it built itself from a
 * fixed category and the saved headline ('Earnings or guidance: "<headline>" (CNBC, 8:45 AM ET)'); no AI-written text.
 * Anything that is not a string (an older reason object with an AI sentence in it) is not shown.
 */
export function chatReason(raw) {
  return typeof raw === "string" ? dropVwap(str(raw, 400)) : "";
}

/** "What the news points to": plain reason lines. */
export const chatReasons = (raw) => (Array.isArray(raw) ? raw : []).map(chatReason).filter(Boolean).slice(0, 8);

/** The owner's AI summary (2026-11-11 evening): the one AI-written paragraph, owner only, under its fixed label. */
export const OWNER_SUMMARY_LABEL = "AI summary, can be wrong. Only you see this.";

/**
 * The summary the "more" call sent, or null. The server sends it to the owner only; this checks again, so a regular
 * user's screen shows nothing even if a field arrived. Plain text, at most 700 characters, always with the fixed label.
 */
export function chatOwnerSummary(raw, isOwner = false) {
  if (isOwner !== true || !raw || typeof raw !== "object") return null;
  const text = str(raw.text, 700).replace(/\s+/g, " ");
  if (!text) return null;
  return { text, label: OWNER_SUMMARY_LABEL, asOf: str(raw.asOf, 80) };
}

/** The second part as the route sends it ({"more": {done, lines, articles, reasons, sections, views, miniChart}}), or as-is. */
export const chatMoreReply = (reply) => (reply && typeof reply === "object" && reply.more && typeof reply.more === "object" ? reply.more : reply);

const VIEW_TITLES = { short: "Short term", long: "Long term" };

/**
 * A closed section's one-line summary: its first line that says something. A first line that only repeats the title
 * ("What the app's rules show:" under "What the app's rules show", 2026-11-19) is skipped.
 */
export function sectionSummary(title, lines) {
  const plain = (v) => String(v || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
  const head = plain(title);
  return (Array.isArray(lines) ? lines : []).find((line) => plain(line) && plain(line) !== head) || "";
}

/** One report section. The server sends them in order: [{key, title, lines, table?, links, asOf, pending?, summary?, view?}]. */
export function chatSection(raw, isOwner = true, tableOf = () => null) {
  if (!raw || typeof raw !== "object") return null;
  const key = str(raw.key, 40).replace(/[^\w-]/g, "");
  const title = str(raw.title, 80);
  if (!key || !title) return null;
  const lines = dropVwapLines(raw.lines).slice(0, 30);
  const view = raw.view === "short" || raw.view === "long" ? raw.view : "";
  return {
    key, title, lines, view,
    table: tableOf(raw.table, isOwner),
    links: cleanLinks(raw.links, isOwner),
    asOf: str(raw.asOf, 40),
    pending: raw.pending === true,
    summary: dropVwap(str(raw.summary, 160)) || sectionSummary(title, lines),
    headlines: chatHeadlines(raw.headlines),
    reasons: chatReasons(raw.reasons),
    analyst: raw.analyst && typeof raw.analyst === "object" ? raw.analyst : null,
    technical: raw.technical && typeof raw.technical === "object" ? raw.technical : null,
  };
}

export function chatSections(raw, isOwner = true, tableOf = () => null) {
  const seen = new Set();
  return (Array.isArray(raw) ? raw : []).map((s) => chatSection(s, isOwner, tableOf)).filter((s) => {
    if (!s || seen.has(s.key)) return false;
    seen.add(s.key);
    return true;
  }).slice(0, 24);
}

/** How many sections start open: the first three; the rest show a one-line summary until tapped. */
export const SECTIONS_OPEN = 3;

/** "5 of 8 rules", or "" when the server gave no count. */
export function rulesText(view) {
  const met = num(view?.met ?? view?.rules?.met);
  const total = num(view?.total ?? view?.rules?.total);
  if (met === null || total === null || total <= 0 || met < 0 || met > total) return "";
  return `${met} of ${total} ${total === 1 ? "rule" : "rules"}`;
}

/** A view's sections: those marked for it; a section with no view goes under short term. */
export const viewSections = (sections, key) => (Array.isArray(sections) ? sections : []).filter((s) => s.view === key || (!s.view && key === "short"));

/** The report's views, short term first. [] = the answer has no views, and its sections show as one list. */
export function chatViews(rawViews, sections) {
  const given = new Map((Array.isArray(rawViews) ? rawViews : []).filter((v) => v && (v.key === "short" || v.key === "long")).map((v) => [v.key, v]));
  const used = new Set((Array.isArray(sections) ? sections : []).map((s) => s.view).filter(Boolean));
  return ["short", "long"].filter((key) => given.has(key) || used.has(key)).map((key) => {
    const v = given.get(key) || {};
    return { key, title: str(v.title, 40) || VIEW_TITLES[key], rules: rulesText(v), sections: viewSections(sections, key) };
  });
}

/**
 * The report's "Technical analysis" card from what the server sends: `views` = one verdict per term
 * [{view: "short"|"mid"|"long", verdict: "Bullish"|"Bearish"|"Neutral", strength, reason}] and `miniChart` =
 * {daily: [bar], weekly: [bar], markers: [{chart: "D"|"W", time, side: "bull"|"bear"|"earnings", name}],
 * support: {name, value}, resistance: {name, value}}. Returns the shape TechnicalCard reads, or null with neither.
 */
export function chatTechnical(rawViews, miniChart, asOf = "") {
  const terms = {};
  for (const v of Array.isArray(rawViews) ? rawViews : []) {
    const key = v?.view;
    if (!["short", "mid", "long"].includes(key) || terms[key] || typeof v.verdict !== "string") continue;
    // The server's own "label" is its not-advice line, never the verdict word, so it is not passed on.
    terms[key] = { verdict: v.verdict.toLowerCase(), strength: num(v.strength), reason: str(v.reason, 240) };
  }
  const chart = miniChart && typeof miniChart === "object" ? miniChart : null;
  const line = (raw) => (raw && typeof raw === "object" && num(raw.value) !== null ? { price: raw.value, label: str(raw.name, 40) } : null);
  const frame = (bars, code) => (Array.isArray(bars) && bars.length ? {
    bars,
    events: (Array.isArray(chart.markers) ? chart.markers : []).filter((m) => m && (m.chart || "D") === code)
      .map((m) => ({ kind: m.side, time: m.time, name: str(m.name, 40) })),
    support: line(chart.support),
    resistance: line(chart.resistance),
  } : null);
  const charts = chart ? { daily: frame(chart.daily, "D"), weekly: frame(chart.weekly, "W") } : {};
  if (!Object.keys(terms).length && !charts.daily && !charts.weekly) return null;
  return { terms, charts, asOf: str(asOf, 40), source: "the app's own chart signals, not advice" };
}

/**
 * A `technical` object from another route (the Long Term page's GET /api/long-term/analyst) as TechnicalCard reads it.
 * The server may send the chat report's own shape ({views: [one verdict per term], miniChart, asOf}, or the views list
 * alone), which goes through chatTechnical; the card's shape ({terms, charts}) passes as it is. null = nothing to show.
 */
export function technicalData(raw) {
  if (Array.isArray(raw)) return chatTechnical(raw, null);
  if (!raw || typeof raw !== "object") return null;
  if (Array.isArray(raw.views) || (raw.miniChart && typeof raw.miniChart === "object")) return chatTechnical(raw.views, raw.miniChart, str(raw.asOf, 40));
  return raw;
}

/** The report's news part: "news" (older replies) or "why" (the server's report). "" = none. */
export const newsSectionKey = (sections) => (Array.isArray(sections) ? sections : []).find((s) => s.key === "news")?.key
  || (Array.isArray(sections) ? sections : []).find((s) => s.key === "why")?.key || "";

/** The fast part's articles: on the report's news part when it has one, otherwise on the answer itself. */
export function placeHeadlines(sections, headlines) {
  const key = newsSectionKey(sections);
  if (!key || !headlines.length) return { sections, headlines };
  return { sections: sections.map((s) => (s.key === key ? { ...s, headlines: chatHeadlines([...s.headlines, ...headlines]) } : s)), headlines: [] };
}

/** The fast part's "still checking" line, word for word as momx/chat_why.py CHECKING_LINE sends it. Once the second
 * part is in (or has failed) it is no longer true, so it is taken off the answer and off its cards. */
export const CHECKING_LINE = "Checking Google News and reading the headlines...";
/** A past day's pending line (momx/chat_why.py READING_SAVED_LINE): the AI is still reading that day's saved headlines. */
export const READING_SAVED_LINE = "Reading that day's saved headlines...";
/** Every line that is true only while the second part is on its way: momx/chat_why.py PENDING_LINES, word for word. */
export const PENDING_LINES = [CHECKING_LINE, READING_SAVED_LINE];
/** The fast report's header ("... follow in a moment." / "... More follows in a moment."), which the second part's replaces. */
export const FAST_HEADER = /(?:follow in a moment|More follows in a moment)\.$/;
const lineText = (line) => (typeof line === "string" ? line : line?.text || "");
const notChecking = (line) => !PENDING_LINES.includes(lineText(line));
const settled = (section) => (section.lines.some((l) => !notChecking(l))
  ? { ...section, lines: section.lines.filter(notChecking), summary: PENDING_LINES.includes(section.summary) ? section.lines.find(notChecking) || "" : section.summary }
  : section);

/** The follow-up token, when the answer has a slower second part. "" = none. */
export const chatMore = (raw) => (typeof raw === "string" && /^[\w.:=-]{1,200}$/.test(raw) ? raw : "");

/** The one follow-up request for a "more" token. */
export const chatMoreBody = (token) => ({ more: token });

/**
 * The answer with its second part added. `reply` is what POST {more} sent back; null = it failed.
 * Pending sections are filled by key; headlines and reasons go to the news section if the report has one,
 * otherwise onto the answer itself. The token is spent either way, so it is never asked again.
 */
export function chatMergeMore(answer, rawReply, isOwner = true, tableOf = () => null) {
  if (!answer) return answer;
  const reply = chatMoreReply(rawReply);
  if (!reply || typeof reply !== "object") {
    return { ...answer, more: "", moreState: "failed", lines: (answer.lines || []).filter(notChecking),
      sections: (answer.sections || []).map((s) => settled(s.pending ? { ...s, pending: false, failed: true } : s)) };
  }
  const extra = chatSections(reply.sections, isOwner, tableOf);
  const byKey = new Map(extra.map((s) => [s.key, s]));
  const headlines = replyHeadlines(reply);
  const reasons = chatReasons(reply.reasons);
  const newsKey = newsSectionKey(answer.sections);
  // A reason the server already wrote into the lines is not shown twice.
  const unsaid = (list, lines) => {
    const said = lines.map((l) => (typeof l === "string" ? l : l?.text || "")).join("\n");
    return list.filter((r) => !said.includes(r.replace(/ \(from .*\)$/, "")));
  };
  let sections = (answer.sections || []).map((s) => {
    const fresh = byKey.get(s.key);
    let next = fresh
      ? { ...fresh, view: fresh.view || s.view, links: fresh.links.length ? fresh.links : s.links, headlines: fresh.headlines.length ? fresh.headlines : s.headlines, pending: false }
      : s.pending ? { ...s, pending: false, failed: true } : s;
    if (s.key === newsKey && (headlines.length || reasons.length)) {
      next = { ...next, headlines: chatHeadlines([...next.headlines, ...headlines]), reasons: unsaid(reasons.length ? reasons : next.reasons, next.lines) };
    }
    return settled(next);
  });
  // A section the fast part did not list yet goes on the end, in the order the server sent it.
  const known = new Set(sections.map((s) => s.key));
  sections = [...sections, ...extra.filter((s) => !known.has(s.key))];
  const onAnswer = !newsKey;
  const more = dropVwapLines(reply.lines).map((text) => ({ bullet: text.startsWith("- "), text: text.replace(/^- /, "") }));
  // A report's second part header ("... with the chart numbers filled in") takes the place of the fast one ("... follow
  // in a moment"); every other line above the cards (the plain "no advice" line, another tool's lines) stays where it was.
  // Otherwise the second part's lines follow the first's.
  const kept = (answer.lines || []).filter(notChecking);
  const header = (answer.sections || []).length && extra.length && more.length ? kept.findIndex((l) => FAST_HEADER.test(lineText(l))) : -1;
  const lines = header >= 0 ? [...kept.slice(0, header), ...more, ...kept.slice(header + 1)] : [...kept, ...more];
  const technical = reply.views || reply.miniChart
    ? chatTechnical(reply.views, reply.miniChart, sections.find((s) => s.key === "technicals")?.asOf) || answer.technical || null
    : answer.technical || null;
  return {
    ...answer,
    sections,
    // The views hold their own lists of sections, so they are rebuilt from the merged ones.
    views: (answer.views || []).map((v) => ({ ...v, sections: viewSections(sections, v.key) })),
    technical,
    more: "",
    moreState: "done",
    lines,
    headlines: onAnswer ? chatHeadlines([...(answer.headlines || []), ...headlines]) : answer.headlines || [],
    reasons: onAnswer ? unsaid(reasons.length ? reasons : answer.reasons || [], lines) : answer.reasons || [],
    asOf: str(reply.asOf, 40) || answer.asOf,
    ownerSummary: chatOwnerSummary(reply.summary, isOwner),
    why: chatWhy(reply.why, isOwner) || answer.why || null,
  };
}

// ---------------------------------------------------------------------------------------------------------------
// The "why did it move" card (2026-11-14). His ask: an answer like Google's, "a crisp clear answer" with "a proper
// reason". The "more" part sends
//   why: {answer, by: "ai"|"code", note, window: {label, text},
//         reasons: [{title, text, sources: [{source, title, atText, url, tone}]}], drags: [{symbol, pct}]}
// and the card shows the answer and its key reasons (each source a link with its tone) on top; the fact lines sit
// under "Show the numbers", closed. Every word comes from the server; the screen only checks and lays it out.

/** The placeholder in the card's top slot while the answer is being written. */
export const WHY_WAIT = "Writing the answer...";
/** The fixed line under the answer, by who wrote it. The screen always shows its own, so an AI answer is always called one. */
export const WHY_LABELS = {
  ai: "Written by AI from the headlines and prices below. Not confirmed.",
  code: "From the headlines and prices below. Not confirmed.",
  // The AI's answer failed a check but some of its reasons passed (momx/chat_why_card.py NOTE_AI_REASONS, 2026-11-14).
  aiReasons: "Reasons written by AI from the headlines below; the answer is from the prices. Not confirmed.",
};
/** At most this many key reasons, and sources under each (his "crisp": three reasons). */
export const WHY_MAX_REASONS = 3;
export const WHY_MAX_SOURCES = 4;
/** The answers that get the card: "why did X move" (today, a window of today, or a named past day). */
export const WHY_INTENTS = ["why_move"];

/** A source under a reason: the article's own name, time and tone, and an https link (null = not linkable). */
export function chatWhySource(raw) {
  if (!raw || typeof raw !== "object") return null;
  const source = str(raw.source, 60);
  const title = str(raw.title, 240);
  if (!source && !title) return null;
  let url = null;
  try {
    const parsed = new URL(String(raw.url || ""));
    if (parsed.protocol === "https:") url = parsed.href;
  } catch { url = null; }
  return { source: source || (url ? new URL(url).hostname.replace(/^www\./, "") : ""), title, time: str(raw.atText || raw.time, 40), url, tone: chatTone(raw.tone) };
}

const oneLine = (value, max) => dropVwap(str(value, max).replace(/\s+/g, " "));

/**
 * The why card from the server's "why" field, or null when there is nothing to show. An AI-written answer
 * (by "ai") is the owner's only (2026-11-11 policy); a regular user's screen drops it even if one arrived.
 */
export function chatWhy(raw, isOwner = false) {
  if (!raw || typeof raw !== "object") return null;
  const by = raw.by === "ai" ? "ai" : raw.by === "code" ? "code" : "";
  if (!by || (by === "ai" && isOwner !== true)) return null;
  const answer = oneLine(raw.answer, 600);
  if (!answer) return null;
  const reasons = (Array.isArray(raw.reasons) ? raw.reasons : []).flatMap((r) => {
    if (!r || typeof r !== "object") return [];
    const title = oneLine(r.title, 80);
    const text = oneLine(r.text, 400);
    if (!title && !text) return [];
    const sources = (Array.isArray(r.sources) ? r.sources : []).map(chatWhySource).filter(Boolean).slice(0, WHY_MAX_SOURCES);
    return [{ title, text, sources }];
  }).slice(0, WHY_MAX_REASONS);
  const win = raw.window && typeof raw.window === "object" ? raw.window : {};
  const drags = (Array.isArray(raw.drags) ? raw.drags : [])
    .filter((d) => d && typeof d.symbol === "string" && /^[A-Z][A-Z0-9.]{0,6}$/.test(d.symbol.trim()) && num(d.pct) !== null)
    .map((d) => ({ symbol: d.symbol.trim(), pct: d.pct }))
    .slice(0, 8);
  const note = by === "ai" && raw.note === WHY_LABELS.aiReasons ? WHY_LABELS.aiReasons : WHY_LABELS[by];
  return { answer, by, note, window: { label: str(win.label, 60), text: str(win.text, 120) }, reasons, drags };
}

/** A drag as the numbers part prints it: the server's percent with its sign ("NVDA -5.89%"). */
export const dragText = (d) => `${d.symbol} ${d.pct > 0 ? "+" : ""}${d.pct.toFixed(2)}%`;

/**
 * How the card is laid out. top: "why" (the answer is in), "wait" (it is being written) or "" (an ordinary answer).
 * collapse: the fact lines and headlines go under "Show the numbers" (closed). A why answer whose second part failed,
 * or came without a card, keeps its lines open as before.
 */
export function whyLayout(answer) {
  if (!answer) return { top: "", collapse: false };
  if (answer.why) return { top: "why", collapse: true };
  if (WHY_INTENTS.includes(answer.intent) && answer.moreState === "waiting") return { top: "wait", collapse: true };
  return { top: "", collapse: false };
}

/**
 * The fact lines under "Show the numbers": no pending line (the top slot says it), and no headline the answer also
 * has as a tagged row (the server may send both; the screen shows the tagged rows).
 */
export function whyFactLines(lines, headlines) {
  const titles = (Array.isArray(headlines) ? headlines : []).map((h) => h.title).filter((t) => t && t.length >= 12);
  const list = (Array.isArray(lines) ? lines : []).filter((l) => !PENDING_LINES.includes(lineText(l)));
  if (!titles.length) return list;
  const isCopy = (l) => titles.some((t) => lineText(l).includes(t));
  // Clock times hold colons ("The app's saved headlines from Thu 4:00 PM to Fri 12:01 PM:"), so anything may sit between.
  const heading = (l) => /headlines\b.*:$/i.test(lineText(l));
  return list.filter((l, i) => {
    if (isCopy(l)) return false;
    if (!heading(l)) return true;
    // A "...headlines...:" heading goes too when every line under it (up to the next heading) was a copy.
    const under = [];
    for (let j = i + 1; j < list.length && !/:$/.test(lineText(list[j])); j += 1) under.push(list[j]);
    return !(under.length && under.every(isCopy));
  });
}

/** The line shown while the second part is on its way, and the one plain line if it fails. */
export const MORE_WAIT = "getting latest news...";
export const MORE_FAILED = "The latest news could not be loaded. The answer above is complete without it.";
