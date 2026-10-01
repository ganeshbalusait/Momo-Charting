// Helpers for the "Auto Alert" left panel on Charts & OI. The server
// (api_server.py + oi_auto_alerts.py) owns the ladders, the 5-minute-close
// confirmation and the event feed; the panel only renders, polls and turns
// unseen events into sound / browser notifications.

export const OI_AUTO_ALERT_SYMBOL_PATTERN = /^[A-Z][A-Z0-9.\-]{0,9}$/;
export const OI_AUTO_ALERT_POLL_RTH_MS = 10_000;
export const OI_AUTO_ALERT_POLL_IDLE_MS = 30_000;

export function normalizeOiAutoAlertSymbol(value) {
  const symbol = String(value || "").trim().toUpperCase();
  return OI_AUTO_ALERT_SYMBOL_PATTERN.test(symbol) ? symbol : "";
}

function toNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

export function compactOiAmount(value) {
  const numeric = Math.abs(toNumber(value));
  if (numeric >= 1_000_000) return `${(numeric / 1_000_000).toFixed(numeric >= 10_000_000 ? 0 : 1).replace(/\.0$/, "")}M`;
  if (numeric >= 1_000) return `${(numeric / 1_000).toFixed(numeric >= 100_000 ? 0 : 1).replace(/\.0$/, "")}K`;
  return String(Math.round(numeric));
}

export function strikeText(value) {
  const numeric = toNumber(value);
  return Number.isInteger(numeric) ? String(numeric) : numeric.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

function expirySuffix(value) {
  const key = String(value || "").slice(0, 10);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(key)) return "";
  return ` · ${Number(key.slice(5, 7))}/${Number(key.slice(8, 10))}`;
}

export function oiAutoAlertLevelText(level) {
  if (!level || !Number.isFinite(Number(level.strike))) return "--";
  const strength = String(level.strength || "").trim();
  return `${strikeText(level.strike)} · ${compactOiAmount(level.openInterest)} OI${strength ? ` · ${strength}` : ""}${expirySuffix(level.expiry)}`;
}

export function oiAutoAlertDistanceText(target) {
  if (!target) return "";
  const distance = Number(target.distance);
  const percent = Number(target.distancePercent);
  if (!Number.isFinite(distance) || !Number.isFinite(percent)) return "";
  return `$${distance.toFixed(2)} / ${percent.toFixed(2)}% away`;
}

export function oiAutoAlertSideSummary(state) {
  switch (String(state || "")) {
    case "touched":
      return { label: "Touched · not confirmed", tone: "touched" };
    case "confirmed":
      return { label: "5m close confirmed", tone: "confirmed" };
    case "done":
      return { label: "Ladder complete", tone: "done" };
    case "empty":
      return { label: "No OI level", tone: "empty" };
    default:
      return { label: "Armed", tone: "armed" };
  }
}

// Server events arrive newest-first. Return the unseen ones oldest-first so a
// burst of two events plays its tones in the order they happened. A missing
// seen-set means the panel has not primed itself yet: nothing is "new".
export function findNewOiAutoAlertEvents(events, seenIds) {
  if (!Array.isArray(events) || !seenIds || typeof seenIds.has !== "function") return [];
  return events
    .filter((event) => event && event.id && !seenIds.has(event.id))
    .slice()
    .reverse();
}

export function splitOiAutoAlertMessage(message) {
  const text = String(message || "").trim();
  if (!text) return { title: "OI auto alert", detail: "" };
  const [title, ...rest] = text.split(" · ");
  return { title: title.trim(), detail: rest.join(" · ").trim() };
}

export function oiAutoAlertPollDelay(regularHours) {
  return regularHours ? OI_AUTO_ALERT_POLL_RTH_MS : OI_AUTO_ALERT_POLL_IDLE_MS;
}

