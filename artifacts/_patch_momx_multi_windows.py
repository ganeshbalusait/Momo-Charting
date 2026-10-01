"""Several scanner windows at once, each on its own list - and the board behind stays.

2026-09-02, with a thinkorswim screenshot: two detached watchlist windows
side by side, one on AlertX Bull Momo and one on 001_Mega7. "i see only one
popup but i want to multiple popup", and then "No chrome popup please" -
which rules out window.open and settles it on windows drawn inside the app.

Two defects in what shipped as a3d1c3a, both fixed here:

1. ONE WINDOW ONLY. The state was a boolean, so there was nothing to open a
   second of. It is now a LIST of windows, each pinned to a list, each with
   its own geometry, stacking order and title.

2. "backend scanner screen black". The panel RETURNED the portal instead of
   returning itself, so opening the window MOVED the single board out of the
   page and left the scanner area empty - which is exactly the black area he
   photographed. The page's board now always renders, and every window mounts
   its OWN board. That is what makes two lists visible at once anyway: one
   board cannot show two lists.

The cost, told to him before building: each open window is a live board on
its own refresh cycle.

Recursion is stopped by `embedded`: a board inside a window has no pop-out
button and manages no windows of its own. `embedded` also stops it writing
the shared active-list key - without that guard, opening a Mag7 window would
silently flip the MAIN page to Mag7 the moment it mounted, because the panel
persists whatever list it is showing.
"""
import io

p = "frontend/src/MomxScannerPanel.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

# ---------------------------------------------------------------------------
# geometry helpers: per LIST, and cascaded so a second window is not hidden
# exactly behind the first
# ---------------------------------------------------------------------------
OLD = '''const MOMX_POPOUT_RECT_KEY = "momx.scanner.popoutRect";'''
NEW = '''const MOMX_POPOUT_RECT_KEY = "momx.scanner.popoutRect";   // + "." + list name'''
assert s.count(OLD) == 1, "rect key anchor"
s = s.replace(OLD, NEW)

OLD = '''function defaultPopoutRect() {
  const vw = typeof window === "undefined" ? 1280 : window.innerWidth;
  const vh = typeof window === "undefined" ? 800 : window.innerHeight;
  const w = Math.max(MOMX_POPOUT_MIN_W, Math.min(1500, Math.round(vw * 0.82)));
  const h = Math.max(MOMX_POPOUT_MIN_H, Math.min(1000, Math.round(vh * 0.82)));
  return { x: Math.round((vw - w) / 2), y: Math.round((vh - h) / 2), w, h };
}'''
NEW = '''function defaultPopoutRect(index = 0) {
  const vw = typeof window === "undefined" ? 1280 : window.innerWidth;
  const vh = typeof window === "undefined" ? 800 : window.innerHeight;
  const w = Math.max(MOMX_POPOUT_MIN_W, Math.min(1500, Math.round(vw * 0.78)));
  const h = Math.max(MOMX_POPOUT_MIN_H, Math.min(1000, Math.round(vh * 0.72)));
  // Cascade, the way every window manager does it: a second window opened at
  // the same coordinates as the first is indistinguishable from no second
  // window at all. Wraps after five so it cannot walk off the screen.
  const step = 28 * (index % 5);
  return {
    x: Math.round((vw - w) / 2) + step,
    y: Math.round((vh - h) / 2) + step,
    w,
    h,
  };
}'''
assert s.count(OLD) == 1, "defaultPopoutRect anchor"
s = s.replace(OLD, NEW)

OLD = '''function readPopoutRect() {
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
}'''
NEW = '''/** Geometry is remembered PER LIST, so the Mag7 window reopens where the
 *  Mag7 window was - not where the last window of any list happened to be. */
function popoutRectKey(list) {
  return MOMX_POPOUT_RECT_KEY + "." + String(list || "default");
}

function readPopoutRect(list) {
  try {
    const raw = JSON.parse(window.localStorage.getItem(popoutRectKey(list)) || "null");
    if (!raw || ["x", "y", "w", "h"].some((k) => !Number.isFinite(Number(raw[k])))) return null;
    return clampPopoutRect(raw);
  } catch {
    return null;   // absent, private mode, or a shape from an older build
  }
}

function writePopoutRect(list, rect) {
  try {
    if (rect) window.localStorage.setItem(popoutRectKey(list), JSON.stringify(rect));
  } catch {
    // Private mode. He loses the remembered position, not the window.
  }
}'''
assert s.count(OLD) == 1, "rect io anchor"
s = s.replace(OLD, NEW)

