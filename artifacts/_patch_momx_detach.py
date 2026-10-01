"""Detach: move a scanner window OUT of the app, onto another monitor.

2026-09-02: "why i'm unable to move out from the chrome?", then a screenshot
of the INSTALLED desktop app with the pop-up still trapped inside it - "even
that i installed in desktop still having issue".

The answer is structural and no amount of installing fixes it: the pop-out
windows are <div>s painted inside the page, and a page cannot paint outside
its own window. Only a real browser-created window can leave, and that is
window.open.

Measured in a real Chrome before building this, so the trade-off is a number
and not an adjective:

    a normal browser window ... 95px of furniture
    a window.open pop-up ...... 72px, of which ~32px is Windows' own title
                                bar. toolbar/locationbar/menubar all report
                                NOT visible - no tabs, no address bar, no
                                bookmarks.
    two pop-ups with different names ... two genuinely separate windows.

So this adds a per-window "detach" button rather than replacing anything. He
keeps the in-app windows (zero furniture, unlimited, but trapped) and can
promote any one of them to a real window (goes anywhere, sits on top of
thinkorswim) when he wants it on the second monitor. Per window, per list,
his choice.

Three details that would each break it silently:

* the detached page must be told WHICH list, or every detached window shows
  whatever list the main app happens to be on. ?popout=momx&list=NAME, read
  in App.jsx and passed as initialList.
* the in-app rect is in VIEWPORT coordinates and window.open wants SCREEN
  coordinates, so the offset of the app window itself has to be added or the
  detached window lands somewhere else entirely.
* detached geometry is remembered under its own key, not the in-app one -
  the two are different coordinate spaces and sharing a key would make each
  corrupt the other's idea of where the window belongs.
"""
import io

