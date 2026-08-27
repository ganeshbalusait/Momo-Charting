import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
const panelSource = readFileSync(new URL("./AutoOiAlertsPanel.jsx", import.meta.url), "utf8");
const cssSource = readFileSync(new URL("./index.css", import.meta.url), "utf8");

test("Charts and OI mounts the collapsible Auto OI alert rail", () => {
  assert.match(appSource, /import AutoOiAlertsPanel from "\.\/AutoOiAlertsPanel"/);
  assert.match(appSource, /className={`charts-oi-page charts-oi-page-full has-auto-oi-alerts/);
  assert.match(appSource, /<AutoOiAlertsPanel/);
  assert.match(appSource, /chartsOiAutoAlertsOpen/);
});

test("Auto OI panel exposes MAG7, manual ticker, refresh, and next-target controls", () => {
  assert.match(panelSource, /\/api\/oi-auto-alerts/);
  assert.match(panelSource, /Monitor MAG7 automatically/);
  assert.match(panelSource, /Add ticker, e\.g\. TSLA/);
  assert.match(panelSource, /CALL CLOSE ABOVE/);
  assert.match(panelSource, /PUT CLOSE BELOW/);
  assert.match(panelSource, /NEXT TARGET/);
  assert.match(panelSource, /5m close confirmation/);
});

test("Auto OI rail remains left-most and stacks above charts at reduced widths", () => {
  assert.match(cssSource, /grid-template-columns: var\(--auto-oi-alert-width\) minmax\(0, 1fr\)/);
  assert.match(cssSource, /> \.auto-oi-alert-panel \{ grid-column: 1; \}/);
  assert.match(cssSource, /@media \(max-width: 900px\)[\s\S]*\.auto-oi-alert-panel/);
  assert.match(cssSource, /\.auto-oi-alert-panel\.is-collapsed/);
});
