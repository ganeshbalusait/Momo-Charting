// Whether the Learn page opens by itself. All three conditions are load-bearing:
// a pop-out chart window must never become a help page, and a browser that
// already has a remembered page is mid-workflow and must not be yanked away.

export const LEARN_SEEN_STORAGE_KEY = "agxLearnSeen";

// Bump to re-introduce a rewritten Learn page to browsers that saw an older one.
// v2 (2026-08-30): the Learn/Setup two-tab split, so existing users see it once.
export const LEARN_CONTENT_VERSION = 2;

export function shouldAutoOpenLearn({ seenVersion, popoutMode, savedView, mustChangePassword } = {}) {
  if (popoutMode) return false;
  // A user who must change their password is held on Settings, and the nav is
  // filtered down to Settings alone. A newly invited user on a fresh browser is
  // exactly the case that would otherwise auto-open: no remembered page and no
  // seen-version. Opening Learn there would move them off the hold and hand
  // them "Take me there" buttons to pages the hold deliberately removed.
  if (mustChangePassword) return false;
  if (savedView) return false;
  const seen = Number(seenVersion);
  if (Number.isFinite(seen) && seen >= LEARN_CONTENT_VERSION) return false;
  return true;
}

export function readLearnSeen(storage) {
  try {
    const raw = storage?.getItem(LEARN_SEEN_STORAGE_KEY);
    // Number(null) is 0, and 0 is finite - so testing Number(raw) directly
    // would report a NEVER-WRITTEN key as version 0 instead of "unseen". This
    // codebase has already been bitten by exactly this (see the legacyNumber
    // note in App.jsx, where it pinned a saved split at its minimum).
    if (raw === null || raw === undefined || raw === "") return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function markLearnSeen(storage, version = LEARN_CONTENT_VERSION) {
  try {
    storage?.setItem(LEARN_SEEN_STORAGE_KEY, String(version));
  } catch {
    // Storage is blocked. The page simply shows again next time; that is a far
    // better outcome than an exception on mount.
  }
}