# ---------------------------------------------------------------------------
# App.jsx: the standalone popout page honours ?list=
# ---------------------------------------------------------------------------
p = "frontend/src/App.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = '''    return {
      mode,
      symbol: normalizeOiChartSymbol(params.get("symbol"), "AAPL"),'''
NEW = '''    return {
      mode,
      // Which MomX list this detached window is for. Without it every
      // detached window would show whatever list the MAIN app was last on,
      // which defeats the entire point of opening two of them.
      list: (params.get("list") || "").trim(),
      symbol: normalizeOiChartSymbol(params.get("symbol"), "AAPL"),'''
assert s.count(OLD) == 1, "popoutConfig anchor"
s = s.replace(OLD, NEW)

OLD = '''  // The scanner popout renders the panel and NOTHING else - no chart board,
  // no alert centre. MomxScannerPanel takes no props and fetches its own data,
  // so a detached window is genuinely just this.
  if (popoutConfig.mode === "momx") {
    return (
      <main className="trading-popout-root is-momx" data-testid="momx-popout-root">
        <MomoAlertWatcher />
        <section className="momx-scanner-view" data-testid="momx-scanner-view">
          <MomxScannerPanel />
        </section>
      </main>
    );
  }'''
NEW = '''  // The scanner popout renders the panel and NOTHING else - no chart board,
  // no alert centre. A detached window is genuinely just this.
  //
  // `initialList` is what makes two detached windows worth having: each is
  // pinned to the list named in its own URL. `embedded` stops it writing that
  // list back into the shared active-list key - without it, opening a Mag7
  // window would reach across and flip the MAIN app window to Mag7, because
  // the panel persists whatever list it is showing and every window here
  // shares one localStorage.
  if (popoutConfig.mode === "momx") {
    return (
      <main className="trading-popout-root is-momx" data-testid="momx-popout-root">
        <MomoAlertWatcher />
        <section className="momx-scanner-view" data-testid="momx-scanner-view">
          <MomxScannerPanel initialList={popoutConfig.list || null} embedded={Boolean(popoutConfig.list)} />
        </section>
      </main>
    );
  }'''
assert s.count(OLD) == 1, "momx popout route anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("App.jsx: the detached scanner page honours ?list=")

# ---------------------------------------------------------------------------
# MomxScannerPanel.jsx: the detach button
# ---------------------------------------------------------------------------
p = "frontend/src/MomxScannerPanel.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

# --- geometry of DETACHED windows, in screen coordinates ------------------
OLD = '''function readPopoutRect(list) {'''
NEW = '''//: Detached (real) windows are remembered separately from the in-app ones.
//: Screen coordinates, not viewport coordinates - sharing one key would have
//: each stomp the other with a number measured from a different origin.
const MOMX_DETACHED_RECT_KEY = "momx.scanner.detachedRect";

function readDetachedRect(list) {
  try {
    const raw = JSON.parse(
      window.localStorage.getItem(MOMX_DETACHED_RECT_KEY + "." + String(list || "default")) || "null",
    );
    if (!raw || ["x", "y", "w", "h"].some((k) => !Number.isFinite(Number(raw[k])))) return null;
    return raw;
  } catch {
    return null;
  }
}

function readPopoutRect(list) {'''
assert s.count(OLD) == 1, "detached rect anchor"
s = s.replace(OLD, NEW)

# --- the detach action ----------------------------------------------------
OLD = '''  // Drag from the title bar, resize from the corner. Pointer capture rather'''
NEW = '''  // Promote an in-app window to a REAL one, so it can go on the second
  // monitor or sit on top of thinkorswim. This is the only thing that can
  // leave the app: a div cannot be painted outside the window that owns it,
  // which is why installing AGX to the desktop did not help (2026-09-02).
  //
  // The window NAME is per list. The original code named every window
  // "agx-momx-scanner", and window.open re-targets an existing name instead
  // of making a new window - which is why only one ever appeared.
  const detachPopout = useCallback((win) => {
    if (typeof window === "undefined") return;
    const url = new URL(window.location.href);
    url.searchParams.set("popout", "momx");
    url.searchParams.set("list", win.list || "");
    // Nothing chart-related belongs in a scanner window.
    ["symbol", "timeframe", "link"].forEach((key) => url.searchParams.delete(key));

    // The in-app rect is measured from the top-left of the PAGE; window.open
    // positions from the top-left of the SCREEN. Without the app window's own
    // offset the detached window lands somewhere unrelated - and on a second
    // monitor that can be off the visible desktop entirely.
    const saved = readDetachedRect(win.list);
    const chromeH = Math.max(0, window.outerHeight - window.innerHeight);
    const left = Math.round(saved ? saved.x : (window.screenX || 0) + win.rect.x);
    const top = Math.round(saved ? saved.y : (window.screenY || 0) + chromeH + win.rect.y);
    const width = Math.round(saved ? saved.w : win.rect.w);
    const height = Math.round(saved ? saved.h : win.rect.h);

    const opened = window.open(
      url.toString(),
      "agx-momx-" + String(win.list || "default").replace(/[^A-Za-z0-9]+/g, "-"),
      `popup=yes,width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`,
    );
    if (!opened) {
      // Blocked. KEEP the in-app window - closing it here would leave him
      // with neither, which is strictly worse than what he had.
      setNotice(
        "Your browser blocked the detached window. Allow pop-ups for this site, then press Detach again.",
      );
      return;
    }
    opened.focus?.();
    // It lives out there now; two copies of the same list on screen would
    // just be confusing.
    closePopout(win.id);
  }, [closePopout]);

  // Drag from the title bar, resize from the corner. Pointer capture rather'''
assert s.count(OLD) == 1, "detach insert anchor"
s = s.replace(OLD, NEW)

# --- the button in each window's title bar --------------------------------
OLD = '''              <b>{win.list || "MomX Scanner"}</b>
              <span>drag to move · corner to resize</span>
              <button
                type="button"
                className="momx-popout-close"'''
NEW = '''              <b>{win.list || "MomX Scanner"}</b>
              <span>drag to move · corner to resize</span>
              <button
                type="button"
                className="momx-popout-detach"
                onClick={() => detachPopout(win)}
                onPointerDown={(event) => event.stopPropagation()}
                title="Move this window out of AGX - its own window, for a second monitor or beside thinkorswim"
                aria-label={"Detach the " + (win.list || "scanner") + " window from AGX"}
              >
                <ExternalLink size={12} aria-hidden="true" />
                detach
              </button>
              <button
                type="button"
                className="momx-popout-close"'''
assert s.count(OLD) == 1, "titlebar anchor"
s = s.replace(OLD, NEW)

# --- a detached window remembers where it was left ------------------------
OLD = '''  // Esc closes the FRONT window only.'''
NEW = '''  // A DETACHED window records its own screen geometry, so re-detaching that
  // list puts it back where he left it - on the second monitor if that is
  // where it was. Written on hide rather than on every move: `resize` fires
  // continuously while dragging an OS window edge.
  useEffect(() => {
    if (!embedded || typeof window === "undefined") return undefined;
    const list = initialList;
    if (!list) return undefined;
    const remember = () => {
      try {
        window.localStorage.setItem(
          MOMX_DETACHED_RECT_KEY + "." + String(list),
          JSON.stringify({
            x: window.screenX, y: window.screenY, w: window.outerWidth, h: window.outerHeight,
          }),
        );
      } catch {
        /* private mode: it simply opens at the default place next time */
      }
    };
    window.addEventListener("pagehide", remember);
    return () => {
      remember();
      window.removeEventListener("pagehide", remember);
    };
  }, [embedded, initialList]);

  // Esc closes the FRONT window only.'''
assert s.count(OLD) == 1, "remember anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("MomxScannerPanel.jsx: detach button + detached geometry memory")

# ---------------------------------------------------------------------------
p = "frontend/src/index.css"
css = io.open(p, encoding="utf-8", newline="").read()
OLD = '''.momx-popout-close {'''
NEW = '''/* "detach" - the only control that can take a window out of AGX. Sits beside
   the close button because that is where a window's own controls belong. */
.momx-popout-detach {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 5px;
  height: 24px;
  padding: 0 9px;
  border: 1px solid rgba(78, 220, 255, .34);
  border-radius: 6px;
  background: rgba(78, 220, 255, .1);
  color: #7fdcf0;
  font-size: .56rem;
  font-weight: 850;
  letter-spacing: .05em;
  text-transform: uppercase;
  cursor: pointer;
}

.momx-popout-detach:hover {
  border-color: rgba(78, 220, 255, .7);
  background: rgba(78, 220, 255, .2);
  color: #d8f6ff;
}

.momx-popout-close {'''
assert css.count(OLD) == 1, "close button css anchor"
css = css.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(css)
print("index.css: .momx-popout-detach")
