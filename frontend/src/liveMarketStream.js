function normalizeSymbol(value) {
  return String(value || "").trim().toUpperCase();
}

function parsePacket(event) {
  try {
    const packet = JSON.parse(event?.data || "{}");
    return packet && typeof packet === "object" ? packet : null;
  } catch {
    return null;
  }
}

export function liveNumber(value, fallback = null) {
  if (value == null || value === "") return fallback;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function liveEquityPrice(packet, fallback = null) {
  const quote = packet?.data && typeof packet.data === "object" ? packet.data : {};
  const bid = liveNumber(quote.bid);
  const ask = liveNumber(quote.ask);
  const midpoint = Number.isFinite(bid) && Number.isFinite(ask) ? (bid + ask) / 2 : null;
  return liveNumber(quote.last, liveNumber(quote.mark, midpoint ?? fallback));
}

export function mergeLiveOptionRows(rows, packetOrPackets) {
  if (!Array.isArray(rows)) return rows;
  const packets = Array.isArray(packetOrPackets) ? packetOrPackets : [packetOrPackets];
  const updates = new Map(packets.flatMap((packet) => {
    const symbol = normalizeSymbol(packet?.symbol);
    return symbol ? [[symbol, packet]] : [];
  }));
  if (!updates.size) return rows;

  let changed = false;
  const nextRows = rows.map((row) => {
    const packet = updates.get(normalizeSymbol(row?.symbol));
    if (!packet) return row;
    const quote = packet?.data && typeof packet.data === "object" ? packet.data : {};
    const volume = liveNumber(quote.totalVolume, row.volume);
    const openInterest = liveNumber(quote.openInterest, row.open_interest);
    const nextQuote = {
      bid: liveNumber(quote.bid, row.bid),
      ask: liveNumber(quote.ask, row.ask),
      last: liveNumber(quote.last, row.last),
      mark: liveNumber(quote.mark, row.mark),
      volume,
      open_interest: openInterest,
      volume_oi_ratio: Number.isFinite(Number(volume))
        ? Number((Number(volume) / Math.max(Number(openInterest || 0), 1)).toFixed(2))
        : row.volume_oi_ratio,
      delta: Math.abs(liveNumber(quote.delta, row.delta) || 0),
      gamma: liveNumber(quote.gamma, row.gamma),
      theta: liveNumber(quote.theta, row.theta),
      vega: liveNumber(quote.vega, row.vega),
    };
    const quoteChanged = Object.entries(nextQuote).some(([key, value]) => !Object.is(row?.[key], value));
    if (!quoteChanged) return row;
    changed = true;
    return {
      ...row,
      ...nextQuote,
      liveQuoteAt: packet.receivedAt || new Date().toISOString(),
    };
  });
  return changed ? nextRows : rows;
}

export function createLiveMarketStreamHub({
  createEventSource,
  schedule,
  cancel,
  reconnectDelayMs = 120,
  // Browsers allow only 6 concurrent HTTP/1.1 connections per origin. The
  // 397-ticker watchlist at 64 symbols/source opened 7 EventSources — the
  // overflow never connected and the held sockets starved every other fetch.
  // 200/source keeps the full universe at 2 connections.
  maxSymbolsPerSource = 200,
  // A server restart drops every EventSource without always firing `error`,
  // so a tab could sit silently subscribed-but-dead: the chart then showed
  // only 30s REST refreshes and read as a "1 minute delay". Any live symbol
  // set should produce traffic well inside this window (status keepalives
  // alone are ~15s), so silence past it means the socket is gone.
  silenceTimeoutMs = 30_000,
  now = () => Date.now(),
} = {}) {
  const sourceFactory = createEventSource || ((url) => (
    typeof globalThis.EventSource === "function" ? new globalThis.EventSource(url) : null
  ));
  const scheduleTask = schedule || ((callback, delay) => globalThis.setTimeout(callback, delay));
  const cancelTask = cancel || ((handle) => globalThis.clearTimeout(handle));
  const subscribers = new Map();
  let sources = [];
  let connectedSignature = "";
  let reconnectHandle = null;
  let lastPacketAt = 0;
  let watchdogHandle = null;

  const activeSymbols = () => [...subscribers.entries()]
    .filter(([, listeners]) => listeners.size)
    .map(([symbol]) => symbol)
    .sort();

  const listenerSetForPacket = (eventType, packet, sourceSymbols = []) => {
    if (eventType === "status") {
      return new Set(sourceSymbols.flatMap((symbol) => [...(subscribers.get(symbol) || [])]));
    }
    const target = eventType === "option"
      ? normalizeSymbol(packet?.underlying)
      : normalizeSymbol(packet?.symbol);
    return new Set(subscribers.get(target) || []);
  };

  const closeSources = () => {
    const previous = sources;
    sources = [];
    connectedSignature = "";
    previous.forEach(({ source }) => source?.close?.());
  };

  const stopWatchdog = () => {
    if (watchdogHandle != null) cancelTask(watchdogHandle);
    watchdogHandle = null;
  };

  const runWatchdog = () => {
    watchdogHandle = null;
    if (!activeSymbols().length) return;
    if (Number(now()) - lastPacketAt > silenceTimeoutMs) {
      // Silently dead socket: rebuild it. connect() early-returns when the
      // signature is unchanged, so the sources must be dropped first.
      closeSources();
      connect();
    }
    startWatchdog();
  };

  const startWatchdog = () => {
    if (watchdogHandle != null) return;
    watchdogHandle = scheduleTask(runWatchdog, Math.max(1_000, Math.floor(silenceTimeoutMs / 2)));
  };

  const connect = () => {
    reconnectHandle = null;
    const symbols = activeSymbols();
    const signature = symbols.join(",");
    if (signature === connectedSignature && sources.length) return;
    closeSources();
    if (!signature) {
      stopWatchdog();
      return;
    }
    connectedSignature = signature;
    // A fresh connection starts its silence window now, so the watchdog does
    // not immediately tear down a socket that simply has not spoken yet.
    lastPacketAt = Number(now());
    startWatchdog();
    const chunkSize = Math.max(1, Math.floor(Number(maxSymbolsPerSource) || 64));
    for (let index = 0; index < symbols.length; index += chunkSize) {
      const sourceSymbols = symbols.slice(index, index + chunkSize);
      const sourceSignature = sourceSymbols.join(",");
      const nextSource = sourceFactory(`/api/live-market-stream?symbols=${encodeURIComponent(sourceSignature)}`);
      if (!nextSource) continue;
      sources.push({ source: nextSource, symbols: sourceSymbols });

      ["status", "equity", "chart", "option"].forEach((eventType) => {
        nextSource.addEventListener(eventType, (event) => {
          if (!sources.some(({ source }) => source === nextSource)) return;
          const packet = parsePacket(event);
          if (!packet) return;
          // Any traffic proves the socket is alive, including status frames.
          lastPacketAt = Number(now());
          listenerSetForPacket(eventType, packet, sourceSymbols).forEach((handlers) => {
            handlers?.[eventType]?.(packet);
          });
        });
      });
      nextSource.onerror = () => {
        if (!sources.some(({ source }) => source === nextSource)) return;
        const listeners = new Set(sourceSymbols.flatMap((symbol) => [...(subscribers.get(symbol) || [])]));
        listeners.forEach((handlers) => handlers?.error?.());
      };
    }
  };

  const scheduleConnection = () => {
    if (reconnectHandle != null) cancelTask(reconnectHandle);
    reconnectHandle = scheduleTask(connect, reconnectDelayMs);
  };

  const subscribe = (symbol, handlers = {}) => {
    const target = normalizeSymbol(symbol);
    if (!target) return () => {};
    const listeners = subscribers.get(target) || new Set();
    listeners.add(handlers);
    subscribers.set(target, listeners);
    scheduleConnection();
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      const current = subscribers.get(target);
      current?.delete(handlers);
      if (!current?.size) subscribers.delete(target);
      scheduleConnection();
    };
  };

  const close = () => {
    if (reconnectHandle != null) cancelTask(reconnectHandle);
    reconnectHandle = null;
    stopWatchdog();
    subscribers.clear();
    closeSources();
  };

  return { activeSymbols, close, subscribe };
}

