function safeTimeframeMinutes(value) {
  const minutes = Number(value);
  return Number.isFinite(minutes) && minutes > 0 ? minutes : 5;
}

/**
 * Intraday session shading is useful on fine charts, but a PRE/POST caption on
 * every 4-hour candle turns a month of history into a striped wall. Keep the
 * bands as quiet context on 4-hour charts and remove them entirely from
 * day-or-higher charts where an intraday session has no meaningful width.
 */
export function chartSessionWindowsForTimeframe(windows, timeframeMinutes) {
  const source = Array.isArray(windows) ? windows : [];
  const minutes = safeTimeframeMinutes(timeframeMinutes);
  if (minutes >= 1_440) return [];
  if (minutes < 240) return source;
  return source.map((window) => ({ ...window, label: "", subdued: true }));
}

/**
 * Fixed 30/60-minute clock markers cannot all be distinguished once one chart
 * candle spans four hours. At that aggregation retain the two meaningful RTH
 * boundaries per date (open and close), and caption only the newest date.
 */
export function chartSessionLinesForTimeframe(lines, timeframeMinutes) {
  const source = Array.isArray(lines) ? lines : [];
  const minutes = safeTimeframeMinutes(timeframeMinutes);
  if (minutes >= 1_440) return [];
  if (minutes < 240) return source;

  const dates = source.map((line) => String(line?.date || "")).filter(Boolean);
  const newestDate = dates.sort().at(-1) || "";
  return source
    .filter((line) => [570, 960].includes(Number(line?.minute)))
    .map((line) => ({
      ...line,
      label: String(line?.date || "") === newestDate ? String(line?.label || "") : "",
      subdued: true,
    }));
}
