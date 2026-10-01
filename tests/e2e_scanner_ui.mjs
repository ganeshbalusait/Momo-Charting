import { chromium } from "playwright";

function assert(condition, message) {
  if (!condition) {
    throw new Error(message);
  }
}

async function waitForNonEmptyText(locator, timeoutMs = 15000) {
  const started = Date.now();
  while ((Date.now() - started) < timeoutMs) {
    const text = (await locator.innerText()).trim();
    if (text) {
      return text;
    }
    await locator.page().waitForTimeout(250);
  }
  return (await locator.innerText()).trim();
}

async function openApp(page, url) {
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
}

async function clickNav(page, label) {
  const navButtons = page.locator("button").filter({ hasText: new RegExp(label, "i") });
  await navButtons.first().click();
  await page.waitForTimeout(1500);
}

async function readCard(page, heading) {
  const section = page.locator("section").filter({ has: page.getByText(heading, { exact: true }) }).first();
  assert(await section.count(), `Missing section: ${heading}`);
  return section;
}

async function testTradingTerminalShell(page) {
  const shell = page.getByTestId("app-shell");
  assert(await shell.count() === 1, "Trading terminal shell did not render");
  const navigation = page.getByTestId("primary-navigation");
  assert(await navigation.count() === 1, "Primary trading navigation did not render");
  assert(await page.getByText("AGENTIC", { exact: true }).count() === 1, "AGENTIC brand did not render");
  assert(await page.getByText("CONNECTED DATA", { exact: true }).count() <= 1, "Duplicate connected-data summary rendered");
}

async function testScannerCommandBar(page) {
  await clickNav(page, "Scanner");
  const mag7Button = page.getByTestId("scanner-universe-mag7");
  const watchlistButton = page.getByTestId("scanner-universe-watchlist");
  const scanButton = page.getByTestId("scanner-scan-now");
  assert(await mag7Button.count() === 1, "MAG7 universe control missing");
  assert(await watchlistButton.count() === 1, "Watchlist universe control missing");
  assert(await scanButton.count() === 1, "Scan Now control missing");

  await watchlistButton.click();
  assert((await watchlistButton.getAttribute("class") || "").includes("is-active"), "Watchlist universe did not activate");
  await mag7Button.click();
  assert((await mag7Button.getAttribute("class") || "").includes("is-active"), "MAG7 universe did not reactivate");

  const resultTable = page.locator('[data-table-id="stock-scanner-mag7-results"]');
  assert(await resultTable.count() === 1, "MAG7 scanner result table missing");
  const columnPicker = resultTable.locator("details.table-column-picker > summary");
  assert(await columnPicker.count() === 1, "MAG7 result column picker missing");
  await columnPicker.click();
  assert(await resultTable.getByRole("button", { name: "Reset" }).count() === 1, "Column picker menu did not open");
  await columnPicker.click();
}

async function testScannerHistory(page) {
  await clickNav(page, "Scanner");
  await page.waitForTimeout(5000);

  const mag7HistorySection = await readCard(page, "SCANNER HISTORY - MAG7");
  const watchlistHistorySection = await readCard(page, "SCANNER HISTORY");

  const mag7SectionText = await waitForNonEmptyText(mag7HistorySection);
  assert(mag7SectionText.includes("Daily symbols:"), "MAG7 history summary did not render");
  assert(
    mag7SectionText.includes("AMDL") || mag7SectionText.includes("No MAG7 scanner history for this date yet."),
    "MAG7 history table did not render either a row or the expected empty state",
  );

  const mag7SearchInput = mag7HistorySection.locator("input[placeholder='Ticker']").first();
  await mag7SearchInput.fill("AMDL");
  await mag7HistorySection.getByRole("button", { name: "Search" }).first().click();
  await page.waitForTimeout(1000);
  const mag7SearchText = await waitForNonEmptyText(mag7HistorySection);
  assert(mag7SearchText.includes("AMDL"), "MAG7 history search did not keep AMDL visible");

  await page.waitForTimeout(7000);
  const mag7StableText = await waitForNonEmptyText(mag7HistorySection);
  assert(mag7StableText.includes("AMDL"), "MAG7 history row disappeared during refresh");

  const watchlistText = await waitForNonEmptyText(watchlistHistorySection);
  assert(watchlistText.includes("Daily symbols:"), "Watchlist history summary did not render");
}

