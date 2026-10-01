"""The scanner pop-out is a WINDOW inside the app, not a full-screen takeover.

2026-09-02: "why you removed popup and put it back mazimize? i want popup".

He is right and this is my error. He asked for "app popup not with chrome",
picked "inside the app" when asked, and I built a fixed overlay pinned to
inset:0 - which is indistinguishable from the Maximize button that already
existed. I also swapped the icon from the external-link glyph he associates
with the pop-out to a maximize glyph, which is what he spotted first. From
where he sits the pop-out was deleted and replaced by a duplicate of
Maximize. It was.

A pop-up is a WINDOW: it floats, you can move it, you can size it, and you
can see and use the app behind it. That is the thing that is not Maximize,
and it is the only reason to have both buttons. So:

* draggable by its title bar, resizable from the bottom-right corner,
* opens centred at a comfortable size, and REMEMBERS where he put it and how
  big he made it,
* clamped so it can never be dragged off-screen and lost,
* no backdrop - the app behind stays visible and clickable, which is the
  whole point of a window rather than an overlay,
* the external-link icon comes back.

Still portalled to <body>: `.momx-scanner-view` sets
container-type:inline-size and is the containing block for fixed descendants
(measured earlier today - a fixed inset:0 box inside the panel came out
1828x1080 in a 1920x1080 window), so the window cannot be positioned from
inside the panel.

On a phone it stays edge-to-edge: a 1200px draggable window on a 375px
screen is not a feature. The title bar and its close button stay, so there is
still an obvious way out.
"""
import io

p = "frontend/src/MomxScannerPanel.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

# --- the icon he recognises comes back ------------------------------------
OLD = "  Maximize2,\n  Minimize2,\n"
NEW = "  ExternalLink,\n"
assert s.count(OLD) == 1, "icon import anchor"
s = s.replace(OLD, NEW)

OLD = '''            {popoutOpen ? (
              <Minimize2 size={13} aria-hidden="true" />
            ) : (
              <Maximize2 size={13} aria-hidden="true" />
            )}'''
NEW = '''            <ExternalLink size={13} aria-hidden="true" />'''
assert s.count(OLD) == 1, "icon use anchor"
s = s.replace(OLD, NEW)

OLD = '''            title={
              popoutOpen
                ? "Shrink the scanner back into the page (Esc)"
                : "Fill the screen with the scanner, inside the app"
            }
            aria-label={popoutOpen ? "Exit the full-screen scanner" : "Fill the screen with the scanner"}'''
NEW = '''            title={
              popoutOpen
                ? "Close the scanner pop-up (Esc)"
                : "Pop the scanner out into a window you can move and resize, inside the app"
            }
            aria-label={popoutOpen ? "Close the scanner pop-up" : "Pop the scanner out into a window"}'''
assert s.count(OLD) == 1, "button title anchor"
s = s.replace(OLD, NEW)

# --- window geometry: state, persistence, drag and resize -----------------
OLD = '''  const [popoutOpen, setPopoutOpen] = useState(false);'''
NEW = '''  const [popoutOpen, setPopoutOpen] = useState(false);
  // Where the pop-up window sits and how big it is. Remembered, because a
  // window you have to re-place every time is not a window. null = never
  // moved, so it opens centred at a comfortable size for the screen.
  const [popoutRect, setPopoutRect] = useState(() => readPopoutRect());
  const popoutDragRef = useRef(null);'''
assert s.count(OLD) == 1, "popoutOpen anchor"
s = s.replace(OLD, NEW)

OLD = '''  const onPopOut = useCallback(() => setPopoutOpen((open) => !open), []);'''
NEW = '''  const onPopOut = useCallback(() => {
    setPopoutOpen((open) => {
      // Place it the first time it is opened on this screen size, so a
      // remembered rect from a big monitor cannot strand it off a laptop.
      if (!open) setPopoutRect((rect) => clampPopoutRect(rect || defaultPopoutRect()));
      return !open;
    });
  }, []);

  // Drag from the title bar, resize from the corner. Pointer capture rather
  // than window listeners so a fast drag that outruns the cursor cannot drop
  // the gesture, and so it works with touch and a pen unchanged.
  const beginPopoutGesture = useCallback((event, mode) => {
    if (event.button !== undefined && event.button !== 0) return;
    const rect = popoutRect || defaultPopoutRect();
    event.currentTarget.setPointerCapture?.(event.pointerId);
    popoutDragRef.current = { mode, startX: event.clientX, startY: event.clientY, rect };
    event.preventDefault();
  }, [popoutRect]);

  const movePopoutGesture = useCallback((event) => {
    const drag = popoutDragRef.current;
    if (!drag || !event.currentTarget.hasPointerCapture?.(event.pointerId)) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    const next = drag.mode === "move"
      ? { ...drag.rect, x: drag.rect.x + dx, y: drag.rect.y + dy }
      : { ...drag.rect, w: drag.rect.w + dx, h: drag.rect.h + dy };
    setPopoutRect(clampPopoutRect(next));
  }, []);

  const endPopoutGesture = useCallback((event) => {
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    popoutDragRef.current = null;
    setPopoutRect((rect) => {
      writePopoutRect(rect);
      return rect;
    });
  }, []);'''
