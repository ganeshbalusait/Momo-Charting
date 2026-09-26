import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
const cssSource = readFileSync(new URL("./index.css", import.meta.url), "utf8");

const mag7ScannerPanel = (() => {
  const start = appSource.indexOf('{activeView === "Mag7 Scanner" && (');
  const end = appSource.indexOf('{activeView === "Option Watchlist" && (', start);
  assert.ok(start > 0 && end > start, "Mag7 Scanner panel not found");
  return appSource.slice(start, end);
})();

test("Mag7 (MomoX) scanner cards render the latest headline for the scanned underlying", () => {
  assert.match(
    mag7ScannerPanel,
    /<small>\{mappedLabel\}<\/small>\n\s*<div className="mag7-scanner-news">\{renderScannerNewsCell\(mag7ScannerNewsSymbol\(row\)\)\}<\/div>/,
  );
  // The underlying (row.mapped) is the news ticker; the typed ticker only stands in when mapped has nothing stored.
  const start = appSource.indexOf("const mag7ScannerNewsSymbol = (row) => {");
  const end = appSource.indexOf("const mag7ScannerNewsSymbols = ", start);
  const picker = appSource.slice(start, end);
  assert.match(picker, /const mapped = String\(row\?\.mapped \|\| ""\)\.trim\(\)\.toUpperCase\(\);/);
  assert.match(picker, /!oiNewsBySymbol\.has\(mapped\) && oiNewsBySymbol\.has\(source\)\) return source;/);
  assert.match(picker, /return mapped;\n\s*\};/);
  // Edit/delete actions stay on the card.
  assert.match(mag7ScannerPanel, /onClick=\{\(\) => startEditMag7ScannerSymbol\(row\.source\)\}/);
  assert.match(mag7ScannerPanel, /onClick=\{\(\) => deleteMag7ScannerSymbol\(row\.source\)\}/);
});

test("Refresh news button posts the visible mapped + source tickers to /api/news-feed", () => {
  assert.match(
    appSource,
    /const refreshMag7ScannerNews = \(\) => runAction\("\/api\/news-feed", \{\n\s*method: "POST",\n\s*headers: \{ "Content-Type": "application\/json" \},\n\s*body: JSON\.stringify\(\{ symbols: mag7ScannerNewsSymbols \}\),\n\s*\}, "mag7-scanner-news"\);/,
  );
  assert.match(
    appSource,
    /const mag7ScannerNewsSymbols = \[\.\.\.new Set\(\n\s*visibleMag7OptionRows\n\s*\.flatMap\(\(row\) => \[row\.mapped, row\.source\]\)\n\s*\.map\(\(symbol\) => String\(symbol \|\| ""\)\.trim\(\)\.toUpperCase\(\)\)\n\s*\.filter\(Boolean\),\n\s*\)\]\.slice\(0, 40\);/,
  );
  assert.match(mag7ScannerPanel, /onClick=\{refreshMag7ScannerNews\}/);
  assert.match(mag7ScannerPanel, /<RefreshCw className=\{submitting === "mag7-scanner-news" \? "is-spinning" : ""\} size=\{14\} \/>/);
  assert.match(mag7ScannerPanel, /disabled=\{!mag7ScannerNewsSymbols\.length \|\| submitting === "mag7-scanner-news"\}/);
});

test("Mag7 scanner meta line reads dashboard.newsFeedMeta and reports coverage", () => {
  assert.match(appSource, /const mag7ScannerNewsMeta = dashboard\.newsFeedMeta && typeof dashboard\.newsFeedMeta === "object" \? dashboard\.newsFeedMeta : null;/);
  assert.match(
    mag7ScannerPanel,
    /\$\{Number\(mag7ScannerNewsMeta\.headlinesRefreshed \|\| 0\)\} headlines · \$\{mag7ScannerNewsSourcesOk\}\/\$\{mag7ScannerNewsSources\.length\} sources OK · last scrape \$\{formatDateTime\(mag7ScannerNewsMeta\.refreshedAt\)\}/,
  );
  assert.match(mag7ScannerPanel, /"Press Refresh news to scrape ticker-tagged headlines for these tickers\."/);
  assert.match(mag7ScannerPanel, /Unavailable: \{mag7ScannerNewsUnavailable\.join\(", "\)\}/);
  assert.match(mag7ScannerPanel, /\{mag7ScannerNewsCoveredCount\}\/\{visibleMag7OptionRows\.length\} tickers have news/);
  assert.match(appSource, /\.filter\(\(source\) => \["blocked", "error"\]\.includes\(String\(source\?\.status \|\| ""\)\.toLowerCase\(\)\)\)/);
});

