// Renders public/icons/*.png from scripts/app-icon.svg.mjs with headless Chromium.
import { chromium } from "playwright";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { appIconSvg } from "./app-icon.svg.mjs";

const out = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../public/icons");
const jobs = [
  ["icon-512.png", 512, { shape: "rounded" }],
  ["icon-192.png", 192, { shape: "rounded" }],
  ["favicon-32.png", 32, { shape: "rounded" }],
  ["apple-touch-icon.png", 180, { shape: "square" }],
  ["icon-maskable-512.png", 512, { shape: "square", scale: 0.8 }],
  ["icon-maskable-192.png", 192, { shape: "square", scale: 0.8 }],
];

const browser = await chromium.launch();
const page = await browser.newPage({ deviceScaleFactor: 1 });
for (const [name, size, opts] of jobs) {
  await page.setViewportSize({ width: size, height: size });
  const svg = appIconSvg(opts).replace('width="512" height="512" viewBox', `width="${size}" height="${size}" viewBox`);
  await page.setContent(`<html><body style="margin:0;background:transparent">${svg}</body></html>`);
  await page.locator("svg").screenshot({ path: path.join(out, name), omitBackground: true });
  console.log("wrote", name);
}
await browser.close();
