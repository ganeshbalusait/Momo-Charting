import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { currentTheme, setTheme, subscribeTheme } from "./theme.js";

// The Dark / Light switch. One button that shows what a press will give you
// (sun = go light, moon = go dark).
//  - default: a round icon button in the top strip (desktop and tablet).
//  - variant="menu": a row in the phone's "More" sheet, where the top strip has
//    no room (and is hidden on the chart screen).
// index.html has already marked the page before this mounts; setTheme keeps the
// page, the stored choice and every mounted switch in step.
export default function ThemeSwitch({ variant = "icon" }) {
  const [theme, setThemeState] = useState(() => currentTheme());

  useEffect(() => subscribeTheme(setThemeState), []);

  const isLight = theme === "light";
  const label = isLight ? "Switch to Dark" : "Switch to Light (preview)";
  const Icon = isLight ? Moon : Sun;
  const toggle = () => setTheme(isLight ? "dark" : "light");

  if (variant === "menu") {
    return (
      <button
        className="agx-theme-menu-item"
        data-testid="theme-toggle-menu"
        role="menuitemcheckbox"
        aria-checked={isLight}
        aria-label={label}
        onClick={toggle}
        type="button"
      >
        <Icon size={19} />
        <span>{isLight ? "Dark" : "Light (preview)"}</span>
      </button>
    );
  }
  return (
    <button
      className="header-icon-button agx-theme-toggle"
      data-testid="theme-toggle"
      onClick={toggle}
      aria-pressed={isLight}
      aria-label={label}
      title={label}
      type="button"
    >
      <Icon size={17} />
    </button>
  );
}
