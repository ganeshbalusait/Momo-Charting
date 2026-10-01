// The Learn page's words, as data. No JSX and no React here on purpose: this
// module is imported directly by node --test, which has no JSX transform, and
// that is what lets the tests check the copy and the navigation targets.
//
// Every `goTo` MUST be an exact optionNavItems label from App.jsx. A test
// enforces it, so a renamed page breaks the suite instead of leaving a dead
// button on this page.
//
// Copy rules (from the design doc, and binding):
//   - never claim the boards match thinkorswim to the decimal
//   - 2026-08-30: the trader reversed the shared-keys-only decision. Copy
//     MAY now walk a user through connecting their OWN broker keys, but ON
//     THE SETUP TAB ONLY (LEARN_SETUP_SECTIONS below) - still with no file
//     names and no internals. The Learn tab keeps the old rule, and its
//     tests still enforce it there.
//   - no data sources, no file names, no endpoint names, no internals

export const LEARN_ICON_NAMES = Object.freeze([
  "chart", "indicators", "options", "alerts", "scanner", "momx",
  "momo-alert", "own-keys", "health",
]);

export const LEARN_STEPS = Object.freeze([
  {
    id: "find-something-moving",
    number: 1,
    title: "Find something moving",
    body: [
      "Trading starts with a shortlist, not a chart. AGX gives you two ways to build one.",
      "Premarket watches nine large-cap names every trading morning between 6:00 and 9:30 AM ET and tells you which are waking up before the bell. MomX Scanner is the wider net: a few hundred names scored the same way, refreshed every minute, all day.",
      "Start with Premarket in the morning. Switch to MomX once the market is open.",
    ],
    goTo: "Premarket Scanner",
    goToLabel: "Open Premarket",
    screenshot: "premarket-scanner",
  },
  {
    id: "put-it-on-a-chart",
    number: 2,
    title: "Put it on a chart",
    body: [
      "Type a ticker at the top of the chart, pick a timeframe, and pick how many charts you want on screen at once.",
      "You can show anything from a single chart up to ten at a time, and there is a MAG7 layout that puts all seven big names side by side. Most people start with one chart on the 5-minute and add more later.",
    ],
    goTo: "Charts & OI",
    goToLabel: "Open the chart",
    screenshot: "chart-workstation",
  },
  {
    id: "read-the-trend",
    number: 3,
    title: "Read the trend",
    body: [
      "Your chart already has five lines on it before you touch anything: three exponential moving averages at 9, 21 and 50 bars, a 200-bar simple moving average, and VWAP, the volume-weighted average price.",
      "The short reading: when the faster lines sit above the slower ones and price sits above VWAP, buyers are in control on that timeframe. When they cross back the other way, that control is changing hands.",
      "Underneath the candles are two more panels showing the same question answered across several timeframes at once, so you can see whether the 5-minute move agrees with the hourly one.",
    ],
    goTo: "Charts & OI",
    goToLabel: "Open the chart",
    screenshot: "chart-workstation",
  },
  {
    id: "see-where-price-stalls",
    number: 4,
    title: "See where price is likely to stall",
    body: [
      "The red and green horizontal bands across the chart are option walls. Red bands sit above price where large numbers of call contracts are open; green bands sit below where large numbers of puts are.",
      "These are the prices where a lot of money has already taken a position, so price often slows down, stalls, or reverses when it reaches one. They are not guarantees. They are the levels worth knowing about before you enter.",
    ],
    goTo: "Charts & OI",
    goToLabel: "Show me the walls",
    screenshot: "chart-oi-walls",
  },
  {
    id: "check-the-option",
    number: 5,
    title: "Check the option itself",
    body: [
      "A good chart setup can still be a bad option. The Options page shows you the contract, not just the stock.",
      "For each strike you get how many days are left, its delta, how much volume and open interest it carries, and how that compares with the at-the-money strike. The expected move tells you roughly how far the stock is priced to travel before expiry.",
      "What you are looking for is a strike with real activity in it, close enough to the money to respond when the stock moves.",
    ],
    goTo: "Quick Options",
    goToLabel: "Open the Options board",
    screenshot: "high-oi-board",
  },
  {
    id: "let-the-app-watch",
    number: 6,
    title: "Let the app watch it for you",
    body: [
      "You cannot stare at nine charts at once, so hand the watching over.",
      "Every trading morning at 9:15 AM ET, AGX builds a ladder of the biggest call strikes above the price and the biggest put strikes below it, then tells you when price actually confirms through one. MAG7 is watched by default, and you can add your own tickers.",
      "You can also draw your own alert on any chart by clicking a price. It snaps to whatever level it lands on, so the alert is named after the thing you cared about.",
    ],
    goTo: "Auto Alert",
    goToLabel: "Open Alerts",
    screenshot: "auto-alert-panel",
  },
  {
    id: "save-your-setup",
    number: 7,
    title: "Save your setup",
    body: [
      "Once a chart looks the way you want it, save it. Your indicator choices can be saved for every ticker on that timeframe at once, so you set up the 5-minute chart once and every symbol you open inherits it.",
      "Your watchlist is shared across the scanners and the chart, so a ticker you save in one place shows up in the others.",
    ],
    goTo: "Watchlist",
    goToLabel: "Open the Watchlist",
    screenshot: null,
  },
  {
    id: "set-up-your-alerts",
    number: 8,
    title: "Let it find you: the Momo Alert",
    body: [
      "You do not have to watch the scanner. When a stock's volume suddenly runs times its normal - while the momentum scan passes - a MOMO banner and sound interrupt whatever panel you are on. That sudden-volume-plus-momentum combination is what the start of a real move looks like.",
      "Set it up once: on the MomX board, open the gear next to refresh, tick the timeframes you want watched, and set how unusual volume must be before it interrupts you. It alerts once per ticker per 15 minutes, and your settings are yours alone.",
      "To get the same alerts on your phone - locked, in your pocket, browser closed - install the free ntfy app, tap Generate under Phone push in that same gear window, and subscribe to the generated name in the app. The full walkthrough is in the Setup tab at the top of this page.",
    ],
    goTo: "MomX Scanner",
    goToLabel: "Open MomX",
    screenshot: null,
  },
]);

