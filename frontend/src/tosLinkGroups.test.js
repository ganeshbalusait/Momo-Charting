import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { TOS_LINK_GROUPS, chartLinkMessage, tosLinkGroup, warmTickerForCharts, sendTickerToCharts, CHART_LINK_EVENT } from "./tosLinkGroups.js";

test("the 9 TOS link colours, Red first", () => {
  assert.equal(TOS_LINK_GROUPS.length, 9);
  assert.deepEqual(TOS_LINK_GROUPS.map((g) => g.value), [1, 2, 3, 4, 5, 6, 7, 8, 9]);
  assert.equal(TOS_LINK_GROUPS[0].name, "Red");
});

test("tosLinkGroup falls back for junk", () => {
  assert.equal(tosLinkGroup("4").name, "Blue");
  assert.equal(tosLinkGroup(null).name, "Red");
  assert.equal(tosLinkGroup("99", 2).name, "Yellow");
});

test("a scanner click becomes a clean chart-link message, or nothing", () => {
  const msg = chartLinkMessage(" aal ", 1);
  assert.equal(msg.symbol, "AAL");
  assert.equal(msg.linkGroup, 1);
  assert.equal(msg.source, "momx-scanner");
  assert.ok(msg.id && msg.id !== chartLinkMessage("AAL", 1).id);   // unique per click
  assert.equal(chartLinkMessage("", 1), null);
  assert.equal(chartLinkMessage("AAL", 0), null);
  assert.equal(chartLinkMessage("AAL", 10), null);
  assert.equal(chartLinkMessage("AAL", "x"), null);
});

test("App.jsx uses the shared colour list, not its own copy", () => {
  const app = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
  assert.match(app, /import \{[^}]*TOS_LINK_GROUPS[^}]*\} from "\.\/tosLinkGroups\.js"/);
  assert.doesNotMatch(app, /const TOS_LINK_GROUPS = /);
  // the navigation intent keeps the colour and allows no timeframe for a link
  assert.match(app, /linkGroup = Number\.isInteger\(group\) && group >= 1 && group <= 9 \? group : null/);
});

test("scanner warm and click messages keep symbol/group ownership and distinct intent", () => {
  const previous=globalThis.window, events=[];
  globalThis.window={location:{search:""},dispatchEvent:e=>events.push(e)};
  try {
    const warm=warmTickerForCharts(" spy ",2),open=sendTickerToCharts("AAPL",2);
    assert.equal(warm.intent,"warm");assert.equal(warm.symbol,"SPY");assert.equal(warm.linkGroup,2);
    assert.equal(open.symbol,"AAPL");assert.equal(open.intent,undefined);
    assert.ok(events.every(e=>e.type===CHART_LINK_EVENT));
    assert.equal(warmTickerForCharts("",2),null);
  } finally {if(previous===undefined)delete globalThis.window;else globalThis.window=previous;}
});
