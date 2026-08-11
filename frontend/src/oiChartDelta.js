// Incremental chart refresh: merges a server delta payload (recent tape tail
// plus live scalars) over the full payload the browser already holds, so
// periodic reconciles cost a few KB instead of re-downloading ~2MB of tape.

const SERIES_KEYS = new Set(["bars", "studyBars", "dailyBars"]);
const CONTROL_KEYS = new Set(["delta", "deltaSince"]);

function mergeSeriesTail(baseRows, deltaRows, since) {
  const cut = Number(since) || 0;
  const base = Array.isArray(baseRows) ? baseRows : [];
  const tail = Array.isArray(deltaRows) ? deltaRows : [];
  if (!tail.length) return base;
  const kept = base.filter((row) => Number(row?.time || 0) < cut);
  return [...kept, ...tail];
}

export function mergeChartDeltaPayload(base, delta) {
  if (!base || typeof base !== "object" || !Array.isArray(base.bars) || !base.bars.length) return null;
  if (!delta || delta.delta !== true) return null;
  const since = Number(delta.deltaSince) || 0;

  const merged = { ...base };
  for (const [key, value] of Object.entries(delta)) {
    if (SERIES_KEYS.has(key) || CONTROL_KEYS.has(key)) continue;
    merged[key] = value;
  }
  merged.bars = mergeSeriesTail(base.bars, delta.bars, since);
  merged.studyBars = mergeSeriesTail(base.studyBars, delta.studyBars, since);
  const dailyTail = Array.isArray(delta.dailyBars) ? delta.dailyBars : [];
  if (dailyTail.length) {
    const firstTime = Number(dailyTail[0]?.time || 0);
    merged.dailyBars = [
      ...(Array.isArray(base.dailyBars) ? base.dailyBars : []).filter((row) => Number(row?.time || 0) < firstTime),
      ...dailyTail,
    ];
  }
  delete merged.delta;
  delete merged.deltaSince;
  return merged;
}

// A delta refresh is only valid when the browser holds a complete tape and
// the server's heavy signal tapes have not been rebuilt since.
export function chartDeltaRequestTime(cachedPayload, { forceFullTape = false } = {}) {
  if (forceFullTape) return 0;
  if (!cachedPayload || cachedPayload.historyLoading !== false) return 0;
  const bars = Array.isArray(cachedPayload.bars) ? cachedPayload.bars : [];
  if (bars.length < 500) return 0;
  return Number(bars[bars.length - 1]?.time || 0) || 0;
}

export function chartDeltaTapeChanged(base, delta) {
  const baseStamp = String(base?.signalTapeUpdatedAt || "");
  const deltaStamp = String(delta?.signalTapeUpdatedAt || "");
  return Boolean(baseStamp && deltaStamp && baseStamp !== deltaStamp);
}
