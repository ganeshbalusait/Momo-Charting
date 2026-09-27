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

test("news cell is coloured by sentiment, never by headline age", () => {
  const start = appSource.indexOf("const renderScannerNewsCell = ");
  const end = appSource.indexOf("const renderOiNewsLink = ");
  const cell = appSource.slice(start, end);
  assert.match(cell, /const tone = newsSentimentTone\(news\.sentiment\);/);
  assert.match(cell, /className=\{`oi-news-status oi-news-\$\{tone\.key\}\$\{tone\.flash \? " oi-news-flash" : ""\}`\}/);
  assert.match(cell, /\{tone\.label\}/);
  // No age-based branch is left: freshness only drives the News Feed filter.
  assert.doesNotMatch(cell, /newsFreshnessMeta|isFresh|oi-news-fresh|oi-news-stored|"Fresh"|"Stored"/);
  // The published time still shows as plain meta text.
  assert.match(cell, /news\.published_at \? formatTimeLabel\(news\.published_at\) : ""/);
  assert.match(appSource, /import \{ newsSentimentTone \} from "\.\/newsSentimentTone";/);
});

test("positive news flashes green and negative news flashes red; neutral stays still", () => {
  const cssSource = readFileSync(new URL("./index.css", import.meta.url), "utf8");
  assert.match(cssSource, /\.oi-news-positive \{[^}]*color: #75f1bf;[^}]*\}/);
  assert.match(cssSource, /\.oi-news-negative \{[^}]*color: #ff9eac;[^}]*\}/);
  assert.match(cssSource, /\.oi-news-positive\.oi-news-flash \{\n\s*animation: oi-news-flash-positive [^;]*infinite;\n\}/);
  assert.match(cssSource, /\.oi-news-negative\.oi-news-flash \{\n\s*animation: oi-news-flash-negative [^;]*infinite;\n\}/);
  assert.match(cssSource, /@keyframes oi-news-flash-positive \{/);
  assert.match(cssSource, /@keyframes oi-news-flash-negative \{/);
  assert.doesNotMatch(cssSource, /\.oi-news-neutral[^{]*\{[^}]*animation/);
  assert.match(cssSource, /@media \(prefers-reduced-motion: reduce\) \{\n\s*\.oi-news-flash \{\n\s*animation: none;/);
  assert.doesNotMatch(cssSource, /\.oi-news-fresh|\.oi-news-stored/);
});

test("a sentiment change re-renders stable scanner rows", () => {
  assert.match(appSource, /row\?\.__news\?\.headline \?\? "",\n\s*row\?\.__news\?\.sentiment \?\? "",/);
});

test("an AI-labelled headline shows the AI tag with its reason; keyword labels do not", () => {
  const start = appSource.indexOf("const renderNewsAiTag = ");
  const end = appSource.indexOf("const renderScannerNewsCell = ");
  const helpers = appSource.slice(start, end);
  assert.match(helpers, /String\(news\?\.sentiment_source \|\| ""\) === "ai"\n\s*\? <span className="news-ai-tag"/);
  assert.match(helpers, /title=\{news\?\.sentiment_reason \? `AI: \$\{news\.sentiment_reason\}` : "Labelled by AI"\}/);
  assert.match(helpers, /: null\);/);
  const cellStart = appSource.indexOf("const renderScannerNewsCell = ");
  const cell = appSource.slice(cellStart, appSource.indexOf("const renderOiNewsLink = "));
  assert.match(cell, /\{tone\.label\}\n\s*<\/a>\n\s*\{renderNewsAiTag\(news\)\}/);
  // The News Feed's Sentiment column carries the same tag.
  assert.match(appSource, /key: "sentiment", label: "Sentiment", render: \(value, row\) => \(\n\s*<span className="news-sentiment-cell">[\s\S]*?\{renderNewsAiTag\(row\)\}/);
  const cssSource = readFileSync(new URL("./index.css", import.meta.url), "utf8");
  assert.match(cssSource, /\.news-ai-tag \{[^}]*cursor: help;[^}]*\}/);
});
