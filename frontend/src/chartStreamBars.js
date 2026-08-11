function finiteNumber(...values) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return null;
}

/**
 * Reconcile a REST candle tape with the ref-owned live tail.
 *
 * REST is authoritative for the forming candle's open/high/low. A stale live
 * buffer may have been seeded from a previous ticker or delayed snapshot, so
 * allowing it to replace the same REST minute can leave a giant false wick
 * forever. Preserve only the newer live close/cumulative volume for that
 * minute; strictly newer live minutes remain intact.
 */
export function reconcileRestBarsWithLiveTail(restBars, liveBars) {
  const rest = Array.isArray(restBars) ? restBars : [];
  const live = Array.isArray(liveBars) ? liveBars : [];
  if (!rest.length) return live.slice();

  const merged = new Map(rest.map((bar) => [Number(bar.time), bar]));
  const restLastTime = Number(rest.at(-1)?.time || 0);
  live.forEach((bar) => {
    const time = Number(bar?.time || 0);
    if (!Number.isFinite(time) || time < restLastTime) return;
    if (time > restLastTime) {
      merged.set(time, bar);
      return;
    }

    const restBar = merged.get(time);
    if (!restBar) return;
    const close = finiteNumber(bar?.close, restBar?.close);
    if (close == null) return;
    const open = finiteNumber(restBar?.open, close);
    const high = Math.max(
      ...[restBar?.high, open, close].map(Number).filter(Number.isFinite),
    );
    const low = Math.min(
      ...[restBar?.low, open, close].map(Number).filter(Number.isFinite),
    );
    const restVolume = finiteNumber(restBar?.volume, 0) ?? 0;
    const liveVolume = finiteNumber(bar?.volume, 0) ?? 0;
    merged.set(time, {
      ...restBar,
      open,
      high,
      low,
      close,
      volume: Math.max(restVolume, liveVolume),
    });
  });
  return [...merged.values()].sort((left, right) => left.time - right.time);
}

/**
 * Merge one live packet into a ref-owned candle buffer.
 *
 * Updates to the forming candle replace only the last array slot. A new array
 * is allocated solely when a new minute is appended, eliminating full-history
 * copies on every quote while keeping React-owned arrays separate.
 */
export function mergeLatestStreamBar(
  currentBars,
  incoming,
  fromTrade = false,
  preserveExistingClose = false,
) {
  const current = Array.isArray(currentBars) ? currentBars : [];
  const time = Math.floor(Number(incoming?.time || 0) / 60) * 60;
  if (!Number.isFinite(time) || time <= 0) return { bars: current, changed: false, appended: false };
  const last = current.at(-1);
  if (last && time < Number(last.time)) return { bars: current, changed: false, appended: false };
  const existing = last && Number(last.time) === time ? last : null;
  // Callers can preserve an already accepted close while still accepting a
  // cumulative volume update from a delayed packet.
  const close = preserveExistingClose && existing
    ? finiteNumber(existing?.close, incoming?.close)
    : finiteNumber(incoming?.close, existing?.close);
  if (close == null) return { bars: current, changed: false, appended: false };
  // A chart snapshot whose close is already known to be stale cannot be
  // trusted for its open/high/low either. Otherwise its old price range leaves
  // a giant false wick even though the newer trade close itself is preserved.
  const open = preserveExistingClose && existing
    ? finiteNumber(existing?.open, close)
    : finiteNumber(incoming?.open, existing?.open, close);
  const high = fromTrade || (preserveExistingClose && existing)
    ? Math.max(
      ...[existing?.high, ...(preserveExistingClose ? [] : [incoming?.high]), close]
        .map(Number)
        .filter(Number.isFinite),
    )
    : finiteNumber(incoming?.high, existing?.high, close);
  const low = fromTrade || (preserveExistingClose && existing)
    ? Math.min(
      ...[existing?.low, ...(preserveExistingClose ? [] : [incoming?.low]), close]
        .map(Number)
        .filter(Number.isFinite),
    )
    : finiteNumber(incoming?.low, existing?.low, close);
  const nextBar = {
    time,
    open,
    high,
    low,
    close,
    volume: fromTrade
      ? Number(existing?.volume || 0)
      : finiteNumber(incoming?.volume, existing?.volume, 0),
  };
  if (existing) {
    current[current.length - 1] = nextBar;
    return { bars: current, changed: true, appended: false };
  }
  return { bars: [...current, nextBar], changed: true, appended: true };
}

/**
 * Accept a Level-1 trade whenever its market minute is at least as new as the
 * candle already displayed. Schwab can repeatedly deliver CHART_EQUITY for a
 * completed minute more than a minute late; receive time therefore cannot be
 * used to suppress a newer trade.
 */
export function shouldUseEquityTradeForChart({
  equityTime,
  latestBarTime,
} = {}) {
  const equityMinute = Math.floor(Number(equityTime || 0) / 60) * 60;
  const latestMinute = Math.floor(Number(latestBarTime || 0) / 60) * 60;
  return Number.isFinite(equityMinute)
    && equityMinute > 0
    && (!(latestMinute > 0) || equityMinute >= latestMinute);
}

/**
 * Keep the visible chart on one consolidated writer: Schwab/TOS.
 * Schwab CHART_EQUITY packets predate source tagging, so an empty source is
 * accepted; every explicitly tagged non-Schwab provider is rejected.
 */
export function isSchwabTosChartPacket(packet) {
  const source = String(packet?.data?.source || "").trim().toLowerCase();
  return !source || source === "schwab" || source.startsWith("schwab-");
}
