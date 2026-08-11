import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
const cssSource = readFileSync(new URL("./index.css", import.meta.url), "utf8");

test("big-screen and detached charts retain visible, ticker-aware price-alert access", () => {
  assert.match(appSource, /className="oi-chart-workspace-toggle oi-chart-workspace-alerts"[\s\S]*?new CustomEvent\(OI_PRICE_ALERT_DRAFT_EVENT,[\s\S]*?symbol: normalizeOiChartSymbol\(activeConfig\.symbol\)/);
  assert.match(appSource, /className="oi-finder-chart-alert"[\s\S]*?Create a price alert for/);
  // The MomoX-compact setter dropped the "Set price alert" heading and the
  // right-click hint; the price line and the arming button are what must stay.
  assert.match(appSource, /className="oi-finder-chart-alert-menu"[\s\S]*?oi-finder-chart-alert-level[\s\S]*?Alert at or/);
  assert.match(appSource, /chartHost\.addEventListener\("contextmenu", openAlertAtChartPrice, \{ capture: true \}\)/);
  assert.match(appSource, /<FullChartsAndOiBoard[\s\S]*?alertCenter=\{popoutConfig\.mode !== "chain" \?[\s\S]*?<GlobalPriceAlertCenter[\s\S]*?showLabel/);
  assert.match(appSource, /\{showLabel \? <span>Alerts<\/span> : null\}/);
  assert.match(cssSource, /body\.global-price-alert-visible \.topbar\.trading-header\s*\{[\s\S]*?z-index: 1900;/);
  assert.match(cssSource, /\.oi-finder-chart-alert-menu\s*\{[\s\S]*?z-index: 100;/);
});
