// Presentation helpers for the MAG7 premarket scanner table
// (GET /api/premarket-scanner, built by premarket_scanner.py).

function shortDate(isoDate) {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(isoDate || ""));
  if (!match) return "";
  return `${Number(match[2])}/${Number(match[3])}`;
}

/**
 * One badge per squeeze fire. A daily candle cannot close during premarket,
 * so a D fire is always a previous session's release: it is labelled with
 * that session's date so it is never mistaken for a fresh premarket event.
 */
export function premarketScannerFireBadges(row) {
  const fires = Array.isArray(row?.fires) ? row.fires : [];
  const dates = row?.fireDates && typeof row.fireDates === "object" ? row.fireDates : {};
  return fires.map((rawLabel) => {
    const label = String(rawLabel || "").trim();
    const when = dates[label];
    if (label === "D") {
      const date = shortDate(when);
      return {
        key: `fire-${label}`,
        text: date ? `🔥D ${date}` : "🔥D",
        title: `Daily squeeze release on ${when || "the previous session"} (previous session's closed daily candle)`,
      };
    }
    return {
      key: `fire-${label}`,
      text: `🔥${label}`,
      title: when ? `${label} squeeze release, bucket closed ${when}` : `${label} squeeze release`,
    };
  });
}

// Stable identity so the memoized DataTable does not re-render every poll.
export function premarketScannerRowKey(row) {
  return String(row?.symbol || "");
}
