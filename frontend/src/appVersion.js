// Notice a new build and get the phone onto it.
//
// Added to the Home Screen, the app has no address bar and no reload button.
// iOS also RESUMES a Home Screen app rather than restarting it, so tapping the
// icon can show a bundle from days ago with nothing on screen admitting it.
//
// The version is the entry script's filename. Vite hashes it from the file's
// contents, so it changes when - and only when - the code changes. That means
// no build step to maintain and no version constant anyone can forget to bump:
// the deployed page already states its own identity.
//
// Everything here fails toward DOING NOTHING. A reload loop on a phone with no
// way to intervene is far worse than staying on yesterday's build for an hour.

const RELOAD_MARK = "agx:reloaded-for";
// Survives the reload, dies with the tab. sessionStorage on purpose: in
// localStorage a mark left behind by a crash mid-reload would open the app on
// the release notes on every future launch, with nothing on screen explaining
// why the chart went away.
const OPEN_NOTES_MARK = "agx:open-release-notes";

/** Ask the NEXT load to land on the release notes. Tap-to-update only. */
export function markReleaseNotesOnNextLoad(storage) {
  try {
    storage?.setItem?.(OPEN_NOTES_MARK, "1");
  } catch {
    // Private mode. Losing the hand-off costs him one tap; refusing to reload
    // because a string would not write costs him the update.
  }
}

/** True once, then never again for this load. Clears the mark as it reads it. */
export function consumeReleaseNotesMark(storage) {
  try {
    if (storage?.getItem?.(OPEN_NOTES_MARK) !== "1") return false;
    storage.removeItem(OPEN_NOTES_MARK);
    return true;
  } catch {
    return false;
  }
}

export function entryScriptFrom(html) {
  if (!html || typeof html !== "string") return null;
  // Match the tag first, then its src, so attribute order does not matter -
  // vite writes `type="module" crossorigin src=...` in builds and
  // `type="module" src=...` in dev.
  const tag = /<script\b[^>]*\btype="module"[^>]*>/i.exec(html);
  if (!tag) return null;
  const src = /\bsrc="([^"]+)"/i.exec(tag[0]);
  return src ? src[1] : null;
}

// How long away counts as having LEFT, rather than having glanced at a text.
// Below this, a reload would throw away the chart he was reading; above it,
// he is arriving fresh and a reload costs him nothing.
export const RESUME_AFTER_MS = 60 * 1000;

export function decideUpdate({ current, latest, awayMs = 0 }) {
  // Either side unknown means we have learned nothing. An expired Cloudflare
  // Access session answers with a login page that has no module script, and
  // reading that as "a new build" would reload straight back into it.
  if (!current || !latest) return "none";
  if (current === latest) return "none";
  return awayMs >= RESUME_AFTER_MS ? "reload" : "notify";
}

export function claimReload(storage, version) {
  // The real loop protection is that after a reload `current` becomes the new
  // version, so the comparison stops matching. This is the backstop for the
  // case where it somehow does not.
  try {
    if (storage.getItem(RELOAD_MARK) === version) return false;
    storage.setItem(RELOAD_MARK, version);
    return true;
  } catch {
    // Safari in private mode throws on setItem. Losing the backstop is
    // survivable; refusing to run because we could not write a string is not.
    return true;
  }
}

export function currentEntryScript(doc) {
  const el = doc?.querySelector?.('script[type="module"][src]');
  if (!el) return null;
  // Compare paths, not full URLs - getAttribute keeps it origin-independent.
  return el.getAttribute("src");
}

export async function fetchLatestEntryScript(fetchImpl, url = "/") {
  try {
    const response = await fetchImpl(url, { cache: "no-store", credentials: "same-origin" });
    if (!response.ok) return null;
    return entryScriptFrom(await response.text());
  } catch {
    // Offline, asleep, or blocked. Not evidence of anything.
    return null;
  }
}

function showUpdateBar(win, onTap) {
  const doc = win.document;
  if (doc.getElementById("agx-update-bar")) return;
  const bar = doc.createElement("button");
  bar.id = "agx-update-bar";
  bar.type = "button";
  bar.textContent = "New version available - tap to update";
  // Inline styles on purpose: index.css is large and edited from more than one
  // session, and this must not depend on a class surviving a redesign.
  bar.setAttribute(
    "style",
    [
      "position:fixed",
      "left:0",
      "right:0",
      "top:0",
      "z-index:2147483647",
      "padding:calc(8px + env(safe-area-inset-top,0px)) 12px 8px",
      "border:0",
      "width:100%",
      "font:600 13px/1.2 Inter,system-ui,sans-serif",
      "color:#04110f",
      "background:#2ee6c5",
      "cursor:pointer",
      "text-align:center",
    ].join(";"),
  );
  bar.addEventListener("click", onTap);
  doc.body.appendChild(bar);
}

export function installUpdateWatch(win, options = {}) {
  const { intervalMs = 15 * 60 * 1000, now = () => Date.now() } = options;
  const doc = win.document;
  const current = currentEntryScript(doc);
  // Nothing to compare against - do not start a timer that can only ever
  // decide "none".
  if (!current) return () => {};

  let stopped = false;
  let hiddenSince = null;

  const check = async (awayMs) => {
    if (stopped) return;
    const latest = await fetchLatestEntryScript(win.fetch.bind(win));
    const action = decideUpdate({ current, latest, awayMs });
    if (action === "reload") {
      // Deliberately NOT marked. This branch fires when he has been away and
      // is coming back - to a chart he left open. Landing him on a release
      // notes page instead is a worse surprise than the silent update was.
      // The unread dot on the Release Notes nav item is what tells him.
      if (claimReload(win.localStorage, latest)) win.location.reload();
    } else if (action === "notify") {
      // He chose to update, so taking him to what changed is what he asked
      // for rather than an interruption.
      showUpdateBar(win, () => {
        markReleaseNotesOnNextLoad(win.sessionStorage);
        win.location.reload();
      });
    }
  };

  // visibilitychange fires when the app is ALREADY visible again, so the state
  // at that moment says nothing about whether he just arrived. What decides it
  // is how long the app spent hidden, which only the hide event knows - hence
  // the timestamp. Getting this wrong is what would have made every resume
  // show a banner instead of simply being up to date.
  const onVisibility = () => {
    if (doc.visibilityState === "hidden") {
      hiddenSince = now();
      return;
    }
    const awayMs = hiddenSince === null ? 0 : now() - hiddenSince;
    hiddenSince = null;
    // Returned, not dropped: the check is async, and a listener that discards
    // its promise cannot be awaited by a test - which would leave the wiring
    // provable only by hand, on a phone.
    return check(awayMs);
  };

  doc.addEventListener("visibilitychange", onVisibility);
  // The periodic check is for a session left open all day; it never reloads on
  // its own, because someone is by definition there to see the offer.
  const timer = win.setInterval(() => check(0), intervalMs);

  return () => {
    stopped = true;
    doc.removeEventListener("visibilitychange", onVisibility);
    win.clearInterval(timer);
  };
}
