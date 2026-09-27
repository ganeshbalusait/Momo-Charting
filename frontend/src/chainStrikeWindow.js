// Keep an ascending option chain centered on ATM. Numeric depths mean that
// many listed strikes on EACH side; "all" (or another non-numeric value)
// deliberately returns the complete listed chain.
export function limitChainRowsAroundAtm(rows, pivotStrike, depth) {
  const list = Array.isArray(rows) ? rows : [];
  const perSide = Number(depth);
  if (!Number.isFinite(perSide) || perSide <= 0) return list;
  const pivot = Number(pivotStrike);
  if (!Number.isFinite(pivot) || pivot <= 0) return list;
  const below = list.filter((row) => Number(row?.strike) < pivot);
  const at = list.filter((row) => Number(row?.strike) === pivot);
  const above = list.filter((row) => Number(row?.strike) > pivot);
  return [...below.slice(-perSide), ...at, ...above.slice(0, perSide)];
}
