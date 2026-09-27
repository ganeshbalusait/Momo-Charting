// thinkorswim-style colour link groups, shared by the charts (App.jsx) and the
// MomX scanner (2026-09-26: "like TOS - when I click the scanner ticker it
// takes me to the charting page"). One list, so a Red scanner and a Red chart
// can never mean two different things.
export const TOS_LINK_GROUPS = Object.freeze([
  { value: 1, name: "Red", color: "#ff475c", text: "#ffffff" },
  { value: 2, name: "Yellow", color: "#f5d000", text: "#171400" },
  { value: 3, name: "Green", color: "#26d07c", text: "#041b10" },
  { value: 4, name: "Blue", color: "#38a8ff", text: "#041420" },
  { value: 5, name: "Purple", color: "#a975ff", text: "#ffffff" },
  { value: 6, name: "Cyan", color: "#35d8e8", text: "#03191d" },
  { value: 7, name: "Orange", color: "#ff9f2d", text: "#211000" },
  { value: 8, name: "Pink", color: "#ff66b3", text: "#210615" },
  { value: 9, name: "Gray", color: "#a3a3ae", text: "#0f0f11" },
]);

export function tosLinkGroup(value, fallback = 1) {
  const group = Number(value);
  return TOS_LINK_GROUPS.find((item) => item.value === group)
    || TOS_LINK_GROUPS.find((item) => item.value === fallback)
    || TOS_LINK_GROUPS[0];
}

// Scanner -> charts. The same message is sent two ways: a window event for the
// scanner inside the main app, and a BroadcastChannel for the detached scanner
// window (a separate page; BroadcastChannel does not deliver to its own page).
// Only the MAIN window listens (App.jsx), so a click never loops.
export const CHART_LINK_EVENT = "agx:chart-link";
export const CHART_LINK_CHANNEL = "agx-chart-link";

let sequence = 0;

export function chartLinkMessage(symbol, linkGroup, source = "momx-scanner") {
  const clean = String(symbol || "").trim().toUpperCase().replace(/[^A-Z0-9./-]/g, "");
  const group = Number(linkGroup);
  if (!clean || !Number.isInteger(group) || group < 1 || group > 9) return null;
  sequence += 1;
  return { symbol: clean, linkGroup: group, source, id: `${source}-${Date.now()}-${sequence}` };
}

function inPopoutWindow() {
  try {
    return Boolean(new URLSearchParams(window.location.search).get("popout"));
  } catch {
    return false;
  }
}

export function sendTickerToCharts(symbol, linkGroup, source = "momx-scanner") {
  const message = chartLinkMessage(symbol, linkGroup, source);
  if (!message || typeof window === "undefined") return null;
  // In the main app: THIS window only (a second app window must not jump).
  if (!inPopoutWindow()) {
    try {
      window.dispatchEvent(new CustomEvent(CHART_LINK_EVENT, { detail: message }));
    } catch {
      // No CustomEvent (very old browser): nothing else to try in-page.
    }
    return message;
  }
  // In the detached scanner window: tell the main app window(s).
  try {
    if (typeof BroadcastChannel !== "undefined") {
      const channel = new BroadcastChannel(CHART_LINK_CHANNEL);
      channel.postMessage(message);
      channel.close();
    }
  } catch {
    // No channel (private mode / old browser): in-app clicks still work.
  }
  return message;
}
