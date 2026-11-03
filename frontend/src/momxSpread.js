// Spread column (2026-11-03, his ask: "which tickers have more wide option
// spread - when we fill mid price it won't fill" -> "add it as a Spread column").
//
// The server (option_spreads.py) reads one small chain per watchlist ticker
// 10:00-15:30 ET and keeps the median bid/ask spread of the contracts he
// trades (front expiry + next monthly, ATM +/- 2 strikes). This file only
// turns /api/option-spreads into a cell. Pure - no fetch, no React.
//
// Honesty rules: a ticker with no reading is BLANK (never "Tight"); a reading
// from an earlier session is shown dimmed and says so.

export const SPREAD_CLASSES = ["Tight", "OK", "Wide", "Avoid"];

// What to do at the order ticket, per class. Wide/Avoid are starting points
// for a BUY limit (mirror it for a sell), not a measured fill rate.
const ADVICE = {
  Tight: "Mid usually fills.",
  OK: "Mid may need a small nudge.",
  Wide: "Mid rarely fills - try mid + 25% of the spread.",
  Avoid: "Mid will not fill - mid + 40% of the spread, or skip.",
};

function finite(value) {
  // Number(null) and Number("") are 0 - a missing reading must stay missing.
  if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

// {spreads: {SYM: {...}}} -> Map(SYM -> entry). Anything malformed is dropped.
export function spreadMapFrom(payload) {
  const out = new Map();
  const spreads = payload && typeof payload === "object" ? payload.spreads : null;
  if (!spreads || typeof spreads !== "object") return out;
  for (const [symbol, entry] of Object.entries(spreads)) {
    if (!entry || typeof entry !== "object") continue;
    const pct = finite(entry.pct);
    if (pct === null || pct < 0) continue;
    const cls = SPREAD_CLASSES.includes(entry.class) ? entry.class : null;
    if (!cls) continue;
    out.set(String(symbol).toUpperCase(), { ...entry, pct, class: cls });
  }
  return out;
}

// YYYY-MM-DD in New York for an instant.
export function etDate(ms) {
  try {
    return new Intl.DateTimeFormat("en-CA", {
      timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
    }).format(new Date(ms));
  } catch {
    return "";
  }
}

function etClock(iso) {
  const ms = Date.parse(iso || "");
  if (Number.isNaN(ms)) return "";
  try {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hour12: false,
    }).format(new Date(ms));
  } catch {
    return "";
  }
}

function money(value) {
  const n = finite(value);
  return n === null ? "" : "$" + n.toFixed(2);
}

// One row's cell: {text, cls, stale, title} or null when there is no reading.
export function spreadCell(entry, nowMs = Date.now()) {
  if (!entry || typeof entry !== "object") return null;
  const pct = finite(entry.pct);
  if (pct === null || !SPREAD_CLASSES.includes(entry.class)) return null;
  const stale = Boolean(entry.session) && entry.session !== etDate(nowMs);
  const text = pct >= 99.5 ? "99+%" : (pct < 10 ? pct.toFixed(1) : String(Math.round(pct))) + "%";
  const readings = finite(entry.readings) || 0;
  const when = etClock(entry.asOf);
  const lines = [
    `${entry.class} - median spread ${pct.toFixed(1)}% of mid (${money(entry.usd)})`,
    ADVICE[entry.class],
  ];
  if (entry.cheapAtmUsd !== null && entry.cheapAtmUsd !== undefined) {
    lines.push(`ATM contracts under $3: ${money(entry.cheapAtmUsd)} spread`);
  }
  if (finite(entry.atmOi) !== null) lines.push(`ATM open interest ${Number(entry.atmOi).toLocaleString("en-US")}`);
  lines.push(
    stale
      ? `From ${entry.session}${when ? " " + when + " ET" : ""} - not read yet today`
      : `${readings} reading${readings === 1 ? "" : "s"} today${when ? ", last " + when + " ET" : ""}`,
  );
  if (Array.isArray(entry.expiries) && entry.expiries.length) {
    lines.push(`Sampled ${entry.expiries.join(" + ")}, ATM +/- 2 strikes, calls and puts`);
  }
  return { text, cls: entry.class, stale, title: lines.join("\n") };
}

// Header sort: the percentage, so a descending sort puts the widest first.
export function spreadSortValue(entry) {
  if (!entry || typeof entry !== "object") return null;
  return finite(entry.pct);
}
