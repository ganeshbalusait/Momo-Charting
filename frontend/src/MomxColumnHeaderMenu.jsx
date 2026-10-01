import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { createColumnDragController } from "./momxColumnDrag.js";
import { LOCKED_KEYS, moveKeyTo, pinnedRun, showAll, toggleHidden, visibleNeighbor } from "./momxColumnLayout.js";

// Column headers you can move with the mouse (2026-09-22: "I don't see column
// rearrange left or right and hide column also. Rearrange using mouse.").
//
//   * Drag a header left or right; a cyan line shows where it will land
//     (before / after the header under the pointer, by which half of it the
//     pointer is over). Drop = move + save. Esc or dropping off the headers
//     changes nothing.
//   * Right-click a header: Hide column / Move left / Move right / Show all.
//
// Both write the SAME layout the Columns dialog edits (momxColumnLayout.js).
//
// The drag logic lives in momxColumnDrag.js (tested with fake events). It sets
// no React state mid-drag; the only state here is the open right-click menu,
// and the handlers are stable functions that read the column key from the
// header's data-col attribute.

export function useColumnHeaderControls(layout, onLayoutChange) {
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const changeRef = useRef(onLayoutChange);
  changeRef.current = onLayoutChange;
  const [menu, setMenu] = useState(null);

  // One controller per panel for its whole life, so the header handlers keep
  // their identity and never re-render the memoized rows.
  const controller = useRef(null);
  if (!controller.current) {
    const ctl = createColumnDragController({
      getLayout: () => layoutRef.current,
      onChange: (next) => { if (changeRef.current) changeRef.current(next); },
      onDragBegin: () => setMenu(null),
    });
    const onContextMenu = (event) => {
      const key = event.currentTarget.getAttribute("data-col");
      if (!key) return;
      event.preventDefault();
      setMenu({ key, x: event.clientX, y: event.clientY });
    };
    controller.current = {
      headerHandlers: {
        onDragStart: ctl.onDragStart,
        onDragEnter: ctl.onDragEnter,
        onDragOver: ctl.onDragOver,
        onDragLeave: ctl.onDragLeave,
        onDrop: ctl.onDrop,
        onDragEnd: ctl.onDragEnd,
        onContextMenu,
      },
      justDragged: ctl.justDragged,
    };
  }

  const closeMenu = useCallback(() => setMenu(null), []);

  return { headerHandlers: controller.current.headerHandlers, justDragged: controller.current.justDragged, menu, closeMenu };
}

// Frozen columns (2026-09-22: "frozen column symbol + setup + time").
//
// pinnedRun picks the block (Symbol + the Setup / Time columns right after
// it). Each pinned column i sticks at left: var(--momx-pin-i), where
// --momx-pin-0 is 0 and each next one is the running sum of the pinned
// header widths. Those widths are MEASURED (a ResizeObserver on the pinned
// <th>s only) and written straight onto the <table> as CSS custom
// properties - no React state, so resizing or scrolling never re-renders the
// memoized rows. The rows themselves are untouched: the body cells are
// pinned by CSS from the table's data-pin-1 / data-pin-2 / data-pin-last
// attributes matched against each cell's existing data-col.
export const PIN_SLOTS = 3;

