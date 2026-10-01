// Reading and shaping the release-notes file.
//
// Kept separate from the component because the interesting part is not the
// rendering, it is the time arithmetic: what shipped, in what order, on which
// trading day, and which entries the trader has not seen. Four deploys landed
// on 2026-08-31 alone, so "31 Aug" alone cannot answer "was that fix in the
// build I was running at 3pm?".
//
// Everything here is total: a missing, torn or hand-edited file must degrade to
// showing less, never to a blank page or a thrown render.

const MARKET_TIMEZONE = "America/New_York";

function instantOf(value) {
  if (typeof value !== "string" || !value.trim()) return null;
  const stamp = new Date(value);
  return Number.isNaN(stamp.getTime()) ? null : stamp;
}

// The ET calendar day, as numbers. Deliberately not UTC: a 9pm ET deploy is
// 01:00 UTC the next day, and numbering it as tomorrow's first release would
// renumber the day underneath him while he reads it.
function easternDayParts(stamp) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: MARKET_TIMEZONE,
    year: "numeric",
    month: "numeric",
    day: "numeric",
  }).formatToParts(stamp);
  const pick = (type) => Number(parts.find((p) => p.type === type)?.value);
  return { year: pick("year"), month: pick("month"), day: pick("day") };
}

const dayKey = (stamp) => {
  const { year, month, day } = easternDayParts(stamp);
  return `${year}-${month}-${day}`;
};

/** Normalised, newest first. Undated entries are KEPT and sorted last. */
export function parseReleases(data) {
  const raw = data && typeof data === "object" ? data.releases : null;
  if (!Array.isArray(raw)) return [];
  const rows = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") continue;
    const heading = typeof item.heading === "string" ? item.heading.trim() : "";
    const items = Array.isArray(item.items) ? item.items.filter((t) => typeof t === "string") : [];
    // An entry with neither a heading nor a line says nothing; it is noise on a
    // page whose whole job is to be scanned quickly.
    if (!heading && items.length === 0) continue;
    const stamp = instantOf(item.at);
    rows.push({
      at: stamp ? item.at : null,
      ms: stamp ? stamp.getTime() : null,
      heading,
      subheading: typeof item.subheading === "string" ? item.subheading.trim() : "",
      items,
    });
  }
  // Undated last: they carry no instant, so any position among the dated ones
  // would be a claim the file does not make.
  rows.sort((a, b) => {
    if (a.ms === null && b.ms === null) return 0;
    if (a.ms === null) return 1;
    if (b.ms === null) return -1;
    return b.ms - a.ms;
  });
  return rows;
}

/**
 * "2026.8.31.3" - year.month.day.Nth-deploy-of-that-ET-day, counted oldest
 * first so a release's number never changes once it has shipped.
 */
export function versionLabel(release, releases) {
  if (!release || release.ms === null) return null;
  const stamp = new Date(release.ms);
  const { year, month, day } = easternDayParts(stamp);
  const key = dayKey(stamp);
  const sameDay = (Array.isArray(releases) ? releases : [])
    .filter((r) => r && r.ms !== null && dayKey(new Date(r.ms)) === key)
    .sort((a, b) => a.ms - b.ms);
  const ordinal = sameDay.findIndex((r) => r.ms === release.ms) + 1;
  return `${year}.${month}.${day}.${ordinal || 1}`;
}

/** "Mon 31 Aug 2026 · 6:00 PM ET" - or "" if there is no usable instant. */
export function formatStamp(value) {
  const stamp = instantOf(value);
  if (!stamp) return "";
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: MARKET_TIMEZONE,
    weekday: "short",
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).formatToParts(stamp);
  const get = (type) => parts.find((p) => p.type === type)?.value || "";
  const date = `${get("weekday")} ${get("day")} ${get("month")} ${get("year")}`;
  const time = `${get("hour")}:${get("minute")} ${get("dayPeriod")}`.trim();
  return `${date} · ${time} ET`;
}

/** The marker to persist once the page has been looked at. */
export function newestAt(releases) {
  const dated = (Array.isArray(releases) ? releases : []).filter((r) => r && r.ms !== null);
  if (dated.length === 0) return null;
  return dated.reduce((best, r) => (r.ms > best.ms ? r : best), dated[0]).at;
}

/**
 * How many dated entries are newer than the last one read.
 *
 * An unreadable marker counts as having read NOTHING. Erring the other way -
 * treating a corrupt value as "all read" - would silently switch the dot off
 * for every future release, and a notification you never see is worse than one
 * you see twice.
 */
export function unreadCount(releases, lastSeenAt) {
  const dated = (Array.isArray(releases) ? releases : []).filter((r) => r && r.ms !== null);
  const seen = instantOf(lastSeenAt);
  if (!seen) return dated.length;
  return dated.filter((r) => r.ms > seen.getTime()).length;
}

// Where "what has he already read" lives. localStorage, not session: having
// read the notes is a fact about him, not about this tab.
export const SEEN_STORAGE_KEY = "agx:release-notes-seen";

export function readSeen(storage) {
  try {
    return storage?.getItem?.(SEEN_STORAGE_KEY) ?? null;
  } catch {
    return null;
  }
}

export function writeSeen(storage, at) {
  if (typeof at !== "string" || !at) return;
  try {
    storage?.setItem?.(SEEN_STORAGE_KEY, at);
  } catch {
    // Private mode. The dot staying on is a far smaller harm than a throw
    // during render.
  }
}
