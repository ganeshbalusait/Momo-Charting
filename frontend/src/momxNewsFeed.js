// MomX NEWS from the ticker-tagged scraper (2026-09-26).
//
// The scanner worker still ships each row with its own best-effort `news`
// (Alpaca/Benzinga bulk + a bounded Yahoo RSS backfill, momx/news.py). The
// api_server now also keeps a STORE of headlines scraped from seven feeds where
// the PUBLISHER tagged the ticker (Yahoo search + RSS, Alpaca, Benzinga, Finviz,
// Nasdaq, SEC EDGAR) - "no guess work": a headline is attributed to a ticker
// only when the source itself says so.
//
// This module is the pure logic between the two: which headline a row shows,
// how the NEWS list is grouped, and what the one-line source health says.
// No React, no fetch - momxNewsFeed.test.js drives it directly.

export const MOMX_NEWS_FRESH_MS = 24 * 60 * 60 * 1000;

// A stored item (api_server.momx_news_latest `latest[SYM]` / `feed[]`) in the
// shape newsOf() reads, marked `stored` so the 24h gate lets it through (the
// store is bounded to the scrape's lookback, 7 days, server-side).
export function storedNewsToRowNews(item) {
  if (!item || typeof item !== "object") return null;
  const headline = typeof item.headline === "string" ? item.headline.trim() : "";
  if (headline === "") return null;
  const related = Array.isArray(item.relatedSymbols) ? item.relatedSymbols.filter((s) => typeof s === "string" && s !== "") : [];
  return {
    headline,
    publishedAt: typeof item.publishedAt === "string" ? item.publishedAt : "",
    source: typeof item.source === "string" ? item.source : "",
    url: typeof item.url === "string" ? item.url : "",
    // Every stored story is publisher-tagged to this ticker. A story tagged
    // with many tickers is still a round-up; same demote-never-hide rule as
    // the worker's Alpaca stamp (momx/news.py SPECIFIC_SYMBOL_LIMIT = 3).
    scope: related.length > 3 ? "market-wide" : "specific",
    namedCount: related.length,
    stored: true,
    via: typeof item.via === "string" ? item.via : "",
    sentiment: typeof item.sentiment === "string" ? item.sentiment : "",
    score: Number.isFinite(Number(item.score)) ? Number(item.score) : 0,
    summary: typeof item.summary === "string" ? item.summary : "",
    tags: typeof item.tags === "string" ? item.tags : "",
  };
}

function publishedMs(news) {
  if (!news || typeof news !== "object") return NaN;
  const iso = typeof news.publishedAt === "string" && news.publishedAt !== ""
    ? news.publishedAt
    : typeof news.at === "string" ? news.at : "";
  return Date.parse(iso);
}

// Rows with the store folded in. Per row: the NEWER of the worker's headline
// and the stored one wins (ties go to the store, which carries publisher, via
// and summary). Rows the store cannot improve keep their IDENTITY, so MomxRow's
// memo re-renders only the rows whose headline actually changed.
export function mergeStoredNews(rows, latestBySymbol) {
  if (!Array.isArray(rows) || rows.length === 0) return rows;
  if (!latestBySymbol || typeof latestBySymbol !== "object") return rows;
  let changed = false;
  const out = rows.map((row) => {
    if (!row || typeof row !== "object" || typeof row.symbol !== "string") return row;
    const stored = storedNewsToRowNews(latestBySymbol[row.symbol.toUpperCase()]);
    if (!stored) return row;
    const own = row.news && typeof row.news === "object" && typeof row.news.headline === "string" && row.news.headline.trim() !== ""
      ? row.news
      : null;
    if (own) {
      const ownMs = publishedMs(own);
      const storedMs = publishedMs(stored);
      // A worker headline with a readable time newer than the store's wins;
      // an unreadable time on either side defers to the store.
      if (Number.isFinite(ownMs) && Number.isFinite(storedMs) && ownMs > storedMs) return row;
      if (Number.isFinite(ownMs) && !Number.isFinite(storedMs)) return row;
    }
    changed = true;
    return { ...row, news: stored };
  });
  return changed ? out : rows;
}

