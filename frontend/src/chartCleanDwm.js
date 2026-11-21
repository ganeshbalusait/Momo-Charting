// Clean Daily / Weekly / Monthly charts (Ganesh, 2026-11-21: "I want to see
// only charts M/W/D with tickers and fib and ema and patterns if I want I will
// enable it and I will save the chart layout"; he chose "Clean single chart").
//
// On a D, W or M chart every study starts OFF except the candles' colours,
// EMA 9 / 21 / 50, the Big swing Fib and the Chart pattern. Once per
// timeframe: a saved D/W/M profile that carries CLEAN_DWM_VERSION (he ticked
// what he wanted and pressed Save) is left exactly as he saved it.

export const CLEAN_DWM_VERSION = "clean-dwm-v1";

// What stays on. tosCandleColors / candleCyanMagentaOnly are the candles
// themselves ("tickers"), not a study.
export const CLEAN_DWM_KEEP = Object.freeze([
  "ema9",
  "ema21",
  "ema50",
  "fibSwing",
  "chartPattern",
  "tosCandleColors",
  "candleCyanMagentaOnly",
]);

const DWM_KEYS = new Set(["D", "W", "M"]);

export function isDwmTimeframe(timeframeKey) {
  return DWM_KEYS.has(String(timeframeKey || ""));
}

// Every on/off study in `defaults` set off, except CLEAN_DWM_KEEP (on).
export function cleanDwmSettings(defaults = {}) {
  const out = { cleanDwmVersion: CLEAN_DWM_VERSION };
  Object.entries(defaults || {}).forEach(([key, value]) => {
    if (typeof value === "boolean") out[key] = CLEAN_DWM_KEEP.includes(key);
  });
  CLEAN_DWM_KEEP.forEach((key) => { out[key] = true; });
  return out;
}

// For a saved profile of `timeframeKey`: the clean set, or {} when the
// timeframe is not D/W/M or he has already saved this timeframe since.
export function migrateCleanDwm(saved, timeframeKey, defaults = {}) {
  if (!isDwmTimeframe(timeframeKey)) return {};
  if (saved && typeof saved === "object" && saved.cleanDwmVersion === CLEAN_DWM_VERSION) return {};
  return cleanDwmSettings(defaults);
}
