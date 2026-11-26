// A section that opens with one tap (2026-11-26, his "Refer deep-research see other bot pages and do it").
// The research report's rule for a phone: a few things first, the rest one step away. What is inside
// stays on the page (only hidden), so nothing it loads or reports changes. An opened section is remembered.
import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { FOLDS_KEY, foldIsOpen, readFolds, toggleFold } from "./botHome.js";

const stored = () => { try { return readFolds(window.localStorage.getItem(FOLDS_KEY)); } catch { return readFolds(null); } };

export default function Fold({ id, title, note, when = true, children }) {
  const [open, setOpen] = useState(() => foldIsOpen(stored(), id));
  if (!when) return null;
  const flip = () => {
    const next = toggleFold(stored(), id);
    try { window.localStorage.setItem(FOLDS_KEY, JSON.stringify(next)); } catch { /* private window */ }
    setOpen(foldIsOpen(next, id));
  };
  return (
    <section className={"bf" + (open ? " is-open" : "")} data-testid={"fold-" + id}>
      <button type="button" className="bf-head" aria-expanded={open} aria-controls={"fold-body-" + id} onClick={flip}>
        <span className="bf-text">
          <span className="bf-title">{title}</span>
          {note ? <span className="bf-note">{note}</span> : null}
        </span>
        <ChevronDown size={18} aria-hidden="true" className="bf-chev" />
      </button>
      <div className="bf-body" id={"fold-body-" + id} hidden={!open}>{children}</div>
    </section>
  );
}
