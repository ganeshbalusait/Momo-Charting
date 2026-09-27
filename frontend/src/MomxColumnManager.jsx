import { useEffect } from "react";
import { createPortal } from "react-dom";

import { LOCKED_KEYS, moveKey, toggleHidden } from "./momxColumnLayout.js";

// The Columns dialog (2026-09-22: "make columns rearrange move left or right,
// hide and show option also"). Lists every scanner column in its current
// order: ◀ ▶ move it one step, the checkbox shows or hides it, Reset puts the
// default back. Symbol can move but not hide - a row must keep its name.
//
// The layout logic is all in momxColumnLayout.js; this only renders it. Uses
// the FILTERS dialog's overlay / head / body / foot classes for the same
// phone-safe scroll shape (one scroller, no sticky inside it).

// "2h (RVOL)" - the short labels repeat across groups, so the group says which.
function columnName(column) {
  const label = column.kind === "color" ? "COLOR (divider)" : column.label;
  return column.group ? label + " (" + column.group + ")" : label;
}

export default function MomxColumnManager({ columns, layout, onChange, onReset, onClose }) {
  // Escape closes the dialog. Capture phase + stopPropagation, like the grade
  // "why" panel, so the popout-window Escape handler never sees the key.
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      if (onClose) onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const byKey = new Map((Array.isArray(columns) ? columns : []).map((column) => [column.key, column]));
  const order = (layout && Array.isArray(layout.order) ? layout.order : []).filter((key) => byKey.has(key));
  const hidden = new Set(layout && Array.isArray(layout.hidden) ? layout.hidden : []);
  const shownCount = order.filter((key) => !hidden.has(key)).length;

  return createPortal(
    <div className="momx-filters-overlay momx-columns-overlay" onClick={onClose}>
      <div
        className="momx-filters momx-columns"
        role="dialog"
        aria-label="Scanner columns"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="momx-filters-head">
          <span>COLUMNS</span>
          <span className="momx-filters-count">
            {shownCount} of {order.length} shown
          </span>
          <button type="button" onClick={onClose} aria-label="Close">x</button>
        </div>

        <div className="momx-filters-body">
          <p className="momx-filters-note">
            Left to right, as on the board. The same layout is used on the History tab.
            The Time column has its own switch in the toolbar and stays beside the column it follows
            (Setup on Live, Symbol on History). Symbol, and Setup / Time while they sit right
            after it, stay frozen on the left when the board scrolls sideways.
          </p>
          <ol className="momx-columns-list">
            {order.map((key, index) => {
              const column = byKey.get(key);
              const locked = LOCKED_KEYS.has(key);
              const isHidden = hidden.has(key);
              const name = columnName(column);
              return (
                <li
                  key={key}
                  className={"momx-columns-row" + (isHidden ? " is-hidden" : "")}
                  data-col={key}
                >
                  <label className="momx-columns-show" title={locked ? "Symbol is always shown" : undefined}>
                    <input
                      type="checkbox"
                      checked={!isHidden}
                      disabled={locked}
                      onChange={() => onChange(toggleHidden(layout, key))}
                      aria-label={(isHidden ? "Show " : "Hide ") + name}
                    />
                    <span className="momx-columns-name">{name}</span>
                  </label>
                  <span className="momx-columns-moves">
                    <button
                      type="button"
                      className="momx-columns-move"
                      disabled={index === 0}
                      onClick={() => onChange(moveKey(layout, key, -1))}
                      aria-label={"Move " + name + " left"}
                      title="Move left"
                    >
                      ◀
                    </button>
                    <button
                      type="button"
                      className="momx-columns-move"
                      disabled={index === order.length - 1}
                      onClick={() => onChange(moveKey(layout, key, 1))}
                      aria-label={"Move " + name + " right"}
                      title="Move right"
                    >
                      ▶
                    </button>
                  </span>
                </li>
              );
            })}
          </ol>
        </div>

        <footer className="momx-filters-foot">
          <span className="momx-filters-sentence">Saved on this device.</span>
          <button type="button" className="momx-btn" onClick={onReset}>
            Reset to default
          </button>
        </footer>
      </div>
    </div>,
    document.body,
  );
}