// The six reference sections. Each one explains what the THING is before it
// names a single control: the reader is a competent trader who has never seen
// this app, not a developer and not a beginner.
//
// Every block carries exactly one of `body`, `rows` or `chips`. A test pins
// that, because a block with two of them renders twice and a block with none
// renders an empty heading.
export const LEARN_SECTIONS = Object.freeze([
  {
    id: "charts",
    title: "Charts",
    icon: "chart",
    goTo: "Charts & OI",
    goToLabel: "Open the chart",
    screenshot: "chart-workstation",
    concept: [
      "A chart is one ticker's price history drawn as candles. Each candle covers a fixed slice of time and shows four things: where price opened, where it closed, and the highest and lowest it reached in between.",
      "The same stock tells a different story depending on the slice you choose. A 5-minute chart shows you today's fight; a daily chart shows you the trend that fight sits inside. Reading both is the point of having eleven timeframes rather than one.",
    ],
    blocks: [
      {
        heading: "Timeframes",
        body: [
          "3m, 5m, 10m, 15m, 30m, 1h, 2h, 4h, D (daily), W (weekly) and M (monthly).",
          "A common habit: check the 4-hour and daily for direction, then drop to the 5-minute to time the entry. If those disagree, the trade is a fight, not a trend.",
        ],
      },
      {
        heading: "Charts on screen at once",
        body: [
          "Single, 2 side by side, 2 stacked, 3 across, 3 grid, 3 stacked, 4 grid, 4 across, 4 stacked, 6 grid, 6 across, 7 across, 8 across, MAG7, 8 grid, 9 grid and 10 grid. Ten panels is the most you can have open at one time.",
          "MAG7 puts all seven big names in a single row. In the one-row layouts the column dividers can be dragged, and every panel keeps its own ticker and its own timeframe.",
        ],
      },
      {
        heading: "Drawing on the chart",
        rows: [
          { term: "Crosshair", meaning: "The default. Move the pointer to read any candle's values, and drag to move the chart." },
          { term: "Select", meaning: "Pick up a drawing you already made, to move it, restyle it or delete it." },
          { term: "Trend line", meaning: "A sloped line between two points, for drawing the path a move has been following." },
          { term: "Horizontal line", meaning: "A flat line at one price, for marking a level you care about." },
          { term: "Fibonacci retracement", meaning: "Drag across a move to mark the levels a pullback commonly stops at." },
          { term: "Rectangle", meaning: "Box off a zone rather than a single price, such as a consolidation range." },
          { term: "Brush", meaning: "Freehand drawing, for when a shape matters more than precision." },
          { term: "Text note", meaning: "Type a note directly on the chart so you remember why you marked something." },
          { term: "Price and time measure", meaning: "Drag from one point to another to read the distance in both price and time." },
        ],
      },
      {
        heading: "Before the bell and after it",
        body: [
          "Shaded areas mark pre-market and after-hours trading. Candles exist there, but far fewer people are trading, so moves can be larger and less reliable than they look.",
          "Vertical session lines mark the times of day that matter, so you can see at a glance where the open and the close fall inside all those candles.",
        ],
      },
      {
        heading: "Saving what you set up",
        body: [
          "The save buttons are named after the timeframe you are standing on, so on a 5-minute chart they read Save 5m for all tickers and Save 5m chart layout. Your panel arrangement and each panel's ticker and timeframe are kept for you automatically, so neither button is about those.",
          "Save 5m for all tickers applies your indicator choices to every symbol you open on the 5-minute, so you configure that timeframe once rather than once per ticker. Save 5m chart layout remembers the framing instead: how far back the chart is zoomed, where the latest candle sits, and how tall the lower panes are. Reset 5m puts that timeframe back to how it arrived.",
        ],
      },
      {
        heading: "Linking and popping out",
        body: [
          "The coloured link buttons tie surfaces together: set two of them to the same colour and changing the ticker in one changes it in the other. Set them to different colours to keep two tickers side by side without them following each other.",
          "Any chart can be popped out into its own window, which is how you fill a second monitor. The pop-out keeps its link colour, so it can still follow the main screen.",
        ],
      },
    ],
  },
  {
    id: "indicators",
    title: "Indicators and studies",
    icon: "indicators",
    goTo: "Charts & OI",
    goToLabel: "Open the indicator list",
    screenshot: "indicators-panel",
    concept: [
      "An indicator is a second opinion drawn on top of price. It does not know anything price does not; it re-arranges what already happened so a pattern is easier to see - a smoothed average, a measure of how compressed the range has got, a marker where two averages crossed.",
      "More is not better. Every study you switch on is another voice, and a chart with thirty voices tells you nothing. The set that is already on when you arrive is deliberately small; add to it only when you know what question the new study answers.",
    ],
    blocks: [
      {
        heading: "What is already on when you arrive",
        rows: [
          { term: "EMA 9, EMA 21, EMA 50", meaning: "Three fast-to-slow moving averages, drawn magenta, yellow and cyan. Their order tells you who is winning: fastest on top is buyers, fastest on the bottom is sellers." },
          { term: "SMA 200", meaning: "A slow dark-green average used as the long-run line in the sand. Price above it is a market in an uptrend on that timeframe." },
          { term: "VWAP", meaning: "The white line: the average price actually paid today, weighted by size. Large funds measure their fills against it, which is why price so often returns to it." },
          { term: "OI levels / High OI", meaning: "The red and green horizontal bands. These are the option strikes carrying the most open contracts above and below price." },
          { term: "EMA clouds", meaning: "Shading between the 9 and 21 averages, and between the 21 and 50. A thick cloud is a strong trend; a flat, thin one is chop." },
          { term: "MTF 4x8 one-sided clouds", meaning: "The same cloud idea computed on several higher timeframes at once, so a 5-minute chart can show you what the hourly is doing." },
          { term: "Ichimoku", meaning: "A Japanese trend system: two fast lines, a forward-projected cloud that acts as support or resistance, and a lagging line confirming the move." },
          { term: "Signal labels", meaning: "The CALL and PUT tags printed on the candles when a momentum cross fires on one of the higher timeframes." },
          { term: "MTF MA levels", meaning: "Flat lines carrying the daily, weekly and monthly moving averages onto whatever timeframe you are looking at." },
          { term: "Session lines and previous day, week and month levels", meaning: "Vertical markers for the times of day that matter, plus the prior period's open, high, low and close - the levels most traders are watching." },
          { term: "Candle colours", meaning: "The cyan and magenta candle scheme, so a candle's own colour already carries the short-term momentum reading." },
          { term: "The three lower panels", meaning: "Under the candles: trend strength across timeframes, a written label of what each timeframe's cloud is doing, and a squeeze grid showing where pressure is building." },
        ],
      },
      {
        heading: "Trend lines",
        rows: [
          { term: "EMA 9 / EMA 21 / EMA 50", meaning: "Exponential moving averages. They weight recent bars more heavily, so they turn sooner than a simple average of the same length." },
          { term: "SMA 200", meaning: "A simple 200-bar average - every bar counts the same. Slow, but it is the line the largest number of people are watching." },
          { term: "VWAP", meaning: "Volume-weighted average price, reset each session. Above it buyers are paying up; below it sellers are." },
        ],
      },
      {
        heading: "Clouds",
        rows: [
          { term: "EMA Clouds", meaning: "Shading between 9/21 and between 21/50. Colour tells you direction, thickness tells you conviction." },
          { term: "MTF 4x8 One-Sided Clouds", meaning: "A 4-versus-8 crossover cloud drawn for the current timeframe and every higher one up to 4 hours." },
          { term: "MTF MACD 6/12/8 Trend Clouds", meaning: "A momentum cloud that needs both a MACD zero-cross and an EMA 9/20 confirmation before it changes colour." },
          { term: "MTF EMA 9x20 Signal Clouds", meaning: "The 9-versus-20 crossover cloud, drawn across several timeframes at once." },
          { term: "MTF Squeeze Release Clouds", meaning: "Clouds plus bubbles marking where a squeeze released, on every timeframe up to daily." },
          { term: "Cloud Bands", meaning: "One fixed timeframe's cloud pinned onto your chart. There is a separate switch for 5m, 15m, 30m, 1h, 2h, 4h and daily." },
          { term: "CloudMAX MTF EMA 1/5/15", meaning: "A denser cloud set with arrows and bubbles, for reading fast intraday turns." },
        ],
      },
      {
        heading: "Signal labels",
        rows: [
          { term: "Call & Put Signals 4x8", meaning: "Yellow CALL and magenta PUT tags from the 4-versus-8 cross, read on the 5-minute through 4-hour timeframes." },
          { term: "Call & Put Signals 9x20", meaning: "The same idea from the 9-versus-20 cross, in cyan and magenta, read on the 30-minute through 4-hour timeframes." },
          { term: "4x8 Daily to Monthly signals", meaning: "The slow version of the 4-versus-8 cross: daily, 2-day, 3-day, 4-day, weekly and monthly, each one waiting on the next timeframe up to confirm it." },
          { term: "9x20 Daily to Monthly signals", meaning: "The 9-versus-20 cross on those same six slow aggregations, printed on the first chart candle of the day it belongs to." },
          { term: "MACD Daily to Monthly signals", meaning: "MACD 6/12/8 crosses on the slow aggregations, for when you want a momentum read rather than a moving-average one." },
        ],
      },
      {
        heading: "Lower panels",
        rows: [
          { term: "MTF ADX Clouds", meaning: "Trend strength, not direction. It answers whether anything is actually trending here, across the current timeframe and five higher ones." },
          { term: "Squeeze Momentum", meaning: "The classic squeeze histogram: dots show when the range is compressed, and the bars show which way the pressure is leaning." },
          { term: "MTF Cloud Label", meaning: "A written row per timeframe saying whether that timeframe's cloud is bullish or bearish, from 5-minute up to weekly." },
          { term: "MTF Squeeze 4/10", meaning: "Eight squeeze rows in their own pane, so you can see at a glance which timeframes are coiled and which have already fired." },
        ],
      },
      {
        heading: "Levels",
        rows: [
          { term: "OI Levels / High OI", meaning: "Full-width call-resistance and put-support bands from the expiry you have selected." },
          { term: "PivotPoints and PersonsPivots", meaning: "Two classic pivot systems that project support and resistance from the previous period's range." },
          { term: "MTF MA Levels", meaning: "Daily, weekly and monthly moving averages carried onto the chart you are on as flat lines." },
          { term: "Previous D/W/M OHLC", meaning: "Yesterday's, last week's and last month's open, high, low and close - each period can be switched on separately." },
          { term: "Session Time Lines", meaning: "Vertical Eastern-time markers for the session boundaries." },
          { term: "Extended Sessions", meaning: "Shading behind the pre-market and after-hours candles so you can tell them apart at a glance." },
          { term: "AutoFib", meaning: "Draws the 38.2%, 50% and 61.8% retracement zone of the current swing for you, without you dragging anything." },
          { term: "Relative Volume Candles", meaning: "Prints a small relative-volume number under each candle, so an unusually heavy bar stands out." },
          { term: "TOS candle colours", meaning: "The reference candle colouring, so a candle's body already tells you the short-term momentum state." },
        ],
      },
      {
        heading: "Turning one on and saving it",
        body: [
          "Open the indicators panel from the chart toolbar. Every study is a checkbox; ticking it draws it immediately. Expanding a row gives you its settings - period lengths on the moving averages, colours, and which timeframes a multi-timeframe study should include.",
          "Then save, or you will do this again tomorrow. Save 5m for all tickers writes the whole set as your profile for that timeframe, so every symbol you open on the 5-minute inherits it. Reset 5m throws your changes away and puts the starting set back. On another timeframe the buttons are named after that one instead.",
        ],
      },
      {
        heading: "The default line colours",
        chips: [
          { label: "EMA 9", tone: "ema9" },
          { label: "EMA 21", tone: "ema21" },
          { label: "EMA 50", tone: "ema50" },
          { label: "SMA 200", tone: "sma200" },
          { label: "VWAP", tone: "vwap" },
        ],
      },
    ],
  },
  {
    id: "options",
    title: "Options and open interest",
    icon: "options",
    goTo: "Quick Options",
    goToLabel: "Open the Options board",
    screenshot: "high-oi-board",
    concept: [
      "An option contract gives its holder the right to buy (a call) or sell (a put) 100 shares at a fixed price, called the strike, until a fixed date. Open interest is simply how many of those contracts at one strike are currently held by somebody and not yet closed.",
      "That matters because open interest is money already committed at a specific price. When a strike carries a very large amount of it, the traders on the other side of those contracts have to hedge as price approaches - so price tends to be pulled toward the strike, and then to stall there. A strike like that is what this app calls a wall.",
      "Walls are not predictions. They are the prices where something is likely to happen: a stall, a squeeze through, or a reversal. Knowing where the next one sits before you enter is most of the value.",
    ],
    blocks: [
      {
        heading: "Finding your way around the board",
        body: [
          "The Options board has a row of tabs across the top: Chain, Heatmap, Flow, C/P Levels and News. It opens on Chain, the strike-by-strike table for one expiry.",
          "Inside Chain there is a second, smaller pair of tabs: Chain and High OI. That pair opens on High OI, which ranks the out-of-the-money strikes that carry real size - the read most people open a ticker for. Switch to the inner Chain tab when you want to compare neighbouring strikes rather than only the crowded ones. Whichever of the two you leave it on is remembered, so the app does not fight you next time.",
        ],
      },
      {
        heading: "The C/P Levels banner",
        body: [
          "The strike-by-strike read described below lives on a different top-row tab: C/P Levels. Open that, and this banner appears above the tables.",
          "LIVE shows the current price with today's change. ATM STRIKE is the strike sitting closest to that price - at the money. EXP RANGE is the expected move: the distance the options market is currently pricing the stock to travel by expiry, shown as a low-to-high band around the price.",
          "Read the expected range as a boundary, not a forecast. It says nothing about direction - only that a move beyond that band would be larger than what is currently priced in.",
          "Below it, CALL ATM and PUT ATM give you the at-the-money strike on each side with its volume and its open interest, so you can see immediately whether today's activity is going into calls or into puts.",
        ],
      },
      {
        heading: "What each column means on C/P Levels",
        rows: [
          { term: "OTM Strike", meaning: "The out-of-the-money strike this row is about - above price for calls, below price for puts." },
          { term: "Exp Move", meaning: "The expected move in dollars for this expiry: how far the stock is priced to travel before it expires." },
          { term: "DTE", meaning: "Days to expiration, with the actual expiry date underneath. Fewer days means faster moves in the option's price, in both directions." },
          { term: "Delta", meaning: "Roughly how much this option's price moves per one dollar move in the stock. 0.30 means about thirty cents." },
          { term: "OTM Vol", meaning: "How many contracts traded at this strike today. Volume is today's activity, and it resets every session." },
          { term: "OTM OI", meaning: "How many contracts are still held open at this strike. Open interest is the accumulated position, and it is reported once a day rather than tick by tick." },
          { term: "Vol/OI", meaning: "Today's volume divided by the existing open interest. A high number means today's traders are piling into a strike that was quiet, which is new positioning rather than old." },
          { term: "ATM Strike", meaning: "The at-the-money strike being compared against, so you can judge this row without leaving the row." },
          { term: "ATM Vol", meaning: "Today's contract volume at that at-the-money strike." },
          { term: "ATM OI", meaning: "Open contracts held at that at-the-money strike." },
          { term: "Flow Type", meaning: "Whether this row's case rests on today's volume or on the standing open interest. Volume-led is fresh; open-interest-led is established." },
          { term: "Liquidity Winner", meaning: "Which of the two strikes is actually getting the money right now - this out-of-the-money strike, or the at-the-money one." },
          { term: "Setup Type", meaning: "A short description of the shape the row forms, so you can scan for the pattern you trade instead of reading every number." },
          { term: "Scanner Tag", meaning: "The label the scan attached to this row, naming why it was worth surfacing." },
          { term: "Strength", meaning: "A 0-100 score combining the above into one bar, so the strongest rows sort to the top. Treat it as a shortlist, not a verdict." },
        ],
      },
      {
        heading: "Strong, moderate, weak",
        body: [
          "Strikes that qualify as walls carry a HIGH OI badge reading STRONG, MOD or WEAK. The grade is this strike's open interest measured against the biggest wall on its own side: two-thirds or more of the leader is STRONG, a third or more is MOD, and below that is WEAK.",
          "So MOD does not mean a weak strike, it means one standing behind the leader - and more than one STRONG wall on the same side is normal when two strikes are carrying comparable size.",
          "It is the same grading the chart uses to draw its red and green bands, so a STRONG badge here is the band you can already see on the chart, not a second opinion about it.",
        ],
      },
      {
        heading: "The colour key",
        chips: [
          { label: "Call wall - resistance above price", tone: "call-wall" },
          { label: "Put wall - support below price", tone: "put-wall" },
        ],
      },
      {
        heading: "The tags on a strike row",
        rows: [
          { term: "CALL ITM", meaning: "Marks the call side that is already in the money - the stock has passed above that strike." },
          { term: "PUT ITM", meaning: "The same on the put side: price has fallen through that strike." },
          { term: "ATM", meaning: "Highlights the strike nearest the current price, the pivot the whole board is measured against." },
          { term: "HIGH OI", meaning: "A wall strike, carried with its STRONG, MOD or WEAK grade." },
          { term: "EM HIGH", meaning: "The top of the expected-move range: a move above this is larger than the options market has priced in." },
          { term: "EM LOW", meaning: "The bottom of that same expected-move range." },
        ],
      },
      {
        heading: "Taking the levels elsewhere",
        body: [
          "If you also chart in thinkorswim or TradingView, the OI Level Scripts page turns the top call and put open-interest levels for one expiry into a study you can paste straight into either platform, so the same lines appear there.",
          "The script is generated from the option chain at the moment you press the button, so regenerate it once the chain has moved on.",
        ],
      },
    ],
  },
  {
    id: "alerts",
    title: "Alerts",
    icon: "alerts",
    goTo: "Auto Alert",
    goToLabel: "Open Alerts",
    screenshot: "auto-alert-panel",
    concept: [
      "An alert is the app watching a price line so you do not have to. You cannot hold nine tickers and their levels in your head at once, and the moment you look away is the moment one of them moves.",
      "There are two kinds here. The automatic ladder builds itself every trading morning from the option open interest, and tells you when price reaches the levels that matter. Manual alerts are the ones you draw yourself, on any price you care about.",
    ],
    blocks: [
      {
        heading: "The morning ladder",
        body: [
          "At 9:15 AM ET on every trading day, the app builds a ladder for each watched ticker: the biggest call strikes stacked above the current price, and the biggest put strikes stacked below it. That is the plan for the day.",
          "The seven big names - AAPL, MSFT, NVDA, AMZN, META, GOOGL and TSLA - are watched by default, and you can type any other ticker into the add box to have a ladder built for it too. Each card shows the ticker's price, the level it is working on now, and the one after that.",
        ],
      },
      {
        heading: "Touched versus CONFIRMED",
        rows: [
          { term: "Touched", meaning: "Price has reached the level with a wick but has not held there. This is an early warning only: it tells you the level is live right now, and the ladder does not move on. A touch fires once per level, so it cannot spam you." },
          { term: "CONFIRMED", meaning: "A complete 5-minute candle has closed through the level. That is the event the app treats as real, because a close through a price is a decision, while a wick through it is often a test that failed." },
          { term: "The next target", meaning: "As soon as a level confirms, the ladder advances and the message names the next strike above or below, how much open interest it carries, and how far away it is in both dollars and percent." },
        ],
      },
      {
        heading: "What the message tells you",
        body: [
          "A confirmation reads like this: the ticker, an arrow for direction, the side and the strike or strikes that were taken out, the word CONFIRMED, the 5-minute closing price that did it, and then the next target with its size and distance. If price closed through two levels in one candle, both are named.",
          "A touch message says so plainly instead, and states what would be needed to confirm - a 5-minute close above the strike for a call level, below it for a put level. Once a side has confirmed for the day it goes quiet until you re-arm it, so a level that has already gone does not keep shouting.",
        ],
      },
      {
        heading: "Alerts you draw yourself",
        body: [
          "On any chart, use the bell button or click the price you care about. If your click lands within a few pixels of a level the chart is already drawing, the alert snaps to it and takes its name - so you get previous day high, or High OI, rather than a bare number you will not recognise a week later.",
          "Armed alerts appear as chips on the price axis, at the price they are watching. Tapping a strike on the option board also starts one, pre-set to fire upward if the strike is above the price and downward if it is below. The live price and existing alert chips are deliberately not snap targets: snapping to the live price would create an alert that fires the instant you made it.",
        ],
      },
      {
        heading: "Sound and notifications",
        body: [
          "Alerts on is the master switch, and MAG7 controls whether the seven big names are included. Enable sound asks your browser for permission to show notifications, and turns on the alert tone.",
          "Once granted the button reads Sound on, and clicking it again plays a test tone so you can check your volume before the market opens. If your browser refuses notifications you still get the tone, and the button says Sound only.",
        ],
      },
    ],
  },
  {
    id: "scanner",
    title: "Scanner (premarket)",
    icon: "scanner",
    goTo: "Premarket Scanner",
    goToLabel: "Open Premarket",
    screenshot: "premarket-scanner",
    concept: [
      "Before the bell the question is short: of the big names, which one is actually waking up? Overnight ranges are thin and most of what happens is noise, so this board is deliberately narrow - nine tickers, one window, one score.",
      "It does not try to find you a stock you have never heard of. It tells you which of the names you were going to trade anyway has momentum behind it this morning, so you know where to point your first chart at 9:30.",
    ],
    blocks: [
      {
        heading: "The window and the names",
        body: [
          "The scan runs between 6:00 and 9:30 AM ET on trading days. Outside that window the board is quiet, and that is expected rather than broken - there is nothing pre-market about the middle of the afternoon.",
          "The nine names are AAPL, AMZN, AVGO, GOOGL, META, MSFT, NFLX, NVDA and TSLA. The list is fixed: it is the set the scanner is built around, and their tapes are kept warm so the board can read them whether or not you have a chart open.",
        ],
      },
      {
        heading: "What the signals mean",
        rows: [
          { term: "CALL2H", meaning: "A bullish momentum cross that has fired on the 2-hour view of the tape - the shorter of the two confirmations." },
          { term: "CALL4H", meaning: "The same cross on the 4-hour view. Slower to appear and harder to earn, so it carries more weight than the 2-hour on its own." },
          { term: "Why it agrees with your chart", meaning: "These are not a second calculation. They are read from the same study that draws the CALL and PUT labels on your chart, so the board and the chart cannot tell you different things about the same morning." },
        ],
      },
      {
        heading: "Squeeze fires",
        body: [
          "A squeeze is a stretch where a stock's range tightens and pressure builds; when it releases, a larger move often follows. The board counts squeeze fires on the 1-hour, 2-hour, 4-hour and Daily views.",
          "15-minute and 30-minute squeezes are deliberately left out. They fire almost continuously, so including them would add a hit to nearly every ticker every morning and drown the score in noise rather than sharpening it.",
        ],
      },
      {
        heading: "The four numbers at the top",
        rows: [
          { term: "MATCHES", meaning: "How many of the nine tickers cleared the scan this morning, with the scan window printed underneath it." },
          { term: "STRONG", meaning: "How many matches are graded strong. The grade is about the quality of the crosses, not how many hits a row has: the cyan 9x20 cross outranks the yellow 4x8 one, so a cyan and a yellow together, or two cyan, is STRONG; a single cyan or two yellow is MODERATE; one yellow alone is WEAK. A row carrying only squeeze fires never reaches STRONG, because a release says a move is coming without saying which way - though two or more fires do lift a row that already has a cross." },
          { term: "LIVE TAPES", meaning: "How many of the nine have usable data for today. This is the honesty number: a ticker with no data today cannot be scanned, and the note names it rather than quietly reporting no setups." },
          { term: "FEED", meaning: "Whether this morning's data arrived complete or with a hole in it. GAP means part of the pre-market window is missing, so treat the 2-hour and 4-hour reads as partial." },
        ],
      },
      {
        heading: "The morning briefing",
        body: [
          "Underneath the board, the briefing is a short written summary of the morning: which tickers matched, what fired on each, and what the shape of the session looks like going into the open.",
          "It is saved with the day, so tomorrow you can read what yesterday's board was actually saying rather than trying to reconstruct it from the numbers.",
        ],
      },
      {
        heading: "Looking back",
        body: [
          "The last 30 mornings are kept, and you can read them three ways. Table gives you one day at a time with arrows to step backward and forward. Calendar lays the month out so you can see which mornings were busy at a glance. List runs everything together for scanning quickly.",
          "This is the fastest way to learn whether a signal is worth anything to you: find the mornings a ticker matched, then look at what the day did afterwards.",
        ],
      },
    ],
  },
  {
    id: "momx",
    title: "MomX Scanner",
    icon: "momx",
    goTo: "MomX Scanner",
    goToLabel: "Open MomX",
    screenshot: "momx-board",
    concept: [
      "MomX is the wide board. Where the pre-market Scanner watches nine names closely, this one scores a few hundred the same way every minute and shows you the ones that pass, all day.",
      "Every row on it has already cleared the same filter, so the board is a shortlist rather than a market. Your job reading it is to choose between candidates, not to decide whether they qualify.",
    ],
    blocks: [
      {
        heading: "What a row has to do to appear",
        body: [
          "Three requirements must all be true. The stock trades at $3 or more. Its 4-hour volume is up at least 0.5% against two bars ago. Its 1-hour price is up at least 0.3% against two bars ago.",
          "Then at least one thing has to actually be happening: a momentum cross on one of the tracked timeframes, a squeeze that has just fired, or a relative-volume hit. Clear the three requirements but show none of those three, and the name stays off the board.",
          "Every reason a row passed is kept, not only the first one, so a name showing four reasons genuinely has four things going for it.",
        ],
      },
      {
        heading: "Reading the board left to right",
        body: [
          "Industry, Symbol, % Chg, then the 1D sparkline. Next a first block of RVOL - 2h, 4h and D - then H/L, then a second RVOL block at 5, 15, 30 and 1h.",
          "After the thin separator column come the squeeze columns at 2h, 4h, D and W, then Skittles at 2h and 4h, then Quote, then the slow Skittles: D, 2D, 3D, 4D, W and Mo.",
          "The RVOL block being split by H/L, and the Skittles block by Quote, is not an accident of layout. This board is a replay of the scan window it was built to mirror, column for column, so it is laid out the way that window is.",
        ],
      },
      {
        heading: "What each column means",
        rows: [
          { term: "Industry", meaning: "The sector the name belongs to. Three or four rows from the same industry at once is usually the real story, not three separate ones." },
          { term: "% Chg", meaning: "How far the stock has moved today, in percent." },
          { term: "1D", meaning: "A miniature chart of the day's shape, so you can tell a steady climb from a spike that has already faded without opening the chart." },
          { term: "RVOL", meaning: "Relative volume: how much is trading now against what is normal for this stock at this time of day. Above 1 is busier than usual; the short timeframes catch a burst, the long ones tell you whether it has lasted." },
          { term: "H/L", meaning: "Where price is sitting inside the recent range, drawn as a fill rather than a number. It reads the last 8 two-hour bars with extended hours on - roughly 16 hours, not just today. Full means price is at the top of that range; EMPTY means the MIDDLE of it, not the bottom. Below the middle the bar fills the other way and turns red." },
          { term: "Squeeze", meaning: "Pressure building and then releasing. A squeeze column tells you that timeframe's range has compressed, and changes when it fires." },
          { term: "The separator column", meaning: "A thin solid stripe with no data in it, there only to break the volume block away from the pressure block so your eye does not slide across them." },
          { term: "Skittles", meaning: "A fast 0-100 momentum reading, coloured by which way the trend is running. The short columns are today's push; the daily-to-monthly ones tell you whether it fits a bigger move." },
          { term: "Quote", meaning: "The quote trend: the last few bars up or down as a tiny bar chart, so you get the immediate direction at a glance without reading a number." },
        ],
      },
      {
        heading: "How often it refreshes",
        body: [
          "The board rebuilds every 60 seconds. That pace is deliberate: scoring a few hundred names is real work, and asking for it more often would make the app slower without telling you anything new.",
          "While a refresh is in flight, the last board you saw stays on screen rather than blanking. A board that looks unchanged for a moment is normal - the build time is what tells you how fresh it is.",
        ],
      },
      {
        heading: "Your own lists",
        body: [
          "The tabs above the board are lists. Switch between them to score a different universe, and the tab you were last on is remembered when you come back.",
          "The Tickers box replaces the active list: paste or type the symbols you want, press Set, and the next build scores those instead. A list that has just been changed shows as building until its first full board lands.",
        ],
      },
      {
        heading: "The Momo Alert",
        body: [
          "This board can also interrupt you: the amber MOMO banner, its sound, and the push that reaches your phone even locked. It is set up once from the gear button next to refresh - the full walkthrough now lives on the Setup tab at the top of this page.",
        ],
      },
    ],
  },
]);

