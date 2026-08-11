import puppeteer from "puppeteer-core";

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const BASE = "http://localhost:3001";
const OUT = "C:\\GANESH\\AgenticAI-Trading 7\\tmp-mobile";
const TABS = ["Charts & OI", "OI Finder", "Watchlist", "Settings"];

const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: "new",
  userDataDir: `${OUT}\\prof`,
  args: ["--no-sandbox", "--disable-gpu", "--hide-scrollbars"],
});

const page = await browser.newPage();
await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 3, isMobile: true, hasTouch: true });
await page.setUserAgent(
  "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
);

await page.goto(BASE, { waitUntil: "networkidle2", timeout: 60000 });
await page.waitForSelector('[data-testid="app-shell"]', { timeout: 30000 });
await wait(4000);

// Report the nav labels actually present so mismatches are visible rather than silent.
// innerText is "" here because the label spans are display:none on mobile — use textContent.
const navLabels = await page.$$eval('[data-testid="primary-navigation"] nav button', (els) =>
  els.map((el) => (el.textContent || el.title || el.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim()),
);
console.log("NAV LABELS:", JSON.stringify(navLabels));

async function capture(name) {
  const base = `${OUT}\\${slug(name)}`;
  await page.screenshot({ path: `${base}-viewport.png` });
  const full = await page.screenshot({ path: `${base}-full.png`, fullPage: true });
  const metrics = await page.evaluate(() => ({
    scrollH: document.documentElement.scrollHeight,
    scrollW: document.documentElement.scrollWidth,
    innerW: window.innerWidth,
  }));
  console.log(`${name} -> ${slug(name)}  fullPageBytes=${full.length}  ${JSON.stringify(metrics)}`);
}

for (const tab of TABS) {
  const clicked = await page.evaluate((label) => {
    const buttons = [...document.querySelectorAll('[data-testid="primary-navigation"] nav button')];
    const text = (b) => (b.textContent || b.title || b.getAttribute("aria-label") || "").replace(/\s+/g, " ").trim();
    const hit = buttons.find((b) => text(b).includes(label));
    if (!hit) return false;
    hit.scrollIntoView({ block: "nearest", inline: "center" });
    hit.click();
    return true;
  }, tab);
  if (!clicked) {
    console.log(`SKIP ${tab} — no matching nav button`);
    continue;
  }
  await wait(5000);
  await capture(tab);
}

// The one view reachable straight from the URL.
await page.goto(`${BASE}/?popout=chart&symbol=AAPL`, { waitUntil: "networkidle2", timeout: 60000 });
await wait(7000);
await capture("charts popout");

await browser.close();
console.log("done");
