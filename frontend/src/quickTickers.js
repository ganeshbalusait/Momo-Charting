// The one-tap ticker rails (phone Options, the chart quick strips) were a
// hardcoded list of 13 names in App.jsx. That made them the surface most likely
// to produce "I added XXX to my watchlist and it isn't there" - because it never
// could be there without a code edit.
//
// The rails now start with the pinned names and continue with the watchlist.
// Pinned entries are kept even when they are not on the watchlist: SPY, QQQ,
// SLV and USO are reference instruments the trader wants one tap away whether
// or not he is trading them. Everything after them follows My Watchlist, so an
// added ticker appears and a removed one disappears.
//
// No cap: the rails scroll horizontally (index.css .mobile-quick-options-tickers
// is overflow-x:auto), so length costs nothing. ORDER is what matters, and
// pinned-first keeps the common names reachable without scrolling.
export function buildQuickTickers(pinned, watchlist) {
  const out = [];
  const seen = new Set();
  const push = (raw) => {
    const symbol = String(raw ?? "").trim().toUpperCase();
    if (!symbol || seen.has(symbol)) return;
    seen.add(symbol);
    out.push(symbol);
  };
  if (Array.isArray(pinned)) pinned.forEach(push);
  if (Array.isArray(watchlist)) watchlist.forEach(push);
  return out;
}
