// Screenshots for the Learn page.
//
// Callouts live HERE, not baked into the PNG. Repositioning a marker, fixing
// its wording, or translating it never means re-shooting the image - and the
// text stays real text, so it is selectable and a screen reader can read it.
//
// Every file must be reproducible by `npm run learn:shots`. Never hand-edit an
// image in this folder.
//
// x/y are PERCENTAGES of the image, and the marker is centred on them.

export const LEARN_SCREENSHOTS = Object.freeze([
  { id: "chart-workstation", file: "chart-workstation.png", width: 1357, height: 856,
    capturedOn: "2026-08-29",
    alt: "A single five-minute chart with candles, moving averages and VWAP drawn over them, and the option board for the same ticker beside it.",
    callouts: [
      { n: 1, x: 8, y: 8, text: "Type the ticker here. Everything else on the page follows it." },
      { n: 2, x: 13, y: 11, text: "The timeframe row. Most people work on the 5-minute and step out to the hour to check it." },
      { n: 3, x: 30, y: 3.2, text: "One-click tickers along the top: pick one and the chart and the option board both jump to it." },
      { n: 4, x: 36, y: 38, text: "The candles, with the moving averages and VWAP drawn straight over them." },
      { n: 5, x: 30, y: 78, text: "The panels underneath ask the same trend question on several timeframes at once." },
      { n: 6, x: 86.5, y: 45, text: "The option board for the same ticker, right beside the chart, so you never have to leave the page to check a strike." },
    ] },
  { id: "chart-oi-walls", file: "chart-oi-walls.png", width: 987, height: 856,
    capturedOn: "2026-08-29",
    alt: "The chart's price area with coloured horizontal option-wall bands above and below the live price.",
    callouts: [
      { n: 1, x: 45, y: 24, text: "A band above price: a strike carrying a large number of open call contracts." },
      { n: 2, x: 88, y: 24, text: "The label counts the contracts stacked there and names the expiry they belong to." },
      { n: 3, x: 45, y: 44.5, text: "A band below price, where the open put contracts are. Price often slows down as it reaches one." },
      { n: 4, x: 84, y: 38.5, text: "The live price on the right-hand axis, so you can see which band it is heading for." },
      { n: 5, x: 45, y: 65.7, text: "Bands further away still matter: they are the next places price is likely to stall." },
    ] },
  { id: "indicators-panel", file: "indicators-panel.png", width: 620, height: 703,
    capturedOn: "2026-08-29",
    alt: "The indicators and studies panel open, listing every available study above the rows of checkboxes that switch them on.",
    callouts: [
      { n: 1, x: 26.5, y: 11.5, text: "Save the studies you have chosen for every ticker on this timeframe at once." },
      { n: 2, x: 26, y: 24.2, text: "Everything that can be added to a chart, listed in one place." },
      { n: 3, x: 4.5, y: 58, text: "Each study has a checkbox. Tick it and it appears on the chart straight away." },
      { n: 4, x: 94.5, y: 58, text: "The gear opens that study's own settings, such as its length or its colour." },
      { n: 5, x: 20, y: 66, text: "The handful already ticked when you arrive is deliberately small. Add to it only when you know what the new study answers." },
    ] },
  { id: "high-oi-board", file: "high-oi-board.png", width: 354, height: 606,
    capturedOn: "2026-08-29",
    alt: "The High OI board showing the expiry header, the expected move, and rows of call and put strikes either side of the last price.",
    callouts: [
      { n: 1, x: 16, y: 8.9, text: "The expiry you are looking at, and how many days are left on it." },
      { n: 2, x: 29, y: 15.9, text: "Two tabs: High OI ranks only the crowded strikes, Chain is the full strike-by-strike table." },
      { n: 3, x: 8, y: 27.4, text: "The expected move: roughly how far the stock is priced to travel before this expiry." },
      { n: 4, x: 63, y: 52.6, text: "Calls above the price, with how many contracts are already open at each strike." },
      { n: 5, x: 44, y: 68.2, text: "Where the stock is trading now. Calls sit above this line, puts below it." },
      { n: 6, x: 63, y: 87, text: "Puts below the price. The longest bars are the strikes traders are leaning on." },
    ] },
  { id: "auto-alert-panel", file: "auto-alert-panel.png", width: 1357, height: 1003,
    capturedOn: "2026-08-29",
    alt: "The Auto Alert page: its switches and add-ticker box above one card per watched ticker, with the past sessions listed underneath.",
    callouts: [
      { n: 1, x: 2.5, y: 10.2, text: "The master switch. Turn it off and nothing is watched for you." },
      { n: 2, x: 8, y: 10.2, text: "The seven big names are watched automatically, without you adding them." },
      { n: 3, x: 13.5, y: 13.8, text: "Add any other ticker you care about here." },
      { n: 4, x: 17, y: 26.3, text: "One card per watched ticker, with its price and the state of today's ladder." },
      { n: 5, x: 50, y: 28.5, text: "Outside the morning build the card says so plainly, rather than showing you levels from an old session as if they were live." },
      { n: 6, x: 10, y: 50.6, text: "Past sessions are kept, so you can look back at what fired and when." },
    ] },
  { id: "momx-board", file: "momx-board.png", width: 1400, height: 800,
    capturedOn: "", alt: "The MomX board: rows of tickers with relative volume, squeeze and Skittles columns.", callouts: [] },
  { id: "premarket-scanner", file: "premarket-scanner.png", width: 1400, height: 800,
    capturedOn: "", alt: "The premarket Scanner board showing matched tickers with their momentum signals.", callouts: [] },
  { id: "phone-more-sheet", file: "phone-more-sheet.png", width: 375, height: 305,
    capturedOn: "2026-08-29",
    alt: "The phone More sheet, with Learn listed first.",
    callouts: [
      { n: 1, x: 25, y: 27.5, text: "Learn sits first in this list, so you can always get back to this page from a phone." },
      { n: 2, x: 78, y: 86, text: "Everything the five buttons along the bottom cannot fit lives behind More, Settings included." },
    ] },
  { id: "settings-schwab-keys", file: "settings-schwab-keys.png", width: 980, height: 1065,
    capturedOn: "2026-08-30",
    alt: "The Schwab connection card in Settings: the OAuth renewal steps, the key and secret boxes, the Save keys and Authenticate buttons, and the callback URL paste box.",
    callouts: [
      { n: 1, x: 25, y: 62.5, text: "Paste your App Key from the Schwab tab into this box." },
      { n: 2, x: 74, y: 62.5, text: "Paste your Secret into this box." },
      { n: 3, x: 6.5, y: 74, text: "Press Save keys first." },
      { n: 4, x: 22.5, y: 74, text: "Then press Authenticate Market Data - a Schwab window opens for you to log in and Allow." },
      { n: 5, x: 25, y: 87, text: "After Schwab sends you to the broken-looking 127.0.0.1 page, copy the whole address and paste it here, then press Complete below it." },
      { n: 6, x: 32, y: 46.3, text: "Your connection's health shows here - green Authenticated when it is working." },
    ] },
  { id: "settings-alpaca-keys", file: "settings-alpaca-keys.png", width: 1372, height: 378,
    capturedOn: "2026-08-30",
    alt: "The MY DATA PROVIDER KEYS card in Settings with the Alpaca Market Data key boxes and the Save Alpaca keys button.",
    callouts: [
      { n: 1, x: 25, y: 50, text: "Paste your API Key ID from the Alpaca tab into this box." },
      { n: 2, x: 25, y: 70, text: "Paste your Secret Key into this box." },
      { n: 3, x: 25, y: 84.5, text: "Press Save Alpaca keys. Done - the masked key appears at the top right of the card." },
    ] },
  { id: "momo-settings-modal", file: "momo-settings-modal.png", width: 320, height: 369,
    capturedOn: "2026-08-30",
    alt: "The MOMO ALERT window with a checkbox and an RVOL threshold box per timeframe, the Sound switch, and the phone-push channel with its Generate button.",
    callouts: [
      { n: 1, x: 8, y: 29, text: "Tick a timeframe to arm it. 5m is on when you arrive." },
      { n: 2, x: 43, y: 29, text: "How unusual volume must be before it alerts: 3 means three times normal." },
      { n: 3, x: 8, y: 68.5, text: "Sound on or off for the in-app alert." },
      { n: 4, x: 87, y: 82, text: "Generate makes your private phone channel name." },
      { n: 5, x: 40, y: 82, text: "Subscribe to this exact name in the ntfy app on your phone." },
    ] },
]);

