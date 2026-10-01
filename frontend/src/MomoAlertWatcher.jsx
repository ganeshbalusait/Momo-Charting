import { useEffect, useRef, useState } from "react";
import { Zap, X } from "lucide-react";

// The Momo Alert's app-wide surface: a fixed banner + beep when a scan-pass
// symbol's RVOL crosses its armed threshold (the DKNG signature - trader,
// 2026-08-30). Mounted ONCE at App level so it interrupts from ANY panel;
// the worker owns all detection (edge-trigger, cooldown, frozen-tape gate),
// this component only polls, dedupes what it has already shown, and renders.
const MOMO_ALERTS_ENDPOINT = "/api/momx-scanner/momo-alerts";
const MOMO_POLL_MS = 15000;
const MOMO_BANNER_MS = 30000; // a missed banner self-dismisses

function beep() {
  // Two rising tones from the Web Audio API - no asset to load or cache.
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    [880, 1320].forEach((freq, index) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.frequency.value = freq;
      osc.type = "sine";
      gain.gain.setValueAtTime(0.12, ctx.currentTime + index * 0.18);
      gain.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + index * 0.18 + 0.16);
      osc.connect(gain).connect(ctx.destination);
      osc.start(ctx.currentTime + index * 0.18);
      osc.stop(ctx.currentTime + index * 0.18 + 0.18);
    });
    window.setTimeout(() => ctx.close(), 800);
  } catch {
    // Autoplay policy or no audio: the visual banner still shows.
  }
}

export default function MomoAlertWatcher() {
  const [banner, setBanner] = useState(null);
  const seenRef = useRef(new Set());
  const inFlightRef = useRef(false);
  const dismissTimerRef = useRef(null);
  const primedRef = useRef(false);

  useEffect(() => {
    const poll = async () => {
      if (inFlightRef.current) return;
      if (typeof document !== "undefined" && document.visibilityState === "hidden") return;
      inFlightRef.current = true;
      try {
        const res = await fetch(MOMO_ALERTS_ENDPOINT, { credentials: "include" });
        if (!res.ok) return; // older backend: feature simply dark
        const data = await res.json();
        const alerts = Array.isArray(data && data.alerts) ? data.alerts : [];
        const fresh = alerts.filter((alert) => {
          const key = `${alert.symbol}|${alert.timeframe}|${alert.at}`;
          if (seenRef.current.has(key)) return false;
          seenRef.current.add(key);
          return true;
        });
        if (seenRef.current.size > 400) {
          seenRef.current = new Set([...seenRef.current].slice(-200));
        }
        // First poll of a page load: absorb history silently. Alerts that
        // fired before this tab existed are context, not interruptions.
        if (!primedRef.current) {
          primedRef.current = true;
          return;
        }
        if (fresh.length > 0) {
          setBanner({ alerts: fresh.slice(0, 3), at: Date.now() });
          if (data.sound) beep();
          if (dismissTimerRef.current) window.clearTimeout(dismissTimerRef.current);
          dismissTimerRef.current = window.setTimeout(() => setBanner(null), MOMO_BANNER_MS);
        }
      } catch {
        // Network blip: next tick retries.
      } finally {
        inFlightRef.current = false;
      }
    };
    poll();
    const timer = window.setInterval(poll, MOMO_POLL_MS);
    return () => {
      window.clearInterval(timer);
      if (dismissTimerRef.current) window.clearTimeout(dismissTimerRef.current);
    };
  }, []);

  if (!banner) return null;
  return (
    <div className="momo-alert-banner" role="alert">
      <Zap size={15} aria-hidden="true" className="momo-alert-bolt" />
      <span className="momo-alert-title">MOMO</span>
      {banner.alerts.map((alert) => (
        <span key={`${alert.symbol}-${alert.timeframe}-${alert.at}`} className="momo-alert-item">
          <b>{alert.symbol}</b>
          <span className="momo-alert-detail">
            RVOL {alert.timeframe} {Number(alert.value).toFixed(1)}x
            {Number.isFinite(Number(alert.pctChange))
              ? ` \u00b7 ${Number(alert.pctChange) >= 0 ? "+" : ""}${Number(alert.pctChange).toFixed(2)}%`
              : ""}
          </span>
        </span>
      ))}
      <button
        type="button"
        className="momo-alert-dismiss"
        onClick={() => setBanner(null)}
        aria-label="Dismiss"
      >
        <X size={14} aria-hidden="true" />
      </button>
    </div>
  );
}
