"""The scanner pop-out opens INSIDE the app, not in a Chrome tab.

2026-09-02: "scanner popup its opening in new chrome tab, but i want app
popup not with chrome". He picked the in-app overlay over a cleaner detached
window when asked.

The button used to be `window.open(url + "?popout=momx", ..., "popup=yes")`.
Chrome was turning that into an ordinary tab for him, which loses the app
(and the installed PWA) entirely. It is now a full-screen overlay over the
app itself.

WHY A PORTAL AND NOT JUST position:fixed. The previous author wrote that a
hand-rolled floating panel "would fight the sticky-header stacking contexts",
and that is measurably true here, with a specific cause: `.momx-scanner-view`
carries `container-type: inline-size`, which makes it the containing block
for every fixed descendant. Measured 2026-09-02 with a probe: a
`position: fixed; inset: 0` box inside the panel resolved to 1828x1080 in a
1920x1080 window, and 341px wide on a 375px phone -- anchored to the view,
not the screen. The identical trap already broke fixed positioning once on
the chart tab (see the `contain: none` fix in the max-width:760px block).

AND WHY THE CONTAINMENT CANNOT SIMPLY BE DROPPED, which is what fixed it on
the chart tab: there the comment notes the stylesheet has no @container
queries so the containment bought nothing. Here it is load-bearing -- the
sticky toolbar/chips/banners are capped at `100cqw` precisely so they keep
viewport width while the table pans underneath them, and cqw resolves against
this container. Removing it would silently un-cap every sticky widget.

So the panel is portalled to <body>, out of the containing block entirely.

The overlay re-uses the `momx-scanner-view` class rather than inventing a
wrapper, so all seven rules scoped to it keep applying -- above all THE
single scroll container that owns both axes (four separate scroll incidents
are recorded against getting that wrong). The flex column is the documented
trap from `.trading-popout-root.is-momx`: the view is `flex: 1;
min-height: 0`, which collapses to zero height inside a plain block.

Also: the pop-out is now offered on the phone. The reason it was desktop-only
was that iOS turns window.open into a background tab -- an objection that
does not survive the feature never leaving the page, and he wants desktop and
mobile to look the same.
"""
import io

p = "frontend/src/MomxScannerPanel.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

# --- icons: this is no longer an "external link" --------------------------
assert s.count("ExternalLink") == 2, "expected exactly the import and the one use"
s = s.replace("  ExternalLink,\n", "  Maximize2,\n  Minimize2,\n", 1)

# --- availability + open state --------------------------------------------
OLD = '''  // Pop-out is offered only where a detached window is a real thing: a wide
  // viewport, and not already inside the pop-out. iOS/Android turn
  // window.open into a background tab, so on a phone this button would do
  // something that reads as broken rather than useful.
  const [canPopOut] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      if (new URLSearchParams(window.location.search).get("popout")) return false;
      return window.matchMedia("(min-width: 761px) and (pointer: fine)").matches;
    } catch {
      return false;
    }
  });'''
NEW = '''  // Offered everywhere now that the pop-out is an in-app overlay instead of
  // an OS window. The old desktop-only gate existed because iOS/Android turn
  // window.open into a background tab; that objection does not survive a
  // pop-out that never leaves the page, and desktop and phone are meant to
  // look the same. Still hidden inside the detached ?popout= window, where a
  // button that re-opens the window you are already in is just confusing.
  const [canPopOut] = useState(() => {
    if (typeof window === "undefined") return false;
    try {
      return !new URLSearchParams(window.location.search).get("popout");
    } catch {
      return true;
    }
  });
  const [popoutOpen, setPopoutOpen] = useState(false);'''
assert s.count(OLD) == 1, "canPopOut anchor"
s = s.replace(OLD, NEW)

# --- the handler ----------------------------------------------------------
OLD = '''  // Open this board in its own OS window, so it can live on a second monitor
  // next to the charts. Deliberately a plain window.open on the SAME url plus
  // ?popout=momx rather than a draggable in-page div: the OS gives move,
  // resize and multi-monitor for free, and a hand-rolled floating panel would
  // fight the sticky-header stacking contexts that already forced two popovers
  // into createPortal.
  //
  // Not offered on a phone - see `canPopOut`. iOS turns window.open into a
  // background tab, so the button would look broken on the device he uses most.
  const onPopOut = useCallback(() => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    url.searchParams.set("popout", "momx");
    // Leave no chart/chain params behind: this window loads the scanner only.
    url.searchParams.delete("symbol");
    url.searchParams.delete("timeframe");
    url.searchParams.delete("link");
    const popup = window.open(
      url.toString(),
      "agx-momx-scanner",
      "popup=yes,width=1500,height=1000,resizable=yes,scrollbars=yes",
    );
    if (popup) popup.focus();
    else setNotice("Your browser blocked the pop-out window. Allow pop-ups for this site and try again.");
  }, []);'''
NEW = '''  // Blow the board up to fill the app, IN the app. This used to be
  // window.open(..., "popup=yes") onto ?popout=momx, which Chrome was
  // serving him as an ordinary tab - losing the app, and the installed PWA
  // with it (2026-09-02: "i want app popup not with chrome"). Nothing is
  // opened now; the same panel is portalled into a fixed overlay. See the
  // render for why a portal and not position:fixed.
  //
  // The ?popout=momx URL still renders standalone for anyone holding that
  // link, and the chart/mag7/chain pop-outs are untouched.
  const onPopOut = useCallback(() => setPopoutOpen((open) => !open), []);

  // Esc closes it. Bound only while open, so it can never swallow Esc from
  // anything else. The guard is for the popovers that portal ABOVE this
  // overlay (news, settings): Esc there should close the popover the trader
  // is looking at, not the whole board underneath it.
  useEffect(() => {
    if (!popoutOpen) return undefined;
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      if (document.querySelector(".momx-news-overlay, .momx-settings-overlay")) return;
      setPopoutOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [popoutOpen]);'''
