import { Bell, ChevronLeft, Plus, RefreshCw, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

function compactNumber(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "--";
  return new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(numeric);
}

function price(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric) || numeric <= 0) return "--";
  return numeric.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 2 });
}

function shortExpiry(value) {
  if (!value) return "--";
  const parsed = new Date(`${String(value).slice(0, 10)}T12:00:00`);
  return Number.isNaN(parsed.getTime())
    ? String(value)
    : parsed.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function playConfirmationTone() {
  try {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    const context = new AudioContextClass();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.frequency.setValueAtTime(720, context.currentTime);
    oscillator.frequency.exponentialRampToValueAtTime(980, context.currentTime + 0.14);
    gain.gain.setValueAtTime(0.0001, context.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.14, context.currentTime + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, context.currentTime + 0.24);
    oscillator.connect(gain).connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.25);
    oscillator.addEventListener("ended", () => context.close().catch(() => {}), { once: true });
  } catch {
    // Audio is an enhancement; browser notification and in-panel event remain available.
  }
}

export default function AutoOiAlertsPanel({ activeSymbol = "", collapsed = false, onToggle, onSelectSymbol }) {
  const [payload, setPayload] = useState(null);
  const [tickerInput, setTickerInput] = useState("");
  const [working, setWorking] = useState(false);
  const [error, setError] = useState("");
  const knownEventIdsRef = useRef(null);

  const acceptPayload = useCallback((nextPayload) => {
    const events = Array.isArray(nextPayload?.events) ? nextPayload.events : [];
    const incomingIds = new Set(events.map((event) => String(event?.id || "")).filter(Boolean));
    if (knownEventIdsRef.current === null) {
      knownEventIdsRef.current = incomingIds;
    } else {
      const unseen = events.filter((event) => {
        const id = String(event?.id || "");
        return id && !knownEventIdsRef.current.has(id);
      });
      if (unseen.length) {
        playConfirmationTone();
        if ("Notification" in window && Notification.permission === "granted") {
          unseen.slice(0, 3).forEach((event) => {
            try {
              new Notification(`${event.symbol || "Ticker"} OI level confirmed`, {
                body: String(event.message || "A completed 5-minute candle confirmed an OI level."),
                tag: String(event.id || `oi-auto-${event.symbol || "ticker"}`),
              });
            } catch {
              // Some embedded browsers expose Notification but disallow construction.
            }
          });
        }
        unseen.forEach((event) => knownEventIdsRef.current.add(String(event.id || "")));
      }
    }
    setPayload(nextPayload);
    setError("");
  }, []);

  const load = useCallback(async () => {
    try {
      const response = await fetch("/api/oi-auto-alerts", { cache: "no-store" });
      const nextPayload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(nextPayload?.error || "Auto OI alerts are unavailable.");
      acceptPayload(nextPayload);
    } catch (loadError) {
      setError(loadError?.message || "Auto OI alerts are unavailable.");
    }
  }, [acceptPayload]);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 10000);
    return () => window.clearInterval(timer);
  }, [load]);

  const mutate = async (body) => {
    setWorking(true);
    setError("");
    try {
      const response = await fetch("/api/oi-auto-alerts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const nextPayload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(nextPayload?.error || "Auto OI alert update failed.");
      acceptPayload(nextPayload);
      return true;
    } catch (mutationError) {
      setError(mutationError?.message || "Auto OI alert update failed.");
      return false;
    } finally {
      setWorking(false);
    }
  };

  const addTicker = async (event) => {
    event.preventDefault();
    const symbol = tickerInput.trim().toUpperCase();
    if (!symbol) return;
    if ("Notification" in window && Notification.permission === "default") {
      Promise.resolve(Notification.requestPermission()).catch(() => {});
    }
    if (await mutate({ action: "add", symbol })) setTickerInput("");
  };
  const rows = Array.isArray(payload?.rows) ? payload.rows : [];
  const latestEvent = Array.isArray(payload?.events) ? payload.events[0] : null;
  const levelOi = (level) => Number(level?.openInterest || 0) > 0
    ? `${compactNumber(level.openInterest)} OI`
    : "Waiting";

  if (collapsed) {
    return <aside className="auto-oi-alert-panel is-collapsed" aria-label="Automatic OI alerts">
      <button type="button" className="auto-oi-alert-expand" onClick={onToggle} title="Open Auto OI alerts" aria-label="Open Auto OI alerts">
        <Bell size={16} />
        <span>AUTO OI</span>
        <i className={payload?.enabled ? "is-live" : ""} />
      </button>
    </aside>;
  }

  return <aside className="auto-oi-alert-panel" aria-label="Automatic OI alerts">
    <header className="auto-oi-alert-header">
      <div><Bell size={15} /><span><b>AUTO OI ALERTS</b><small>5m close confirmation</small></span></div>
      <button type="button" onClick={onToggle} title="Collapse Auto OI alerts" aria-label="Collapse Auto OI alerts"><ChevronLeft size={15} /></button>
    </header>
    <div className="auto-oi-alert-controls">
      <button
        type="button"
        className={`auto-oi-alert-switch${payload?.enabled ? " is-on" : ""}`}
        role="switch"
        aria-checked={Boolean(payload?.enabled)}
        disabled={working || !payload}
        onClick={() => mutate({ action: "configure", enabled: !payload?.enabled })}
      >
        <i /><span>{payload?.enabled ? "ARMED" : "PAUSED"}</span>
      </button>
      <button className="auto-oi-alert-refresh" type="button" disabled={working} onClick={() => mutate({ action: "refresh", force: true })} title="Rebuild OI ladders now" aria-label="Rebuild OI ladders now">
        <RefreshCw className={payload?.refreshing || working ? "is-spinning" : ""} size={14} />
      </button>
    </div>
    <label className="auto-oi-alert-mag7">
      <input
        type="checkbox"
        checked={Boolean(payload?.includeMag7)}
        disabled={working || !payload}
        onChange={(event) => mutate({ action: "configure", includeMag7: event.target.checked })}
      />
      <span>Monitor MAG7 automatically</span>
    </label>
    <form className="auto-oi-alert-add" onSubmit={addTicker}>
      <input
        aria-label="Ticker to add"
        maxLength="10"
        onChange={(event) => setTickerInput(event.target.value.toUpperCase())}
        placeholder="Add ticker, e.g. TSLA"
        value={tickerInput}
      />
      <button type="submit" disabled={working || !tickerInput.trim()} title="Add ticker" aria-label="Add ticker"><Plus size={14} /></button>
    </form>
    <div className="auto-oi-alert-summary">
      <span className={payload?.enabled ? "is-live" : ""}><i />{payload?.status || "Connecting"}</span>
      <small>{payload?.session || "--"} · refresh 9:15 ET</small>
    </div>
    {error ? <div className="auto-oi-alert-error" role="alert">{error}</div> : null}
    <div className="auto-oi-alert-list">
      {rows.length ? rows.map((row) => {
        const symbol = String(row?.symbol || "").toUpperCase();
        const manual = String(row?.sourceGroup || "").toLowerCase().includes("manual");
        return <article className={`auto-oi-alert-card${symbol === activeSymbol ? " is-active" : ""}`} key={`auto-oi-${symbol}`}>
          <button className="auto-oi-alert-symbol" type="button" onClick={() => onSelectSymbol?.(symbol)} title={`Load ${symbol} chart`}>
            <span><b>{symbol}</b><small>{row?.sourceGroup || "Auto"}</small></span>
            <em>{row?.status || "Queued"}</em>
          </button>
          {manual ? <button className="auto-oi-alert-remove" type="button" disabled={working} onClick={() => mutate({ action: "remove", symbol })} title={`Remove ${symbol}`} aria-label={`Remove ${symbol}`}><X size={12} /></button> : null}
          <div className="auto-oi-alert-levels is-call">
            <span><small>CALL CLOSE ABOVE</small><b>{price(row?.activeCall?.strike)}</b><em>{levelOi(row?.activeCall)}</em></span>
            <i>→</i>
            <span><small>NEXT TARGET</small><b>{price(row?.nextCall?.strike)}</b><em>{levelOi(row?.nextCall)}</em></span>
          </div>
          <div className="auto-oi-alert-levels is-put">
            <span><small>PUT CLOSE BELOW</small><b>{price(row?.activePut?.strike)}</b><em>{levelOi(row?.activePut)}</em></span>
            <i>→</i>
            <span><small>NEXT TARGET</small><b>{price(row?.nextPut?.strike)}</b><em>{levelOi(row?.nextPut)}</em></span>
          </div>
          <footer>
            <span>Last 5m {row?.lastClose ? `$${price(row.lastClose)}` : "waiting"}</span>
            <span>{row?.monthlyExpiry ? `OPEX ${shortExpiry(row.monthlyExpiry)}` : "OI ladder queued"}</span>
          </footer>
        </article>;
      }) : <div className="auto-oi-alert-empty">Connecting to the OI alert worker…</div>}
    </div>
    <footer className="auto-oi-alert-feed">
      <b>{latestEvent ? "LATEST CONFIRMATION" : "AUTOMATION RULE"}</b>
      <span>{latestEvent?.message || payload?.confirmationRule || "Completed regular-session 5-minute candles only."}</span>
    </footer>
  </aside>;
}
