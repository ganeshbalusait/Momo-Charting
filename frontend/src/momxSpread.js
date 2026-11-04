// The board's Spread and Price columns (2026-11-03, his ask: "show which names
// to skip or price above the mid", then "add one more column for current
// price also").
//
// Spread = the ticker's option bid/ask width class, measured by the worker
// (momx/option_spread.py) from chains the app already saved - median of
// (ask - bid) / mid over the nearest Friday weekly + next monthly, ATM +/-2
// strikes, calls and puts. The worker only serves FRESH classes, so a symbol
// missing from the map prints "-". Pure helpers; the panel owns the fetch.

export const SPREAD_URL = "/api/momx-scanner/option-spreads";
export const SPREAD_POLL_MS = 2 * 60 * 1000;

const CLASSES = ["Tight", "OK", "Wide", "Avoid"];
// Sort: widest first on a descending sort - the class, then the median %.
const RANK = { Tight: 0, OK: 1, Wide: 2, Avoid: 3 };

const RULES = "Tight <3% · OK 3-8% · Wide 8-15% · Avoid >15% or ATM spread $0.30+ on an option under $3";

function finite(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

function etClock(epochSeconds) {
  const ms = finite(epochSeconds);
  if (ms === null) return "";
  try {
    return new Date(ms * 1000).toLocaleTimeString("en-US", {
      hour: "numeric", minute: "2-digit", timeZone: "America/New_York",
    });
  } catch {
    return "";
  }
}

function sameEntry(a, b) {
  return Boolean(a && b) && a.cls === b.cls && a.medPct === b.medPct && a.medUsd === b.medUsd
    && a.chainTs === b.chainTs && a.reads === b.reads;
}

// {SYMBOL: entry} from the worker -> Map. An entry equal to the previous one
// keeps its OBJECT IDENTITY, so a row's memo only re-renders when its class
// actually changed. Junk entries are dropped, never shown as a class.
export function spreadMapFrom(payload, previous) {
  const out = new Map();
  const spreads = payload && typeof payload === "object" ? payload.spreads : null;
  if (!spreads || typeof spreads !== "object") return out;
  for (const [symbol, entry] of Object.entries(spreads)) {
    if (!entry || typeof entry !== "object" || !CLASSES.includes(entry.cls)) continue;
    const key = String(symbol).toUpperCase();
    const held = previous instanceof Map ? previous.get(key) : undefined;
    out.set(key, sameEntry(held, entry) ? held : entry);
  }
  return out;
}

// What the cell prints and its hover text. No entry -> "-".
export function spreadCell(entry) {
  if (!entry || !CLASSES.includes(entry.cls)) {
    return {
      text: "-",
      tone: "none",
      title: "Spread: no fresh option chain for this ticker. Measured 10:00-15:30 ET from chains the app already read.\n" + RULES,
    };
  }
  const pct = finite(entry.medPct);
  const usd = finite(entry.medUsd);
  const lines = [
    `Spread: ${entry.cls}`,
    `Median spread ${pct === null ? "?" : pct.toFixed(1) + "%"} of the mid (${usd === null ? "?" : "$" + usd.toFixed(2)})`,
  ];
  if (entry.atmRule) lines.push("Avoid because an at-the-money option under $3 is $0.30+ wide.");
  if (entry.note) lines.push(entry.note);
  const exps = entry.expiries && typeof entry.expiries === "object" ? entry.expiries : {};
  const used = [exps.weekly ? "weekly " + exps.weekly : "", exps.monthly ? "monthly " + exps.monthly : ""].filter(Boolean);
  const contracts = finite(entry.contracts);
  lines.push(`From ${contracts === null ? "?" : contracts} quotes, ATM ±2 strikes, calls + puts${used.length ? " · " + used.join(", ") : ""}`);
  const at = etClock(entry.chainTs);
  const reads = finite(entry.reads);
  if (at) lines.push(`Chain read ${at} ET${reads && reads > 1 ? ` · median of the last ${reads} reads` : ""}`);
  lines.push(RULES);
  return { text: entry.cls, tone: entry.cls.toLowerCase(), title: lines.join("\n") };
}

export function spreadSortValue(entry) {
  if (!entry || !(entry.cls in RANK)) return null;
  const pct = finite(entry.medPct);
  return RANK[entry.cls] * 1000 + (pct === null ? 0 : Math.max(0, Math.min(999, pct)));
}

// Current stock price: the live quote when the fastlane has one, else the
// board's last. Under $1 keeps 4 decimals (penny names move in 0.0001s).
export function currentPrice(liveLast, row) {
  const live = finite(liveLast);
  if (live !== null && live > 0) return live;
  const last = finite(row && row.last);
  return last !== null && last > 0 ? last : null;
}

export function formatPrice(value) {
  const number = finite(value);
  if (number === null || number <= 0) return "";
  return number < 1 ? number.toFixed(4) : number.toFixed(2);
}