assert s.count(OLD) == 1, "onPopOut anchor"
s = s.replace(OLD, NEW)

# --- helpers, beside the other stored-preference helpers ------------------
ANCHOR = "// The tab names, remembered."
assert s.count(ANCHOR) == 1, "helpers anchor"
HELPERS = '''// Geometry of the pop-up scanner window: where he put it, how big he made
// it. Kept out of the component so the clamping is testable on its own and
// so a corrupt stored value can only ever degrade to "open it centred".
const MOMX_POPOUT_RECT_KEY = "momx.scanner.popoutRect";
const MOMX_POPOUT_MIN_W = 420;
const MOMX_POPOUT_MIN_H = 260;

function defaultPopoutRect() {
  const vw = typeof window === "undefined" ? 1280 : window.innerWidth;
  const vh = typeof window === "undefined" ? 800 : window.innerHeight;
  const w = Math.max(MOMX_POPOUT_MIN_W, Math.min(1500, Math.round(vw * 0.82)));
  const h = Math.max(MOMX_POPOUT_MIN_H, Math.min(1000, Math.round(vh * 0.82)));
  return { x: Math.round((vw - w) / 2), y: Math.round((vh - h) / 2), w, h };
}

/** Keep the window on screen and above the minimum size. A window dragged
 *  off the edge is a window he cannot get back without clearing storage. */
function clampPopoutRect(rect) {
  if (!rect) return defaultPopoutRect();
  const vw = typeof window === "undefined" ? 1280 : window.innerWidth;
  const vh = typeof window === "undefined" ? 800 : window.innerHeight;
  const w = Math.max(MOMX_POPOUT_MIN_W, Math.min(Number(rect.w) || 0, vw));
  const h = Math.max(MOMX_POPOUT_MIN_H, Math.min(Number(rect.h) || 0, vh));
  return {
    w,
    h,
    x: Math.max(0, Math.min(Number(rect.x) || 0, vw - w)),
    y: Math.max(0, Math.min(Number(rect.y) || 0, vh - h)),
  };
}

function readPopoutRect() {
  try {
    const raw = JSON.parse(window.localStorage.getItem(MOMX_POPOUT_RECT_KEY) || "null");
    if (!raw || ["x", "y", "w", "h"].some((k) => !Number.isFinite(Number(raw[k])))) return null;
    return clampPopoutRect(raw);
  } catch {
    return null;   // absent, private mode, or a shape from an older build
  }
}

function writePopoutRect(rect) {
  try {
    if (rect) window.localStorage.setItem(MOMX_POPOUT_RECT_KEY, JSON.stringify(rect));
  } catch {
    // Private mode. He loses the remembered position, not the window.
  }
}

// The tab names, remembered.'''
s = s.replace(ANCHOR, HELPERS)

# --- render the window ----------------------------------------------------
OLD = '''  // A PORTAL, not position:fixed. `.momx-scanner-view` carries
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
NEW = '''  // A PORTAL, not position:fixed on the panel. `.momx-scanner-view` carries
  // container-type:inline-size, which makes it the containing block for every
  // fixed descendant: measured 2026-09-02, a `fixed; inset:0` box inside this
  // panel came out 1828x1080 in a 1920x1080 window, and 341px wide on a 375px
  // phone. The same containment already broke fixed positioning once on the
  // chart tab, where the fix was to drop it - not an option here, because the
  // sticky toolbar's 100cqw width cap resolves against this very container.
  //
  // Deliberately NOT aria-modal and with no backdrop: this is a window, not a
  // dialog. The app behind it stays visible and usable, which is the entire
  // difference between this and the Maximize button.
  //
  // The inner wrapper re-uses the `momx-scanner-view` class so every rule
  // scoped to it still applies, above all THE single scroll container that
  // owns both axes. The flex column is the trap documented on
  // `.trading-popout-root.is-momx`: that view is `flex: 1; min-height: 0` and
  // collapses to zero height inside a plain block, taking the board with it.
  const rect = popoutRect || defaultPopoutRect();
  return createPortal(
    <div
      className="momx-popout-window"
      role="dialog"
      aria-label="MomX scanner pop-up window"
      style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
    >
      <div
        className="momx-popout-titlebar"
        onPointerDown={(event) => beginPopoutGesture(event, "move")}
        onPointerMove={movePopoutGesture}
        onPointerUp={endPopoutGesture}
        onPointerCancel={endPopoutGesture}
        onDoubleClick={() => setPopoutRect(clampPopoutRect(defaultPopoutRect()))}
        title="Drag to move. Double-click to re-centre."
      >
        <b>MomX Scanner</b>
        <span>drag to move · corner to resize</span>
        <button
          type="button"
          className="momx-popout-close"
          onClick={() => setPopoutOpen(false)}
          onPointerDown={(event) => event.stopPropagation()}
          title="Close the pop-up (Esc)"
          aria-label="Close the scanner pop-up"
        >
          ×
        </button>
      </div>
      <div className="momx-scanner-view">{panel}</div>
      <div
        className="momx-popout-resize"
        role="separator"
        aria-label="Resize the pop-up window"
        onPointerDown={(event) => beginPopoutGesture(event, "resize")}
        onPointerMove={movePopoutGesture}
        onPointerUp={endPopoutGesture}
        onPointerCancel={endPopoutGesture}
        title="Drag to resize"
      />
    </div>,
    document.body,
  );
}
'''
assert s.count(OLD) == 1, "render anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("MomxScannerPanel.jsx: the pop-out is a movable, resizable window")

# ---------------------------------------------------------------------------
p = "frontend/src/index.css"
css = io.open(p, encoding="utf-8", newline="").read()
OLD_START = css.index("/* ---------------------------------------------------------------------------\n   MomX pop-out, IN the app (2026-09-02).")
OLD_END = css.index(".momx-scanner-panel .momx-popout-btn.is-active {")
BLOCK = '''/* ---------------------------------------------------------------------------
   MomX pop-out: a WINDOW inside the app (2026-09-02).

   First cut of this pinned the panel to inset:0, which is Maximize with a
   different button - "why you removed popup and put it back mazimize? i want
   popup". A pop-up floats, moves, resizes, and leaves the app visible and
   usable behind it. No backdrop, and not aria-modal, for exactly that reason.

   Position and size come from inline styles the component clamps to the
   viewport; only the chrome is here. Portalled to <body> - see the render
   comment for why it cannot be positioned from inside the panel.

   z-index 3000: above the app shell (highest below it is a 2500 chart
   tooltip) and below the news popover (9400) and the Momo Alert banner
   (9500), both of which must still appear over the board.
   -------------------------------------------------------------------------- */