// The Setup tab: every first-time-setup step in one place, so nobody has to
// dig them out of step 8 or the bottom of the MomX chapter again. Same block
// shape the reference sections use, so the renderer already knows it.
//
// This is the ONE place allowed to name brokers and keys (see the 2026-08-30
// note in the copy rules above). The Learn-tab banned-terms tests deliberately
// do not join this export.
export const LEARN_SETUP_SECTIONS = Object.freeze([
  {
    id: "tos-api-key",
    title: "TOS API key setup",
    icon: "own-keys",
    screenshot: "settings-schwab-keys",
    concept: [
      "This is optional. Without it, everything in AGX already works on the app's shared data. Do this only if you want your charts and option chains running on your own Schwab account (Schwab is the company behind thinkorswim - same login).",
      "It has two parts: first you get two codes from Schwab's website, then you paste them into AGX. The only slow part is Schwab: they take a few days to approve new apps.",
    ],
    blocks: [
      {
        heading: "What you need before you start",
        body: [
          "Your Schwab (or thinkorswim) username and password. A computer - this part is easier with a mouse than a phone. About ten minutes now, then a few days of waiting for Schwab's approval.",
        ],
      },
      {
        heading: "Part A - get your two codes from Schwab",
        rows: [
          { term: "1", meaning: "Open a new browser tab and type this address exactly: developer.schwab.com" },
          { term: "2", meaning: "Click Sign In at the top right, and log in with the SAME username and password you use for Schwab or thinkorswim." },
          { term: "3", meaning: "The first visit asks you to finish a short developer profile. Where it asks what kind of developer you are, choose Individual." },
          { term: "4", meaning: "On your dashboard, find the button named Create App and click it." },
          { term: "5", meaning: "In the form: choose the API product that mentions Market Data, and where it asks for a Callback URL, type exactly: https://127.0.0.1 - then submit the form. No slash on the end: Schwab matches this string exactly against what the app sends." },
          { term: "6", meaning: "Now you wait. Schwab checks new apps by hand and it usually takes a few days. When your dashboard shows the app as Ready For Use, come back here and continue." },
          { term: "7", meaning: "Open your approved app on that dashboard. You will see two long codes: an App Key and a Secret. Leave this tab open - you are about to copy both." },
        ],
      },
      {
        heading: "Part B - paste the codes into AGX",
        rows: [
          { term: "1", meaning: "In AGX on your phone, tap More at the bottom right, then tap the Settings tile (the gear). On a computer, click Settings in the menu." },
          { term: "2", meaning: "Scroll down until you see the card titled Connect your personal Market Data app. The picture above is that card." },
          { term: "3", meaning: "Click inside the box labeled MARKET DATA APP KEY and paste the App Key from your Schwab tab." },
          { term: "4", meaning: "Click the box labeled MARKET DATA APP SECRET and paste the Secret." },
          { term: "5", meaning: "Press the Save keys button." },
          { term: "6", meaning: "Press Authenticate Market Data. A Schwab window opens: log in and press Allow." },
          { term: "7", meaning: "You will land on a page that looks broken - the address starts with 127.0.0.1. That is expected and correct. Copy the ENTIRE address from the address bar." },
          { term: "8", meaning: "Back in AGX, paste that address into the box labeled MARKET DATA CALLBACK URL and press the Complete button under it." },
        ],
      },
      {
        heading: "You are done when...",
        body: [
          "The card shows a green Authenticated badge next to Market Data, and Test Market Data comes back happy. Your charts, option chains and live stream now run on your own Schwab connection.",
          "One thing to expect: about once a week Schwab expires the connection and the app will ask you to repeat Part B steps 6 to 8. AGX warns you three days before that happens.",
        ],
      },
    ],
  },
  {
    id: "alpaca-key",
    title: "Alpaca key setup",
    icon: "own-keys",
    screenshot: "settings-alpaca-keys",
    concept: [
      "Also optional, and much quicker - about five minutes, no waiting. An Alpaca key makes the trading features act on YOUR account instead of the shared one, and Alpaca's free account is enough.",
    ],
    blocks: [
      {
        heading: "Part A - get your two codes from Alpaca",
        rows: [
          { term: "1", meaning: "Open a new browser tab and type this address exactly: app.alpaca.markets - then click Sign Up and create a free account (just an email and a password)." },
          { term: "2", meaning: "After signing in you land on the Paper Trading dashboard - practice money, and exactly the right place to start." },
          { term: "3", meaning: "On the right side of that dashboard, find the section named API Keys and press Generate. Two codes appear: an API Key ID and a Secret Key. Leave the tab open." },
        ],
      },
      {
        heading: "Part B - paste the codes into AGX",
        rows: [
          { term: "1", meaning: "In AGX: tap More at the bottom right, then Settings (on a computer, click Settings in the menu)." },
          { term: "2", meaning: "Scroll to the card titled MY DATA PROVIDER KEYS and find the box named Alpaca Market Data. The picture above is that card." },
          { term: "3", meaning: "Click the box labeled API KEY ID and paste the API Key ID from your Alpaca tab." },
          { term: "4", meaning: "Click the box labeled API SECRET and paste the Secret Key." },
          { term: "5", meaning: "Press Save Alpaca keys." },
        ],
      },
      {
        heading: "You are done when...",
        body: [
          "The Alpaca Market Data card shows a short piece of your key at its top right instead of Not configured. Trading features now use your own account.",
        ],
      },
    ],
  },
  {
    id: "momo-alert",
    title: "Momo Alert setup",
    icon: "momo-alert",
    screenshot: "momo-settings-modal",
    concept: [
      "The Momo Alert watches the scanner for you. When a stock suddenly trades several times its normal volume - while the momentum scan passes - a MOMO banner and a sound interrupt whatever screen you are on. That combination is what the start of a real move looks like.",
      "It alerts once per ticker per 15 minutes, only while the market is live, and everything you set here is yours alone - it never changes another user's alerts.",
    ],
    blocks: [
      {
        heading: "Turn it on",
        rows: [
          { term: "1", meaning: "Open the MomX scanner: tap MomX in the bottom bar (on a computer, MomX Scanner in the menu)." },
          { term: "2", meaning: "In the row of buttons above the table, press the gear - it sits right next to the round refresh arrow. The MOMO ALERT window in the picture above opens." },
          { term: "3", meaning: "Tick the timeframes you want watched. 5m catches a move the moment volume arrives; 30m, 1h and 2h catch a slower, sustained mover." },
          { term: "4", meaning: "The number next to each timeframe is how unusual volume must be before it interrupts you: 3 means three times normal. Start with 3; raise it to 4 or 5 if you get too many alerts." },
        ],
      },
      {
        heading: "Get it on your phone - locked, in your pocket",
        rows: [
          { term: "1", meaning: "On your phone, open the App Store (or Play Store), search for ntfy, and install it. It is free, with no account and no sign-up." },
          { term: "2", meaning: "Back in the MOMO ALERT window, under Phone push, press Generate. That creates your private channel name and saves it. The name works like a password - do not share it." },
          { term: "3", meaning: "Open the ntfy app, tap the + button, choose Subscribe to topic, and type your channel name EXACTLY as AGX shows it." },
          { term: "4", meaning: "Back in AGX, press Send test next to Generate. Your phone should buzz within a few seconds. If it does not, the name in ntfy and the name in AGX do not match - check them letter by letter, they are case-sensitive." },
          { term: "4", meaning: "When your phone asks to allow notifications, tap Allow." },
        ],
      },
      {
        heading: "You are done when...",
        body: [
          "The Send test buzzed your phone, the MOMO ALERT window shows your timeframes ticked, and the ntfy app lists your channel. From now on alerts reach your phone even with the browser closed and the phone locked, because the scanner itself sends them - not this page. Tip: in ntfy you can give the channel its own sound so you know a MOMO buzz without looking.",
        ],
      },
    ],
  },
  // 2026-09-04: the trader asked for this page so the app can be kept running
  // with no developer on call. Names the check script and two commands on
  // purpose - that IS the content. For the person at the keyboard of the PC
  // that runs AGX; a phone user cannot run any of it.
  {
    id: "health-check",
    title: "How to check app health",
    icon: "health",
    concept: [
      "AGX runs on one Windows PC and heals itself: anything that stops is restarted within about ten minutes, and the journal, keys and code are copied to Google Drive twice a day. When it still looks wrong, one read-only check tells you which part is down and the exact command to run next - so you never have to guess.",
      "Everything here is done at that PC, in PowerShell. Nothing in the check changes anything; it only reads.",
    ],
    blocks: [
      {
        heading: "First, rule out the browser",
        rows: [
          { term: "1", meaning: "Reload the page hard: Ctrl+F5 on a computer. On the phone close the AGX tab completely and open it again - a tab left open for days keeps an old copy of the app and can show stale lamps." },
          { term: "2", meaning: "Look at the header lamps. TOS MARKET and TOS TRADING each show the days left on their login; a blinking amber lamp means under two days, EXPIRED means that data source is off until you re-authenticate in Settings. ALPACA red outside market hours is normal." },
          { term: "3", meaning: "\"Unable to sign in\" on the login page almost never means a wrong password - it means the app server is down. Go to the next block." },
        ],
      },
      {
        heading: "Step 0 - run the health check (reads, never changes)",
        body: [
          "On the AGX PC: click Start, type powershell, press Enter, then paste this line and press Enter:",
          "powershell -ExecutionPolicy Bypass -File \"C:\\GANESH\\AgenticAI-Trading 7\\AgenticAI-Trading 2\\scripts\\check_health.ps1\"",
          "It prints one OK or DOWN line for each part: the five network ports the app listens on, the three scheduled tasks that keep it alive, the public tunnel for app.agxtrade.com, Google Drive and the age of the newest backup, both TOS logins with the days left, and the last fifteen lines of the error log. Under every DOWN line it prints the command that fixes that part.",
        ],
      },
      {
        heading: "Step 1 - the fix that covers almost everything",
        body: [
          "If any port is DOWN, or the login page says it cannot sign you in, paste this and press Enter:",
          "Start-ScheduledTask 'AgenticAI-Trading-24x7'",
          "Then wait two to three minutes - a cold start is slow - and run Step 0 again. This starts the supervisor that launches and watches every part of the app; if it is already running the command does nothing, so it is always safe.",
        ],
      },
      {
        heading: "Step 2 - by symptom",
        rows: [
          { term: "Error 1033 at app.agxtrade.com", meaning: "The tunnel to the internet dropped. In a PowerShell opened as Administrator, run: Restart-Service cloudflared . The PC address (127.0.0.1:5173) keeps working the whole time." },
          { term: "A TOS lamp blinks or reads EXPIRED", meaning: "The weekly login ran out. Settings, then the Schwab card, then Authenticate for that app: log in, approve, copy the 127.0.0.1 address from the browser bar, paste it back, Complete. If the PC gets Access Denied from the login page, do the login on your phone over mobile data and paste the address on the PC." },
          { term: "Everything OK but no phone alerts", meaning: "Open the ntfy app and check it is still subscribed to your channel name. Press Send test in the MOMO ALERT window to prove the chain." },
          { term: "Backup line says DOWN", meaning: "Start Google Drive for Desktop from the Start menu, then run: Start-ScheduledTask 'AGX-Nightly-Backup' and check again in five minutes. Until Drive is back, backups land in C:\\AGX-Backups on the PC instead." },
          { term: "Still wrong after all of this", meaning: "Copy the whole output of Step 0 and paste it to whoever is helping you - a person or any AI assistant. The full written procedure lives in the docs folder of the app as RUNBOOK.md, and MOVE_TO_NEW_LAPTOP.md covers rebuilding on another machine." },
        ],
      },
      {
        heading: "What happens on its own",
        body: [
          "Crashed parts restart within ten minutes. Backups run at 1:30 AM and after the close. Your phone gets a push one day before either TOS login expires, and once more if it does expire. None of this needs a developer; the only weekly chore is the TOS re-authentication above.",
        ],
      },
    ],
  },
]);

