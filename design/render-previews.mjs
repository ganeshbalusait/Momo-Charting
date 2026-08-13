import puppeteer from "puppeteer-core";

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const SITE = "file:///C:/GANESH/AgenticAI-Trading%207/.claude/worktrees/momox-app-design-029fd0/web/index.html";
const OUT = "C:\\GANESH\\AgenticAI-Trading 7\\.claude\\worktrees\\momox-app-design-029fd0\\web";

const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: "new",
  userDataDir: `${OUT}\\..\\tmp-mobile\\prof`,
  args: ["--no-sandbox", "--disable-gpu", "--hide-scrollbars", "--allow-file-access-from-files"],
});

// --- desktop hero, retina ---
const page = await browser.newPage();
await page.setViewport({ width: 1440, height: 900, deviceScaleFactor: 2 });
await page.goto(SITE, { waitUntil: "networkidle2", timeout: 60000 });
await wait(2500);
await page.screenshot({ path: `${OUT}\\preview-hero.png` });

// --- full page, 1x so the file stays sane ---
const full = await browser.newPage();
await full.setViewport({ width: 1440, height: 900, deviceScaleFactor: 1 });
await full.goto(SITE, { waitUntil: "networkidle2", timeout: 60000 });
await wait(2500);
const buf = await full.screenshot({ path: `${OUT}\\preview-full.png`, fullPage: true });
const m = await full.evaluate(() => ({
  h: document.documentElement.scrollHeight,
  font: getComputedStyle(document.querySelector("h1")).fontFamily,
}));
console.log(`full page -> ${buf.length} bytes  ${JSON.stringify(m)}`);

// --- mobile ---
const mob = await browser.newPage();
await mob.setViewport({ width: 390, height: 844, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
await mob.goto(SITE, { waitUntil: "networkidle2", timeout: 60000 });
await wait(2000);
await mob.screenshot({ path: `${OUT}\\preview-mobile.png` });

await browser.close();
console.log("done");
