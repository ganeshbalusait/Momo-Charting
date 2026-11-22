# Agentic AI trading for AGX: design

Date: 2026-11-04 (ET). Owner: Ganesh. Status: draft for his review.

## Why

Ganesh trades short-dated OTM calls and puts on momentum. He wants AGX to be agentic AI from end to end: AI that uses the app's own scanner, Record column and BOT trading to catch momentum the way he does, and that trades by itself in the future.

What prompted it: on 2026-11-04 the BOT's AAPL entries at 09:30 (Nov 6 $342.50C, $3.40 to $11.28, +232%) and 09:32 to 09:35 (+103% to +151%) were big wins. Every AAPL rule that fired after 10:00 lost or roughly broke even (−18% to +9%). That is the skill he wants the AI to learn: spot a real momentum day early and skip the late, tired entries.

His second worry, in his words: "BOT trading still im not happy the result not sure is correct". He is right to doubt it (see stage 1). The AI can only learn from honest numbers, so trusting the BOT comes first. He agreed to this order.

## Today (audit of 2026-11-04)

- One-shot AI helpers: morning brief paragraph, scanner triage, catalyst explainer, journal lessons, option supervisor, Jev news/scanner/BOT tags. None of them acts on its own.
- A real multi-agent team in BOT paper trading (`momx/paper_learning.py`): scanner, rotating specialist, reviewer, critic and Hermes agents review finished trades every night. The experiment planner (`momx/paper_experiments.py`) proposes one rule a day, code back-tests and forward-tests it, and it switches on or off for the Paper 5 Alpaca paper account automatically.
- Agents have no tools (`"tools": {"deny": ["*"]}`), and nothing places real-money orders.
- The scanner, chart and Record maths are deterministic and must keep matching TOS.

## Goals

1. BOT numbers he can trust: every result shown at the mid, at a realistic fill and at the worst fill, and checked every day against real Alpaca paper fills.
2. A Momentum Watch agent that picks, from the live scanner and the Record column, the few names moving like AAPL did, says why, and is graded on every pick automatically.
3. A Paper AI trader that turns those picks into real Alpaca PAPER orders on its own account, with his sizing and exits, reviewed nightly by the existing agent team.
4. Later, real money with one tap: an order is proposed and waits for his tap, with a hard daily loss limit.

## Non-goals

- AI never changes the scanner, chart, Setup or Record maths. TOS parity stays exact.
- No real-money order is ever sent without his tap. Fully automatic live trading is out of scope. Only Ganesh can decide that later, after stage 4 has a record.
- No AI trading for other AGX users. The agents run on his account only. AI trading for other people is a separate legal and regulatory question.
- Don't touch the stock-picks work in the live repo's `ai_picks/` folder (another session, started 2026-11-04 19:20).

## Anti-goals (a failure even if it works)

- Results that look better than real fills. Every P/L number must say how it was priced.
- Rules picked by guesswork. He said "no guess work": a change to a rule needs a held-out back-test or a forward test, including fees and missed fills.
- The AI slowing the scanner or charts in market hours. Agents run off the scan path, on the research worker, at low priority.
- Tuning to one great day. AAPL 11-04 is the example, not the training set.

## Stage 1: trust the BOT numbers (build first)

The BOT (`momx/option_track.py`) buys and sells at the mid (`_price`, about line 625). It already records `entryBid/entryAsk` and `exitBid/exitAsk` per trade (bid/ask exist from 2026-10-09).

**Revised 2026-11-04 night after checking the code and data:**
- On 2026-10-16 Ganesh asked to "Remove all use only mid order" (he trades limit orders at the mid). So the BOT page stays mid-only, and items 1 and 4 below are replaced by the truth check.
- The truth check shows what really happens to his mid orders. From Oct 19 to Nov 4:
  - **Paper 3:** 212 of 319 orders never filled. On the 105 that did, the BOT says −5.0% ROI and paper got −10.7%.
  - **Paper 5:** 53 of 75 never filled. On the 22 that did, the BOT says +27.9% and paper got +11.6%.
  - **AAPL 11-04:** the BOT's +232% trade was a Paper 3 limit at $3.15 that never filled.
- Stage 1 as built is therefore:
  - (a) the truth check (item 3), shown on the BOT page, overall and per rule;
  - (b) the $0-bid fix (item 2), with an unsellable exit scored at $0 on the BOT page and in the Record column.

Original items, for the record:

1. **Three prices per result.** Each trade and every total on the BOT page and in the Record column shows three ways:
   - **Mid:** today's number.
   - **Real fill:** halfway between the mid and the worst fill, so buy at mid + ¼ of the spread and sell at mid − ¼ of the spread. Once the daily truth check (item 3) has 30+ matched paper fills, Real switches to the gap his Alpaca paper fills actually get, measured per price band.
   - **Worst fill:** buy at the ask, sell at the bid.

   The BOT page header says which one is showing. Default is **Real fill** (my call; Ganesh can flip it).
   - Trades from before 2026-10-09 have no recorded bid/ask. They stay at mid, labelled "mid only", and are left out of the Real/Worst totals and re-scored leaderboards (my call).