# ---------------------------------------------------------------------------
# the component takes an initial list and an embedded flag
# ---------------------------------------------------------------------------
OLD = '''export default function MomxScannerPanel() {'''
NEW = '''export default function MomxScannerPanel({ initialList = null, embedded = false }) {
  // `embedded` = this board is inside a pop-out window. It owns no windows of
  // its own (that would recurse), shows no pop-out button, and - the one that
  // silently breaks the main page if forgotten - never writes the shared
  // active-list key, because a Mag7 window mounting would otherwise flip the
  // page behind it to Mag7.'''
assert s.count(OLD) == 1, "signature anchor"
s = s.replace(OLD, NEW)

OLD = '''  const [boardList, setBoardList] = useState(() => readStoredList());'''
NEW = '''  const [boardList, setBoardList] = useState(() => initialList || readStoredList());'''
assert s.count(OLD) == 1, "boardList anchor"
s = s.replace(OLD, NEW)

OLD = '''  const [activeList, setActiveList] = useState(() => readStoredList());'''
NEW = '''  const [activeList, setActiveList] = useState(() => initialList || readStoredList());'''
assert s.count(OLD) == 1, "activeList anchor"
s = s.replace(OLD, NEW)

OLD = '''  useEffect(() => {
    if (activeList) writeStoredList(activeList);
  }, [activeList]);'''
NEW = '''  useEffect(() => {
    // Never from inside a pop-out window: see `embedded` above.
    if (!embedded && activeList) writeStoredList(activeList);
  }, [activeList, embedded]);'''
assert s.count(OLD) == 1, "writeStoredList anchor"
s = s.replace(OLD, NEW)

# ---------------------------------------------------------------------------
# state: a LIST of windows, not a boolean
# ---------------------------------------------------------------------------
OLD = '''  const [popoutOpen, setPopoutOpen] = useState(false);
  // Where the pop-up window sits and how big it is. Remembered, because a
  // window you have to re-place every time is not a window. null = never
  // moved, so it opens centred at a comfortable size for the screen.
  const [popoutRect, setPopoutRect] = useState(() => readPopoutRect());
  const popoutDragRef = useRef(null);'''
NEW = '''  // The open pop-out windows: [{ id, list, rect, z }]. A LIST, not a boolean -
  // he runs two thinkorswim watchlist windows side by side on different lists
  // and wants the same here. Never populated for an embedded board.
  const [popouts, setPopouts] = useState(EMPTY_ROWS);
  const popoutDragRef = useRef(null);
  const popoutSeqRef = useRef(0);
  const popoutTopRef = useRef(10);'''
assert s.count(OLD) == 1, "popout state anchor"
s = s.replace(OLD, NEW)

