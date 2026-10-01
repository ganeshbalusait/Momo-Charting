"""Per-chart Maximize: an adjustable option chain, and a card that fits.

"from the grid chart let say i maximze the chart AAPL or AMZN, i dont see the
chart scroll and option adjust" (2026-09-01, reported ~7 times).

Two maximize buttons exist. "Big screen" (toolbar) enlarges the whole grid and
has had a draggable chain divider for weeks -- every fix so far went there.
The one he uses is the per-panel Maximize (the arrows on AAPL's own header),
which renders `.oi-finder-chart-maximized-chain` at a FIXED
clamp(390px, 31vw, 560px) with no handle at all. Nothing to drag existed.

This gives that chain the same divider the big screen has, reusing the same
pointer maths (workspaceCompanionWidthAtPointer) and the same remember-it
contract, written to --oi-maximized-chain-width on the card. CSS falls back
to the old clamp when nothing was dragged.
"""
import io

p = "frontend/src/App.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

# --- state, beside the existing maximize state -----------------------------
OLD = '''  const [isMaximized, setIsMaximized] = useState(false);
  const [isMaximizedCompanionVisible, setIsMaximizedCompanionVisible] = useState(true);
'''
NEW = '''  const [isMaximized, setIsMaximized] = useState(false);
  const [isMaximizedCompanionVisible, setIsMaximizedCompanionVisible] = useState(true);
  // Width of the option chain beside a per-panel maximized chart. null means
  // "never dragged": CSS then uses its own clamp(390px, 31vw, 560px). Kept
  // separate from the big-screen companion width because the two modes have
  // different chart widths and he sizes them differently.
  const [maximizedChainWidth, setMaximizedChainWidth] = useState(() => {
    try {
      const raw = Number(localStorage.getItem(MAXIMIZED_CHAIN_WIDTH_STORAGE_KEY));
      return Number.isFinite(raw) && raw >= 240 && raw <= 1200 ? raw : null;
    } catch {
      return null;
    }
  });
  useEffect(() => {
    try {
      if (maximizedChainWidth == null) localStorage.removeItem(MAXIMIZED_CHAIN_WIDTH_STORAGE_KEY);
      else localStorage.setItem(MAXIMIZED_CHAIN_WIDTH_STORAGE_KEY, String(Math.round(maximizedChainWidth)));
    } catch {
      /* private mode: the width simply is not remembered */
    }
  }, [maximizedChainWidth]);
  const resizeMaximizedChain = (event) => {
    const card = event.currentTarget.closest(".oi-finder-chart-card");
    const bounds = card?.getBoundingClientRect?.();
    if (!bounds) return;
    const nextWidth = workspaceCompanionWidthAtPointer({
      containerLeft: bounds.left,
      containerWidth: bounds.width,
      pointerX: event.clientX,
    });
    if (nextWidth != null) setMaximizedChainWidth(nextWidth);
  };
'''
assert s.count(OLD) == 1, "state anchor"
s = s.replace(OLD, NEW)

# --- the constant, next to the other chart storage keys ---------------------
OLD = 'const OI_CHART_LAYOUT_PROFILES_STORAGE_KEY = "oiFinderChartLayoutProfiles";'
NEW = OLD + '\nconst MAXIMIZED_CHAIN_WIDTH_STORAGE_KEY = "oiFinderMaximizedChainWidth";'
assert s.count(OLD) == 1, "key anchor"
s = s.replace(OLD, NEW)

# --- the width reaches CSS through the card -------------------------------
OLD = '''      style={{ "--active-link-color": linkConfig.color }}
      aria-label={`${symbol || "Ticker"} live price and OI wall chart`}'''
NEW = '''      style={{
        "--active-link-color": linkConfig.color,
        "--oi-maximized-chain-width": maximizedChainWidth == null ? undefined : `${maximizedChainWidth}px`,
      }}
      aria-label={`${symbol || "Ticker"} live price and OI wall chart`}'''
assert s.count(OLD) == 1, "style anchor"
s = s.replace(OLD, NEW)

# --- the divider, rendered beside the chain -------------------------------
OLD = '''      {isMaximized && maximizeCompanion && isMaximizedCompanionVisible ? (
        <aside
          className="oi-finder-chart-maximized-chain"'''
NEW = '''      {isMaximized && maximizeCompanion && isMaximizedCompanionVisible ? (
        <div
          className="oi-finder-chart-maximized-chain-handle"
          role="separator"
          aria-label="Resize chart and option chain"
          aria-orientation="vertical"
          tabIndex="0"
          onPointerDown={(event) => {
            event.preventDefault();
            event.stopPropagation();
            event.currentTarget.setPointerCapture?.(event.pointerId);
          }}
          onPointerMove={(event) => {
            if (!event.currentTarget.hasPointerCapture?.(event.pointerId)) return;
            resizeMaximizedChain(event);
          }}
          onPointerUp={(event) => event.currentTarget.releasePointerCapture?.(event.pointerId)}
          onPointerCancel={(event) => event.currentTarget.releasePointerCapture?.(event.pointerId)}
          onClick={(event) => event.stopPropagation()}
          onDoubleClick={(event) => {
            event.stopPropagation();
            setMaximizedChainWidth(null);
          }}
          onKeyDown={(event) => {
            if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
            event.preventDefault();
            const bounds = event.currentTarget.closest(".oi-finder-chart-card")?.getBoundingClientRect?.();
            if (!bounds) return;
            const chain = event.currentTarget.parentElement?.querySelector(".oi-finder-chart-maximized-chain");
            const currentWidth = maximizedChainWidth || chain?.getBoundingClientRect?.().width || bounds.width * 0.31;
            const requestedWidth = currentWidth + (event.key === "ArrowLeft" ? 24 : -24);
            const nextWidth = workspaceCompanionWidthAtPointer({
              containerLeft: bounds.left,
              containerWidth: bounds.width,
              pointerX: bounds.left + bounds.width - requestedWidth,
            });
            if (nextWidth != null) setMaximizedChainWidth(nextWidth);
          }}
          title="Drag left or right to resize the chart and option chain. Double-click to reset."
        ><i /></div>
      ) : null}
      {isMaximized && maximizeCompanion && isMaximizedCompanionVisible ? (
        <aside
          className="oi-finder-chart-maximized-chain"'''
assert s.count(OLD) == 1, "chain anchor"
s = s.replace(OLD, NEW)

io.open(p, "w", encoding="utf-8", newline="").write(s)
print("App.jsx: per-panel maximized chain has a divider")