.momx-popout-window {
  position: fixed;
  z-index: 3000;
  /* Flex column, because the view inside is `flex: 1; min-height: 0` and
     would resolve to zero height in a plain block - the board's only scroll
     container would collapse and every row below the fold would stop
     existing. Same trap as .trading-popout-root.is-momx. */
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid rgba(78, 220, 255, .34);
  border-radius: 10px;
  background: #0c0c0d;
  box-shadow: 0 26px 70px rgba(0, 0, 0, .62);
}

.momx-popout-window > .momx-scanner-view {
  flex: 1 1 auto;
  min-height: 0;
}

.momx-popout-titlebar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 10px;
  padding: 7px 9px 7px 12px;
  border-bottom: 1px solid #26262c;
  border-radius: 9px 9px 0 0;
  background: #141416;
  cursor: move;
  /* The drag is pointer-driven; without this a touch drag scrolls the page
     underneath instead of moving the window. */
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}

.momx-popout-titlebar b {
  color: #d8f6ff;
  font-size: .68rem;
  font-weight: 850;
  letter-spacing: .06em;
}

.momx-popout-titlebar span {
  flex: 1 1 auto;
  color: #6f6f7c;
  font-size: .55rem;
  font-weight: 700;
  letter-spacing: .03em;
}

.momx-popout-close {
  display: grid;
  flex: 0 0 auto;
  place-items: center;
  width: 26px;
  height: 24px;
  padding: 0;
  border: 1px solid rgba(120, 120, 134, .38);
  border-radius: 6px;
  background: rgba(57, 57, 63, .4);
  color: #cfcfd8;
  font-size: .95rem;
  line-height: 1;
  cursor: pointer;
}

.momx-popout-close:hover {
  border-color: rgba(255, 120, 152, .6);
  background: rgba(255, 120, 152, .16);
  color: #ff92ad;
}

.momx-popout-resize {
  position: absolute;
  right: 0;
  bottom: 0;
  width: 18px;
  height: 18px;
  cursor: nwse-resize;
  touch-action: none;
  /* The two diagonal ticks that say "grab here", drawn rather than an icon
     so it cannot be clipped by the window's overflow:hidden. */
  background:
    linear-gradient(135deg, transparent 0 46%, rgba(148, 163, 184, .55) 46% 54%, transparent 54%),
    linear-gradient(135deg, transparent 0 70%, rgba(148, 163, 184, .55) 70% 78%, transparent 78%);
}

@media (max-width: 760px) {
  /* A draggable 1200px window on a 375px screen is not a feature. Edge to
     edge on a phone; the title bar and its close button stay, so there is
     still an obvious way out. !important beats the component's inline
     geometry, which is the whole point here. */
  .momx-popout-window {
    inset: 0 !important;
    width: auto !important;
    height: auto !important;
    border: 0;
    border-radius: 0;
  }

  .momx-popout-titlebar {
    cursor: default;
    border-radius: 0;
  }

  .momx-popout-titlebar span { display: none; }
  .momx-popout-resize { display: none; }
}

'''
css = css[:OLD_START] + BLOCK + css[OLD_END:]
io.open(p, "w", encoding="utf-8", newline="").write(css)
print("index.css: .momx-popout-window replaces the full-screen overlay")
