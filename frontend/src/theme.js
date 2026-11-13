// Dark / Light theme (clean look). The theme is a data-theme attribute on <html>;
// index.html sets it before the app mounts and this module keeps it in step
// afterwards. Dark is the default and is never chosen from the system setting:
// nobody's app should change colour by itself.

export const THEME_KEY = "agx.theme";

// Page background per theme; also the phone status-bar colour.
export const THEME_COLORS = { dark: "#000000", light: "#f5f5f7" };

export function normalizeTheme(value) {
  return value === "light" ? "light" : "dark";
}

function defaultStorage() {
  try {
    return globalThis.localStorage;
  } catch {
    // Reading the property itself throws when site data is blocked.
    return undefined;
  }
}

export function readStoredTheme(storage = defaultStorage()) {
  try {
    return normalizeTheme(storage?.getItem(THEME_KEY));
  } catch {
    return "dark";
  }
}

// What the page is showing right now. A switch that mounts later (the phone's
// More sheet mounts on every open) must read this, not storage: with storage
// blocked the page can be light while storage still says nothing.
export function currentTheme({ doc = globalThis.document, storage = defaultStorage() } = {}) {
  const shown = doc?.documentElement?.getAttribute?.("data-theme");
  return shown === "light" || shown === "dark" ? shown : readStoredTheme(storage);
}

// Every switch on the page (the top strip's and the phone menu's) hears a change.
const listeners = new Set();

export function applyTheme(theme, doc = globalThis.document) {
  const next = normalizeTheme(theme);
  doc?.documentElement?.setAttribute("data-theme", next);
  doc?.querySelector?.('meta[name="theme-color"]')?.setAttribute("content", THEME_COLORS[next]);
  for (const listener of [...listeners]) listener(next);
  return next;
}

export function setTheme(theme, { storage = defaultStorage(), doc = globalThis.document } = {}) {
  const next = applyTheme(theme, doc);
  try {
    storage?.setItem(THEME_KEY, next);
  } catch {
    // Blocked storage: the choice still holds for this visit.
  }
  return next;
}

// Calls listener with the new theme whenever it changes: from a switch in this
// window, or from another window (a chart pop-out, the installed app), in which
// case this window is brought into step first.
export function subscribeTheme(listener, win = globalThis.window) {
  listeners.add(listener);
  const onStorage = (event) => {
    if (event?.key === THEME_KEY) applyTheme(event.newValue);
  };
  win?.addEventListener?.("storage", onStorage);
  return () => {
    listeners.delete(listener);
    win?.removeEventListener?.("storage", onStorage);
  };
}