// ---------------------------------------------------------------------------
// Mirror into the manual price-alert list.
// The server owns the ladders; the browser mirrors, per Auto Alert ticker,
// the CURRENT target on each side (2 lines per ticker: next call level
// above, next put level below) plus today's confirmed levels as triggered
// entries. That way the existing chart chips ("Alert @ Auto CALL OI 345"),
// the top-bar bell badge and its list all show the auto alerts without a
// second alert system. Records carry `source: "auto"` so the tick evaluator
// skips them (only a completed 5-minute close, judged server-side, fires them).
// ---------------------------------------------------------------------------
export const OI_AUTO_ALERT_MIRROR_SOURCE = "auto";

function mirrorId(symbol, side, sessionDate, strike) {
  return `auto:${symbol}:${side}:${sessionDate || "na"}:${strikeText(strike)}`;
}

function isoMs(value) {
  const stamp = Date.parse(String(value || ""));
  return Number.isFinite(stamp) ? stamp : 0;
}

// Nearest un-confirmed wall on each side of a LIVE price: the call the trader
// watches for a break is the nearest wall above price, the put the nearest
// below. Recomputed as price moves so the chart lines never sit on the wrong
// side of price (the overnight build froze them at the pre-market spot). The
// server still fires the alert only on a completed 5-minute close.
export function activeLevelsForPrice(row, price) {
  const live = Number(price);
  const callLevels = Array.isArray(row?.callLevels) ? row.callLevels : [];
  const putLevels = Array.isArray(row?.putLevels) ? row.putLevels : [];
  if (!Number.isFinite(live) || live <= 0) {
    return { activeCall: row?.activeCall || callLevels[0] || null, activePut: row?.activePut || putLevels[0] || null };
  }
  // Every wall is a level, whichever side's list it was built into: a call
  // wall the price has risen above is now support (AMZN 265 with price 265.61
  // is the PUT/breakdown level, not a skipped line), and a put wall price has
  // fallen under is resistance. Search both lists for the nearest wall above
  // and below price, deduped by strike (larger OI wins).
  const byStrike = new Map();
  [...callLevels, ...putLevels].forEach((level) => {
    const strike = Number(level?.strike);
    if (!Number.isFinite(strike) || strike <= 0) return;
    const existing = byStrike.get(strike);
    if (!existing || Number(level?.openInterest || 0) > Number(existing?.openInterest || 0)) byStrike.set(strike, level);
  });
  const allLevels = [...byStrike.values()];
  // A level that already fired today is still the level in front of price: TSLA
  // confirmed 342.5 at 09:50, then price fell back to 340, so 342.5 is again
  // the nearest wall above and must stay the drawn call line (skipping to 345
  // hid the level the trader is actually watching). Confirmation only controls
  // whether the SERVER re-fires an alert, never what the chart shows.
  const above = allLevels.filter((level) => Number(level.strike) > live);
  const below = allLevels.filter((level) => Number(level.strike) <= live);
  // Nearest wall above price is the call trigger, nearest at/below is the put
  // trigger; if price ran past every wall on a side, keep the outermost one.
  const ascending = (list) => [...list].sort((a, b) => Number(a.strike) - Number(b.strike));
  const activeCall = above.length
    ? ascending(above)[0]
    : ascending(allLevels)[allLevels.length - 1] || null;
  const activePut = below.length
    ? ascending(below)[below.length - 1]
    : ascending(allLevels)[0] || null;
  const nextCall = activeCall
    ? ascending(allLevels).find((level) => Number(level.strike) > Number(activeCall.strike)) || null
    : null;
  const nextPut = activePut
    ? ascending(allLevels).reverse().find((level) => Number(level.strike) < Number(activePut.strike)) || null
    : null;
  return { activeCall, activePut, nextCall, nextPut };
}

