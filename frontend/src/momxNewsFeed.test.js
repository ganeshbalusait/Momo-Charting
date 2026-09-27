import test from "node:test";
import assert from "node:assert/strict";
import {
  boardSymbols,
  feedAge,
  groupFeedBySymbol,
  mergeStoredNews,
  sentimentTone,
  sourceHealthLine,
  storedNewsToRowNews,
} from "./momxNewsFeed.js";

const NOW = Date.parse("2026-09-26T20:00:00Z");
const iso = (hoursAgo) => new Date(NOW - hoursAgo * 3600000).toISOString();

const stored = (symbol, headline, hoursAgo, extra = {}) => ({
  symbol,
  headline,
  url: "https://example.com/" + symbol,
  source: "Reuters",
  via: "Yahoo Finance",
  publishedAt: iso(hoursAgo),
  sentiment: "Strong",
  score: 3,
  summary: "teaser",
  relatedSymbols: [symbol],
  ...extra,
});

test("storedNewsToRowNews: newsOf-shaped, marked stored, round-ups demoted", () => {
  const news = storedNewsToRowNews(stored("AAPL", "Apple beats", 2));
  assert.equal(news.headline, "Apple beats");
  assert.equal(news.stored, true);
  assert.equal(news.scope, "specific");
  assert.equal(news.via, "Yahoo Finance");
  assert.equal(news.sentiment, "Strong");
  const wide = storedNewsToRowNews(stored("AAPL", "Ten stocks", 2, { relatedSymbols: ["AAPL", "MSFT", "NVDA", "AMD", "TSLA"] }));
  assert.equal(wide.scope, "market-wide");
  assert.equal(wide.namedCount, 5);
  assert.equal(storedNewsToRowNews({ headline: "   " }), null);
  assert.equal(storedNewsToRowNews(null), null);
});

test("mergeStoredNews: newer headline wins, ties go to the store, untouched rows keep identity", () => {
  const rows = [
    { symbol: "AAPL", news: { headline: "Worker older", publishedAt: iso(5), source: "Benzinga" } },
    { symbol: "MSFT", news: { headline: "Worker newer", publishedAt: iso(1), source: "Benzinga" } },
    { symbol: "NVDA" },
    { symbol: "TSLA", news: { headline: "Worker no time", source: "Benzinga" } },
    { symbol: "AMD" },
  ];
  const latest = {
    AAPL: stored("AAPL", "Store newer", 2),
    MSFT: stored("MSFT", "Store older", 3),
    NVDA: stored("NVDA", "Store only", 4),
    TSLA: stored("TSLA", "Store timed", 4),
  };
  const merged = mergeStoredNews(rows, latest);
  assert.notEqual(merged, rows);
  assert.equal(merged[0].news.headline, "Store newer");
  assert.equal(merged[0].news.stored, true);
  assert.equal(merged[1], rows[1], "worker headline newer than the store keeps the row untouched");
  assert.equal(merged[2].news.headline, "Store only");
  assert.equal(merged[3].news.headline, "Store timed", "an unreadable worker time defers to the store");
  assert.equal(merged[4], rows[4], "no store entry -> same row object");
  // Nothing to merge -> the SAME array comes back, so memo chains hold.
  assert.equal(mergeStoredNews(rows, {}), rows);
  assert.equal(mergeStoredNews(rows, null), rows);
  assert.equal(mergeStoredNews([], latest).length, 0);
});

test("boardSymbols: board order, deduped, upper-cased, capped", () => {
  const rows = [{ symbol: "nvda" }, { symbol: "AAPL" }, { symbol: "NVDA" }, {}, { symbol: " " }, { symbol: "MSFT" }];
  assert.deepEqual(boardSymbols(rows), ["NVDA", "AAPL", "MSFT"]);
  assert.deepEqual(boardSymbols(rows, 2), ["NVDA", "AAPL"]);
  assert.deepEqual(boardSymbols(null), []);
});

test("feedAge prints the compact age", () => {
  assert.equal(feedAge(iso(0.5), NOW), "30m");
  assert.equal(feedAge(iso(3), NOW), "3h");
  assert.equal(feedAge(iso(50), NOW), "2d");
  assert.equal(feedAge("garbage", NOW), "");
});

test("groupFeedBySymbol: only board tickers, newest ticker first, newest story first", () => {
  const feed = [
    stored("MSFT", "MSFT old", 10),
    stored("AAPL", "AAPL mid", 5),
    stored("MSFT", "MSFT new", 1),
    stored("AAPL", "AAPL old", 9),
    stored("ZZZ", "Not on the board", 0.1),
    stored("AAPL", "   ", 0.2),
  ];
  const groups = groupFeedBySymbol(feed, ["AAPL", "MSFT", "NVDA"]);
  assert.deepEqual(groups.map((g) => g.symbol), ["MSFT", "AAPL"]);
  assert.deepEqual(groups[0].items.map((i) => i.headline), ["MSFT new", "MSFT old"]);
  assert.deepEqual(groups[1].items.map((i) => i.headline), ["AAPL mid", "AAPL old"]);
  assert.equal(groups[0].newestMs, Date.parse(iso(1)));
  assert.deepEqual(groupFeedBySymbol([], ["AAPL"]), []);
});

test("sourceHealthLine: counts, OK ratio, unavailable labels, and the not-yet states", () => {
  const meta = {
    sources: [
      { name: "yahoo_search", label: "Yahoo Finance", status: "ok" },
      { name: "alpaca", label: "Alpaca News (Benzinga)", status: "partial" },
      { name: "finviz", label: "Finviz", status: "blocked" },
      { name: "nasdaq", label: "Nasdaq", status: "error" },
    ],
  };
  const feed = [stored("AAPL", "a", 1), stored("AAPL", "b", 2), stored("MSFT", "c", 3)];
  assert.equal(
    sourceHealthLine({ feed, newsFeedMeta: meta }),
    "3 headlines · 2/4 sources OK · Unavailable: Finviz, Nasdaq",
  );
  assert.equal(sourceHealthLine({ feed: [stored("AAPL", "a", 1)], newsFeedMeta: { sources: meta.sources.slice(0, 1) } }), "1 headline · 1/1 sources OK");
  assert.equal(sourceHealthLine({ feed: [], newsFeedMeta: {} }), "0 headlines · sources not refreshed yet");
  assert.equal(sourceHealthLine({ feed, refreshing: true, newsFeedMeta: meta }), "3 headlines · refreshing the sources now");
  assert.equal(sourceHealthLine({ feed: [], refreshError: "RuntimeError: edge blocked" }), "0 headlines · last refresh failed: RuntimeError: edge blocked");
  assert.equal(sourceHealthLine(null), "0 headlines · sources not refreshed yet");
});

test("sentimentTone maps the scraper's words", () => {
  assert.equal(sentimentTone("Strong"), "up");
  assert.equal(sentimentTone("Positive"), "up");
  assert.equal(sentimentTone("Negative"), "down");
  assert.equal(sentimentTone("Neutral"), "flat");
  assert.equal(sentimentTone(""), null);
  assert.equal(sentimentTone(undefined), null);
});
