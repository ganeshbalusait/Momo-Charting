"""The remaining pop-outs open in the app too, not in a Chrome tab.

Follow-on from 212ff8d (the MomX scanner). Auditing what was left, there were
exactly two real window.open calls in the frontend - the trading pop-out and
the Schwab OAuth window. The OAuth one MUST stay a real window; it is a
third-party login and cannot live in an overlay. That leaves
openTradingPopout, with three surfaces:

  chain - four call sites. The only one with no in-app equivalent, so it
          gets one here.
  mag7  - one call site, sitting next to the "Big screen" toggle, which is
          already the in-app version of exactly this. Repointed at it rather
          than building a second one.
  chart - one call site, gated on `popoutEnabled`, which is destructured with
          a default of false and PASSED BY NOBODY. Measured: the button never
          renders. Repointed anyway so it cannot come back wrong if someone
          wires the flag up later; it sits beside the panel Maximize button,
          which is the in-app version.

WHY A CLASS AND NOT A PORTAL, the opposite of the MomX fix. That one needed
createPortal because `.momx-scanner-view` sets container-type:inline-size and
became the containing block for fixed descendants. Measured here before
choosing: the chain has NO such ancestor - a `fixed; inset: 0` probe inside
`#charts-oi-option-chain` resolved to the full 1920x1080 viewport. So a class
is enough, and it is strictly better than a portal would be: the chain is not
re-parented, so it never remounts and the expiry accordion keeps its state
and its scroll position.
"""
import io

p = "frontend/src/App.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

# --- state, beside the board's other view state ---------------------------
OLD = '''  const [linkedGroup, setLinkedGroup] = useState(() => Math.min(9, Math.max(1, Number(popoutLinkGroup) || 2)));'''
NEW = '''  const [linkedGroup, setLinkedGroup] = useState(() => Math.min(9, Math.max(1, Number(popoutLinkGroup) || 2)));
  // The option chain, filling the screen INSIDE the app. This replaces a
  // window.open that Chrome was serving him as an ordinary tab (2026-09-02,
  // same complaint as the MomX pop-out). A plain class rather than a portal:
  // measured, nothing above the chain creates a containing block, so
  // position:fixed reaches the viewport - and not re-parenting it means the
  // expiry accordion keeps its state and scroll instead of remounting.
  const [chainFullscreen, setChainFullscreen] = useState(false);
  useEffect(() => {
    if (!chainFullscreen) return undefined;
    const onKey = (event) => {
      if (event.key === "Escape") setChainFullscreen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [chainFullscreen]);'''
assert s.count(OLD) == 1, "linkedGroup anchor"
s = s.replace(OLD, NEW)

# --- the four chain call sites -------------------------------------------
before = s.count('openTradingPopout("chain"')
assert before == 4, f"expected 4 chain call sites, found {before}"
s = s.replace('openTradingPopout("chain", symbol, linkedGroup)', 'setChainFullscreen((open) => !open)')
s = s.replace('openTradingPopout("chain", symbol, linkGroup)', 'setChainFullscreen((open) => !open)')
assert s.count('openTradingPopout("chain"') == 0, "a chain call site survived"

# The titles said "separate window", which is no longer what happens.
s = s.replace(
    'title="Open option chain in a separate window" aria-label="Pop out option chain"',
    'title="Fill the screen with the option chain, inside the app (Esc to exit)" '
    'aria-label="Fill the screen with the option chain"',
)

# --- the chain sections carry the class ----------------------------------
OLD = '''{chainOpen ? <section className="charts-oi-tos-chain" id="charts-oi-option-chain">'''
NEW = '''{chainOpen ? <section className={`charts-oi-tos-chain${chainFullscreen ? " is-fullscreen" : ""}`} id="charts-oi-option-chain">'''
assert s.count(OLD) == 1, "inline chain anchor"
s = s.replace(OLD, NEW)

popout_chain = s.count('<section className="charts-oi-tos-chain is-popout-chain">')
assert popout_chain == 2, f"expected 2 popout chain sections, found {popout_chain}"
s = s.replace(
    '<section className="charts-oi-tos-chain is-popout-chain">',
    '<section className={`charts-oi-tos-chain is-popout-chain${chainFullscreen ? " is-fullscreen" : ""}`}>',
)