# ---------------------------------------------------------------------------
# open / close / focus / drag / resize, all keyed by window id
# ---------------------------------------------------------------------------
start = s.index("  const onPopOut = useCallback(() => {")
end = s.index("  const onPickMomentum = useCallback(")
NEW_BLOCK = '''  const focusPopout = useCallback((id) => {
    popoutTopRef.current += 1;
    const z = popoutTopRef.current;
    setPopouts((windows) => windows.map((w) => (w.id === id ? { ...w, z } : w)));
  }, []);

  const closePopout = useCallback((id) => {
    setPopouts((windows) => windows.filter((w) => w.id !== id));
  }, []);

  // Open a window for the list currently on screen. A list that already has a
  // window gets that window raised instead of a duplicate - two windows on the
  // same list would show identical data and be impossible to tell apart.
  const onPopOut = useCallback(() => {
    const list = activeList || "";
    setPopouts((windows) => {
      const existing = windows.find((w) => w.list === list);
      if (existing) {
        popoutTopRef.current += 1;
        const z = popoutTopRef.current;
        return windows.map((w) => (w.id === existing.id ? { ...w, z } : w));
      }
      popoutSeqRef.current += 1;
      popoutTopRef.current += 1;
      return [
        ...windows,
        {
          id: "popout-" + popoutSeqRef.current,
          list,
          // Where he last left this list's window, clamped to today's screen
          // so a rect remembered from a bigger monitor cannot strand it.
          rect: clampPopoutRect(readPopoutRect(list) || defaultPopoutRect(windows.length)),
          z: popoutTopRef.current,
        },
      ];
    });
  }, [activeList]);

  // Drag from the title bar, resize from the corner. Pointer capture rather
  // than window listeners so a fast drag that outruns the cursor cannot drop
  // the gesture, and so touch and pen work unchanged.
  const beginPopoutGesture = useCallback((event, mode, id) => {
    if (event.button !== undefined && event.button !== 0) return;
    const target = event.currentTarget;
    setPopouts((windows) => {
      const win = windows.find((w) => w.id === id);
      if (win) {
        popoutDragRef.current = {
          id, mode, startX: event.clientX, startY: event.clientY, rect: win.rect,
        };
      }
      return windows;
    });
    target.setPointerCapture?.(event.pointerId);
    focusPopout(id);
    event.preventDefault();
  }, [focusPopout]);

  const movePopoutGesture = useCallback((event) => {
    const drag = popoutDragRef.current;
    if (!drag || !event.currentTarget.hasPointerCapture?.(event.pointerId)) return;
    const dx = event.clientX - drag.startX;
    const dy = event.clientY - drag.startY;
    const next = clampPopoutRect(
      drag.mode === "move"
        ? { ...drag.rect, x: drag.rect.x + dx, y: drag.rect.y + dy }
        : { ...drag.rect, w: drag.rect.w + dx, h: drag.rect.h + dy },
    );
    setPopouts((windows) => windows.map((w) => (w.id === drag.id ? { ...w, rect: next } : w)));
  }, []);

  const endPopoutGesture = useCallback((event) => {
    const drag = popoutDragRef.current;
    event.currentTarget.releasePointerCapture?.(event.pointerId);
    popoutDragRef.current = null;
    if (!drag) return;
    setPopouts((windows) => {
      const win = windows.find((w) => w.id === drag.id);
      if (win) writePopoutRect(win.list, win.rect);
      return windows;
    });
  }, []);

  // Esc closes the FRONT window only. Closing the whole stack on one key
  // would throw away an arrangement he spent time building. Bound only while
  // something is open, so it can never swallow Esc from anything else, and
  // skipped while a popover that portals ABOVE the windows is up.
  useEffect(() => {
    if (popouts.length === 0) return undefined;
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      if (document.querySelector(".momx-news-overlay, .momo-settings-overlay, .momx-picker-overlay")) return;
      setPopouts((windows) => {
        if (windows.length === 0) return windows;
        const top = windows.reduce((a, b) => (b.z > a.z ? b : a));
        return windows.filter((w) => w.id !== top.id);
      });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [popouts.length]);

'''
s = s[:start] + NEW_BLOCK + s[end:]

# ---------------------------------------------------------------------------
# the toolbar button
# ---------------------------------------------------------------------------
OLD = '''        {canPopOut ? ('''
NEW = '''        {canPopOut && !embedded ? ('''
assert s.count(OLD) == 1, "button gate anchor"
s = s.replace(OLD, NEW)

OLD = '''            className={"momx-icon-btn momx-popout-btn" + (popoutOpen ? " is-active" : "")}'''
NEW = '''            className={
              "momx-icon-btn momx-popout-btn" +
              (popouts.some((w) => w.list === activeList) ? " is-active" : "")
            }'''
assert s.count(OLD) == 1, "button class anchor"
s = s.replace(OLD, NEW)

OLD = '''            title={
              popoutOpen
                ? "Close the scanner pop-up (Esc)"
                : "Pop the scanner out into a window you can move and resize, inside the app"
            }
            aria-label={popoutOpen ? "Close the scanner pop-up" : "Pop the scanner out into a window"}
            aria-pressed={popoutOpen}'''
NEW = '''            title={
              popouts.some((w) => w.list === activeList)
                ? (activeList || "This list") + " is already popped out - click to bring its window to the front"
                : "Pop " + (activeList || "this list") +
                  " out into its own window. Switch list and press again for a second window."
            }
            aria-label={"Pop " + (activeList || "this list") + " out into its own window"}
            aria-pressed={popouts.some((w) => w.list === activeList)}'''
assert s.count(OLD) == 1, "button title anchor"
s = s.replace(OLD, NEW)