assert s.count(OLD) == 1, "onPopOut anchor"
s = s.replace(OLD, NEW)

# --- the button, now a toggle --------------------------------------------
OLD = '''        {/* Desktop only, and hidden inside the pop-out itself - a button that
            opens a copy of the window you are already in is just confusing. */}
        {canPopOut ? (
          <button
            type="button"
            className="momx-icon-btn momx-popout-btn"
            onClick={onPopOut}
            title="Open the scanner in its own window"
            aria-label="Open the scanner in its own window"
          >
            <ExternalLink size={13} aria-hidden="true" />
          </button>
        ) : null}'''
NEW = '''        {/* Hidden inside the detached ?popout= window - a button that re-opens
            the window you are already in is just confusing. */}
        {canPopOut ? (
          <button
            type="button"
            className={"momx-icon-btn momx-popout-btn" + (popoutOpen ? " is-active" : "")}
            onClick={onPopOut}
            title={
              popoutOpen
                ? "Shrink the scanner back into the page (Esc)"
                : "Fill the screen with the scanner, inside the app"
            }
            aria-label={popoutOpen ? "Exit the full-screen scanner" : "Fill the screen with the scanner"}
            aria-pressed={popoutOpen}
          >
            {popoutOpen ? (
              <Minimize2 size={13} aria-hidden="true" />
            ) : (
              <Maximize2 size={13} aria-hidden="true" />
            )}
          </button>
        ) : null}'''
assert s.count(OLD) == 1, "button anchor"
s = s.replace(OLD, NEW)

# --- render: portal the panel when open -----------------------------------
OLD = '''  return (
    <section className="momx-scanner-panel" data-testid="momx-scanner-panel">'''
NEW = '''  const panel = (
    <section className="momx-scanner-panel" data-testid="momx-scanner-panel">'''
assert s.count(OLD) == 1, "panel open anchor"
s = s.replace(OLD, NEW)

OLD = '''    </section>
  );
}
'''
NEW = '''    </section>
  );

  if (!popoutOpen) return panel;

  // A PORTAL, not position:fixed. `.momx-scanner-view` carries
  // container-type:inline-size, which makes it the containing block for every
  // fixed descendant: measured 2026-09-02, a `fixed; inset:0` box inside this
  // panel came out 1828x1080 in a 1920x1080 window, and 341px wide on a 375px
  // phone. The same containment already broke fixed positioning once on the
  // chart tab, where the fix was to drop it - not an option here, because the
  // sticky toolbar's 100cqw width cap resolves against this very container.
  //
  // The wrapper re-uses the `momx-scanner-view` class so every rule scoped to
  // it still applies, above all THE single scroll container that owns both
  // axes. The flex column on the overlay is the trap documented on
  // `.trading-popout-root.is-momx`: that view is `flex: 1; min-height: 0` and
  // collapses to zero height inside a plain block, taking the board with it.
  return createPortal(
    <div
      className="momx-popout-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="MomX scanner, full screen"
    >
      <div className="momx-scanner-view">{panel}</div>
    </div>,
    document.body,
  );
}
'''
assert s.count(OLD) == 1, "panel close anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("MomxScannerPanel.jsx: pop-out is an in-app overlay")

# ---------------------------------------------------------------------------
# CSS, appended at the end of the file (later source order wins - the mobile
# cascade trap this stylesheet documents twice).
# ---------------------------------------------------------------------------
p = "frontend/src/index.css"
css = io.open(p, encoding="utf-8", newline="").read()
assert ".momx-popout-overlay" not in css, "overlay CSS already present"
css += '''
/* ---------------------------------------------------------------------------
   MomX pop-out, IN the app (2026-09-02). The button used to window.open a
   detached window; Chrome served it to him as a plain tab, which loses the
   app and the installed PWA. It is now this overlay, portalled to <body> -
   see the comment at the bottom of MomxScannerPanel.jsx for why a portal is
   required rather than position:fixed on the panel itself.

   z-index 3000: above the app shell (the highest thing under it is a 2500
   chart tooltip) and deliberately BELOW the news popover (9400) and the Momo
   Alert banner (9500), both of which must still appear over the board.
   -------------------------------------------------------------------------- */
.momx-popout-overlay {
  position: fixed;
  z-index: 3000;
  inset: 0;
  /* Flex column, because the view inside is `flex: 1; min-height: 0` and
     would resolve to zero height in a plain block - the board's only scroll
     container would collapse and every row below the fold would stop
     existing. Same trap as .trading-popout-root.is-momx. */
  display: flex;
  flex-direction: column;
  /* The view owns the scrolling, on both axes. Nothing scrolls out here. */
  overflow: hidden;
  background: #0c0c0d;
}

.momx-popout-overlay > .momx-scanner-view {
  flex: 1 1 auto;
  min-height: 0;
}

/* The toggle reads as pressed while the overlay is up - it is the same
   button, in the same place, and it is the way back out. */
.momx-scanner-panel .momx-popout-btn.is-active {
  border-color: rgba(78, 220, 255, .55);
  background: rgba(78, 220, 255, .14);
  color: #4edcff;
}
'''
io.open(p, "w", encoding="utf-8", newline="").write(css)
print("index.css: .momx-popout-overlay")
