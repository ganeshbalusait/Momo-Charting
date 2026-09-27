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
  // VOLUME ON THE FORMING CANDLE.
  //
  // This used to be `fromTrade ? Number(existing?.volume || 0) : ...`, which
  // stamped a hard 0 on every minute a trade tick opened - App.jsx's trade call
  // passes only {time, close}, so `existing` is null and Number(undefined || 0)
  // is 0. The strip then read "Vol 0" over a bar the server had at 22,821
  // (QQQ 08:10 ET 2026-09-04), and because that 0 is a finite number rather
  // than null, the "Vol --" guard shipped in 6ea4932 could never fire for it.
  //
  // The real number is already on the wire and was simply unused: Schwab's
  // Level-1 equity packet carries totalVolume, the running DAY cumulative
  // (verified live 2026-09-04: NVDA 134,527,448 -> ...449 -> ...472 -> ...492
  // across four seconds). The volume traded inside this minute is therefore
  // today's total minus whatever it stood at when the minute opened.
  //
  // dayVolumeSeen on the PREVIOUS bar is the better baseline than this tick's
  // own total: anchoring on the first tick of a minute silently discards that
  // trade's own size. Falling back to the tick's total is still right the first
  // time we ever see a symbol, it just undercounts one print.
  //
  // A chart packet (fromTrade false) carries the authoritative per-minute
  // volume Schwab computed, so it always wins over anything derived here.
  // NOT finiteNumber() for these. finiteNumber takes the first candidate whose
  // Number() is finite, and Number(null) is 0 - so finiteNumber(null, 100)
  // returns 0, not 100. That is the same coercion that made the ticker rail
  // print a confident 0.00% (973469a) and the OHLC strip print Chg 0.00%
  // (6ea4932); it silently zeroed an existing volume here too, and the
  // pre-existing tests in this file caught it. Explicit null checks only.
  const asNumber = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
  };
  const dayVolume = asNumber(incoming?.totalVolume);
  const priorAnchor = existing
    ? asNumber(existing.dayVolumeAtOpen)
    : asNumber(last?.dayVolumeSeen);
  const dayVolumeAtOpen = priorAnchor !== null ? priorAnchor : dayVolume;
  let volume;
  if (fromTrade) {
    const derived =
      dayVolume !== null && dayVolumeAtOpen !== null
        ? Math.max(0, dayVolume - dayVolumeAtOpen)
        : null;
    if (derived === null) {
      volume = asNumber(existing?.volume);
    } else if (existing) {
      // Never go backwards: a late or out-of-order packet must not shrink a
      // volume already on screen.
      volume = Math.max(derived, asNumber(existing.volume) ?? 0);
    } else {
      volume = derived;
    }
  } else {
    const authoritative = asNumber(incoming?.volume);
    volume = authoritative !== null ? authoritative : asNumber(existing?.volume);
  }
  const nextBar = {
    time,
    open,
    high,
    low,
    close,
    // Still `?? 0` at the boundary, deliberately: Lightweight Charts treats a
    // null `value` on a histogram point as a broken number and rejects the
    // whole series, which would blank the chart rather than blank one cell.
    // The unknown case is carried by volumeKnown instead.
    volume: volume == null ? 0 : volume,
    volumeKnown: volume != null,
    dayVolumeAtOpen: dayVolumeAtOpen == null ? undefined : dayVolumeAtOpen,
    dayVolumeSeen: dayVolume == null ? existing?.dayVolumeSeen : dayVolume,
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
// How far ahead of the newest REST bar a live quote may still be treated as
// the forming candle. Beyond this the tape is stale, not the quote early:
// admitting the quote drew a lone O=H=L=C / Vol 0 bar hours past the last real
// candle, and the future whitespace projected from it rendered as a wide empty
// band with a disconnected spike - the "gap" seen on any watchlist ticker
// whose cache had not been rebuilt during the current session.
export const MAX_LIVE_BAR_GAP_SECONDS = 2 * 3_600;

export function shouldUseEquityTradeForChart({
  equityTime,
  latestBarTime,
} = {}) {
  const equityMinute = Math.floor(Number(equityTime || 0) / 60) * 60;
  const latestMinute = Math.floor(Number(latestBarTime || 0) / 60) * 60;
  if (!Number.isFinite(equityMinute) || equityMinute <= 0) return false;
  // No tape yet: nothing to be disconnected from, and refusing would leave a
  // cold chart permanently blank.
  if (!(latestMinute > 0)) return true;
  if (equityMinute < latestMinute) return false;
  return equityMinute - latestMinute <= MAX_LIVE_BAR_GAP_SECONDS;
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
