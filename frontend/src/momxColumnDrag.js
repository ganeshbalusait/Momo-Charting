// Drag-to-reorder for the MomX column headers - the DOM-event half, kept out of
// React so it can be tested with fake events (momxColumnDrag.test.js).
//
// Nothing here sets React state while a drag is in flight: the insertion line
// and the dimmed header are attributes set on the two <th> elements directly,
// so dragging over 30 headers never re-renders the board.
//
// Drop reliability (2026-09-22 real-mouse test: 4 of 8 fast drags did nothing):
// the browser only fires `drop` when the element's LAST dragenter/dragover was
// preventDefault'd. Releasing the mouse the instant the pointer crosses into a
// new header sends dragenter and then dragend - no dragover - so dragenter must
// accept the drop too, and `drop` must work out before/after from the pointer
// itself when no dragover got to place the marker.

import { moveKeyTo } from "./momxColumnLayout.js";

export const DROP_ATTR = "data-drop";
export const DRAGGING_CLASS = "is-col-dragging";
// The drag carries ONLY this private type. With text/plain on it, dropping a
// header onto the ticker box typed the column's internal key ("rvol.2h") into
// it; a text box ignores a type it does not understand. Firefox still needs
// SOME data to start a drag, and any type satisfies it.
export const DRAG_MIME = "application/x-momx-column";
// A header click that lands this soon after a drag ended is the drag's own
// mouse-up, not a sort request.
export const CLICK_AFTER_DRAG_MS = 300;

// Which half of the header the pointer is over.
export function dropPlace(clientX, rect) {
  if (!rect || !Number.isFinite(clientX)) return "after";
  return clientX < rect.left + rect.width / 2 ? "before" : "after";
}

export function createColumnDragController({
  getLayout,
  onChange,
  onDragBegin,
  defer = (fn) => setTimeout(fn, 0),
  now = () => Date.now(),
}) {
  // over = the header the insertion line is DRAWN on; target = the header the
  // pointer is over. They differ when the line is pushed past a Time column
  // (see lineHost below).
  const state = { key: null, source: null, over: null, target: null, place: null, endedAt: -Infinity };

  const owns = (key) => {
    const layout = getLayout();
    const order = layout && Array.isArray(layout.order) ? layout.order : [];
    return typeof key === "string" && order.includes(key);
  };

  const clearMarker = () => {
    if (state.over) state.over.removeAttribute(DROP_ATTR);
    state.over = null;
    state.target = null;
    state.place = null;
  };

  // Where the line goes. The Time column is not in the layout and always
  // follows the column it is anchored to, so "after Setup" really lands after
  // Setup's Time: draw the line on the right edge of the last layout-less
  // header that follows, not between Setup and Time.
  const lineHost = (th, place) => {
    let host = th;
    if (place !== "after") return host;
    for (let next = th.nextElementSibling; next && !owns(next.getAttribute("data-col")); next = next.nextElementSibling) {
      host = next;
    }
    return host;
  };

  const finish = () => {
    clearMarker();
    if (state.source) state.source.classList.remove(DRAGGING_CLASS);
    state.source = null;
    state.key = null;
    state.endedAt = now();
  };

  const onDragStart = (event) => {
    const th = event.currentTarget;
    const key = th.getAttribute("data-col");
    if (!owns(key)) {
      event.preventDefault();
      return;
    }
    if (onDragBegin) onDragBegin();
    state.key = key;
    state.source = th;
    try {
      event.dataTransfer.effectAllowed = "move";
      // Firefox will not start a drag without some data.
      event.dataTransfer.setData(DRAG_MIME, key);
    } catch {
      // Some embedded browsers lock dataTransfer; the drag still works.
    }
    // Dim AFTER the browser has taken its drag picture, or the picture is dim too.
    defer(() => {
      if (state.source === th) th.classList.add(DRAGGING_CLASS);
    });
  };

  // Shared by dragenter and dragover: accept the drop and place the marker.
  const onDragTarget = (event) => {
    if (!state.key) return; // a file or text dragged in from elsewhere
    const th = event.currentTarget;
    const key = th.getAttribute("data-col");
    if (!owns(key)) {
      clearMarker();
      return; // no preventDefault -> "can't drop here" cursor
    }
    event.preventDefault();
    try { event.dataTransfer.dropEffect = "move"; } catch { /* locked */ }
    if (key === state.key) {
      clearMarker();
      return;
    }
    const place = dropPlace(event.clientX, th.getBoundingClientRect());
    if (state.target === th && state.place === place) return;
    clearMarker();
    const host = lineHost(th, place);
    host.setAttribute(DROP_ATTR, place);
    state.over = host;
    state.target = th;
    state.place = place;
  };

  const onDragLeave = (event) => {
    const th = event.currentTarget;
    const next = event.relatedTarget;
    if (next && th.contains(next)) return; // moving onto the label inside it
    if (state.target === th) clearMarker();
  };

  const onDrop = (event) => {
    if (!state.key) return;
    event.preventDefault();
    const th = event.currentTarget;
    const target = th.getAttribute("data-col");
    const place = state.target === th && state.place
      ? state.place
      : dropPlace(event.clientX, th.getBoundingClientRect());
    const key = state.key;
    finish();
    if (!owns(target)) return;
    const layout = getLayout();
    const next = moveKeyTo(layout, key, target, place);
    if (next !== layout && onChange) onChange(next);
  };

  const onDragEnd = () => {
    finish();
  };

  // True for the click the browser may fire at the end of a drag.
  const justDragged = () => Boolean(state.key) || now() - state.endedAt < CLICK_AFTER_DRAG_MS;

  return {
    onDragStart,
    onDragEnter: onDragTarget,
    onDragOver: onDragTarget,
    onDragLeave,
    onDrop,
    onDragEnd,
    justDragged,
  };
}