/**
 * One batched one-second REST safety net for the charts currently on screen.
 * The Schwab socket remains authoritative; chart components ignore these
 * packets while recent Level-1 events are arriving. A shared poller prevents a
 * four-chart grid from making four separate quote requests every two seconds.
 */
export function createLiveChartQuotePoller({
  requestJson,
  schedule,
  cancel,
  now = () => Date.now(),
  intervalMs = 1_000,
} = {}) {
  const fetchJson = requestJson || (async (url) => {
    const response = await globalThis.fetch(url);
    if (!response.ok) throw new Error(`Quote fallback failed (${response.status}).`);
    return response.json();
  });
  const scheduleTask = schedule || ((callback, delay) => globalThis.setTimeout(callback, delay));
  const cancelTask = cancel || ((handle) => globalThis.clearTimeout(handle));
  const subscribers = new Map();
  let timer = null;
  let inFlight = false;

  const activeSymbols = () => [...subscribers.entries()]
    .filter(([, listeners]) => listeners.size)
    .map(([symbol]) => symbol)
    .sort();

  const schedulePoll = (delay = intervalMs) => {
    if (timer != null || inFlight || !subscribers.size) return;
    timer = scheduleTask(poll, Math.max(0, Number(delay) || 0));
  };

  const poll = async () => {
    timer = null;
    const symbols = activeSymbols();
    if (!symbols.length || inFlight) return;
    inFlight = true;
    const startedAt = Number(now());
    try {
      const payload = await fetchJson(`/api/live-chart-quotes?symbols=${encodeURIComponent(symbols.join(","))}`);
      const receivedMillis = Number(now());
      const receivedAt = new Date(receivedMillis).toISOString();
      (Array.isArray(payload?.rows) ? payload.rows : []).forEach((row) => {
        const symbol = normalizeSymbol(row?.symbol);
        const price = liveNumber(row?.lastPrice);
        if (!symbol || !Number.isFinite(price) || price <= 0) return;
        const packet = {
          symbol,
          receivedAt,
          data: {
            source: "schwab-rest-1s",
            last: price,
            mark: price,
            quoteTime: receivedMillis,
            tradeTime: receivedMillis,
          },
        };
        (subscribers.get(symbol) || []).forEach((listener) => listener(packet));
      });
    } catch {
      // A transient REST failure must never disturb a healthy stream candle.
    } finally {
      inFlight = false;
      // Keep a one-second start-to-start cadence. Adding the HTTP duration to
      // the interval can stretch a 1s safety poll beyond the chart's 2s SLA
      // whenever Schwab takes a few hundred milliseconds to answer.
      const elapsed = Math.max(0, Number(now()) - startedAt);
      schedulePoll(Math.max(0, intervalMs - elapsed));
    }
  };

  const subscribe = (symbol, listener) => {
    const target = normalizeSymbol(symbol);
    if (!target || typeof listener !== "function") return () => {};
    const listeners = subscribers.get(target) || new Set();
    listeners.add(listener);
    subscribers.set(target, listeners);
    schedulePoll(0);
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      const current = subscribers.get(target);
      current?.delete(listener);
      if (!current?.size) subscribers.delete(target);
      if (!subscribers.size && timer != null) {
        cancelTask(timer);
        timer = null;
      }
    };
  };

  const close = () => {
    if (timer != null) cancelTask(timer);
    timer = null;
    subscribers.clear();
  };

  return { activeSymbols, close, subscribe };
}

const sharedLiveMarketStream = createLiveMarketStreamHub();
const sharedLiveChartQuotePoller = createLiveChartQuotePoller();

export function subscribeLiveMarketStream(symbol, handlers) {
  return sharedLiveMarketStream.subscribe(symbol, handlers);
}

export function subscribeLiveChartQuoteFallback(symbol, listener) {
  return sharedLiveChartQuotePoller.subscribe(symbol, listener);
}
