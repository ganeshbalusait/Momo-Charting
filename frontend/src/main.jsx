import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import AppErrorBoundary from "./AppErrorBoundary";
import { installPhoneLayoutDiagnostics, isDiagnosticsRequested } from "./phoneLayoutDiagnostics";
import { installUpdateWatch } from "./appVersion";
import "./index.css";

// Register the network-only worker so Chrome offers "Install AGX" again
// (trader, 2026-09-01: the address-bar install button had disappeared).
//
// It vanished because this block used to UNREGISTER every worker - the escape
// hatch from an old caching worker that pinned browsers to stale bundles. That
// removed the last registered worker, and Chrome only offers installation when
// one with a fetch handler controls the page.
//
// Registering again is safe now because public/sw.js caches NOTHING: every
// request goes straight to the network, and it wipes any cache an older worker
// left behind when it activates. Staleness is handled by installUpdateWatch
// below, which compares the entry bundle's content hash over the network and
// does not depend on the worker at all.
//
// Registration is deliberately AFTER load: a service worker fetch during
// startup competes with the app's own first requests, and the install prompt
// is not worth a slower first paint on a phone.
if (typeof navigator !== "undefined" && navigator.serviceWorker) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Blocked, private mode, or an unsupported browser. The app works
      // exactly the same; it simply cannot be installed.
    });
  });
}

// ?diag=1 only - a phone has no devtools, and the chart-height collapse has
// never reproduced in a desktop browser at phone width.
if (isDiagnosticsRequested(window.location.search, window.location.hash, window.localStorage)) {
  installPhoneLayoutDiagnostics(window);
}

// Render-cost accounting readable from browser tooling (window.__prof). The
// Profiler is inert in production bundles, so this costs nothing there.
function recordProfile(id, phase, actualDuration) {
  const store = window.__prof || (window.__prof = {});
  const entry = store[id] || (store[id] = { n: 0, ms: 0, max: 0 });
  entry.n += 1;
  entry.ms += actualDuration;
  entry.max = Math.max(entry.max, actualDuration);
}
window.__recordProfile = recordProfile;

ReactDOM.createRoot(document.getElementById("root")).render(
  <React.StrictMode>
    <AppErrorBoundary>
      <React.Profiler id="app" onRender={recordProfile}>
        <App />
      </React.Profiler>
    </AppErrorBoundary>
  </React.StrictMode>,
);

// Added to an iOS Home Screen there is no address bar and no reload button,
// and tapping the icon RESUMES the app rather than restarting it - so a phone
// can sit on a bundle from days ago with nothing on screen admitting it.
installUpdateWatch(window);
