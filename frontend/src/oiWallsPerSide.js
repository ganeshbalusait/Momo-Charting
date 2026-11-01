import { useEffect, useState } from "react";

// How many OI walls per side (calls above / puts below) every chart draws.
// 2026-09-29 (his ask): "show only the top 10 walls each side" with a 3/5/10
// picker in the option chain. One shared preference for every chart and
// timeframe, so the chain picker and the chart settings never disagree.
export const OI_WALLS_PER_SIDE_OPTIONS = Object.freeze([3, 5, 10]);
export const OI_WALLS_PER_SIDE_DEFAULT = 10;
export const OI_WALLS_PER_SIDE_STORAGE_KEY = "oiChartWallsPerSide";
export const OI_WALLS_PER_SIDE_EVENT = "oi-chart-walls-per-side";

export function normalizeOiWallsPerSide(value) {
  const numeric = Number(value);
  return OI_WALLS_PER_SIDE_OPTIONS.includes(numeric) ? numeric : OI_WALLS_PER_SIDE_DEFAULT;
}

export function readOiWallsPerSide() {
  try {
    return normalizeOiWallsPerSide(localStorage.getItem(OI_WALLS_PER_SIDE_STORAGE_KEY));
  } catch {
    return OI_WALLS_PER_SIDE_DEFAULT;
  }
}

export function writeOiWallsPerSide(value) {
  const next = normalizeOiWallsPerSide(value);
  try {
    localStorage.setItem(OI_WALLS_PER_SIDE_STORAGE_KEY, String(next));
  } catch {
    // Storage blocked: the change still applies to this window via the event.
  }
  window.dispatchEvent(new CustomEvent(OI_WALLS_PER_SIDE_EVENT, { detail: next }));
  return next;
}

export function useOiWallsPerSide() {
  const [value, setValue] = useState(readOiWallsPerSide);
  useEffect(() => {
    const sync = (event) => {
      if (event.type === "storage" && event.key !== OI_WALLS_PER_SIDE_STORAGE_KEY) return;
      setValue(event.type === "storage" ? readOiWallsPerSide() : normalizeOiWallsPerSide(event.detail));
    };
    window.addEventListener(OI_WALLS_PER_SIDE_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(OI_WALLS_PER_SIDE_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  return value;
}
