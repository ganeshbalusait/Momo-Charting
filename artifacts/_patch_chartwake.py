import io, re
p = "frontend/src/App.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

# 1. import the helpers
anchor = 'import {\n  isSchwabTosChartPacket,'
assert s.count(anchor) == 1, "import anchor"
s = s.replace(anchor, 'import { chartStaleLabel, chartWakeDecision } from "./chartWake";\nimport {\n  isSchwabTosChartPacket,', 1)

# 2. replace the hand-rolled wake handler with the shared decision
OLD = """    const onChartVisible = () => {
      if (typeof document === "undefined" || document.visibilityState !== "visible") return;
      // Clear a request that can no longer complete. Bounded by age so this can
      // never abort a genuinely running fetch - only one that outlived the
      // suspension that killed its socket.
      if (requestInFlight && requestStartedAt && Date.now() - requestStartedAt > 30_000) {
        requestInFlight = false;
      }
      loadChart(true);
    };
    document.addEventListener("visibilitychange", onChartVisible);"""
NEW = """    // WAKE. Driven by the AGE OF THE DATA ON SCREEN, not by how long the tab was
    // hidden - a request that died while the phone slept and a server tape that
    // stalled while the tab stayed visible leave the identical shape (a price
    // that ticks over candles that never extend), so one rule must catch both.
    //
    // Three events, because a phone can come back in three different ways:
    // visibilitychange (tab refocused), pageshow (restored from the bfcache,
    // which fires NO visibilitychange), and online (radio came back).
    //
    // A fresh tape does nothing at all. The previous version reloaded on every
    // wake, which spent a request each time the trader glanced at the app.
    const onChartWake = (event) => {
      if (
        event && event.type === "visibilitychange"
        && (typeof document === "undefined" || document.visibilityState !== "visible")
      ) return;
      const decision = chartWakeDecision({
        nowMs: Date.now(),
        latestBarTime: latestRawBarTimeRef.current,
        requestInFlight,
        requestStartedAt,
      });
      // A request older than the abandon window cannot still be running: its
      // socket died with the suspension and its `finally` will never clear the
      // in-flight flag, so every later loadChart returns early forever. This is
      // what left CRWV frozen on 19:55 candles for twelve hours.
      if (decision.abandonRequest) requestInFlight = false;
      if (decision.mode === "full") loadChart(true, true);
      else if (decision.mode === "delta") loadChart(true);
    };
    document.addEventListener("visibilitychange", onChartWake);
    window.addEventListener("pageshow", onChartWake);
    window.addEventListener("online", onChartWake);"""
assert s.count(OLD) == 1, "wake handler anchor"
s = s.replace(OLD, NEW)

OLD2 = """      document.removeEventListener("visibilitychange", onChartVisible);"""
NEW2 = """      document.removeEventListener("visibilitychange", onChartWake);
      window.removeEventListener("pageshow", onChartWake);
      window.removeEventListener("online", onChartWake);"""
assert s.count(OLD2) == 1, "cleanup anchor"
s = s.replace(OLD2, NEW2)

# 3. the strip stops claiming STREAMING over a stale tape
OLD3 = """        {" · "}{streamConnected ? "STREAMING" : "REST FALLBACK"}"""
NEW3 = """        {" · "}{
          // A tape whose newest candle is older than the stale window is not
          // streaming, whatever the socket believes. Saying STREAMING over
          // twelve-hour-old candles is the same lie as the green TOS lamp over
          // a dead hour - the label has to be able to report bad news.
          chartStaleLabel(Date.now() - Number(displayedBar.time || 0) * 1000)
          || (streamConnected ? "STREAMING" : "REST FALLBACK")
        }"""
assert s.count(OLD3) == 1, "strip label anchor"
s = s.replace(OLD3, NEW3)

io.open(p, "w", encoding="utf-8", newline="").write(s)
print("App.jsx: chartWake wired into visibilitychange + pageshow + online, strip shows STALE")
