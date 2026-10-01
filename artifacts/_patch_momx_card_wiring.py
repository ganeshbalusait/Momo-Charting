"""Click the lightning bolt, get that ticker's card in its own small window.

2026-09-02: "we can do the pop up where are the Zap it open small window",
plus a screenshot of the card he wants, plus "remove - weekly option, see WFC
screenshot i want same".

The window manager built this morning already does drag, resize, stacking,
per-list geometry and detach. A card is just a second KIND of window, so the
windows array grows a `kind` field rather than growing a second manager
beside the first. One consequence worth stating: raising, moving, closing and
Esc behave identically for cards and boards because it is literally the same
code path.

The bolt itself changes meaning here, from "has weekly options" (badgeOf,
now unused) to "these signals fired" (firesOf over scanReasons), and becomes
a BUTTON. It appears on any row with fires, which on a matches-only board is
every row - the weeklies badge appeared on a minority, which is why it was a
poor handle for "open this ticker".

Card windows do not offer Detach yet: a detached card needs its own
?popout= route and there is no point shipping a button that opens a blank
window. Board windows keep theirs.
"""
import io

p = "frontend/src/MomxScannerPanel.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

# --- imports --------------------------------------------------------------
OLD = 'import { createPortal } from "react-dom";'
NEW = 'import { createPortal } from "react-dom";\n\nimport MomxTickerCard from "./MomxTickerCard.jsx";'
assert s.count(OLD) == 1, "portal import anchor"
s = s.replace(OLD, NEW)

OLD = "  badgeOf,\n"
NEW = "  firesOf,\n"
assert s.count(OLD) == 1, "badgeOf import anchor"
s = s.replace(OLD, NEW)

# --- the two row renderers: the bolt is now the fires, and it is clickable --
OLD_BADGE = '''              {badge ? (
                <span className="momx-badge" title={badge.tooltip}>
                  <Zap size={10} aria-hidden="true" />
                </span>
              ) : null}'''
NEW_LIVE = '''              {fires.length > 0 ? (
                <button
                  type="button"
                  className="momx-badge is-fires"
                  title={"Signals fired: " + fires.join(" · ") + " - click for the full card"}
                  aria-label={"Open the " + (row.symbol || entry.symbol) + " card"}
                  onClick={(event) => {
                    event.stopPropagation();
                    onOpenCard(row.symbol || entry.symbol);
                  }}
                >
                  <Zap size={10} aria-hidden="true" />
                </button>
              ) : null}'''
NEW_STATIC = '''              {fires.length > 0 ? (
                <span className="momx-badge is-fires" title={"Signals fired: " + fires.join(" · ")}>
                  <Zap size={10} aria-hidden="true" />
                </span>
              ) : null}'''
assert s.count(OLD_BADGE) == 2, "expected the live and the static row renderer"
first = s.index(OLD_BADGE)
second = s.index(OLD_BADGE, first + 1)
s = s[:second] + NEW_STATIC + s[second + len(OLD_BADGE):]
s = s[:first] + NEW_LIVE + s[first + len(OLD_BADGE):]

# `badge` was computed per row; it is `fires` now.
assert s.count("const badge = badgeOf(") >= 1, "badge computation anchor"
s = s.replace("const badge = badgeOf(", "const fires = firesOf(")

# --- windows gain a kind --------------------------------------------------
OLD = '''  // The open pop-out windows: [{ id, list, rect, z }]. A LIST, not a boolean -'''
NEW = '''  // The open pop-out windows: [{ id, kind, list, symbol?, rect, z }]. A LIST,
  // not a boolean -'''
assert s.count(OLD) == 1, "windows comment anchor"
s = s.replace(OLD, NEW)

OLD = '''      popoutSeqRef.current += 1;
      popoutTopRef.current += 1;
      return [
        ...windows,
        {
          id: "popout-" + popoutSeqRef.current,
          list,'''
