// MomoX names a price alert after whatever level it lands on — "Alert @ pDH
// 222.27", "Alert @ High OI 217.50" — and shows a bare price everywhere else.
// Every drawn level already flows through addIndicatorName into the axis-label
// definitions, so those are the authoritative snap source: no level geometry is
// recomputed here, which keeps the alert name and the drawn label from drifting
// apart when a study's price changes.

export const PRICE_ALERT_SNAP_PIXELS = 8;

// The alert chips and the live-price chip are deliberately not snap targets.
// Snapping a new alert onto an existing alert's label would stack two alerts on
// one price, and snapping to the live price would arm an alert that fires the
// instant it is created.
function isSnapTarget(definition) {
  const key = String(definition?.key || "");
  return !key.startsWith("price-alert-") && key !== "live-price";
}

export function priceAlertLevelTitle(definition) {
  // OI wall titles carry size and expiry ("85k 10/16"). That is useful on the
  // price axis but reads as noise inside an alert chip, and MomoX labels every
  // one of them High OI regardless of which strike it is.
  if (String(definition?.key || "").startsWith("oi-wall-")) return "High OI";
  return String(definition?.title || "").trim();
}

/**
 * Nearest named chart level to a pointer position, or null when the click is in
 * open space. `priceToCoordinate` is the series' own projection, so the snap
 * tolerance stays a constant number of screen pixels at every zoom level —
 * snapping by price distance instead would grab levels far off-screen when the
 * price scale is tight and grab nothing when it is loose.
 */
export function findPriceAlertLevel(
  definitions,
  pointerY,
  priceToCoordinate,
  snapPixels = PRICE_ALERT_SNAP_PIXELS,
) {
  if (typeof priceToCoordinate !== "function") return null;
  const targetY = Number(pointerY);
  if (!Number.isFinite(targetY)) return null;
  return (Array.isArray(definitions) ? definitions : [])
    .filter(isSnapTarget)
    .flatMap((definition) => {
      const label = priceAlertLevelTitle(definition);
      const price = Number(definition?.price);
      if (!label || !Number.isFinite(price) || price <= 0) return [];
      const coordinate = Number(priceToCoordinate(price));
      if (!Number.isFinite(coordinate)) return [];
      const distance = Math.abs(coordinate - targetY);
      return distance <= snapPixels ? [{ label, price, distance }] : [];
    })
    .sort((left, right) => left.distance - right.distance)[0] || null;
}

/**
 * MomoX-style chip text: "Alert @ 222.24" or "Alert @ pDH 222.27". Paused and
 * triggered alerts keep the same shape so the chip never changes width class
 * mid-session, only its leading word and colour.
 */
export function priceAlertChipText(alert = {}) {
  const price = Number(alert?.price);
  if (!Number.isFinite(price)) return "";
  const level = String(alert?.levelLabel || "").trim();
  const prefix = alert?.status === "triggered"
    ? "Triggered"
    : alert?.enabled === false ? "Paused" : "Alert";
  return `${prefix} @ ${level ? `${level} ` : ""}${price.toFixed(2)}`;
}
