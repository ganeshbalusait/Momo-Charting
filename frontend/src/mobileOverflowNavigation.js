// The phone hides the desktop sidebar and replaces it with a six-button bar,
// so every destination that bar does not name became unreachable: Mag7
// Scanner, Option Watchlist, News Feed, Earnings Calendar, ROI Calc and OI
// Level Scripts had no entry point at all, because "More" navigated straight
// to Settings instead of opening anything. This computes what belongs behind
// "More" so the bar's own destinations are never duplicated there.

// Destinations the bottom bar already reaches by name. Options is deliberately
// absent from older versions of this list; it now names Quick Options directly,
// because that IS an optionNavItems destination.
// 2026-08-27: after the fourteen-view cut and the partial restore, the bar
// names the five primary surfaces, so the sheet carries everything else -
// the six secondary pages plus Settings, which the trader moved in here.
// 2026-11-06 (trader): "Alert tab move to more and sector next to options" -
// Sectors takes the third slot, Alerts moves into the sheet (the More button
// carries the unseen-alert badge so a new alert is still visible).
export const BOTTOM_NAV_LABELS = [
  "Charts & OI",
  "Quick Options",
  "Sectors",
  "Premarket Scanner",
  "MomX Scanner",
];

// 2026-11-08 (trader, phone): "Move 'long term' next to 'bot'". The sheet is a
// two-column grid, so these lead it, in this order, and share the first row;
// every other destination keeps the sidebar's order behind them. A label the
// user cannot see (Bot is admin only) is simply skipped.
export const SHEET_FIRST_LABELS = ["Bot", "Long Term"];

export function selectOverflowDestinations(visibleNavItems, bottomNavLabels = BOTTOM_NAV_LABELS) {
  if (!Array.isArray(visibleNavItems)) return [];
  const covered = new Set(bottomNavLabels);
  const sheet = visibleNavItems.filter((item) => item && item.label && !covered.has(item.label));
  const first = SHEET_FIRST_LABELS.map((label) => sheet.find((item) => item.label === label)).filter(Boolean);
  return [...first, ...sheet.filter((item) => !first.includes(item))];
}

// "More" should read as the active tab whenever the view on screen is one it
// owns, not only when the sheet happens to be open.
export function isOverflowDestinationActive(activeView, overflowDestinations) {
  if (!activeView || !Array.isArray(overflowDestinations)) return false;
  return overflowDestinations.some((item) => item && item.label === activeView);
}

// The sidebar renames some labels for display ("OI Finder" -> "Charts & OI")
// while the label stays the view key used across activeView comparisons.
export function overflowDestinationLabel(item) {
  if (!item) return "";
  return item.displayLabel || item.label || "";
}
