// "Start / Pause Stock Bot" and "Scan MAG7 OI" described once (2026-11-14).
// Both lived only on the "Dashboard" view, which is not in the menu, so the
// trader could not reach them. The Dashboard strip, the Stock Paper Trading
// banner and the Mag7 Watchlist page all read these, so there is one copy of
// each request. `action` is the argument list for App.jsx's runAction.
//
// PAPER only: /api/bot-control is the Alpaca paper stock bot (owner only on
// the server). Nothing here names a real-money or Schwab order route.

const jsonPost = (body) => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

// The pending keys the Dashboard strip always used; shared so every copy of a
// control shows the same busy state.
export const STOCK_BOT_PENDING = "dashboard-stock-bot";
export const MAG7_OI_PENDING = "dashboard-mag7-oi";

export function stockBotControl(botState) {
  const running = botState === "Running";
  return {
    running,
    label: running ? "Pause Stock Bot" : "Start Stock Bot",
    action: ["/api/bot-control", jsonPost({ state: running ? "Paused" : "Running" }), STOCK_BOT_PENDING],
  };
}

export function mag7OiScanControl(submitting, oiScannerAuto) {
  const scanning = submitting === MAG7_OI_PENDING;
  return {
    scanning,
    label: scanning ? "Scanning MAG7..." : "Scan MAG7 OI",
    note: `${oiScannerAuto?.symbols?.length || 7} symbols · Schwab/TOS`,
    action: ["/api/oi-scan", jsonPost({ universe: "mag7" }), MAG7_OI_PENDING],
  };
}
