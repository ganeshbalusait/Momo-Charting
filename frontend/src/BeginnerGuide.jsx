import { useState } from "react";

import "./beginnerGuide.css";
import { guideFor } from "./beginnerGuide.js";

// A "What is this page?" box, closed by default after the first visit (remembered in this browser only).
export default function BeginnerGuide({ page }) {
  const guide = guideFor(page);
  const key = `beginnerGuideSeen:${page}`;
  const [open, setOpen] = useState(() => {
    try { return window.localStorage.getItem(key) !== "1"; } catch { return true; }
  });
  if (!guide) return null;
  const toggle = (event) => {
    const now = event.currentTarget.open;
    setOpen(now);
    if (!now) { try { window.localStorage.setItem(key, "1"); } catch { /* private window */ } }
  };
  return (
    <details className="bg-guide" open={open} onToggle={toggle} data-testid="beginner-guide">
      <summary>{guide.title}</summary>
      <dl>
        {guide.steps.map(([term, meaning]) => (
          <div key={term}><dt>{term}</dt><dd>{meaning}</dd></div>
        ))}
      </dl>
      <p className="bg-guide-warn">{guide.warning}</p>
    </details>
  );
}
