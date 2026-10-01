const DAY_MS = 24 * 60 * 60 * 1000;

function marketDayTimestamp(value) {
  const day = String(value || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) return NaN;
  return Date.parse(`${day}T12:00:00Z`);
}

export function optionExpiryDte(expiry, asOfDay) {
  const expiryTime = marketDayTimestamp(expiry);
  const asOfTime = marketDayTimestamp(asOfDay);
  if (!Number.isFinite(expiryTime) || !Number.isFinite(asOfTime)) return null;
  return Math.round((expiryTime - asOfTime) / DAY_MS);
}

export function isCurrentOptionExpiry(expiry, asOfDay, maxDte = 31) {
  const dte = optionExpiryDte(expiry, asOfDay);
  return dte != null && dte >= 0 && dte <= Math.max(Number(maxDte) || 0, 0);
}