test("scanner news stays information-only: no scoring, sizing or order calls read it", () => {
  const start = appSource.indexOf("const mag7ScannerNewsSymbol = (row) => {");
  const end = appSource.indexOf('"mag7-scanner-news");', start);
  const block = appSource.slice(start, end);
  assert.doesNotMatch(block, /placeOrder|score|rank|sizing|quantity/i);
});

test("Charts & OI dock and OI Finder news items show publisher via aggregator and the summary", () => {
  assert.match(appSource, /function newsPublisherLabel\(item\) \{\n\s*const publisher = String\(item\?\.source \|\| ""\)\.trim\(\) \|\| "News Feed";\n\s*const aggregator = String\(item\?\.via \|\| ""\)\.trim\(\);/);
  const dockStart = appSource.indexOf("const dockRelatedNews = ");
  const dock = appSource.slice(dockStart, appSource.indexOf("const highOiListPanel = ", dockStart));
  assert.match(dock, /<small>\{newsPublisherLabel\(item\)\} · \{item\?\.published_at \? formatTimeLabel\(item\.published_at\) : "Stored news"\}<\/small>/);
  assert.match(dock, /\{item\?\.summary \? <p className="news-item-summary">\{item\.summary\}<\/p> : null\}/);
  const finderStart = appSource.indexOf('<section className="oi-finder-related-news data-card"');
  const finder = appSource.slice(finderStart, appSource.indexOf('<div className="oi-finder-side-stack">', finderStart));
  assert.match(finder, /<small>\{newsPublisherLabel\(item\)\} · \{item\?\.published_at \? formatTimeLabel\(item\.published_at\) : "Stored news"\}<\/small>/);
  assert.match(finder, /\{item\?\.summary \? <p className="news-item-summary">\{item\.summary\}<\/p> : null\}/);
});

test("Mag7 scanner news styles wrap inside the card and stay single-column on phones", () => {
  assert.match(cssSource, /\.mag7-scanner-news \{[^}]*min-width: 0;[^}]*\}/);
  assert.match(cssSource, /\.mag7-scanner-news \.scanner-news-cell \{[^}]*white-space: normal;[^}]*\}/);
  assert.match(cssSource, /\.mag7-scanner-news \.scanner-news-headline \{[^}]*-webkit-line-clamp: 2;[^}]*\}/);
  assert.match(cssSource, /\.mag7-scanner-news > \.oi-news-status \{[^}]*justify-self: start;[^}]*\}/);
  assert.match(cssSource, /\.watchlist-panel-head p\.mag7-scanner-news-meta \{[^}]*font-size: 11px;[^}]*\}/);
  const singleColumn = /@media \(max-width: 760px\) \{\n\s*\.mag7-watchlist-grid \{\n\s*grid-template-columns: 1fr;/.exec(cssSource);
  assert.ok(singleColumn, "760px single-column rule for the Mag7 grid");
  const twoColumn = /@media \(max-width: 960px\) \{[\s\S]*?\.mag7-watchlist-grid \{\n\s*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\);/.exec(cssSource);
  assert.ok(twoColumn, "960px two-column rule for the Mag7 grid");
  assert.ok(singleColumn.index > twoColumn.index, "760px single-column rule must come after the 960px two-column rule so it wins in the cascade");
  assert.match(cssSource, /\.news-item-summary \{[^}]*-webkit-line-clamp: 2;[^}]*\}/);
});
