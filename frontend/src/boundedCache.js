export function setBoundedCacheEntry(
  cache,
  key,
  value,
  maxEntries,
  isProtected = () => false,
) {
  if (!(cache instanceof Map)) return value;
  const limit = Math.max(1, Math.floor(Number(maxEntries) || 1));
  cache.delete(key);
  cache.set(key, value);
  while (cache.size > limit) {
    const evictable = [...cache.entries()].find(([candidateKey, candidateValue]) => (
      candidateKey !== key && !isProtected(candidateValue)
    ));
    if (!evictable) break;
    cache.delete(evictable[0]);
  }
  return value;
}
