import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
const FE = fileURLToPath(new URL("../frontend/", import.meta.url));
const { chromium } = createRequire(path.join(FE, "package.json"))("playwright");
const b = await chromium.launch();
const page = await b.newPage({ viewport: { width: 1500, height: 900 } });
const errs = [], chartReqs = [];
page.on("pageerror", e => errs.push(String(e).slice(0, 200)));
page.on("console", m => { if (m.type() === "error") errs.push("console: " + m.text().slice(0, 160)); });
page.on("request", r => { if (/oi-finder-chart/.test(r.url())) chartReqs.push(r.url().slice(0, 110)); });

await page.goto("http://127.0.0.1:5173/", { waitUntil: "domcontentloaded", timeout: 60000 });
await page.waitForTimeout(4000);
await page.evaluate(() => {
  const b = [...document.querySelectorAll("button")].find(x => x.innerText.trim() === "Chart");
  if (b) b.click();
});
await page.waitForTimeout(15000);
const before = chartReqs.length;
const strip = await page.evaluate(() => (document.querySelector(".oi-finder-chart-ohlc") || {}).innerText || "");
console.log("STRIP:", JSON.stringify((strip || "").replace(/\s+/g, " ").slice(0, 190)));
console.log("canvases:", await page.evaluate(() => document.querySelectorAll("canvas").length));

// simulate a phone tab coming back
await page.evaluate(() => {
  Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
  window.dispatchEvent(new Event("pageshow"));
  window.dispatchEvent(new Event("online"));
});
await page.waitForTimeout(6000);
console.log("chart requests before wake:", before, "| after:", chartReqs.length);
console.log("PAGE ERRORS:", errs.length); errs.slice(0, 5).forEach(e => console.log("   ", e));
await b.close();
