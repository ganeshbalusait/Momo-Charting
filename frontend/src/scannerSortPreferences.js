const PREFIX = "momx.scanner.sort.v1:";
export function readScannerSort(scope, allowedKeys, fallback, storage) {
  try {
    const value = JSON.parse((storage ?? window.localStorage).getItem(PREFIX + scope));
    if (value?.direction === "none" && value.key === null) return { key: null, direction: "none" };
    if (value && allowedKeys.includes(value.key) && ["asc", "desc"].includes(value.direction)) {
      return { key: value.key, direction: value.direction };
    }
  } catch { /* Missing, blocked or outdated preferences use the normal default. */ }
  return fallback;
}
export function writeScannerSort(scope, value, storage) {
  try { (storage ?? window.localStorage).setItem(PREFIX + scope, JSON.stringify(value)); }
  catch { /* Sorting still works when storage is unavailable. */ }
}
