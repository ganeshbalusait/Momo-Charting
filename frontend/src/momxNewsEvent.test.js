import test from "node:test";
import assert from "node:assert/strict";
import { shortAge, newsColumnCell, earningsMapFrom, eventColumnCell } from "./momxCells.js";

const NOW = Date.parse("2026-09-25T14:00:00Z");

test("shortAge prints MomoX-style ages", () => {
  assert.equal(shortAge(NOW - 43 * 60000, NOW), "43m");
  assert.equal(shortAge(NOW - 2 * 3600000, NOW), "2h");
  assert.equal(shortAge(NOW - 26 * 3600000, NOW), "1d");
  assert.equal(shortAge(NaN, NOW), "");
});

test("news cell: age, fresh flag, AI direction; blank without a headline", () => {
  const row = {
    news: { headline: "RKLB wins launch contract", publishedAt: new Date(NOW - 2 * 3600000).toISOString(), source: "Benzinga" },
    catalyst: { category: "CONTRACT", direction: "bullish", summary: "New contract" },
  };
  const cell = newsColumnCell(row, NOW);
  assert.equal(cell.text, "2h");
  assert.equal(cell.fresh, true);
  assert.equal(cell.ai, "up");
  assert.match(cell.title, /good news/);
  assert.equal(newsColumnCell({ news: null }, NOW), null);
  assert.equal(newsColumnCell({ ...row, catalyst: { category: "UNKNOWN" } }, NOW).ai, null);
  // older than the 24h window -> blank, same gate as the news badge
  assert.equal(newsColumnCell({ news: { headline: "x", publishedAt: new Date(NOW - 30 * 3600000).toISOString() } }, NOW), null);
});

test("earnings map keeps the nearest future date per symbol", () => {
  const map = earningsMapFrom({ rows: [
    { symbol: "AMD", date: "2026-11-02", daysUntil: 38, timingCode: "amc", timing: "After market close" },
    { symbol: "AMD", date: "2026-10-01", daysUntil: 6, timingCode: "bmo" },
    { symbol: "OLD", date: "2026-09-20", daysUntil: -5 },
    null,
  ] });
  assert.equal(map.get("AMD").date, "2026-10-01");
  assert.equal(map.has("OLD"), false);
  assert.equal(earningsMapFrom(null).size, 0);
});

test("event cell: M/D, before/after icon, amber within 7 days", () => {
  const soon = eventColumnCell({ date: "2026-10-01", daysUntil: 6, timingCode: "bmo" });
  assert.equal(soon.text, "10/1");
  assert.equal(soon.icon, "☀");
  assert.equal(soon.soon, true);
  const later = eventColumnCell({ date: "2026-11-02", daysUntil: 38, timingCode: "amc", timing: "After market close" });
  assert.equal(later.icon, "☾");
  assert.equal(later.soon, false);
  assert.match(later.title, /in 38 days/);
  assert.equal(eventColumnCell(null), null);
  assert.equal(eventColumnCell({ date: "bad" }), null);
});

// Ticker-tagged store (2026-09-26): a stored headline older than 24h still
// shows, labelled stored; the sparkle is on every row and takes the keyword
// sentiment when the AI has not judged the story.
test("news cell: stored headline past 24h shows as stored, sparkle tone from sentiment, AI wins", () => {
  const stored = {
    news: {
      headline: "Apple beats", publishedAt: new Date(NOW - 50 * 3600000).toISOString(),
      source: "Reuters", via: "Yahoo Finance", stored: true, sentiment: "Strong", summary: "teaser",
    },
  };
  const cell = newsColumnCell(stored, NOW);
  assert.equal(cell.text, "2d");
  assert.equal(cell.stored, true);
  assert.equal(cell.fresh, false);
  assert.equal(cell.ai, null);
  assert.equal(cell.tone, "up");
  assert.equal(cell.aiJudged, false);
  assert.match(cell.title, /Stored/);
  assert.match(cell.title, /via Yahoo Finance/);
  // the worker's own headline past 24h is still hidden (unchanged rule)
  assert.equal(newsColumnCell({ news: { ...stored.news, stored: false } }, NOW), null);
  // no sentiment, no AI -> a plain sparkle
  assert.equal(newsColumnCell({ news: { headline: "x", publishedAt: new Date(NOW - 3600000).toISOString() } }, NOW).tone, "none");
  // the AI verdict outranks the keyword sentiment
  const judged = newsColumnCell({ ...stored, catalyst: { category: "OFFERING", direction: "bearish" } }, NOW);
  assert.equal(judged.tone, "down");
  assert.equal(judged.aiJudged, true);
  assert.match(judged.title, /Fresh|Stored/);
});
