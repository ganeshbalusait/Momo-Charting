// Mirrors ACTIVE_VIEW_STORAGE_KEY in App.jsx. Kept here, in a plain .js module,
// so the crash-recovery logic is unit-testable without importing the 30k-line
// App module (node:test cannot load .jsx). A test asserts App.jsx still writes
// this same string, so the two cannot drift apart silently.
export const REMEMBERED_VIEW_STORAGE_KEY = "agenticActiveView";

// The app remembers the last page in localStorage and restores it on boot. If a
// page is removed (fourteen were, on 2026-08-27) or one of them starts throwing,
// the remembered value re-enters the broken state on every reload and the trader
// has no way out from the phone. Clearing the key is that way out.
export function clearRememberedView(storage) {
  try {
    storage?.removeItem?.(REMEMBERED_VIEW_STORAGE_KEY);
    return true;
  } catch {
    // Private-mode or blocked storage: reloading is still worth offering.
    return false;
  }
}