// The words a new user will hit in the first ten minutes and have no way to
// look up. Kept short and non-circular: no definition leans on another term in
// this list without explaining it in passing.
export const LEARN_GLOSSARY = Object.freeze([
  { id: "open-interest", term: "Open interest", plain: "The number of option contracts at one strike that are currently held by someone. High open interest means a lot of money already has a position at that price." },
  { id: "wall", term: "Wall", plain: "A strike carrying so much open interest that price tends to slow down, stall or turn when it gets there. Red walls sit above price, green walls below." },
  { id: "atm-otm-itm", term: "ATM, OTM, ITM", plain: "At the money means the strike is roughly where the stock is trading. Out of the money means it still needs to move for the option to be worth anything at expiry. In the money means it already is." },
  { id: "dte", term: "DTE", plain: "Days to expiration - how many days are left before the option expires. Fewer days means faster moves in the option's price, in both directions." },
  { id: "delta", term: "Delta", plain: "Roughly how much the option's price moves for a one dollar move in the stock. A delta of 0.30 means about thirty cents per dollar." },
  { id: "expected-move", term: "Expected move", plain: "How far the options market is pricing the stock to travel by expiry, up or down. It is a range, not a prediction of direction." },
  { id: "vwap", term: "VWAP", plain: "Volume-weighted average price - the average price paid today, weighted by how much traded at each level. Price above it usually means buyers are in control." },
  { id: "ema-sma", term: "EMA and SMA", plain: "Moving averages, which smooth price into a line. An EMA reacts faster to recent moves than an SMA of the same length." },
  { id: "rvol", term: "RVOL", plain: "Relative volume - how much is trading now compared with what is normal for this stock at this time of day. Above one means busier than usual." },
  { id: "squeeze", term: "Squeeze", plain: "A period where a stock's range tightens and pressure builds. When the squeeze fires, that pressure releases and a larger move often follows." },
  { id: "skittles", term: "Skittles", plain: "A fast momentum reading between 0 and 100, coloured by which way the trend is running. High and green means momentum is stretched upward." },
  { id: "rth-ext", term: "Regular and extended hours", plain: "Regular hours run 9:30 AM to 4:00 PM ET. Extended hours are the trading before and after that, where far fewer people are active and moves are less reliable." },
  { id: "five-minute-close", term: "5-minute close confirmation", plain: "Waiting for a full five-minute candle to finish above or below a level before treating it as broken. It filters out brief spikes that immediately reverse." },
]);