2. **Fix the $0-bid exit bug.** 2,216 trades with a $0 exit bid were logged as sold above the true mid, for example a $0.005 entry "exited" at $0.01 = +100%. A $0 bid means the option couldn't be sold. Score those exits at $0 for Real and Worst, and flag them `unsellable`.
3. **Daily truth check.** After 16:40 ET a job compares each Paper 3 and Paper 5 Alpaca fill with the virtual BOT trade it mirrors: entry and exit price gaps, missed fills, and trades the BOT counted but Alpaca never filled. A "Truth check" card on the BOT page shows yesterday's gap. It reuses the existing stale-chain, frozen-price and below-intrinsic `badQuote` checks and adds anything new it finds.
4. **Re-score history.** Rebuild the leaderboards and the Record column's 10-day win rate and ROI at Real fill, so the rules the paper accounts really trade (`ACCOUNT_RULES` in `momx/paper_mirror.py`; today both paper3 and paper5 use `PICKED_RULES`, while `PAPER5_RULES` is defined but unused) are judged on real-fill numbers. Show him which rules change rank. Don't change his picks for him.

Done when the BOT page, Record column and truth check all agree, and every number states how it was priced.

## Stage 2: Momentum Watch agent (advice only)

- From 09:30 to 11:00 ET, and 13:00 to 15:30 ET (his BOT entry windows), it reads each finished scanner build (bull and bear), the Record column and the BOT's live trades. Its tools are read-only functions in the app: board rows, Record, a ticker's 5m candles, its OI walls, its news.
- This is the first agent that **uses tools in a loop**: it picks candidates, looks deeper at each, and decides "watch / take / skip" with a reason.
- Up to 3 picks a day per side, with this output: ticker, side, why now, the BOT's suggested contract, and the time and price of the pick. Shown as a "Momentum Watch" strip on the scanner, plus the existing alert path (banner/ntfy). The alert can be turned off.
- Every pick is graded automatically at Real fill using the BOT's contract and the BOT's own exit (5m close below VWAP/EMA21), against a baseline of every BOT trade in the same minute with the same exit. Same exit on both sides, so the scorecard measures pick quality only.
- Speed: a pick must arrive within about 60 seconds of the scanner build, because the AAPL edge was gone within minutes. The local Ollama model takes minutes per call, so Momentum Watch uses the cloud model as primary (Gemini via `agents/ai_provider.py`, already live on the free tier), with no local fallback in market hours. If it can't answer in time, there's no pick. The nightly team stays on local Ollama (my call). Hard limits: a 60 s budget per scan and a cap on calls per day that stays within the free tier.

## Stage 3: Paper AI trader

- A new Alpaca **paper** account ("Paper AI") receives Momentum Watch "take" picks as real paper orders. It uses his rules:
  - **Sizing:** ≤$4.00 → max(3, $300 // cost) contracts; $4.01 to $8 → 1; over $8 → skip.
  - **Entry:** limit at the mid.
  - **Exit:** sell 20% at +100% and move the stop to break-even; ratchet the stop at each further +100%; sell everything from 15:52.
  - **Caps:** 8 open, 25 a day, one per ticker.
- It reuses `momx/paper_mirror.py`, which already refuses non-paper credentials.
- Its baseline is Paper 3 with the same exits, so the stage 4 gate compares like with like.
- The existing learning team reviews the Paper AI trades nightly. The planner may propose rule changes only through the existing back-test → forward-test → auto-rollback path.

## Stage 4: real money, one tap (later, his decision)

The gate to open it: at least 4 weeks of Paper AI results beating the BOT baseline at Real fill on days it never trained on, and positive after fees. Then each live order is proposed on his phone and in the app, and is sent only on his tap. A hard daily loss limit (default $500, his to set) stops new proposals. There is an off switch file and an env flag, like the paper mirror.

## Constraints

- **Code lives in the live repo** `C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2` (branch `main`; `OI-scanner-BOT` is a separate, older branch, so don't build on it). The outer repo is a mirror. Check the live repo's `git log` and `git status` before starting, and commit only your own paths (`git commit -- <paths>`).
- **Tests:** every stage ships unit + integration (`tests/api_harness.py`, `tests/worker_harness.py`) + end-to-end (`tests/e2e/`) tests, plus a `tests/FEATURE_TESTS.md` entry.
  - Between 09:30 and 16:00 ET, run only the focused tests.
  - No restarts in market hours without announcing to other sessions.
- **Release notes:** a `frontend/public/release-notes.json` entry for each shipped stage, written in trader language.
- **Explain results in plain language.** Ganesh is a trader, not a developer.

## Planning scope

The first plan covers stage 1 in full. Stages 2 and 3 get their own plans once stage 1's numbers are in, because the real-fill results may change which rules Momentum Watch should lean on. Stage 4 stays an outline until its gate is met (my call).

## Left to the builder

File layout, function names, how the three prices are cached, the truth-check report format, the Momentum Watch tool signatures and prompt, and the UI placement within the existing BOT page and scanner styling.