// Ids with no image on disk yet. The test asserts these files are ABSENT, so an
// id cannot sit here forever pretending an image is coming.
//
// premarket-scanner can only be captured on a trading morning between 06:00 and
// 09:30 ET - outside that window the board is empty and the picture teaches
// nothing. Confirmed by trying, 2026-08-29 (a Saturday): the board came back
// with 0 matches, 0/9 live tapes and a "no data yet today" note, so the shot
// was deleted rather than wired up.
//
// momx-board reads best during regular hours. The same Saturday run produced a
// board with two stale rows and three-quarters of the frame empty, which reads
// as broken rather than as a shortlist - deleted too. Re-run
// `pnpm run learn:shots momx-board` while the market is open.
export const PENDING_CAPTURE = Object.freeze([
  "momx-board", "premarket-scanner",
]);

const BY_ID = new Map(LEARN_SCREENSHOTS.map((shot) => [shot.id, shot]));

export function screenshotById(id) {
  return BY_ID.get(id) || null;
}

// A shot is renderable only once it has actually been captured. The Learn page
// falls back to its text treatment for anything still pending, so a missing
// image is a quieter page, never a broken-image icon.
export function isScreenshotReady(id) {
  return Boolean(BY_ID.has(id) && !PENDING_CAPTURE.includes(id));
}
