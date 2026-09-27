// Opt-in on-screen readout of the phone chart's height chain, enabled with
// ?diag=1. The chart fills by flex through eight nested levels, and when it
// collapses on a real device there is no way to inspect which level gave up -
// phones have no devtools and the failure has never reproduced in a desktop
// browser at phone width. This prints every level's measured height so the
// broken one can be read off a screenshot.
//
// Deliberately plain DOM rather than a React component: it must render even if
// the app tree itself is mid-collapse, and it must not join the layout it is
// measuring.
const CHAIN = [
  [".app-shell", "shell"],
  [".app-shell > .workspace", "workspace"],
  [".scanner-results-view", "scrollview"],
  [".charts-oi-page-full", "page-full"],
  [".charts-oi-full-chart", "full-chart"],
  [".oi-chart-multilayout", "multilayout"],
  [".oi-chart-layout-grid", "grid"],
  [".oi-finder-chart-card", "card"],
  [".oi-finder-candle-chart", "candle"],
  [".oi-finder-chart-canvas", "canvas"],
];

const PANEL_ID = "phone-layout-diagnostics";

const DIAGNOSTICS_STORAGE_KEY = "agxPhoneLayoutDiagnostics";

// Cloudflare Access bounces an unauthenticated request through its own login
// and returns to the bare path, dropping the query string - so ?diag=1 could
// silently never arrive. Accept the hash form too (hashes are never sent to
// the server, so they survive that round trip), and remember the flag once
// seen so a later redirect cannot lose it. ?diag=0 clears it again.
export function isDiagnosticsRequested(search = "", hash = "", storage = null) {
  const wanted = /(?:^|[?&])diag=1(?:&|$)/.test(String(search))
    || /(?:^|[#&])diag=1(?:&|$)/.test(String(hash));
  const cleared = /(?:^|[?&#])diag=0(?:&|$)/.test(String(search) + String(hash));

  if (!storage) return wanted && !cleared;
  try {
    if (cleared) {
      storage.removeItem(DIAGNOSTICS_STORAGE_KEY);
      return false;
    }
    if (wanted) {
      storage.setItem(DIAGNOSTICS_STORAGE_KEY, "1");
      return true;
    }
    return storage.getItem(DIAGNOSTICS_STORAGE_KEY) === "1";
  } catch {
    // Private mode or blocked storage: fall back to the URL alone.
    return wanted && !cleared;
  }
}

export function measurePhoneLayout(doc = document, win = window) {
  const rows = CHAIN.map(([selector, label]) => {
    const el = doc.querySelector(selector);
    if (!el) return { label, height: null, missing: true };
    const rect = el.getBoundingClientRect();
    return {
      label,
      height: Math.round(rect.height),
      top: Math.round(rect.top),
      bottom: Math.round(rect.bottom),
    };
  });

  const candle = doc.querySelector(".oi-finder-candle-chart");
  const nav = doc.querySelector(".mobile-chart-bottom-nav");
  const gap = candle && nav
    ? Math.round(nav.getBoundingClientRect().top - candle.getBoundingClientRect().bottom)
    : null;

  // The quick-ticker row renders on every desktop browser tried and on the
  // live app driven from one, but not on the reported iPhone, where its
  // reserved band shows empty. Report enough to tell WHICH way it fails:
  // absent from the DOM, present but display:none, present but zero-sized, or
  // present and painted but covered by something above it.
  const qt = doc.querySelector(".mobile-chart-quick-tickers");
  let tickers;
  if (!qt) {
    tickers = { state: "NOT IN DOM" };
  } else {
    const qr = qt.getBoundingClientRect();
    const cs = win.getComputedStyle(qt);
    const buttons = qt.querySelectorAll("button");
    const sized = [...buttons].filter((b) => b.getBoundingClientRect().width > 0).length;
    // Sample a quarter of the way across, not the middle: this panel sits at
    // the top right and would otherwise report itself as the thing covering
    // the row.
    const hit = doc.elementFromPoint(Math.round(qr.left + qr.width / 4), Math.round(qr.top + qr.height / 2));
    tickers = {
      state: "in DOM",
      display: cs.display,
      visibility: cs.visibility,
      opacity: cs.opacity,
      h: Math.round(qr.height),
      top: Math.round(qr.top),
      buttons: buttons.length,
      sizedButtons: sized,
      topmostAtCentre: hit ? (hit.className && String(hit.className).slice(0, 22)) || hit.tagName : "none",
    };
  }

  const vv = win.visualViewport;
  return {
    rows,
    gap,
    tickers,
    innerHeight: win.innerHeight,
    visualViewportHeight: vv ? Math.round(vv.height) : null,
    appVh: doc.documentElement.style.getPropertyValue("--app-vh") || "unset",
    dvhSupported: !!(win.CSS && win.CSS.supports && win.CSS.supports("height", "100dvh")),
    shellClasses: doc.querySelector(".app-shell")
      ? doc.querySelector(".app-shell").className.replace("app-shell charts-workstation", "").trim() || "(none)"
      : "(no shell)",
  };
}

// Deliberately not a per-level guess. Chrome legitimately eats space at
// several levels (113px of top bars, a 116px card header, a 27px OHLC row), so
// "child shorter than parent" flags healthy levels too. The one unambiguous
// signal is dead space above the fixed nav: every real element between the
// chart and the nav is small, so a large gap means the chain gave up. The
// per-level heights are printed alongside so the actual break can be read off.
export const COLLAPSE_GAP_PX = 120;

export function isChainCollapsed(gap) {
  return Number.isFinite(Number(gap)) && Number(gap) > COLLAPSE_GAP_PX;
}

function render(panel, data) {
  const collapsed = isChainCollapsed(data.gap);
  const lines = data.rows.map((row) => {
    const value = row.missing ? "absent" : `${row.height}`;
    return `${row.label.padEnd(12)}${String(value).padStart(5)}`;
  });

  panel.innerHTML = "";
  const pre = document.createElement("pre");
  pre.style.cssText = "margin:0;font:600 10px/1.35 ui-monospace,Menlo,monospace;white-space:pre;color:#e7ecf3";
  const t = data.tickers || {};
  const tickerLines = t.state === "NOT IN DOM"
    ? ["TICKER ROW: NOT IN DOM"]
    : [
      "---- ticker row ----",
      `display     ${t.display}`,
      `visibility  ${t.visibility}`,
      `opacity     ${t.opacity}`,
      `height      ${t.h}`,
      `buttons     ${t.buttons} (${t.sizedButtons} sized)`,
      `painted     ${t.topmostAtCentre}`,
    ];

  pre.textContent = [
    `gap above nav  ${data.gap}px`,
    `innerHeight    ${data.innerHeight}`,
    `visualViewport ${data.visualViewportHeight}`,
    `--app-vh       ${data.appVh}`,
    `dvh supported  ${data.dvhSupported}`,
    `tab            ${data.shellClasses}`,
    "---- chain heights ----",
    ...lines,
    collapsed ? `>> CHAIN COLLAPSED (${data.gap}px dead)` : "chain looks intact",
    ...tickerLines,
  ].join("\n");
  panel.appendChild(pre);

  const close = document.createElement("button");
  close.textContent = "close";
  close.style.cssText = "margin-top:6px;padding:6px 10px;border:1px solid #45d0e8;border-radius:4px;background:rgba(69,208,232,.12);color:#45d0e8;font:700 11px system-ui";
  close.addEventListener("click", () => panel.remove());
  panel.appendChild(close);
}

export function installPhoneLayoutDiagnostics(win = window) {
  const doc = win.document;
  if (!doc || !doc.body) return () => {};
  if (doc.getElementById(PANEL_ID)) return () => {};

  const panel = doc.createElement("div");
  panel.id = PANEL_ID;
  panel.style.cssText = [
    // Below the pinned quick-ticker row, not over it - the row is often the
    // thing being diagnosed, and a panel covering it makes the screenshot
    // useless.
    "position:fixed", "top:78px", "right:8px", "z-index:99999",
    "max-width:70vw", "padding:8px 10px",
    "border:1px solid #45d0e8", "border-radius:6px",
    "background:rgba(6,8,11,.94)", "pointer-events:auto",
  ].join(";");
  doc.body.appendChild(panel);

  const update = () => {
    if (!panel.isConnected) return;
    render(panel, measurePhoneLayout(doc, win));
  };

  // Poll for as long as the panel is open rather than on a fixed schedule. The
  // chart mounts well after load (chain data, studies, the stream), and a
  // burst of early timers reported every level as "absent" - which is exactly
  // the useless screenshot this readout exists to avoid. A handful of
  // getBoundingClientRect calls a second costs nothing next to the chart.
  update();
  const interval = win.setInterval(update, 1000);
  win.addEventListener("resize", update, { passive: true });
  if (win.visualViewport) win.visualViewport.addEventListener("resize", update, { passive: true });

  return () => {
    win.clearInterval(interval);
    win.removeEventListener("resize", update);
    if (win.visualViewport) win.visualViewport.removeEventListener("resize", update);
    panel.remove();
  };
}
