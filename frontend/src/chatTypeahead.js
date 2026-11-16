// Type-ahead for the chat's ask box (2026-11-11). His "auto text features - 'why tsla' next need autofill
// based on frequently asked". Everything here runs in the browser, on every keystroke, with no AI and no
// server call: his own past questions on this device first (most asked first), then built-in question
// patterns filled with the ticker he typed. Pure, so node:test can run it.

import { chatQuestion } from "./botChat.js";

export const TYPEAHEAD_MAX = 6;

// Common company names a trader types instead of the ticker. Lower case, one or more words.
export const COMPANY_TICKERS = {
  tesla: "TSLA", apple: "AAPL", nvidia: "NVDA", microsoft: "MSFT", amazon: "AMZN", google: "GOOGL", alphabet: "GOOGL",
  meta: "META", facebook: "META", netflix: "NFLX", broadcom: "AVGO", intel: "INTC", palantir: "PLTR", coinbase: "COIN",
  microstrategy: "MSTR", strategy: "MSTR", uber: "UBER", disney: "DIS", walmart: "WMT", costco: "COST", boeing: "BA",
  "jp morgan": "JPM", jpmorgan: "JPM", oracle: "ORCL", salesforce: "CRM", adobe: "ADBE", shopify: "SHOP", paypal: "PYPL",
  "super micro": "SMCI", supermicro: "SMCI", qualcomm: "QCOM", micron: "MU", "taiwan semi": "TSM", tsmc: "TSM",
  robinhood: "HOOD", sofi: "SOFI", rivian: "RIVN", lucid: "LCID", alibaba: "BABA", "s&p": "SPY", "s&p 500": "SPY",
  nasdaq: "QQQ", "russell": "IWM", snowflake: "SNOW", crowdstrike: "CRWD", "advanced micro": "AMD", arm: "ARM",
  "home depot": "HD", "goldman": "GS", "bank of america": "BAC", exxon: "XOM", chevron: "CVX", nike: "NKE",
  starbucks: "SBUX", "mcdonalds": "MCD", "coca cola": "KO", pepsi: "PEP", pfizer: "PFE", moderna: "MRNA",
  "eli lilly": "LLY", lilly: "LLY", "novo": "NVO", "unitedhealth": "UNH", visa: "V", mastercard: "MA", dell: "DELL",
  "hims": "HIMS", rocket: "RKLB", "rocket lab": "RKLB", "ionq": "IONQ", "rigetti": "RGTI", "carvana": "CVNA",
};

// Tickers the app always knows, so completion works before any answer has named one.
export const BASE_TICKERS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AMD", "AVGO", "NFLX", "PLTR"];

// Question families the chat answers, with the ticker as {T}. `owner` patterns are never offered to a regular user.
// No VWAP anywhere (his rule: "Don't use vwap").
export const TICKER_PATTERNS = [
  { text: "why {T} went up today" },
  { text: "why {T} is down today" },
  { text: "latest news on {T}" },
  { text: "{T} chart levels" },
  { text: "{T} option chain" },
  { text: "{T} call wall" },
  { text: "{T} today on the scanner" },
  { text: "{T} at 9:30 on the scanner" },
  { text: "{T} signals on 5m" },
  { text: "{T} put wall" },
  { text: "{T} expected move" },
  { text: "max pain for {T}" },
  { text: "{T} price" },
  { text: "how is {T} doing" },
  { text: "when is {T} earnings" },
  { text: "long term {T}" },
  { text: "open {T} chart" },
  { text: "{T} scanner yesterday" },
  { text: "why was {T} missed", owner: true },
  { text: "why {T} failed", owner: true },
  { text: "{T} if I take how much it went up", owner: true },
];

export const GENERAL_PATTERNS = [
  { text: "which sector is hot" },
  { text: "premarket today" },
  { text: "scanner at 9:30 give me the list of tickers" },
  { text: "which tickers stayed on top today" },
  { text: "top 5 mag7 at 10:15" },
  { text: "what was hot at noon" },
  { text: "morning brief" },
  { text: "what is RVOL" },
  { text: "where is the scanner" },
  { text: "release notes" },
  { text: "what is the bot doing", owner: true },
  { text: "scorecard", owner: true },
];

// Words that are never a ticker, even though they look like one.
const NOT_TICKERS = new Set(("a an and any are at be by can do for from go had has have he how i if in is it its me my no not of on or " +
  "out so the to up us was we what when where which who why will with you your today news chart level levels option options chain call " +
  "put wall walls scanner signal signals price long term open close top hot more show see give list day week did does down went just " +
  "near about all get than then this that these here there max pain move earnings report brief yes ok please tell much").split(" "));

const NO_VWAP = /\bvwap\b/i;
const words = (s) => s.toLowerCase().split(/[^a-z0-9&:.]+/).filter(Boolean);

/** Every ticker the chat can complete to: the base set, the page's ticker and whatever the app has shown. Upper case, unique. */
export function knownTickers(...lists) {
  const out = new Set(BASE_TICKERS);
  for (const list of lists) {
    for (const raw of Array.isArray(list) ? list : [list]) {
      const t = String(raw || "").trim().toUpperCase();
      if (/^[A-Z][A-Z0-9.]{0,5}$/.test(t)) out.add(t);
    }
  }
  return [...out];
}

