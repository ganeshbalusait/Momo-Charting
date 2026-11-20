// Ticker story card helpers (2026-11-20): one ticker in simple words, the same on LEAP OI Scanner and AGX Flow.

export const STORY_POLL_PENDING_MS = 6000;      // while the pages are still reading the ticker
export const STORY_POLL_READY_MS = 5 * 60 * 1000;

/** The request for one ticker, or "" for a blank. */
export function storyUrl(ticker) {
  const t = String(ticker || "").trim().toUpperCase();
  return t ? `/api/ticker-story?symbol=${encodeURIComponent(t)}` : "";
}
