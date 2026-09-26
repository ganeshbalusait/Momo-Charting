import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

test("stock scanner shows the latest ticker-tagged headline next to Why Setup", () => {
  assert.match(
    appSource,
    /\{ key: "setup_name", label: "Why Setup"[^\n]*\},\n\s*\{ key: "__news", label: "News", sortable: false, render: renderStockNewsCell \}/,
  );
});

test("OI scanner tables render the same news cell as the stock scanner", () => {
  const oiNewsColumns = appSource.match(/\{ key: "__news", label: "News", sortable: false, render: renderOiNewsLink \}/g) || [];
  assert.equal(oiNewsColumns.length, 2);
  assert.match(appSource, /const renderOiNewsLink = \(_, row\) => renderScannerNewsCell\(row\.underlying \|\| row\.history_symbol \|\| row\.symbol, row\.__news\);/);
});

test("OI rows carry their headline so stable tables refresh when news arrives", () => {
  // DataTable's memo compares columns by shape, so a render closure alone
  // never re-renders a table whose rows are held stable.
  for (const name of ["oiScannerRows", "oiMag7ScannerRows", "selectedWatchlistDailyRows", "selectedMag7DailyRows"]) {
    assert.match(appSource, new RegExp(`useStableTableRows\\(withOiNews\\(${name}\\)\\);`), name);
  }
  assert.match(appSource, /row\?\.__news\?\.published_at \?\? "",\n\s*row\?\.__news\?\.headline \?\? "",/);
});

test("news cell links to the article and to the News Feed, never to trading actions", () => {
  const start = appSource.indexOf("const renderScannerNewsCell = ");
  const end = appSource.indexOf("const renderOiNewsLink = ");
  const cell = appSource.slice(start, end);
  assert.match(cell, /href=\{news\.url\} target="_blank" rel="noreferrer"/);
  assert.match(cell, /openTickerNews\(symbol\)/);
  assert.doesNotMatch(cell, /runAction|placeOrder|submit/);
});

test("saved scanner column profiles gain the News column once", () => {
  assert.match(appSource, /const profileSchemaVersion = 10;/);
  assert.match(
    appSource,
    /String\(storageKey\)\.includes\("scanner"\)\) \{\n\s*visibleKeys = \[\.\.\.visibleKeys, \.\.\.newsColumnKeys/,
  );
});