// Every word in the built-in questions, so a half-typed word ("ch" for chart) is not read as a ticker.
const VOCAB = new Set([...TICKER_PATTERNS, ...GENERAL_PATTERNS].flatMap((p) => words(p.text.replace("{T}", ""))));
const startsVocab = (word) => [...VOCAB].some((v) => v.startsWith(word));

// Which typed word is the ticker, and which tickers it may be. Exact tickers and company names win, then
// completing the word still being typed, then a word that only looks like a ticker. {at:-1} = none.
function findTicker(tokens, known, typingLast) {
  for (let i = tokens.length - 1; i >= 0; i -= 1) {
    const pair = i > 0 ? COMPANY_TICKERS[`${tokens[i - 1]} ${tokens[i]}`] : null;
    if (pair) return { at: [i - 1, i], choices: [pair] };
    const up = tokens[i].toUpperCase();
    if (COMPANY_TICKERS[tokens[i]]) return { at: [i], choices: [COMPANY_TICKERS[tokens[i]]] };
    if (known.includes(up) && !NOT_TICKERS.has(tokens[i])) return { at: [i], choices: [up] };
  }
  const last = tokens.length - 1;
  if (typingLast && last >= 0 && !NOT_TICKERS.has(tokens[last])) {
    const word = tokens[last];
    const up = word.toUpperCase();
    const fromTickers = known.filter((t) => t.startsWith(up));
    const fromNames = word.length >= 3 ? Object.entries(COMPANY_TICKERS).filter(([n]) => n.startsWith(word)).map(([, t]) => t) : [];
    const all = [...new Set([...fromTickers, ...fromNames])];
    if (all.length && !(startsVocab(word) && last > 0)) return { at: [last], choices: all.slice(0, 2) };
  }
  for (let i = tokens.length - 1; i >= 0; i -= 1) {
    const word = tokens[i];
    if (!/^[a-z]{2,5}$/.test(word) || NOT_TICKERS.has(word)) continue;
    if (i === last && typingLast && startsVocab(word)) continue;
    return { at: [i], choices: [word.toUpperCase()] };
  }
  return { at: [], choices: [] };
}

// Does every typed word start a word of the suggestion? "why" matches "why", "up" matches "up", "9" matches "9:30".
function covers(typed, suggestion) {
  const have = words(suggestion);
  return typed.every((w) => have.some((h) => h.startsWith(w)));
}

/**
 * Up to `max` suggestions for what has been typed.
 * history = [{q, n?, at?}] this account's questions on this device. known = tickers to complete from.
 * Returns strings, best first, no repeats (ignoring case).
 */
export function typeahead(typed, { history = [], known = [], isOwner = true, pageTicker = "", max = TYPEAHEAD_MAX } = {}) {
  const text = chatQuestion(typed).toLowerCase();
  if (text.length < 2) return [];
  const tokens = words(text);
  if (!tokens.length) return [];
  const tickers = knownTickers(known, pageTicker);
  const endsTyping = !/\s$/.test(String(typed || ""));
  const out = [];
  const seen = new Set();
  const add = (s) => {
    const q = chatQuestion(s);
    const key = q.toLowerCase();
    if (!q || seen.has(key) || NO_VWAP.test(q) || out.length >= max) return;
    seen.add(key);
    out.push(q);
  };

  // 1. His own questions, most asked first, then newest.
  const own = (Array.isArray(history) ? history : [])
    .map((item, i) => ({ q: String(item?.q || ""), n: Number.isFinite(item?.n) && item.n > 0 ? item.n : 1, i }))
    .filter(({ q }) => q && covers(tokens, q))
    .sort((a, b) => b.n - a.n || a.i - b.i);
  for (const { q } of own.slice(0, 3)) add(q);

  // 2. Built-in questions for the ticker he typed.
  const { at, choices } = findTicker(tokens, tickers, endsTyping);
  const rest = tokens.filter((_, i) => !at.includes(i));
  const allowed = (p) => isOwner || !p.owner;

  if (choices.length) {
    for (const t of choices) {
      const filled = TICKER_PATTERNS.filter(allowed).map((p) => p.text.replace("{T}", t));
      // Patterns that also match the other words first, then the rest of that ticker's questions.
      filled.filter((s) => covers(rest, s)).forEach(add);
    }
    if (rest.length && out.length < max) {
      for (const t of choices) TICKER_PATTERNS.filter(allowed).map((p) => p.text.replace("{T}", t)).forEach(add);
    }
  }
  // 3. Questions with no ticker, then the same questions about the ticker on screen.
  GENERAL_PATTERNS.filter(allowed).map((p) => p.text).filter((s) => covers(tokens, s)).forEach(add);
  const onScreen = String(pageTicker || "").trim().toUpperCase();
  if (!choices.length && onScreen) {
    TICKER_PATTERNS.filter(allowed).map((p) => p.text.replace("{T}", onScreen)).filter((s) => covers(tokens, s)).forEach(add);
  }
  return out;
}