# --- mag7: the in-app version is the Big screen toggle beside it ---------
OLD = '''              onClick={() => openTradingPopout("mag7", activeConfig.symbol, activeConfig.linkGroup)}
              title="Pop out MAG7 — open the complete MAG7 workspace in a separate window"
              aria-label="Pop out the complete MAG7 workspace"'''
NEW = '''              onClick={() => setIsWorkspaceMaximized(true)}
              title="MAG7 full screen — fill the app with the complete MAG7 workspace"
              aria-label="Fill the screen with the complete MAG7 workspace"'''
assert s.count(OLD) == 1, "mag7 anchor"
s = s.replace(OLD, NEW)

# --- chart: dead today, but point it at the panel Maximize beside it -----
OLD = '''                openTradingPopout("chart", symbol, linkGroup, chartTimeframe);
              }}
              title={`Open ${symbol || "this chart"} in a separate window for another monitor`}
              data-tooltip="Pop out"
              aria-label={`Pop out ${symbol || "chart"} to a separate window`}'''
NEW = '''                setIsMaximized(true);
                setIsMaximizedCompanionVisible(true);
              }}
              title={`Fill the screen with ${symbol || "this chart"} and its option chain`}
              data-tooltip="Full screen"
              aria-label={`Fill the screen with ${symbol || "chart"}`}'''
assert s.count(OLD) == 1, "chart popout anchor"
s = s.replace(OLD, NEW)

# openTradingPopout now has no callers; leaving a dead exported-looking helper
# behind is how the next reader ends up re-wiring a Chrome tab.
assert s.count("openTradingPopout(") == 1, "openTradingPopout should only be its definition now"
OLD_FN_START = "function openTradingPopout(surface, symbol, linkGroup = 2, timeframe = \"5m\") {"
start = s.index(OLD_FN_START)
end = s.index("\n}\n", start) + len("\n}\n")
s = s[:start] + (
    "// openTradingPopout lived here. Every surface it served now opens INSIDE the\n"
    "// app - the chain fills the screen via .is-fullscreen, MAG7 uses the Big\n"
    "// screen workspace, and a single chart uses the panel Maximize. Deleted\n"
    "// rather than left dead: he asked twice for no Chrome tabs, and a helper\n"
    "// sitting here named `open...Popout` is how one comes back.\n"
    "// The ?popout= URLs still RENDER standalone for anyone holding a link.\n"
) + s[end:]
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("App.jsx: chain fills the screen in-app; mag7 -> big screen; chart -> maximize; opener deleted")

# ---------------------------------------------------------------------------
p = "frontend/src/index.css"
css = io.open(p, encoding="utf-8", newline="").read()
assert ".charts-oi-tos-chain.is-fullscreen" not in css, "already present"
css += '''
/* ---------------------------------------------------------------------------
   Option chain, full screen INSIDE the app (2026-09-02). Was a window.open
   that Chrome served as an ordinary tab.

   position:fixed and not a portal because - measured - nothing above
   #charts-oi-option-chain creates a containing block, unlike the MomX panel
   whose view sets container-type:inline-size. Keeping it in place means the
   expiry accordion never remounts.

   z-index 3000 matches the MomX overlay: over the app, under the Momo Alert
   banner (9500).
   -------------------------------------------------------------------------- */
.charts-oi-tos-chain.is-fullscreen {
  position: fixed;
  z-index: 3000;
  inset: 0;
  width: auto;
  max-width: none;
  height: auto;
  max-height: none;
  margin: 0;
  border: 0;
  border-radius: 0;
  /* Column + auto so the chain owns its own scrolling at full height; the
     base rule is overflow:hidden, which at this size would simply hide the
     strikes below the fold. */
  display: flex;
  flex-direction: column;
  overflow: auto;
  overscroll-behavior: contain;
  background: #0c0c0d;
}

/* The header stays put while the strikes scroll under it - at full height
   there are far more rows than fit, and losing the spot price and the expiry
   while scrolling is what makes a long chain unreadable. */
.charts-oi-tos-chain.is-fullscreen > header {
  position: sticky;
  top: 0;
  z-index: 2;
  background: #111113;
}
'''
io.open(p, "w", encoding="utf-8", newline="").write(css)
print("index.css: .charts-oi-tos-chain.is-fullscreen")