// Every page the six sections do NOT teach. A test asserts that this list, plus
// the taught pages, plus Learn itself, covers the whole nav - so a page cannot
// be added to the app without this page acknowledging that it exists.
export const LEARN_ELSEWHERE = Object.freeze([
  { goTo: "Watchlist", goToLabel: "Watchlist", oneLiner: "Your saved tickers, shared by the scanners and the chart." },
  { goTo: "News Feed", goToLabel: "News Feed", oneLiner: "Headlines and catalysts for the big names and your watchlist." },
  { goTo: "Earnings Calendar", goToLabel: "Earnings Calendar", oneLiner: "Which of your saved tickers report, and when." },
  { goTo: "Mag7 Scanner", goToLabel: "Mag7 Watchlist", oneLiner: "A dense board of signals and flow for the seven largest names." },
  { goTo: "ROI Calc", goToLabel: "ROI Calc", oneLiner: "A quick expected-move and premium screen when you want to check a number by hand." },
  { goTo: "OI Level Script TOS", goToLabel: "OI Level Scripts", oneLiner: "Export the same option levels the chart draws, to use in thinkorswim or TradingView." },
  { goTo: "Release Notes", goToLabel: "Release Notes", oneLiner: "Every change to AGX with the date and time it shipped - the first place to look when something starts behaving differently." },
  { goTo: "Settings", goToLabel: "Settings", oneLiner: "Your account and workspace preferences." },
]);
