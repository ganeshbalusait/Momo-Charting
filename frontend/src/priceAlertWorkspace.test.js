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

test("every option-chain strike is an accessible price-alert trigger", () => {
  assert.equal((appSource.match(/className="tos-strike-alert"/g) || []).length, 2);
  assert.match(appSource, /buildStrikePriceAlertDraft\(symbol, row\.strike, spotPrice\)/);
  assert.match(appSource, /Set \$\{symbol \|\| "ticker"\} price alert at strike/);
  assert.match(cssSource, /\.charts-oi-tos-chain \.tos-strike-alert:focus-visible/);
});

test("mobile full-screen keeps the bottom menu and uses compact chart controls", () => {
  assert.match(cssSource, /body\.oi-chart-card-maximized \.mobile-chart-bottom-nav\s*\{[\s\S]*?display: grid !important;/);
  assert.match(cssSource, /@media \(max-width: 760px\)\s*\{[\s\S]*?\.app-shell\.charts-workstation \.mobile-terminal-action-bar\s*\{[\s\S]*?display: none !important;/);
  assert.match(cssSource, /\.oi-finder-chart-card\.is-maximized[\s\S]*?bottom: calc\(70px \+ env\(safe-area-inset-bottom\)\) !important;/);
  assert.match(cssSource, /\.oi-finder-chart-card\.is-maximized \.oi-finder-timeframes button\s*\{[\s\S]*?font-size: 8px;/);
  assert.match(cssSource, /\.oi-finder-chart-card\.is-maximized \.oi-finder-mobile-draw-toggle,[\s\S]*?font-size: 8px !important;/);
});

test("mobile chart exposes the complete quick chart and chain ticker strip", () => {
  assert.match(appSource, /className="mobile-chart-quick-tickers"[\s\S]*?OI_FINDER_QUICK_TICKERS\.map[\s\S]*?chooseOiFinderTicker\(symbol\)/);
  assert.match(appSource, /const OI_FINDER_QUICK_TICKERS = \["SPY", "QQQ", "SLV", "AAPL", "AMZN", "GOOGL", "META", "MSFT", "NFLX", "NVDA", "TSLA", "AVGO", "USO"\]/);
  assert.match(cssSource, /@media \(max-width: 760px\)[\s\S]*?\.mobile-chart-quick-tickers\s*\{[\s\S]*?display: grid;/);
});

test("mobile Options opens the lightweight warm chain before lazy research sections", () => {
  const primaryOptionsHandler = appSource.match(/const openMobileOptions = \(\) => \{[\s\S]*?\n  \};/)?.[0] || "";
  assert.match(primaryOptionsHandler, /warmMobileQuickOptions\(oiFinderSymbol\)[\s\S]*?navigateMobileTerminal\("Quick Options"\)/);
  assert.doesNotMatch(primaryOptionsHandler, /chartsAndOiPageActive|focusMobileOptions|mobileChartSection/);
  assert.match(appSource, /data-testid="mobile-primary-options"[\s\S]*?onClick=\{openMobileOptions\}/);
  assert.match(appSource, /className=\{quickOptionsPageActive \? "is-active" : ""\}[\s\S]*?data-testid="mobile-primary-options"/);
  assert.match(appSource, /aria-label="Chart sections"[\s\S]*?onClick=\{focusMobileOptions\}[\s\S]*?<span>Chain<\/span>/);
  assert.match(appSource, /\/api\/oi-finder-chain\?symbol=\$\{encodeURIComponent\(key\)\}&initial=true/);
  assert.doesNotMatch(appSource, /oi-finder-chain\?symbol=\$\{encodeURIComponent\(key\)\}&initial=true&fast=true/);
  assert.doesNotMatch(appSource, /useMobileQuickOptionsFeed \? "&fast=true"/);
  assert.match(appSource, /activeView === "Quick Options"[\s\S]*?<MobileQuickOptionsBoard/);
  assert.match(appSource, /activeSection === "chain"[\s\S]*?<FullChartsAndOiBoard[\s\S]*?surfaceMode="mobile-options"/);
  assert.match(appSource, /surfaceMode === "mobile-options"[\s\S]*?data-testid="mobile-full-option-chain"[\s\S]*?LIVE OPTION CHAIN[\s\S]*?chainViewTabs[\s\S]*?TosChainColorKey[\s\S]*?<TosExpiryAccordion/);
  assert.match(appSource, /surfaceMode === "mobile-options"[\s\S]*?chainView === "highOi" \? highOiListPanel/);
  assert.match(appSource, /const callContracts = rawCallContracts;[\s\S]*?const putContracts = rawPutContracts;/);
  assert.match(appSource, /All shows every listed strike/);
  assert.doesNotMatch(appSource, /const keepChainContract =/);
  assert.match(appSource, /data-testid="mobile-quick-options"[\s\S]*?Tap any strike to set an alert/);
  assert.match(appSource, /MOBILE_OPTIONS_SECTIONS\.map[\s\S]*?mobile-options-section-/);
  assert.match(appSource, /ref=\{sectionNavRef\}[\s\S]*?className="mobile-options-sections"/);
  assert.match(appSource, /mobile-options-research-launcher[\s\S]*?mobile-options-launch-\$\{section\.key\}/);
  assert.match(appSource, /sectionNavRef\.current\?\.scrollIntoView\?\.\(\{ block: "start", behavior: "auto" \}\)/);
  assert.match(appSource, /if \(!result\?\.started \|\| result\?\.failed\) \{[\s\S]*?Unable to load \$\{nextSection\} research/);
  assert.match(appSource, /const refreshOiFinderNews[\s\S]*?return \{ started: true, failed: false \};[\s\S]*?return \{ started: true, failed: true \};/);
  assert.match(appSource, /fullAnalytics: true,[\s\S]*?researchSection: normalizedSection/);
  assert.match(appSource, /onPointerDown=\{\(\) => warmMobileQuickOptions\(oiFinderSymbol\)/);
  assert.match(cssSource, /\.mobile-quick-options-table-wrap[\s\S]*?overflow: hidden;/);
  assert.match(cssSource, /\.mobile-options-sections\s*\{[\s\S]*?position: sticky;[\s\S]*?top: 0;/);
  assert.match(cssSource, /\.mobile-full-option-chain\s*\{[\s\S]*?width: 100%;[\s\S]*?margin: 0;[\s\S]*?overflow: hidden;/);
  assert.match(cssSource, /\.mobile-full-option-chain \.charts-oi-expiry-panel \.charts-oi-tos-scroll\s*\{[\s\S]*?max-height: min\(58dvh, 520px\);/);
  assert.match(cssSource, /\.mobile-options-research-launcher nav\s*\{[\s\S]*?grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/);
  assert.match(cssSource, /\.oi-finder-chart-actions > \.oi-finder-candle-countdown,[\s\S]*?\.oi-finder-chart-actions > \.oi-finder-chart-control\s*\{[\s\S]*?display: none !important;/);
});
