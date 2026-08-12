const finiteNumber = (value) => {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
};

const signalKey = (signal) => [
  String(signal?.family || ""),
  String(signal?.timeframe || "").toUpperCase(),
  Math.floor(Number(signal?.candleTimestamp || signal?.time || 0)),
].join("|");

export function mtfSignalVisualSignature(signals) {
  return (Array.isArray(signals) ? signals : [])
    .map((signal) => [
      signalKey(signal),
      Math.floor(Number(signal?.time || 0)),
      String(signal?.label || ""),
      String(signal?.direction || ""),
      signal?.isLiveSignal === true ? "live" : "closed",
    ].join(":"))
    .sort()
    .join(";");
}

/**
 * Reconcile the one active TOS EMA signal per forming secondary candle.
 *
 * The server supplies the preceding completed EMA values. Every Schwab trade
 * can then update the forming EMA 4/8 (or 9/20) in constant time without
 * rebuilding 60 days of studies. Markers are created, relabelled, or removed
 * immediately; completed-candle markers remain immutable.
 */
export function reconcileLiveMtfSignals(currentSignals, liveContexts, update) {
  const eventTime = Math.floor(Number(update?.time || 0));
  const price = finiteNumber(update?.price);
  if (eventTime <= 0 || price == null) return Array.isArray(currentSignals) ? currentSignals : [];

  const contexts = (Array.isArray(liveContexts) ? liveContexts : []).filter((context) => {
    const start = finiteNumber(context?.candleTimestamp);
    const minutes = finiteNumber(context?.signalMinutes);
    return start != null && start > 0 && minutes != null && minutes > 0;
  });
  if (!contexts.length) return Array.isArray(currentSignals) ? currentSignals : [];

  const contextByKey = new Map(contexts.map((context) => [signalKey(context), context]));
  const next = [];
  (Array.isArray(currentSignals) ? currentSignals : []).forEach((signal) => {
    if (signal?.streamManaged === false) {
      // The optional price/EMA-body reclaim trial is reconciled from the
      // five-second REST study snapshot. Preserve it between snapshots; the
      // next response removes it if the forming reclaim no longer exists.
      next.push(signal);
      return;
    }
    if (signal?.isLiveSignal !== true) {
      next.push(signal);
      return;
    }
    const context = contextByKey.get(signalKey(signal));
    const candleStart = finiteNumber(context?.candleTimestamp);
    const signalMinutes = finiteNumber(context?.signalMinutes);
    if (!context || candleStart == null || signalMinutes == null) return;
    if (eventTime >= candleStart + signalMinutes * 60) {
      next.push({
        ...signal,
        updatedAt: Math.max(Number(signal?.updatedAt || 0), eventTime - 1),
        liveForming: false,
        isLiveSignal: false,
        isCandleClosed: true,
      });
    }
    // An unexpired marker is rebuilt below from the newest live price. If the
    // crossover reversed, no replacement is added and it disappears.
  });

  contexts.forEach((context) => {
    const candleStart = finiteNumber(context.candleTimestamp);
    const signalMinutes = finiteNumber(context.signalMinutes);
    if (
      candleStart == null
      || signalMinutes == null
      || eventTime < candleStart
      || eventTime >= candleStart + signalMinutes * 60
    ) return;

    const fastLength = finiteNumber(context.fastLength);
    const slowLength = finiteNumber(context.slowLength);
    const baseFast = finiteNumber(context.baseFastEma);
    const baseSlow = finiteNumber(context.baseSlowEma);
    const higherBaseFast = finiteNumber(context.higherBaseFastEma);
    const higherBaseSlow = finiteNumber(context.higherBaseSlowEma);
    if (
      fastLength == null
      || slowLength == null
      || baseFast == null
      || baseSlow == null
      || higherBaseFast == null
      || higherBaseSlow == null
    ) return;

    const fastAlpha = 2 / (fastLength + 1);
    const slowAlpha = 2 / (slowLength + 1);
    const fastEma = fastAlpha * price + (1 - fastAlpha) * baseFast;
    const slowEma = slowAlpha * price + (1 - slowAlpha) * baseSlow;
    const baseDifference = baseFast - baseSlow;
    const difference = fastEma - slowEma;
    let direction = "";
    if (baseDifference <= 0 && difference > 0) direction = "CALL";
    if (baseDifference >= 0 && difference < 0) direction = "PUT";
    if (!direction) return;

    const higherFastEma = fastAlpha * price + (1 - fastAlpha) * higherBaseFast;
    const higherSlowEma = slowAlpha * price + (1 - slowAlpha) * higherBaseSlow;
    const higherBullish = higherFastEma >= higherSlowEma;
    const confirmed = higherBullish === (direction === "CALL");
    const prefix = confirmed ? direction : direction === "CALL" ? "C" : "P";
    const existing = (Array.isArray(currentSignals) ? currentSignals : []).find((signal) => (
      signal?.isLiveSignal === true && signalKey(signal) === signalKey(context)
    ));

    next.push({
      ...existing,
      family: context.family,
      color: context.color,
      timeframe: context.timeframe,
      confirmationTimeframe: context.confirmationTimeframe,
      time: Math.floor(Number(existing?.time || eventTime) / 60) * 60,
      candleTimestamp: Math.floor(candleStart),
      updatedAt: eventTime,
      direction,
      streamManaged: true,
      signalBasis: "ema_cross",
      label: `${prefix}${context.timeframe}`,
      compact: prefix === "C" || prefix === "P",
      price,
      fastEma,
      slowEma,
      higherFastEma,
      higherSlowEma,
      liveForming: true,
      isLiveSignal: true,
      isCandleClosed: false,
      secondaryBucketStart: false,
      tosPrimaryBarProjection: true,
      aggregationAnchor: context.aggregationAnchor || "TOS_START_DAY_ET",
    });
  });

  const unique = new Map();
  next.forEach((signal) => unique.set(signalKey(signal), signal));
  return [...unique.values()].sort((left, right) => (
    Number(left?.time || 0) - Number(right?.time || 0)
    || String(left?.family || "").localeCompare(String(right?.family || ""))
    || String(left?.timeframe || "").localeCompare(String(right?.timeframe || ""))
  ));
}
