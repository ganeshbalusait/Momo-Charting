// Incremental chart refresh: merges a server delta payload (recent tape tail
// plus live scalars) over the full payload the browser already holds, so
// periodic reconciles cost a few KB instead of re-downloading ~2MB of tape.

// fineStudyBars is the 60-day five-minute tape; the server sends only its tail
// since the cutoff (mirrored in _delta_chart_payload), so it must be merged like
// bars - copying it whole left the browser holding 1 row instead of 13,806.
const SERIES_KEYS = new Set(["bars", "studyBars", "fineStudyBars", "dailyBars"]);
const CONTROL_KEYS = new Set(["delta", "deltaSince"]);

// A live quote can move the last displayed bar far beyond downloaded candle
// history. It must never advance the watermark used to request missing bars.
export function chartHistoryBarsThrough(payload) {
  const last = Number(payload?.bars?.at(-1)?.time || 0);
  const known = Number(payload?.historyBarsThrough);
  return Number.isFinite(known) && known > 0 ? Math.min(known, last) : last;
}

export function updateChartPayloadWithQuote(payload, price, time) {
  const bars = payload?.bars;
  const last = bars?.at(-1);
  if (!last || !(price > 0) || !Number.isFinite(price)
      || !Number.isFinite(time) || time < Number(last.time)) return payload;
  const next = Number(last.time) === time
    ? { ...last, high: Math.max(Number(last.high ?? price), price),
      low: Math.min(Number(last.low ?? price), price), close: price }
    : { time, open: price, high: price, low: price, close: price, volume: 0, volumeKnown: false };
  return { ...payload, historyBarsThrough: chartHistoryBarsThrough(payload),
    bars: Number(last.time) === time ? [...bars.slice(0, -1), next] : [...bars, next] };
}

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
  const delivered = Number(delta.bars?.at(-1)?.time || 0);
  merged.historyBarsThrough = Math.max(chartHistoryBarsThrough(base), delivered);
  merged.studyBars = mergeSeriesTail(base.studyBars, delta.studyBars, since);
  merged.fineStudyBars = mergeSeriesTail(base.fineStudyBars, delta.fineStudyBars, since);
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
  return chartHistoryBarsThrough(cachedPayload);
}

export function chartDeltaTapeChanged(base, delta) {
  const baseStamp = String(base?.signalTapeUpdatedAt || "");
  const deltaStamp = String(delta?.signalTapeUpdatedAt || "");
  return Boolean(baseStamp && deltaStamp && baseStamp !== deltaStamp);
}