export function buildOiAutoAlertMirror(payload, { dismissedIds = new Set(), livePrices = null } = {}) {
  const rows = Array.isArray(payload?.rows) ? payload.rows : [];
  const events = Array.isArray(payload?.events) ? payload.events : [];
  const enabled = payload?.enabled !== false;
  const records = [];
  const seen = new Set();
  const priceFor = (symbol) => {
    if (!livePrices) return 0;
    const entry = typeof livePrices.get === "function" ? livePrices.get(symbol) : livePrices[symbol];
    const value = Number(entry && typeof entry === "object" ? entry.price : entry);
    return Number.isFinite(value) && value > 0 ? value : 0;
  };
  const push = (record) => {
    if (!record || seen.has(record.id) || dismissedIds.has(record.id)) return;
    seen.add(record.id);
    records.push(record);
  };

  rows.forEach((row) => {
    const symbol = String(row?.symbol || "").toUpperCase();
    if (!symbol || row?.status === "unavailable") return;
    const sessionDate = String(row?.sessionDate || "").slice(0, 10);
    const levelsAt = isoMs(row?.levelsUpdatedAt) || Date.now();
    // Prefer the live price so the drawn call/put sit on the right side of it.
    const livePrice = priceFor(symbol);
    const live = livePrice > 0 ? activeLevelsForPrice(row, livePrice) : null;
    [["CALL", "activeCall", "touchedCallStrikes", "nextCall"], ["PUT", "activePut", "touchedPutStrikes", "nextPut"]].forEach(([side, activeKey, touchedKey, nextKey]) => {
      const active = live ? live[activeKey] : row?.[activeKey];
      const strike = Number(active?.strike);
      if (!active || !Number.isFinite(strike) || strike <= 0) return;
      const touched = (Array.isArray(row?.[touchedKey]) ? row[touchedKey] : []).some((value) => Number(value) === strike);
      const next = live ? live[nextKey] : row?.[nextKey];
      const nextText = next && Number.isFinite(Number(next.strike)) ? ` · next ${strikeText(next.strike)}` : " · last level in ladder";
      push({
        id: mirrorId(symbol, side, sessionDate, strike),
        source: OI_AUTO_ALERT_MIRROR_SOURCE,
        symbol,
        price: strike,
        condition: side === "CALL" ? "above" : "below",
        levelLabel: touched ? `Auto ${side} OI · touched` : `Auto ${side} OI`,
        note: `${compactOiAmount(active.openInterest)} OI · ${String(active.strength || "").trim() || "level"}${nextText}`,
        enabled,
        status: "active",
        createdAt: levelsAt,
        triggeredAt: null,
        triggeredPrice: null,
        hideOnChart: false,
        autoSide: side,
        autoState: touched ? "touched" : "armed",
        autoSessionDate: sessionDate,
        autoNextStrike: next && Number.isFinite(Number(next.strike)) ? Number(next.strike) : null,
      });
    });
  });

  // Today's confirmations become triggered entries: the bell badge counts
  // them and its list says which ticker/level fired and what comes next.
  events
    .filter((event) => event && event.kind === "confirm")
    .forEach((event) => {
      const symbol = String(event.symbol || "").toUpperCase();
      const row = rows.find((item) => String(item?.symbol || "").toUpperCase() === symbol);
      if (!row) return;
      const sessionDate = String(row?.sessionDate || "").slice(0, 10);
      const eventDate = String(event.at || event.recordedAt || "").slice(0, 10);
      if (sessionDate && eventDate && eventDate !== sessionDate) return;
      const side = String(event.side || "CALL").toUpperCase();
      const crossed = Array.isArray(event.crossedLevels) && event.crossedLevels.length ? event.crossedLevels : [event.level || {}];
      const nextText = event.nextTarget?.strike != null ? ` → next ${strikeText(event.nextTarget.strike)}` : " · ladder complete";
      crossed.forEach((level) => {
        const strike = Number(level?.strike);
        if (!Number.isFinite(strike) || strike <= 0) return;
        push({
          id: mirrorId(symbol, side, sessionDate, strike),
          source: OI_AUTO_ALERT_MIRROR_SOURCE,
          symbol,
          price: strike,
          condition: side === "CALL" ? "above" : "below",
          levelLabel: `Auto ${side} OI`,
          note: `5m close ${Number(event.price || 0).toFixed(2)}${nextText}`,
          enabled: false,
          status: "triggered",
          createdAt: isoMs(event.at || event.recordedAt),
          triggeredAt: isoMs(event.at || event.recordedAt),
          triggeredPrice: Number(event.price) || strike,
          hideOnChart: true,
          autoSide: side,
          autoState: "confirmed",
          autoSessionDate: sessionDate,
          autoNextStrike: event.nextTarget?.strike != null ? Number(event.nextTarget.strike) : null,
          autoEventId: event.id,
        });
      });
    });

  return records;
}