export function usePinnedColumns(columns) {
  const tableRef = useRef(null);
  const pinned = useMemo(() => pinnedRun(columns), [columns]);
  const signature = pinned.join("|");
  const watched = useRef({ table: null, signature: null, observer: null });

  const tableAttrs = useMemo(() => {
    const attrs = {};
    if (pinned.length) attrs["data-pin-0"] = pinned[0];
    for (let i = 1; i < Math.min(pinned.length, PIN_SLOTS); i += 1) attrs["data-pin-" + i] = pinned[i];
    if (pinned.length) attrs["data-pin-last"] = pinned[Math.min(pinned.length, PIN_SLOTS) - 1];
    return attrs;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  // No deps on purpose: cheap identity checks every render, so a remounted
  // <table> (or a different pinned set) is re-measured; otherwise nothing.
  useLayoutEffect(() => {
    const table = tableRef.current;
    const seen = watched.current;
    if (seen.table === table && seen.signature === signature) return;
    if (seen.observer) seen.observer.disconnect();
    seen.observer = null;
    seen.table = table;
    seen.signature = signature;
    if (!table) return;
    const keys = signature ? signature.split("|").slice(0, PIN_SLOTS) : [];
    const heads = () =>
      keys.map((key) => {
        for (const th of table.querySelectorAll("thead .momx-head-row th[data-col]")) {
          if (th.getAttribute("data-col") === key) return th;
        }
        return null;
      });
    const write = () => {
      const ths = heads();
      let offset = 0;
      for (let i = 0; i < PIN_SLOTS; i += 1) {
        if (i < keys.length) {
          table.style.setProperty("--momx-pin-" + i, offset + "px");
          offset += ths[i] ? ths[i].getBoundingClientRect().width : 0;
        } else {
          table.style.removeProperty("--momx-pin-" + i);
        }
      }
    };
    write();
    if (typeof ResizeObserver !== "function") return;
    const observer = new ResizeObserver(write);
    heads().forEach((th) => { if (th) observer.observe(th); });
    seen.observer = observer;
  });
  useEffect(() => () => {
    if (watched.current.observer) watched.current.observer.disconnect();
  }, []);

  const pinIndex = useMemo(() => new Map(pinned.slice(0, PIN_SLOTS).map((key, i) => [key, i])), [pinned]);
  // Header / group-row cell props: class + left offset for a pinned key, or
  // null for a scrolling one.
  const headPin = useCallback(
    (key) => {
      const i = pinIndex.get(key);
      if (i === undefined) return null;
      const last = i === pinIndex.size - 1;
      return { className: last ? "momx-pinned momx-pinned-last" : "momx-pinned", style: { left: "var(--momx-pin-" + i + ", 0px)" } };
    },
    [pinIndex],
  );

  return { tableRef, tableAttrs, headPin, pinned };
}

// "2h (RVOL)" - the same naming as the Columns dialog.
function columnName(column) {
  if (!column) return "";
  const label = column.kind === "color" ? "COLOR" : column.label;
  return column.group ? label + " (" + column.group + ")" : label;
}

const MARGIN = 6;

export default function MomxColumnHeaderMenu({ menu, columns, layout, onChange, onClose }) {
  const ref = useRef(null);
  const [pos, setPos] = useState(null);

  // Open at the pointer, pulled back inside the window near an edge.
  useLayoutEffect(() => {
    if (!menu || !ref.current) return;
    const box = ref.current.getBoundingClientRect();
    const left = Math.max(MARGIN, Math.min(menu.x, window.innerWidth - box.width - MARGIN));
    const top = Math.max(MARGIN, Math.min(menu.y, window.innerHeight - box.height - MARGIN));
    setPos({ left, top });
  }, [menu]);

  // Esc, a click anywhere else, a scroll, a resize or leaving the window close
  // it. Escape is captured + stopped so the popout-window Escape handler never
  // sees it (same as the Columns dialog).
  useEffect(() => {
    if (!menu) return undefined;
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      onClose();
    };
    const onPointer = (event) => {
      if (ref.current && ref.current.contains(event.target)) return;
      onClose();
    };
    const onScroll = (event) => {
      if (ref.current && ref.current.contains(event.target)) return;
      onClose();
    };
    window.addEventListener("keydown", onKey, true);
    window.addEventListener("mousedown", onPointer, true);
    window.addEventListener("touchstart", onPointer, { capture: true, passive: true });
    window.addEventListener("scroll", onScroll, true);
    window.addEventListener("resize", onClose);
    window.addEventListener("blur", onClose);
    return () => {
      window.removeEventListener("keydown", onKey, true);
      window.removeEventListener("mousedown", onPointer, true);
      window.removeEventListener("touchstart", onPointer, { capture: true });
      window.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("resize", onClose);
      window.removeEventListener("blur", onClose);
    };
  }, [menu, onClose]);

  if (!menu) return null;

  const list = Array.isArray(columns) ? columns : [];
  const column = list.find((c) => c && c.key === menu.key);
  const owned = layout && Array.isArray(layout.order) && layout.order.includes(menu.key);
  const canHide = owned && !LOCKED_KEYS.has(menu.key);
  const left = visibleNeighbor(list, layout, menu.key, -1);
  const right = visibleNeighbor(list, layout, menu.key, 1);
  const anyHidden = Boolean(layout && Array.isArray(layout.hidden) && layout.hidden.length);

  const act = (next) => {
    onClose();
    if (next && next !== layout) onChange(next);
  };

  const hideTitle = !owned
    ? "The Time column has its own switch in the toolbar"
    : LOCKED_KEYS.has(menu.key)
      ? "Symbol is always shown"
      : undefined;

  return createPortal(
    <div
      ref={ref}
      className="momx-colmenu"
      role="menu"
      aria-label={"Column " + columnName(column)}
      style={pos ? { left: pos.left, top: pos.top } : { left: menu.x, top: menu.y, visibility: "hidden" }}
      onContextMenu={(event) => event.preventDefault()}
      data-testid="momx-colmenu"
    >
      <div className="momx-colmenu-title">{columnName(column) || "Column"}</div>
      <button
        type="button"
        role="menuitem"
        className="momx-colmenu-item"
        disabled={!canHide}
        title={hideTitle}
        onClick={() => act(toggleHidden(layout, menu.key))}
      >
        Hide column
      </button>
      <button
        type="button"
        role="menuitem"
        className="momx-colmenu-item"
        disabled={!left}
        onClick={() => act(moveKeyTo(layout, menu.key, left, "before"))}
      >
        ◀ Move left
      </button>
      <button
        type="button"
        role="menuitem"
        className="momx-colmenu-item"
        disabled={!right}
        onClick={() => act(moveKeyTo(layout, menu.key, right, "after"))}
      >
        Move right ▶
      </button>
      <div className="momx-colmenu-sep" role="separator" />
      <button
        type="button"
        role="menuitem"
        className="momx-colmenu-item"
        disabled={!anyHidden}
        title={anyHidden ? undefined : "No columns are hidden"}
        onClick={() => act(showAll(layout))}
      >
        Show all columns
      </button>
    </div>,
    document.body,
  );
}