async function testOiScannerPage(page) {
  await clickNav(page, "OI Scanner");
  await page.waitForTimeout(5000);

  const bodyText = await waitForNonEmptyText(page.locator("body"));
  assert(bodyText.includes("SCANNER RESULT MAG7"), "OI scanner result MAG7 section missing");
  assert(bodyText.includes("WATCHLIST REVIEW MAG7"), "OI watchlist review MAG7 section missing");
  assert(bodyText.includes("SCANNER HISTORY - MAG7"), "OI scanner history MAG7 section missing");
  assert(bodyText.includes("WATCHLIST REVIEW HISTORY - MAG7"), "OI review history MAG7 section missing");
  assert(bodyText.includes("SCANNER HISTORY"), "OI scanner history section missing");
}

async function testSettingsStorage(page) {
  await clickNav(page, "Settings");
  await page.waitForTimeout(1500);

  const settingsSection = await readCard(page, "SCANNER STORAGE");
  const settingsText = await waitForNonEmptyText(settingsSection);
  assert(settingsText.includes("Scanner History Retention Days"), "Scanner storage settings did not render");

  const retentionInput = settingsSection.locator("input[type='number']").first();
  const originalRetention = await retentionInput.inputValue();
  await retentionInput.fill("45");
  await settingsSection.getByRole("button", { name: "Save Scanner Storage" }).click();
  await page.waitForTimeout(1500);

  const refreshedText = await waitForNonEmptyText(settingsSection);
  assert(refreshedText.includes("45"), "Scanner storage retention value did not stay visible after save");

  await retentionInput.fill(originalRetention || "60");
  await settingsSection.getByRole("button", { name: "Save Scanner Storage" }).click();
  await page.waitForTimeout(1000);
}

async function testMobileLayout(browser, baseUrl) {
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
  try {
    await openApp(page, baseUrl);
    const layout = await page.evaluate(() => ({
      bodyWidth: document.body.scrollWidth,
      viewportWidth: document.documentElement.clientWidth,
      navVisible: Boolean(document.querySelector('[data-testid="primary-navigation"]')),
    }));
    assert(layout.navVisible, "Mobile navigation did not render");
    assert(layout.bodyWidth <= layout.viewportWidth + 2, `Mobile page overflows horizontally: ${layout.bodyWidth} > ${layout.viewportWidth}`);
  } finally {
    await page.close();
  }
}

export async function runScannerUiE2E({
  baseUrl = "http://127.0.0.1:5173",
  executablePath = "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
} = {}) {
  const browser = await chromium.launch({ headless: true, executablePath });
  const page = await browser.newPage({ viewport: { width: 1800, height: 3400 } });
  try {
    await openApp(page, baseUrl);
    const bodyText = await waitForNonEmptyText(page.locator("body"));
    assert(bodyText.includes("AGENTIC"), "App shell did not load");
    assert(bodyText.includes("Dashboard"), "Dashboard landing view did not render");

    await testTradingTerminalShell(page);
    await testScannerCommandBar(page);
    await testScannerHistory(page);
    await testOiScannerPage(page);
    await testSettingsStorage(page);
    await testMobileLayout(browser, baseUrl);

    return {
      ok: true,
      checked: [
        "app_shell",
        "trading_terminal_shell",
        "scanner_universe_switch",
        "scanner_column_picker",
        "scanner_history_mag7_visible",
        "scanner_history_search",
        "scanner_history_stable_after_refresh",
        "watchlist_history_visible",
        "oi_scanner_sections_visible",
        "settings_storage_visible",
        "settings_storage_save",
        "mobile_navigation_visible",
        "mobile_no_horizontal_overflow",
      ],
    };
  } finally {
    await browser.close();
  }
}
