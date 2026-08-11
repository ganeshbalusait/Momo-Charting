export const OI_CHART_DRAWINGS_STORAGE_KEY = "oiFinderChartDrawingsV1";
export const FIB_RETRACEMENT_LEVELS = Object.freeze([0, 0.236, 0.382, 0.5, 0.618, 0.786, 1]);

const DRAWING_TYPES = new Set([
  "trend",
  "horizontal",
  "fib",
  "rectangle",
  "brush",
  "text",
  "measure",
]);

function normalizePoint(point) {
  const time = Number(point?.time);
  const price = Number(point?.price);
  if (!Number.isFinite(time) || !Number.isFinite(price)) return null;
  return { time, price };
}

export function normalizeChartDrawings(value) {
  if (!Array.isArray(value)) return [];
  return value.flatMap((drawing) => {
    const type = String(drawing?.type || "");
    if (!DRAWING_TYPES.has(type)) return [];
    const points = (Array.isArray(drawing?.points) ? drawing.points : [])
      .map(normalizePoint)
      .filter(Boolean)
      .slice(0, type === "brush" ? 1200 : 2);
    const minimumPoints = ["horizontal", "text"].includes(type) ? 1 : 2;
    if (points.length < minimumPoints) return [];
    return [{
      id: String(drawing.id || `drawing-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`),
      type,
      points,
      color: /^#[0-9a-f]{6}$/i.test(String(drawing.color || "")) ? drawing.color : "#d9e5ec",
      text: type === "text" ? String(drawing.text || "Note").slice(0, 120) : "",
      locked: drawing.locked === true,
      createdAt: Number(drawing.createdAt || Date.now()),
    }];
  });
}

export function chartDrawingScopeKey(symbol, timeframe) {
  return `${String(symbol || "").trim().toUpperCase() || "UNKNOWN"}:${String(timeframe || "5m")}`;
}

export function loadChartDrawings(scopeKey) {
  if (typeof window === "undefined") return [];
  try {
    const store = JSON.parse(window.localStorage.getItem(OI_CHART_DRAWINGS_STORAGE_KEY) || "{}");
    return normalizeChartDrawings(store?.[scopeKey]);
  } catch {
    return [];
  }
}

export function saveChartDrawings(scopeKey, drawings) {
  if (typeof window === "undefined") return false;
  try {
    const parsed = JSON.parse(window.localStorage.getItem(OI_CHART_DRAWINGS_STORAGE_KEY) || "{}");
    const store = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    store[scopeKey] = normalizeChartDrawings(drawings);
    window.localStorage.setItem(OI_CHART_DRAWINGS_STORAGE_KEY, JSON.stringify(store));
    return true;
  } catch {
    return false;
  }
}

export function drawingMeasurement(points, timeframeMinutes = 5, visibleBars = []) {
  if (!Array.isArray(points) || points.length < 2) return null;
  const [start, end] = points;
  const priceDelta = Number(end.price) - Number(start.price);
  const percentDelta = Number(start.price)
    ? (priceDelta / Number(start.price)) * 100
    : 0;
  const seconds = Number(end.time) - Number(start.time);
  let bars = Math.round(seconds / Math.max(1, Number(timeframeMinutes) || 1) / 60);
  if (Array.isArray(visibleBars) && visibleBars.length) {
    const nearestIndex = (targetTime) => {
      let result = -1;
      let distance = Infinity;
      visibleBars.forEach((bar, index) => {
        const nextDistance = Math.abs(Number(bar?.time) - Number(targetTime));
        if (!Number.isFinite(nextDistance) || nextDistance >= distance) return;
        distance = nextDistance;
        result = index;
      });
      return result;
    };
    const startIndex = nearestIndex(start.time);
    const endIndex = nearestIndex(end.time);
    if (startIndex >= 0 && endIndex >= 0) bars = endIndex - startIndex;
  }
  return {
    priceDelta,
    percentDelta,
    bars,
    seconds,
  };
}

export function drawingHasDistinctPoints(points) {
  if (!Array.isArray(points) || points.length < 2) return false;
  const [start, end] = points;
  return Number(start?.time) !== Number(end?.time)
    || Math.abs(Number(start?.price) - Number(end?.price)) > Number.EPSILON;
}

export function fibonacciRetracementPrices(
  points,
  levels = FIB_RETRACEMENT_LEVELS,
) {
  if (!Array.isArray(points) || points.length < 2 || !Array.isArray(levels)) return [];
  const startPrice = Number(points[0]?.price);
  const endPrice = Number(points[1]?.price);
  if (!Number.isFinite(startPrice) || !Number.isFinite(endPrice)) return [];
  return levels.flatMap((level) => {
    const numericLevel = Number(level);
    if (!Number.isFinite(numericLevel)) return [];
    return [{
      level: numericLevel,
      price: startPrice + (endPrice - startPrice) * numericLevel,
    }];
  });
}

export function translateDrawingPoints(points, startPoint, endPoint) {
  if (!Array.isArray(points) || !startPoint || !endPoint) return [];
  const timeDelta = Number(endPoint.time) - Number(startPoint.time);
  const priceDelta = Number(endPoint.price) - Number(startPoint.price);
  if (!Number.isFinite(timeDelta) || !Number.isFinite(priceDelta)) return points;
  return points.map((point) => ({
    time: Number(point.time) + timeDelta,
    price: Number(point.price) + priceDelta,
  }));
}

export function nearestDrawingMagnetPoint({
  bars,
  cursorX,
  cursorY,
  timeToCoordinate,
  priceToCoordinate,
  maxDistancePx = 12,
}) {
  if (!Array.isArray(bars) || !bars.length
    || typeof timeToCoordinate !== "function"
    || typeof priceToCoordinate !== "function") return null;
  const maximumDistance = Math.max(1, Number(maxDistancePx) || 12);
  let nearestPoint = null;
  let nearestDistance = Infinity;
  for (const bar of bars) {
    const barX = Number(timeToCoordinate(Number(bar?.time)));
    if (!Number.isFinite(barX)) continue;
    const horizontalDistance = Math.abs(barX - Number(cursorX));
    if (horizontalDistance > maximumDistance) continue;
    for (const key of ["open", "high", "low", "close"]) {
      const price = Number(bar?.[key]);
      const priceY = Number(priceToCoordinate(price));
      if (!Number.isFinite(price) || !Number.isFinite(priceY)) continue;
      const distance = Math.hypot(
        horizontalDistance,
        priceY - Number(cursorY),
      );
      if (distance >= nearestDistance || distance > maximumDistance) continue;
      nearestDistance = distance;
      nearestPoint = {
        time: Number(bar.time),
        price,
        distance,
      };
    }
  }
  return nearestPoint;
}
