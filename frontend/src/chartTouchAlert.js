export const CHART_TOUCH_ALERT_HOLD_MS = 550;
export const CHART_TOUCH_ALERT_MOVE_TOLERANCE_PX = 10;

/**
 * A mobile alert hold must yield to an intentional pan or pinch. Using radial
 * distance also catches diagonal movement without making horizontal and
 * vertical drags feel different.
 */
export function touchAlertGestureShouldCancel(
  startX,
  startY,
  currentX,
  currentY,
  tolerance = CHART_TOUCH_ALERT_MOVE_TOLERANCE_PX,
) {
  const points = [startX, startY, currentX, currentY].map(Number);
  const safeTolerance = Number(tolerance);
  if (points.some((value) => !Number.isFinite(value))) return true;
  if (!Number.isFinite(safeTolerance) || safeTolerance < 0) return true;
  return Math.hypot(points[2] - points[0], points[3] - points[1]) > safeTolerance;
}