// The tickers the tab asks the store about: the board's rows in board order
// (ranked), deduped, capped. The server caps again (NEWS_MOMX_REFRESH_MAX_SYMBOLS).
export function boardSymbols(rows, limit = 120) {
  const out = [];
  const seen = new Set();
  for (const row of Array.isArray(rows) ? rows : []) {
    const symbol = row && typeof row.symbol === "string" ? row.symbol.trim().toUpperCase() : "";
    if (symbol === "" || seen.has(symbol)) continue;
    seen.add(symbol);
    out.push(symbol);
    if (out.length >= limit) break;
  }
  return out;
}

// "43m" / "2h" / "3d" - the same compact age the column prints.
export function feedAge(iso, nowMs = Date.now()) {
  const t = Date.parse(typeof iso === "string" ? iso : "");
  if (!Number.isFinite(t)) return "";
  const minutes = Math.max(0, Math.floor((nowMs - t) / 60000));
  if (minutes < 60) return minutes + "m";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + "h";
  return Math.floor(hours / 24) + "d";
}

// The NEWS list: one group per ticker, tickers ordered by their newest story
// (newest first), stories inside a group newest first. Only tickers that are
// on the board (`symbolOrder`) appear, so a stale store entry for a ticker he
// removed from the list cannot show.
export function groupFeedBySymbol(feed, symbolOrder) {
  const wanted = new Set((Array.isArray(symbolOrder) ? symbolOrder : []).map((s) => String(s || "").toUpperCase()));
  const groups = new Map();
  for (const item of Array.isArray(feed) ? feed : []) {
    if (!item || typeof item !== "object") continue;
    const symbol = typeof item.symbol === "string" ? item.symbol.toUpperCase() : "";
    if (symbol === "" || (wanted.size > 0 && !wanted.has(symbol))) continue;
    const headline = typeof item.headline === "string" ? item.headline.trim() : "";
    if (headline === "") continue;
    const ms = Date.parse(typeof item.publishedAt === "string" ? item.publishedAt : "");
    const entry = { ...item, headline, atMs: Number.isFinite(ms) ? ms : null };
    if (!groups.has(symbol)) groups.set(symbol, { symbol, items: [], newestMs: null });
    const group = groups.get(symbol);
    group.items.push(entry);
    if (entry.atMs !== null && (group.newestMs === null || entry.atMs > group.newestMs)) group.newestMs = entry.atMs;
  }
  const out = Array.from(groups.values());
  for (const group of out) {
    group.items.sort((a, b) => (b.atMs || 0) - (a.atMs || 0));
  }
  out.sort((a, b) => (b.newestMs || 0) - (a.newestMs || 0));
  return out;
}

// "N headlines · k/n sources OK · Unavailable: Finviz, Nasdaq" - the one line
// under the NEWS list that says where the headlines came from and which feeds
// did not answer on the last scrape. Before any scrape: says so, never "0/0".
export function sourceHealthLine(payload) {
  const meta = payload && typeof payload === "object" && payload.newsFeedMeta && typeof payload.newsFeedMeta === "object"
    ? payload.newsFeedMeta
    : null;
  const headlines = payload && Array.isArray(payload.feed) ? payload.feed.length : 0;
  const parts = [headlines + (headlines === 1 ? " headline" : " headlines")];
  if (payload && payload.refreshing) {
    parts.push("refreshing the sources now");
    return parts.join(" · ");
  }
  const sources = meta && Array.isArray(meta.sources) ? meta.sources : [];
  if (sources.length === 0) {
    parts.push(payload && payload.refreshError ? "last refresh failed: " + payload.refreshError : "sources not refreshed yet");
    return parts.join(" · ");
  }
  const ok = sources.filter((s) => s && (s.status === "ok" || s.status === "partial"));
  const down = sources.filter((s) => s && (s.status === "blocked" || s.status === "error"));
  parts.push(ok.length + "/" + sources.length + " sources OK");
  if (down.length > 0) parts.push("Unavailable: " + down.map((s) => s.label || s.name).join(", "));
  return parts.join(" · ");
}

// Sentiment word -> the class the badge/sparkle uses. The scraper's keyword
// score is weaker evidence than the AI read, so the caller lets an AI
// direction win; this is the fallback.
export function sentimentTone(sentiment) {
  const word = typeof sentiment === "string" ? sentiment.trim().toLowerCase() : "";
  if (word === "strong" || word === "positive") return "up";
  if (word === "negative") return "down";
  if (word === "neutral") return "flat";
  return null;
}
