export function normalizeLightweightChartSeriesData(data) {
  const byTime = new Map();
  (Array.isArray(data) ? data : []).forEach((item) => {
    const time = Math.floor(Number(item?.time));
    if (!Number.isFinite(time) || time <= 0) return;
    // Keep the newest point for a timestamp. Broker snapshots can overlap the
    // live-forming candle, and several studies project onto the same bucket.
    byTime.set(time, { ...item, time });
  });
  return [...byTime.values()].sort((left, right) => left.time - right.time);
}

export function guardLightweightChartSeriesTree(value, path = "chart", visited = new Set()) {
  if (!value || typeof value !== "object" || visited.has(value)) return;
  visited.add(value);
  if (typeof value.setData === "function") {
    const setData = value.setData.bind(value);
    value.setData = (data) => {
      const normalized = normalizeLightweightChartSeriesData(data);
      try {
        return setData(normalized);
      } catch (error) {
        // A malformed optional study must never unmount the chart workspace.
        // Clear only that series and leave candles/option chain operational.
        console.error(`Skipped invalid ${path} series data.`, error);
        try {
          return setData([]);
        } catch {
          return undefined;
        }
      }
    };
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => guardLightweightChartSeriesTree(item, `${path}[${index}]`, visited));
    return;
  }
  const prototype = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) return;
  Object.entries(value).forEach(([key, item]) => {
    guardLightweightChartSeriesTree(item, `${path}.${key}`, visited);
  });
}
