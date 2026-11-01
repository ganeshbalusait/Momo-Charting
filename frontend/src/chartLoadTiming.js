// Chart load timing from the TRADER'S device (2026-10-05, his "charts take 30 sec on the
// phone" while the same charts answered in 0.03-1.1 s on the server). Without numbers from
// the phone itself every theory - tunnel, home upload, phone CPU - is a guess. For each
// chart load this records: request sent -> first byte -> body read -> parsed -> candles
// drawn, plus bytes, and POSTs one small line to /api/client-timing. Best-effort: a failed
// report is dropped silently and never touches the chart.

const sessions = new Map();          // key -> {t0, symbol, kind, marks:{}, bytes, sent}
let lastStartedKey = null;
let watchingVisibility = false;

const now = () => (typeof performance !== "undefined" && performance.now ? performance.now() : Date.now());

export function timingStart(symbol, kind) {
  const key = String(symbol || "").toUpperCase() + "|" + kind + "|" + Math.round(now());
  const hiddenNow = typeof document !== "undefined" && document.visibilityState === "hidden";
  sessions.set(key, { t0: now(), symbol: String(symbol || "").toUpperCase(), kind, marks: {}, bytes: null, sent: false, hiddenDuringLoad: hiddenNow });
  if (typeof document !== "undefined" && !watchingVisibility) {
    watchingVisibility = true;
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "hidden") return;
      sessions.forEach((open) => { if (!open.sent) open.hiddenDuringLoad = true; });
    });
  }
  lastStartedKey = key;
  if (sessions.size > 20) sessions.delete(sessions.keys().next().value);
  return key;
}

export function timingMark(key, phase, extra = {}) {
  const s = key && sessions.get(key);
  if (!s) return;
  s.marks[phase] = Math.round(now() - s.t0);
  Object.assign(s, extra);
  if (phase === "error" || phase === "noBars") send(key);
}

/** Called by the chart's series writer: the first big candle write after a load = painted. */
export function timingCandlesDrawn(count) {
  if (!lastStartedKey || !(count > 50)) return;
  const s = sessions.get(lastStartedKey);
  if (!s || s.marks.drawn != null || s.marks.parsed == null) return;
  s.marks.drawn = Math.round(now() - s.t0);
  s.candles = count;
  send(lastStartedKey);
}

function send(key) {
  const s = sessions.get(key);
  if (!s || s.sent) return;
  s.sent = true;
  const conn = typeof navigator !== "undefined" ? navigator.connection : null;
  const body = JSON.stringify({
    symbol: s.symbol, kind: s.kind, marks: s.marks, bytes: s.bytes, candles: s.candles || null,
    error: s.error || null, net: conn ? { type: conn.effectiveType || null, downlink: conn.downlink || null, rtt: conn.rtt || null } : null,
    ua: typeof navigator !== "undefined" ? String(navigator.userAgent || "").slice(0, 160) : "",
    screen: typeof window !== "undefined" ? window.innerWidth + "x" + window.innerHeight : "",
    // A hidden tab pauses drawing, so a long parsed->drawn gap only means "slow"
    // if the page was visible (QQQ on the Mac 2026-10-05: 0.3-2 s, spikes 13-59 s).
    visibility: typeof document !== "undefined" ? document.visibilityState : null,
    hiddenDuringLoad: Boolean(s.hiddenDuringLoad),
  });
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 5000);
  try {
    fetch("/api/client-timing", { method: "POST", headers: { "Content-Type": "application/json" }, body,
      keepalive: true, priority: "low", signal: controller.signal })
      .catch(() => {}).finally(() => clearTimeout(deadline));
  } catch {
    clearTimeout(deadline);
    /* best-effort */
  }
}