// Merge the mirror into the saved list: manual alerts untouched, auto records
// replaced wholesale. Returns null when nothing changed so callers can skip
// the storage write (and the chart rebuild it triggers).
export function mergeOiAutoAlertMirror(currentAlerts, mirrorRecords) {
  const current = Array.isArray(currentAlerts) ? currentAlerts : [];
  const manual = current.filter((alert) => alert?.source !== OI_AUTO_ALERT_MIRROR_SOURCE);
  const existingAuto = current.filter((alert) => alert?.source === OI_AUTO_ALERT_MIRROR_SOURCE);
  const next = Array.isArray(mirrorRecords) ? mirrorRecords : [];
  const signature = (list) => JSON.stringify(list.map((alert) => [
    alert.id, alert.price, alert.condition, alert.levelLabel, alert.note, alert.enabled, alert.status, alert.triggeredAt, alert.hideOnChart, alert.autoState,
  ]));
  if (signature(existingAuto) === signature(next)) return null;
  return [...manual, ...next];
}

export const OI_AUTO_ALERT_RECENT_MS = 30 * 60 * 1000;

function eventTime(event) {
  const stamp = Date.parse(String(event?.at || event?.recordedAt || ""));
  return Number.isFinite(stamp) ? stamp : 0;
}

// The newest touch/confirm on either side of a ticker's ladder.
export function oiAutoAlertRowLastEvent(row) {
  const candidates = [row?.lastCallEvent, row?.lastPutEvent].filter((event) => event && event.id);
  if (!candidates.length) return null;
  return candidates.sort((left, right) => eventTime(right) - eventTime(left))[0];
}

// Short answer to "which one fired?": "CALL 345 confirmed → next 350",
// "PUT 340 broken → next 330", "CALL 345 touched · not confirmed".
export function oiAutoAlertEventLabel(event) {
  if (!event) return "";
  const side = String(event.side || "CALL").toUpperCase();
  const strikes = (Array.isArray(event.crossedLevels) && event.crossedLevels.length
    ? event.crossedLevels
    : [event.level || {}]).map((level) => strikeText(level?.strike)).join(" → ");
  if (event.kind === "touch") return `${side} ${strikes} touched · not confirmed`;
  // Both sides read "confirmed" - see format_event_message in oi_auto_alerts.py.
  const next = event.nextTarget?.strike != null ? ` → next ${strikeText(event.nextTarget.strike)}` : " · ladder complete";
  return `${side} ${strikes} confirmed${next}`;
}

// Tickers that fired recently float to the top (newest first); everything
// else keeps the server order (MAG7, then manual tickers).
export function sortOiAutoAlertRows(rows, nowMs = Date.now(), recentMs = OI_AUTO_ALERT_RECENT_MS) {
  const list = Array.isArray(rows) ? rows : [];
  const decorated = list.map((row, index) => {
    const lastEvent = oiAutoAlertRowLastEvent(row);
    const at = eventTime(lastEvent);
    const recent = Boolean(lastEvent) && at > 0 && nowMs - at <= recentMs;
    return { row, index, lastEvent, recent, at };
  });
  return decorated
    .sort((left, right) => {
      if (left.recent !== right.recent) return left.recent ? -1 : 1;
      if (left.recent && right.recent && left.at !== right.at) return right.at - left.at;
      return left.index - right.index;
    })
    .map(({ row, lastEvent, recent }) => ({ row, lastEvent, recent }));
}
