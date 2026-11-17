// Chat links (2026-11-11): his "It will take me that page or link". A link in a chat answer is a button, not a
// URL: the server builds it from checked values, cleanLink checks it again here, and openAppLink hands it to the
// page that knows how to go there. A link only moves the screen; it can never place, change or cancel a trade.
// Pure apart from the one window event, so node:test can run it.

/** Pages only the owner may open. The page branches do not all check this themselves. */
export const ADMIN_VIEWS = ["Bot", "My Trading", "Option Paper Trading", "Paper Trading"];

/** Every page a link may open (the left rail's view keys). */
export const LINK_VIEWS = [
  "Charts & OI", "Quick Options", "Auto Alert", "Premarket Scanner", "MomX Scanner", "Long Term", "Learn + Setup",
  "Watchlist", "Sectors", "LEAP OI Scanner", "AGX Flow", "News Feed", "Earnings Calendar", "Mag7 Scanner", "ROI Calc", "OI Level Script TOS",
  "Release Notes", "Settings", ...ADMIN_VIEWS,
];

export const OPEN_LINK_EVENT = "agx:open-link";

const VIEWS = new Set(LINK_VIEWS);
const TIMEFRAMES = new Set(["1m", "2m", "3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h", "D", "W", "M"]);
const TICKER = /^\$?[A-Z][A-Z0-9.\-]{0,11}$/;
const LIST = /^[A-Za-z0-9][A-Za-z0-9 &+.\-_/()#%:]{0,47}$/;
const SUB = /^[A-Za-z0-9][A-Za-z0-9_\-]{0,31}$/;
const given = (value) => value !== undefined && value !== null && value !== "";

function realDay(value) {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

const realTime = (value) => typeof value === "string" && /^([01]\d|2[0-3]):[0-5]\d$/.test(value);

/** A link as the app may follow it, with nothing but the known fields. null = not a link we trust. */
export function cleanLink(raw) {
  if (!raw || typeof raw !== "object" || Array.isArray(raw) || !VIEWS.has(raw.view)) return null;
  const label = given(raw.label) ? raw.label : raw.view;
  if (typeof label !== "string") return null;
  const shown = label.replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 80);
  if (!shown) return null;
  const link = { label: shown, view: raw.view };
  if (given(raw.ticker)) {
    const ticker = typeof raw.ticker === "string" ? raw.ticker.trim().toUpperCase() : "";
    if (!TICKER.test(ticker)) return null;
    link.ticker = ticker;
  }
  if (given(raw.timeframe)) {
    if (!TIMEFRAMES.has(raw.timeframe)) return null;
    link.timeframe = raw.timeframe;
  }
  if (given(raw.list)) {
    if (typeof raw.list !== "string" || !LIST.test(raw.list)) return null;
    link.list = raw.list;
  }
  if (given(raw.side)) {
    if (raw.side !== "bull" && raw.side !== "bear") return null;
    link.side = raw.side;
  }
  if (given(raw.tab)) {
    if (raw.tab !== "live" && raw.tab !== "history") return null;
    link.tab = raw.tab;
  }
  if (given(raw.day)) {
    if (!realDay(raw.day)) return null;
    link.day = raw.day;
  }
  if (given(raw.time)) {
    if (!realTime(raw.time)) return null;
    link.time = raw.time;
  }
  if (given(raw.sub)) {
    if (typeof raw.sub !== "string" || !SUB.test(raw.sub)) return null;
    link.sub = raw.sub;
  }
  return link;
}

/** May this person follow this link? A regular user never gets an owner page. */
export function linkAllowed(link, isOwner) {
  return Boolean(link) && (Boolean(isOwner) || !ADMIN_VIEWS.includes(link.view));
}

/** The links of a reply that are safe to show this person, at most `max`, without repeats. */
export function cleanLinks(raw, isOwner = true, max = 6) {
  const seen = new Set();
  const out = [];
  for (const item of Array.isArray(raw) ? raw : []) {
    const link = cleanLink(item);
    if (!link || !linkAllowed(link, isOwner)) continue;
    const key = JSON.stringify({ ...link, label: "" });
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(link);
    if (out.length >= max) break;
  }
  return out;
}

// A scanner link waits here until the MomX panel has mounted and can take it.
let pendingScanner = null;
const scannerListeners = new Set();
const tellScannerListeners = () => scannerListeners.forEach((listener) => listener());

/** What is waiting, without taking it (the snapshot for useSyncExternalStore). */
export const peekPendingScannerLink = () => pendingScanner;

/** Hands over the waiting scanner link, once. null = nothing waiting. */
export function takePendingScannerLink() {
  const link = pendingScanner;
  if (link) {
    pendingScanner = null;
    tellScannerListeners();
  }
  return link;
}

export function subscribePendingScannerLink(listener) {
  scannerListeners.add(listener);
  return () => scannerListeners.delete(listener);
}

/** Go there. Returns the cleaned link, or null when the link was not valid and nothing happened. */
export function openAppLink(raw) {
  const link = cleanLink(raw);
  if (!link) return null;
  if (link.view === "MomX Scanner") {
    pendingScanner = link;
    tellScannerListeners();
  }
  if (typeof window !== "undefined" && typeof window.dispatchEvent === "function") {
    window.dispatchEvent(new CustomEvent(OPEN_LINK_EVENT, { detail: link }));
  }
  return link;
}
