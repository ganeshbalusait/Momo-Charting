// Faked replies for the Analysts and Technical analysis cards (2026-11-11): the chat's ticker report and the Long
// Term ticker detail. ANALYST_ROUTE and CAPTURED are the shapes the real routes send; the older flat ANALYST,
// TECHNICAL and reportReply() are the screen's own card shapes (still accepted, used for the card details).
import { readFileSync } from "node:fs";

// 30 daily bars ending Tue 2026-11-10, and 12 weekly bars.
function days(n, end = "2026-11-10") {
  const out = [];
  const d = new Date(`${end}T00:00:00Z`);
  while (out.length < n) {
    if (d.getUTCDay() !== 0 && d.getUTCDay() !== 6) out.unshift(d.toISOString().slice(0, 10));
    d.setUTCDate(d.getUTCDate() - 1);
  }
  return out;
}
const bar = (time, i) => {
  const open = 180 + Math.sin(i / 3) * 6;
  const close = open + (i % 3 === 0 ? -2 : 1.5);
  return { time, open, high: Math.max(open, close) + 1.2, low: Math.min(open, close) - 1.1, close, volume: 2e7 + (i % 5) * 3e6 };
};
const dailyDays = days(30);
const weeklyDays = Array.from({ length: 12 }, (_, i) => { const d = new Date("2026-08-24T00:00:00Z"); d.setUTCDate(d.getUTCDate() + i * 7); return d.toISOString().slice(0, 10); });

export const TECHNICAL = {
  asOf: "as of 15:58 ET",
  source: "the app's daily bars",
  terms: {
    short: { verdict: "bearish", label: "Bearish signal", strength: 3, reason: "Closed under the 21-day average two days running." },
    mid: { verdict: "neutral", strength: 2, reason: "Between the 50-day average and resistance." },
    long: { verdict: "bullish", label: "Bullish signal", strength: 4, reason: "Above the 200-day average and rising." },
  },
  charts: {
    daily: {
      bars: dailyDays.map(bar),
      events: [
        { time: dailyDays[6], kind: "bullish", name: "Crossed above the 50-day average" },
        { time: dailyDays[14], kind: "bearish", name: "Bearish engulfing candle" },
        { time: dailyDays[20], kind: "neutral", name: "Inside day" },
        { time: dailyDays[24], kind: "earnings", name: "Earnings" },
      ],
      support: { price: 172.5 },
      resistance: { price: 191.25 },
    },
    weekly: {
      bars: weeklyDays.map(bar),
      events: [{ time: weeklyDays[5], kind: "bullish", name: "Weekly breakout" }],
      support: { price: 168 },
      resistance: { price: 195 },
    },
  },
};

export const ANALYST = {
  symbol: "AAPL",
  strongBuy: 20, buy: 25, hold: 10, sell: 4, strongSell: 1,
  consensus: "Buy",
  updated: "2026-11-01",
  source: "Finnhub",
  price: 180,
  target: { low: 150, average: 200, high: 250, source: "FMP" },
  months: [
    { period: "2026-08-01", strongBuy: 18, buy: 24, hold: 12, sell: 4, strongSell: 1 },
    { period: "2026-09-01", strongBuy: 19, buy: 25, hold: 11, sell: 4, strongSell: 1 },
    { period: "2026-10-01", strongBuy: 20, buy: 24, hold: 11, sell: 4, strongSell: 1 },
    { period: "2026-11-01", strongBuy: 20, buy: 25, hold: 10, sell: 4, strongSell: 1 },
  ],
};

/**
 * GET /api/long-term/analyst as the real route answers it (momx/analyst.payload(): the cached doc under "views").
 * Same numbers as ANALYST above; the route sends no price, so the page draws the targets against its own row price.
 * See frontend/src/analystGauge.routeReply.json for one built by payload() itself.
 */
export const ANALYST_ROUTE = {
  symbol: "AAPL",
  status: "fresh",
  label: "Analyst views from Finnhub and FMP, not the app's rules and not advice.",
  views: {
    symbol: "AAPL", fetchedAt: "2026-11-01T12:40:00-04:00", day: "2026-11-01",
    finnhub: { months: [...ANALYST.months].reverse() },
    targets: { targetHigh: 250, targetLow: 150, targetConsensus: 200, targetMedian: 198 },
    summary: null,
    grades: { strongBuy: 20, buy: 25, hold: 10, sell: 4, strongSell: 1, consensus: "Buy" },
    errors: {},
  },
  lines: ["FMP grades (60 grades): ..."],
};

/**
 * Replies captured from the real POST /api/bot-chat (tests/api_harness.py with a fake AI, 2026-11-11): for each
 * question the fast part and the route's answer to its "more" read ({"more": {...}}). Used as-is so the specs
 * test the shape the server really sends, not one the screen made up.
 */
export const CAPTURED = JSON.parse(readFileSync(new URL("../../frontend/src/chatReport.serverReply.json", import.meta.url), "utf8"));

/** A full ticker report as the server sends it: 7 sections in two views, two of them still on their way. */
export function reportReply(ticker = "NVDA") {
  return {
    intent: "report", ticker, lines: [`${ticker} report`], asOf: "as of 15:58 ET",
    views: [{ key: "short", title: "Short term", met: 5, total: 8 }, { key: "long", title: "Long term", met: 3, total: 5 }],
    sections: [
      { key: "price", title: "Price", view: "short", lines: [`${ticker} $190.10, up 2.4% today`], asOf: "as of 15:58 ET", links: [{ label: `Open ${ticker} chart`, view: "Charts & OI", ticker }] },
      { key: "levels", title: "Chart levels", view: "short", lines: ["21 EMA $188.40, VWAP $188.10, 50 EMA $185.00", "VWAP $188.10"] },
      { key: "technical", title: "Technical analysis", view: "short", lines: [], technical: TECHNICAL },
      { key: "news", title: "News", view: "short", pending: true, lines: [] },
      { key: "scanner", title: "On the scanner", view: "short", lines: ["Mag7 calls 09:31-15:55, 94 saved rows"], summary: "On Mag7 calls all day" },
      { key: "options", title: "Options", view: "short", lines: ["Call wall $200 (High OI board)"] },
      { key: "analysts", title: "Analysts", view: "long", pending: true, lines: [] },
      { key: "value", title: "Long Term value", view: "long", lines: ["WAIT: price above the safety buy"] },
    ],
    more: "rep_tok_1",
  };
}

export const REPORT_MORE = {
  sections: [
    { key: "news", title: "News", view: "short", lines: ["2 headlines today"] },
    { key: "analysts", title: "Analysts", view: "long", lines: ["Consensus Buy from 60 analysts"], analyst: { ...ANALYST, symbol: "NVDA" } },
  ],
  headlines: [
    { title: "Nvidia supplier lifts guidance", source: "Reuters", url: "https://example.com/nvda-1", time: "14:05 ET" },
    { title: "Chip stocks rally into the close", source: "CNBC", url: "https://example.com/nvda-2", time: "15:30 ET" },
  ],
  reasons: ["A supplier's higher guidance lifted the chip names.", "The whole semiconductor group rallied."],
};