# ---------------------------------------------------------------------------
# render: the page ALWAYS keeps its board; the windows are extra boards
# ---------------------------------------------------------------------------
marker = "  if (!popoutOpen) return panel;"
start = s.index(marker)
NEW_TAIL = '''  // The page's own board is ALWAYS returned. a3d1c3a returned the portal
  // INSTEAD, which moved the single board into the window and left the
  // scanner area of the page empty - the black rectangle he photographed.
  // Each window mounts its OWN board, which is also the only way two lists
  // can be on screen at once: one board cannot show two lists.
  if (embedded || popouts.length === 0) return panel;

  // PORTALS, not position:fixed on the panel. `.momx-scanner-view` carries
  // container-type:inline-size, which makes it the containing block for every
  // fixed descendant: measured 2026-09-02, a `fixed; inset:0` box inside this
  // panel came out 1828x1080 in a 1920x1080 window, and 341px wide on a 375px
  // phone. The same containment already broke fixed positioning once on the
  // chart tab, where the fix was to drop it - not an option here, because the
  // sticky toolbar's 100cqw width cap resolves against this very container.
  //
  // Deliberately NOT aria-modal and with no backdrop: these are windows, not
  // dialogs. The app behind them stays visible and usable - the whole point.
  //
  // The inner wrapper re-uses the `momx-scanner-view` class so every rule
  // scoped to it still applies, above all THE single scroll container that
  // owns both axes. The flex column is the trap documented on
  // `.trading-popout-root.is-momx`: that view is `flex: 1; min-height: 0` and
  // collapses to zero height inside a plain block, taking the board with it.
  return (
    <>
      {panel}
      {popouts.map((win) =>
        createPortal(
          <div
            className="momx-popout-window"
            role="dialog"
            aria-label={(win.list || "MomX") + " scanner window"}
            style={{ left: win.rect.x, top: win.rect.y, width: win.rect.w, height: win.rect.h, zIndex: 3000 + win.z }}
            onPointerDownCapture={() => focusPopout(win.id)}
          >
            <div
              className="momx-popout-titlebar"
              onPointerDown={(event) => beginPopoutGesture(event, "move", win.id)}
              onPointerMove={movePopoutGesture}
              onPointerUp={endPopoutGesture}
              onPointerCancel={endPopoutGesture}
              onDoubleClick={() =>
                setPopouts((windows) =>
                  windows.map((w) =>
                    w.id === win.id ? { ...w, rect: clampPopoutRect(defaultPopoutRect(0)) } : w,
                  ),
                )
              }
              title="Drag to move. Double-click to re-centre."
            >
              <b>{win.list || "MomX Scanner"}</b>
              <span>drag to move · corner to resize</span>
              <button
                type="button"
                className="momx-popout-close"
                onClick={() => closePopout(win.id)}
                onPointerDown={(event) => event.stopPropagation()}
                title="Close this window (Esc closes the front one)"
                aria-label={"Close the " + (win.list || "scanner") + " window"}
              >
                ×
              </button>
            </div>
            <div className="momx-scanner-view">
              <MomxScannerPanel initialList={win.list} embedded />
            </div>
            <div
              className="momx-popout-resize"
              role="separator"
              aria-label="Resize this window"
              onPointerDown={(event) => beginPopoutGesture(event, "resize", win.id)}
              onPointerMove={movePopoutGesture}
              onPointerUp={endPopoutGesture}
              onPointerCancel={endPopoutGesture}
              title="Drag to resize"
            />
          </div>,
          document.body,
          win.id,
        ),
      )}
    </>
  );
}
'''
s = s[:start] + NEW_TAIL
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("MomxScannerPanel.jsx: many windows, each on its own list; the page keeps its board")

# ---------------------------------------------------------------------------
# CSS: z-index now comes from the component (stacking order), so drop the
# fixed one, and let the title bar show the list name.
# ---------------------------------------------------------------------------
p = "frontend/src/index.css"
css = io.open(p, encoding="utf-8", newline="").read()
OLD = '''.momx-popout-window {
  position: fixed;
  z-index: 3000;'''
NEW = '''.momx-popout-window {
  position: fixed;
  /* z-index is set INLINE per window: clicking one raises it above the
     others, which is the whole reason several can be open at once. The base
     is 3000 - above the app shell, below the news popover (9400) and the
     Momo Alert banner (9500), both of which must stay on top. */'''
assert css.count(OLD) == 1, "window z anchor"
css = css.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(css)
print("index.css: stacking order comes from the component")