NEW = '''      popoutSeqRef.current += 1;
      popoutTopRef.current += 1;
      return [
        ...windows,
        {
          id: "popout-" + popoutSeqRef.current,
          kind: "board",
          list,'''
assert s.count(OLD) == 1, "board window anchor"
s = s.replace(OLD, NEW)

# --- opening a card -------------------------------------------------------
OLD = '''  // Drag from the title bar, resize from the corner. Pointer capture rather'''
NEW = '''  // One symbol's card, in its own small window. Same window manager as the
  // board windows - a card is a KIND, not a second system - so it drags,
  // resizes, stacks and closes by exactly the same code.
  const onOpenCard = useCallback((symbol) => {
    const wanted = String(symbol || "").trim().toUpperCase();
    if (!wanted) return;
    setPopouts((windows) => {
      const existing = windows.find((w) => w.kind === "card" && w.symbol === wanted);
      if (existing) {
        // Already open: raise it. A second identical card is just clutter.
        popoutTopRef.current += 1;
        const z = popoutTopRef.current;
        return windows.map((w) => (w.id === existing.id ? { ...w, z } : w));
      }
      popoutSeqRef.current += 1;
      popoutTopRef.current += 1;
      const cards = windows.filter((w) => w.kind === "card").length;
      const base = defaultPopoutRect(cards);
      return [
        ...windows,
        {
          id: "card-" + popoutSeqRef.current,
          kind: "card",
          symbol: wanted,
          list: activeList || "",
          // A card is a fixed amount of information, so it gets a fixed,
          // smaller box rather than the board's near-full-screen default.
          rect: clampPopoutRect({ x: base.x, y: base.y, w: 820, h: 470 }),
          z: popoutTopRef.current,
        },
      ];
    });
  }, [activeList]);

  // Drag from the title bar, resize from the corner. Pointer capture rather'''
assert s.count(OLD) == 1, "onOpenCard anchor"
s = s.replace(OLD, NEW)

# --- pass the opener down to the rows -------------------------------------
assert s.count("onNewsToggle={onNewsToggle}") >= 1, "row prop anchor"
s = s.replace("onNewsToggle={onNewsToggle}", "onNewsToggle={onNewsToggle}\n                onOpenCard={onOpenCard}")

# --- render: a card window shows the card, a board window shows the board --
OLD = '''              <b>{win.list || "MomX Scanner"}</b>
              <span>drag to move · corner to resize</span>
              <button
                type="button"
                className="momx-popout-detach"'''
NEW = '''              <b>{win.kind === "card" ? win.symbol : win.list || "MomX Scanner"}</b>
              <span>drag to move · corner to resize</span>
              {win.kind === "card" ? null : (
              <button
                type="button"
                className="momx-popout-detach"'''
assert s.count(OLD) == 1, "titlebar kind anchor"
s = s.replace(OLD, NEW)

OLD = '''                <ExternalLink size={12} aria-hidden="true" />
                detach
              </button>
              <button
                type="button"
                className="momx-popout-close"'''
NEW = '''                <ExternalLink size={12} aria-hidden="true" />
                detach
              </button>
              )}
              <button
                type="button"
                className="momx-popout-close"'''
assert s.count(OLD) == 1, "detach close anchor"
s = s.replace(OLD, NEW)

OLD = '''            <div className="momx-scanner-view">
              <MomxScannerPanel initialList={win.list} embedded />
            </div>'''
NEW = '''            <div className="momx-scanner-view">
              {win.kind === "card" ? (
                // Looked up from the LIVE rows, not copied at open time, so
                // the card keeps refreshing with the board behind it.
                <MomxTickerCard
                  row={rows.find((r) => String(r.symbol || "").toUpperCase() === win.symbol) || null}
                  stampLabel={updatedLabel}
                  nowMs={momentumNow}
                />
              ) : (
                <MomxScannerPanel initialList={win.list} embedded />
              )}
            </div>'''
assert s.count(OLD) == 1, "window body anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("MomxScannerPanel.jsx: the bolt opens a card window")
