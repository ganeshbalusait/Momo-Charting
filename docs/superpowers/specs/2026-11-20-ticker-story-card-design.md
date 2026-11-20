# Ticker story card (one answer per ticker) - short design

2026-11-20. Ganesh: "If I see leap oi scanner like INTC I don't know anything like buyers or seller ... then I have to
search agx flow ... I feel little confusion. How to avoid that and fix it", then "Build like this 10 kid can understand
or who don't know any stock market", then "Which is better do it" (he handed the how to Claude).

## Intent
One place that answers "what are people betting on this stock, and did they buy or sell?" for one ticker, in words a
10-year-old or a first-time trader can follow. No page-hopping between LEAP OI Scanner and AGX Flow.

## Goals
- Search a ticker on LEAP OI Scanner **or** AGX Flow -> the same "INTC in simple words" card at the top of the page.
- The card joins what both pages already know: stock price now, today's money on Calls vs Puts (bought or sold),
  the biggest bets today, the long bets most people hold (3-12 months), and what is new since yesterday.
- Every sentence is a number the app already has; plain words; "What this means"; "not advice".

## Non-goals / anti-goals
- No new Schwab requests: it reads the copies the two pages already keep (a missing ticker is queued like today).
- Never a pick, a forecast, or "it will go up". No orders. Paper only.
- The two pages stay (they are boards across many tickers); the card is what a single-ticker search shows first.

## Approaches considered
- A. Keep two pages, each showing the other's key answer (partly shipped: Today's trades on LEAP cards). Still two
  places to look.
- B. **Chosen (Claude's call, Ganesh: "Which is better do it")**: one server answer per ticker
  (`momx/ticker_story.py`, `GET /api/ticker-story?symbol=INTC`) built from `leap_oi_scanner.board` and
  `agx_flow.board`, shown as one card on both pages when a ticker is searched. Sentences are built on the server so
  Ask can reuse them later.
- C. A new third page. Rejected: one more place to look is the opposite of what he asked.

## Shape
`story(ticker, root)` -> `{ticker, ready, pending, spot, verdict, sections:[{title, lines:[{mark, text}]}], meaning,
notAdvice}` with sections: "Price", "Today's bets", "Long bets (3 to 12 months)", "New since yesterday".
Marks as in `frontend/src/simpleWhy.js` (yes / no / info).

## Left to the builder
Exact sentence wording (tested), card styling (matches the "In simple words" box), what shows while a ticker is
still being read ("Reading INTC... the card fills in when both pages have read it").
