// The Alpaca watchlist runs to ~358 tickers, past what anyone can scan by eye on
// a phone. This is the search behind that list, kept out of App.jsx so it can be
// tested directly rather than through a source grep.
//
// Substring, not prefix: the trader looks for "MST" to find MSTR, and for "GOO"
// to find both GOOG and GOOGL. Case and surrounding space are the user's
// problem to make, not theirs to avoid.
export function normalizeWatchlistSearch(term) {
  return String(term ?? "").trim().toUpperCase();
}

export function filterWatchlistSymbols(symbols, term) {
  const list = Array.isArray(symbols) ? symbols : [];
  const needle = normalizeWatchlistSearch(term);
  if (!needle) return list;
  return list.filter((symbol) => String(symbol ?? "").toUpperCase().includes(needle));
}
