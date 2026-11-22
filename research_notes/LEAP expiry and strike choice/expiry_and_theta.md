# LEAP call options: expiry choice and time decay (theta)

Scope: US-listed equity and ETF options. General education, not personalised advice. Researched 2026-11-20.

How to read these notes:
- "Cited Findings" = what a named source says. Items marked **(snippet only)** were seen in a search-result summary and the page itself was NOT opened and read by me; treat them as lower confidence.
- "Inferences" = my own reasoning or my own arithmetic. The theta tables are my own Black-Scholes calculations (standard textbook model), NOT numbers published by a source. No source I could open published a clean "theta by months-to-expiry" table.
- Rules of thumb are labelled as such. None of the rolling rules of thumb is backed by a study that I could find.

---

## 1. What counts as a LEAP (definition, January expiries, how far out listings go)

### Takeaway
A LEAP is simply an ordinary listed option with a long life. Cboe's specification says equity LEAPS can run up to 39 months from first listing and expire in January only; practitioners loosely call anything with more than roughly 9-12 months left a LEAP. Mechanically it is the same contract as a short-dated option (100 shares, American-style exercise, third-Friday expiry).

### Cited Findings
- Cboe equity LEAPS specification, expiration months: "May be up to 39 months from the date of initial listing, January expiration only." The same page describes them as expiring "in approximately two to three years from the date of initial listing." — [Cboe Equity LEAPS Product Specifications](https://cboe.com/tradable_products/equity_indices/leaps_options/specifications/)
- Same Cboe page: exercise style is American ("generally may be exercised on any business day up to and including on the expiration date"); expiration date is "The third Friday of the expiration month"; strike intervals are 2.5 points for strikes $5-$25, 5 points for $25-$200, 10 points above $200. — [Cboe Equity LEAPS Product Specifications](https://cboe.com/tradable_products/equity_indices/leaps_options/specifications/)
- tastylive: "LEAPS can last up to two or three years from the time they are issued." Apart from the longer life, they are no different from standard listed stock and index options, and as expiration nears they "behave more like standard short-term options." — [tastylive, LEAPS](https://www.tastylive.com/concepts-strategies/leaps)
- Practitioner working definition (looser than the exchange one): "Options with more than 9 months until expiration are considered LEAPS." — [The Options Playbook, Buying LEAPS Calls as a Stock Substitute](https://www.optionsplaybook.com/rookies-corner/buying-leap-options)
- SpotGamma describes LEAPS as options with "roughly a year or more until expiration", typically "1-2+ years to expiration", listed out to about three years. — [SpotGamma, LEAPS options strategy](https://spotgamma.com/leaps-options-strategy/)
- **(snippet only)** ISE education page: equity LEAPS have one expiration month, January, in two different years; once a LEAP gets inside about a year to expiry it keeps trading as an ordinary shorter-term option. — [ISE, LEAPS](https://www.iseoptions.com/education/learn/leaps.html)
- **(snippet only)** Index LEAPS differ: a Schwab options calendar says equity LEAPS expire in January while index LEAPS expire in December, January and June. — [Schwab options calendar PDF](https://client.schwab.com/secure/file/P-714495)
- **(snippet only)** Cboe announced in October 2021 that it would list three long-dated SPX expirations (December 2024, December 2025, December 2026) from 1 November 2021, i.e. index options listed about five years out. — [Cboe press release](https://ir.cboe.com/news/news-details/2021/Cboe-Options-Exchange-to-List-Three-Long-Dated-SPX-Options-Expirations-Beginning-November-1-2021-10-27-2021/default.aspx)
- **(snippet only, sources conflict on listing month)** A 2018 Cboe notice said new January LEAP series would be listed in the September monthly-expiration week; Cboe's 2025 calendar snippet shows "2028 Equity and ETP LEAPS added" in November; an older education page says May/June/July. — [Cboe 2018 January LEAP listing schedule](https://cdn.cboe.com/resources/release_notes/2018/January-LEAP-Series-Listing-Schedule.pdf); [Cboe 2025 options calendar](https://res-certification.cboe.com/resources/options/Cboe2025OPTIONSCalendar.pdf)

### Inferences
- In November 2026 a stock with LEAPS would normally show January 2027 (now a 2-month ordinary option), January 2028 (about 14 months) and January 2029 (about 26 months, if already added). So the practical choice for a "LEAP" bought today is roughly 14 months or roughly 26 months, plus whatever ordinary monthly/quarterly expiries sit in the 9-12 month zone (for example June or September 2027). This is my reading of the January-only rule, not a quote; the live chain should be checked.
- Many heavily traded ETFs and index products list extra long-dated months (December, June), so the choice of expiry is wider there than on single stocks.
- "LEAP" has two meanings in the wild: the exchange product (January, up to 39 months) and the trader shorthand (anything 9-12+ months out). Sources using the word are not always talking about the same thing.

### Gaps
- I could not open the OIC (optionseducation.org) LEAPS page (404) or the OCC LEAPS page (403), so the "official" definition here rests on Cboe's specification page, which I did read.
- The current (2026) month in which the next January series (January 2029 / 2030) is added was not confirmed; sources seen disagree (September vs November), probably because the schedule has changed over the years.
- I did not confirm that every optionable stock has LEAPS; it does not (only selected underlyings do), but I have no opened source stating the count.

---

## 2. How theta changes with time to expiry (acceleration, square-root-of-time, concrete numbers)

### Takeaway
For an at-the-money option, time value shrinks roughly with the square root of time left, so the daily loss as a share of what you paid is tiny at 24 months and about 20 times larger at 1 month. But the "decay accelerates at the end" story is an at-the-money story: for a deep in-the-money call the time value drains at a nearly even pace, and for an out-of-the-money call the decay arrives earlier.

### Cited Findings
- Square-root rule, stated with an example: if a one-month at-the-money option trades for $1, a two-month one trades for about $1.41 (1 x square root of 2) and a three-month one for about $1.73 (1 x square root of 3). **(snippet only)** — [Investing.com, "Time is on your side"](https://www.investing.com/analysis/time-is-on-your-side,-yes-it-is-375536)
- tastylive: "this erosion (or theta) is slower in the early days and accelerates as the option approaches its expiration date"; for LEAPS, "the time decay (theta) is generally slower than that of a short-term option." — [tastylive, LEAPS](https://www.tastylive.com/concepts-strategies/leaps)
- Options Playbook: "as expiration approaches, options lose their value at an accelerating rate." — [The Options Playbook](https://www.optionsplaybook.com/rookies-corner/buying-leap-options)
- SpotGamma: "Time decay is not linear - it accelerates as expiration approaches"; a two-year option loses relatively little per day in its first year and "most of the decay is concentrated in the final months." — [SpotGamma](https://spotgamma.com/leaps-options-strategy/)
- Lawrence McMillan on the shape by moneyness (this is the important nuance):
  - At-the-money: "it generally holds its value pretty well until expiration starts to get near, and then it decays rather rapidly."
  - Out-of-the-money: "out-of-the-moneys decay faster initially, and then more slowly as expiration approaches."
  - In-the-money time value: "It is a rather flat decay - almost linear."
  - "In general, you get nearly two-thirds of your time decay in just about the half the time to expiration." His example: selling a one-month out-of-the-money combo (stock 100, 120 calls / 80 puts) gives "78% of your time decay in just 15 days if the underlying is unchanged."
  — [McMillan, Option Strategist, "Time Decay of Options"](https://optionstrategist.com/node/5141)
- **(snippet only)** McMillan also says the rate of time decay of a LEAPS call is much smaller than that of a shorter-term call. — [Option Strategist, "Understanding LEAPS"](https://optionstrategist.com/node/6671)
- **(snippet only)** Theta scales with implied volatility: for an at-the-money option, theta at 40% implied volatility is double the theta at 20%. — [Investing.com, same article](https://www.investing.com/analysis/time-is-on-your-side,-yes-it-is-375536)
- No source I found defines an exact "60-90 day" cliff. The search summary explicitly found no authoritative source naming a precise acceleration threshold; the nearest was a forum answer saying option sellers favour 30-45 days because that is the steepest part of the curve. **(snippet only)** — [Acadifi community answer](https://www.acadifi.com/community/theta-decay-term-structure-acceleration-expiry)

### Inferences
All numbers below are MY OWN Black-Scholes calculations, not published figures. Assumptions: stock $100, no dividend, interest rate 4%, implied volatility 30% (a typical large-cap level), stock price unchanged, 365-day year. Real quotes will differ (dividends, volatility skew, bid-ask spread).

**Table A. At-the-money call (strike 100), 30% volatility**

| Months left | Option price | Delta | Decay per day | Daily decay as % of premium |
|---|---|---|---|---|
| 36 | $25.50 | 0.69 | $0.013 | 0.05% |
| 24 | $20.28 | 0.66 | $0.016 | 0.08% |
| 18 | $17.25 | 0.64 | $0.018 | 0.10% |
| 12 | $13.75 | 0.61 | $0.021 | 0.15% |
| 9 | $11.73 | 0.60 | $0.024 | 0.20% |
| 6 | $9.39 | 0.58 | $0.028 | 0.30% |
| 3 | $6.46 | 0.56 | $0.038 | 0.59% |
| 2 | $5.21 | 0.55 | $0.045 | 0.87% |
| 1 | $3.62 | 0.53 | $0.062 | 1.72% |

Reading: per $100 of premium paid, the at-the-money call loses about 8 cents a day at 24 months, 15 cents at 12 months, 30 cents at 6 months, 59 cents at 3 months and $1.72 at 1 month. The percentage rate roughly doubles each time the remaining life halves (that is what "square root of time" means in practice: daily % decay is about 1 / (2 x days left)). There is no single cliff; the curve bends steadily, and it becomes hard to ignore somewhere inside the last 3-6 months. At 50% volatility the dollar decay is bigger but the percentage figures are almost identical (0.07% at 24 months, 0.14% at 12, 0.29% at 6, 0.57% at 3, 1.68% at 1).

**Table B. Deep in-the-money call (strike 75, stock 100, about 0.85-0.90 delta), 30% volatility**

| Months left | Option price | of which time value | Delta | Decay per day | Daily decay as % of premium | Daily decay as % of time value |
|---|---|---|---|---|---|---|
| 36 | $38.61 | $13.61 | 0.85 | $0.011 | 0.03% | 0.08% |
| 24 | $34.48 | $9.48 | 0.86 | $0.012 | 0.04% | 0.13% |
| 18 | $32.18 | $7.18 | 0.87 | $0.013 | 0.04% | 0.18% |
| 12 | $29.70 | $4.70 | 0.89 | $0.014 | 0.05% | 0.30% |
| 9 | $28.40 | $3.40 | 0.91 | $0.015 | 0.05% | 0.43% |
| 6 | $27.08 | $2.08 | 0.94 | $0.014 | 0.05% | 0.69% |
| 3 | $25.86 | $0.86 | 0.98 | $0.012 | 0.05% | 1.38% |
| 1 | $25.25 | $0.25 | 1.00 | $0.008 | 0.03% | 3.34% |

Reading: the deep in-the-money call loses about 1.1 to 1.5 cents a day the whole way, which is 0.03%-0.05% of the premium per day at every horizon. Dollar decay does NOT accelerate into expiry; it is close to a straight line (about $0.40 a month in this example), which matches McMillan's "almost linear" remark. Most of what you paid is intrinsic value that does not decay. Part of the "time value" here is really an interest charge on the strike you have not paid yet (75 x 4% is about $3 a year, about $0.25 a month); with interest set to 0% the time value of this call at 24 months drops from $9.48 to $5.38.

**Table C. Out-of-the-money call (strike 120, stock 100), 30% volatility**

| Months left | Option price | Delta | Daily decay as % of premium |
|---|---|---|---|
| 24 | $12.82 | 0.49 | 0.12% |
| 12 | $6.59 | 0.37 | 0.29% |
| 6 | $2.93 | 0.25 | 0.72% |
| 3 | $1.02 | 0.14 | 1.96% |
| 1 | $0.07 | 0.02 | 11.3% |

Reading: the out-of-the-money call bleeds two to seven times faster in percentage terms than the at-the-money call at the same horizon, and by the last three months it is losing about 2% of its value per day with the stock standing still. This is the zone short-dated out-of-the-money call buyers live in.

**Holding-period arithmetic (same assumptions, stock unchanged):**
- At-the-money, bought at 24 months and sold at 12 months: $20.28 falls to $13.75, a loss of $6.53 (32% of premium), about $0.54 a month.
- At-the-money, bought at 12 months and held to expiry: $13.75 falls to zero, 100% of premium, about $1.14 a month; the last 3 months alone cost $6.46 (about $2.14 a month).
- At-the-money, 12 months down to 6 months: lose $4.36 (32%). 6 months down to 3 months: lose $2.93 (31% of what was left).
- Deep in-the-money (strike 75), 24 months down to 12 months: $34.48 falls to $29.70, a loss of $4.78 (14% of premium), about $0.40 a month. From 12 months to expiry: a further $4.70 (16%), about $0.39 a month.
- So "sell before the last year" roughly halves the monthly time cost for an at-the-money LEAP, but makes almost no difference to the monthly time cost of a deep in-the-money LEAP.

Other inferences:
- The popular "decay accelerates in the last 60-90 days" line is true for at-the-money options and is the right mental picture for someone buying near the money. It is the wrong picture for deep in-the-money stock-replacement LEAPs, whose main risks are instead a fall in the stock (delta near 1 means you take nearly the full dollar loss) and losing the downside cushion, not a late theta cliff.
- Because a LEAP's price is mostly "volatility times square root of time", it is much more sensitive to a change in implied volatility than a short-dated option. A drop in implied volatility can cost more than months of theta (SpotGamma makes this point; see section 3).

### Gaps
- No authoritative source (Cboe, OIC, Fidelity, Schwab) was found publishing a table of daily decay as a percentage of premium at 24/12/6/3/1 months. The tables above are model output and should be presented as illustrations.
- The OIC article "LEAPS Time Erosion vs. Delta Effect" is the most relevant official piece; mirrors of it exist but the copies I tried returned 404, so its figures are not included.
- The tables ignore dividends (which lower call prices and can make early exercise sensible on deep in-the-money calls) and the volatility skew.

---

## 3. What practitioner sources recommend for expiry length and when to roll

### Takeaway
The common practitioner template is: buy at least a year out (often 18-24 months), deep in-the-money at around 0.75-0.80+ delta if the aim is stock replacement, and sell or roll while there is still meaningful time left, with the most-quoted windows being 6-12 months remaining or, at the latest, about 90-120 days. These are rules of thumb; none of the sources I found cites a study for the roll timing, and they disagree with each other.

### Cited Findings
- Expiry length: "As a general rule of thumb, consider buying a call that won't expire for at least a year or more." — [The Options Playbook](https://www.optionsplaybook.com/rookies-corner/buying-leap-options)
- Strike/delta for stock replacement: "look for a delta of .80 or more at the strike price you choose"; "consider a LEAPS call that is at least 20% of the stock price in-the-money" (for a $100 stock, strike $80 or lower); "for particularly volatile stocks, you may need to go deeper in-the-money to get the delta you're looking for." — [The Options Playbook](https://www.optionsplaybook.com/rookies-corner/buying-leap-options)
- Position size: "You should usually trade the same quantity of options as the number of shares you're accustomed to trading" (100 shares = one call), i.e. do not use the lower cost to buy more exposure. — [The Options Playbook](https://www.optionsplaybook.com/rookies-corner/buying-leap-options)
- Exit discipline: have "a predefined price at which you'll be satisfied with your option gains", and "a pre-defined stop-loss if the price of your option(s) goes down sharply." Expiry risk: "If the stock shoots skyward the day after your option expires, it does you no good." Worst case: "if your call options finish out-of-the-money, you may lose your entire investment." — [The Options Playbook](https://www.optionsplaybook.com/rookies-corner/buying-leap-options)
- SpotGamma: "Prefer 70-80 delta if the goal is stock replacement"; "Most holders close or roll with six to twelve months remaining"; LEAPS are usually closed or rolled early rather than held to expiry. — [SpotGamma](https://spotgamma.com/leaps-options-strategy/)
- SpotGamma on other risks: "IV crush hurts for a long time" (buying when implied volatility is high can leave the position under water even if the stock drifts up); "Liquidity is thinner in long-dated chains" and "wide bid/ask spreads can cost a meaningful fraction of the edge on entry and exit." Note: the fetch summary also reported the article as advising to buy when IV is elevated, which contradicts the IV-crush warning on the same page; I believe the intended advice is to avoid buying at elevated IV, but that line should be re-checked on the page. — [SpotGamma](https://spotgamma.com/leaps-options-strategy/)
- tastylive describes rolling without a timing rule: holders "sell it and use the proceeds to buy another LEAP with a later expiration date." Its stock-replacement illustration: 100 shares at $100 ($10,000) versus a $70-strike call at a $35 premium ($3,500). Listed drawbacks: higher premiums, "LEAPS can be less liquid than standard options, which might result in wider bid-ask spreads", no dividends or voting rights, and "As the expiration date approaches, the time decay of a LEAPS will accelerate." — [tastylive, LEAPS](https://www.tastylive.com/concepts-strategies/leaps)
- **(snippet only; blogs and course pages, not authoritative, and they conflict)** Roll-timing rules seen in search results: (a) roll when 3-6 months remain into a new option with 18+ months; (b) roll when 6-9 months remain; (c) roll at 90-120 days, with a warning that waiting to 30-45 days gives up the benefit. A delta guideline of 0.75 or higher attributed to The Blue Collar Investor also appeared. The search summary did not let me map each rule to one page; candidate pages were — [Traders Agency](https://tradersagency.com/blog/leaps-options-strategy-long-term-investors); [Option Alpha LEAPS guide](https://optionalpha.com/leaps-option-contracts-a-complete-guide-for-traders-196068.html); [Luckbox, LEAPS as stock replacement](https://luckboxmagazine.com/?p=16744); [Options Trading IQ](https://optionstradingiq.com/?p=27532)
- McMillan's general timing observation supports acting around the half-way point rather than the end: "you get nearly two-thirds of your time decay in just about the half the time to expiration" (said of out-of-the-money options from the seller's side). — [Option Strategist](https://optionstrategist.com/node/5141)

### Inferences
- Summary of the rules of thumb (labelled as such, not facts):
  - Buy: 12 months minimum; 18-24 months is the commonly preferred zone because it leaves room to hold for a year and still sell with 6-12 months left.
  - Strike: 0.75-0.85 delta, roughly 20% in-the-money, deeper for volatile names.
  - Roll or exit: with 6-12 months left (most conservative), or no later than about 3-4 months (90-120 days) left.
- My Table A arithmetic backs the 6-12 month rule for near-the-money LEAPs: daily decay is 0.15% of premium at 12 months, 0.30% at 6 months and 0.59% at 3 months, so each halving of remaining life doubles the bleed. For deep in-the-money LEAPs (Table B) the theta argument for rolling early is weak; the better reasons to roll there are (1) keeping time for the idea to work, (2) resetting the strike after a big rise so less capital is at risk, and (3) tax timing (section 6).
- Rolling is not free: each roll pays two bid-ask spreads in a thinly traded part of the chain, and (in a taxable account) realises the gain. A rule that rolls too often can cost more than the theta it saves. No source quantified this.
- A 24-26 month purchase is also the only way to combine "hold more than 12 months for long-term tax treatment" with "sell while 6-12 months remain".

### Gaps
- Schwab's LEAPS article could not be opened (access error), and I found no Fidelity or Cboe page giving a specific roll-timing rule. So the roll windows rest on The Options Playbook (Ally), SpotGamma, and assorted blogs.
- McMillan's book ("Options as a Strategic Investment") chapter on LEAPS was not accessible; only his free website articles were read. No direct book quote on LEAP roll timing.
- tastylive's research segments on LEAPS (their studies, as opposed to the concept page) were not found.
- No back-test of "roll at X months left" versus "hold to expiry" was found.

---

## 4. Cost per month of time: is a 24-month option cheaper per month than a 12-month one?

### Takeaway
Yes for at-the-money options: because price grows with the square root of time, doubling the time costs only about 40-50% more, so each month is cheaper the further out you go. For deep in-the-money calls the time cost per month is about the same at every expiry, and the total cash outlay is always larger for the longer option, so "cheaper per month" does not mean "cheaper".

### Cited Findings
- Square-root pricing example: one-month at-the-money option $1.00, two-month $1.41, three-month $1.73, so the second month costs $0.41 and the third $0.32. **(snippet only)** — [Investing.com](https://www.investing.com/analysis/time-is-on-your-side,-yes-it-is-375536)
- **(snippet only)** "If a one-month option is trading at one, the four-month option is not trading at four." — [MoneyShow](https://moneyshow.com/articles/optionsidea-22899)
- **(snippet only; illustrative figures from non-authoritative sites)** One comparison of the same at-the-money call across expiries put the cost of a month of time at $2.20 a share on a December 2028 expiry versus $5.90 on January 2027; another put an at-the-money LEAP with 18 months left at roughly $0.02 a day of theta versus $0.15 a day for a 30-day option. The search summary did not identify which page each came from; candidates — [AllInvestView LEAPS guide](https://www.allinvestview.com/articles/leaps-options-guide/); [ConvexTrade glossary](https://convextrade.com/glossary/leaps)
- tastylive lists as a possible LEAPS advantage that implied volatility can be lower in some cases, meaning cheaper premium relative to time, and as a drawback that the premium "is generally higher than that of shorter-term options." — [tastylive, LEAPS](https://www.tastylive.com/concepts-strategies/leaps)

### Inferences
My own Black-Scholes numbers (stock $100, 30% volatility, 4% rate, no dividend):

**At-the-money (strike 100): price divided by months of life**

| Expiry | Price | Price per month bought |
|---|---|---|
| 1 month | $3.62 | $3.62 |
| 3 months | $6.46 | $2.15 |
| 6 months | $9.39 | $1.57 |
| 12 months | $13.75 | $1.15 |
| 18 months | $17.25 | $0.96 |
| 24 months | $20.28 | $0.84 |
| 36 months | $25.50 | $0.71 |

- The 24-month option costs 47% more than the 12-month one ($20.28 vs $13.75) for twice the time; the extra 12 months cost $6.53, about $0.54 a month, versus $1.15 a month for the first 12. A 1-month option rolled twelve times would cost about $43 in premium against $13.75 for one 12-month option (ignoring stock movement), which is the plain-number version of why short-dated buying is expensive.
- Deep in-the-money (strike 75): time value is $4.70 at 12 months, $7.18 at 18, $9.48 at 24, $13.61 at 36, i.e. about $0.38-$0.40 a month at every expiry. Going further out does not make time cheaper per month here; you simply pay for more months at the same rate (and much of that rate is interest on the unpaid strike).
- Out-of-the-money (strike 120): $6.59 at 12 months vs $12.82 at 24 months, about $0.55 and $0.53 a month, so roughly the same per month, but the 24-month one has a much better chance of finishing in-the-money (delta 0.49 vs 0.37).
- What the longer option costs you in exchange: more dollars at risk up front, more sensitivity to a fall in implied volatility, wider bid-ask spreads, and lower leverage (a given rise in the stock produces a smaller percentage gain on the option).
- The saving is only real if you sell before the final stretch. If you buy 24 months and hold all the way to expiry, you pay for every month, including the expensive last ones.

### Gaps
- No authoritative source with real quoted chains (as opposed to model prices) comparing 12-month and 24-month premiums per month was opened. Real chains can deviate from the square-root rule because long-dated implied volatility is often different from short-dated, and because of dividends and interest rates.
- I have no data on typical LEAP bid-ask spreads as a percentage of premium versus near-dated options.

---

## 5. Academic and back-test evidence: long-dated vs short-dated calls, stock replacement, and the poor returns of short-dated out-of-the-money calls

### Takeaway
The academic evidence is clear on one thing: buying short-dated out-of-the-money calls on individual stocks has had negative average returns, because buyers overpay for lottery-like payoffs. The evidence I found is almost entirely about one-month options; I found no solid academic study that directly measures returns to buying LEAP calls versus short-dated calls or versus holding the stock, so the case for LEAPs rests on mechanics (slower decay, higher delta), not on a proven return advantage.

### Cited Findings
- Sophie Xiaoyan Ni, "Stock Option Returns: A Puzzle" (August 2008): sample January 1996 to June 2005, one-month calls on individual stocks with no ex-dividend date before expiry, held to expiration. Out-of-the-money calls had negative average returns; calls more than 15% out-of-the-money averaged -28% (per one-month holding period); the higher the strike, the worse the average return. Purchases were assumed at the bid-ask midpoint; buying at the ask may wipe out the positive average returns shown for at-the-money and in-the-money calls. — [CXO Advisory summary of Ni (2008)](https://www.cxoadvisory.com/equity-options/returns-for-call-options-on-individual-stocks/)
- **(snippet only)** Boyer and Vorkink, "Stock Options as Lotteries", Journal of Finance 69(4), 2014, pp. 1485-1527: options with more lottery-like payoffs (high expected skewness, i.e. far out-of-the-money and short-dated) earn much lower average returns; "Differences in average returns for option portfolios sorted on ex ante skewness range from 10% to 50% per week, even after controlling for risk." The authors read the premium as compensation to dealers for supplying lottery-like options that investors demand. — [IDEAS/RePEc abstract](https://ideas.repec.org/a/bla/jfinan/v69y2014i4p1485-1527.html)
- **(snippet only)** CXO Advisory's summary of the Boyer-Vorkink working paper adds that the effect is strongest near expiration and that bid-ask spreads widen sharply with skewness. — [CXO Advisory, "Sell lottery tickets..."](https://www.cxoadvisory.com/equity-options/sell-lottery-tickets-for-their-fun-and-your-profit)
- **(snippet only)** Coval and Shumway, "Expected Option Returns", Journal of Finance 56(3), 2001, pp. 983-1009: theory says expected call returns should exceed the stock's and rise with the strike (calls are leveraged stock); yet "Market-neutral straddles on the S&P 500 index receive average weekly returns of minus three percent per week", evidence that option buyers pay a premium for volatility. — [EconPapers abstract](https://econpapers.repec.org/RePEc:bla:jfinan:v:56:y:2001:i:3:p:983-1009)
- **(snippet only)** Index options show the same pattern: Bakshi, Madan and Panayotov (2010) are cited as documenting that average returns of out-of-the-money index calls are negative and decline with the strike. — [JFQA paper citing them](https://core-prod.cambridgecore.org/core/services/aop-cambridge-core/content/view/48A7123D44269F898FCDB82B0CF96590/S0022109022000333a.pdf/pricing_of_volatility_and_jump_risks_in_the_crosssection_of_index_option_returns.pdf)
- **(snippet only)** A paper using cumulative prospect theory argues that investors' overweighting of small probabilities explains why out-of-the-money single-stock calls are priced richly. — [CFS Working Paper 566](https://gfk-cfs.de/media/CFS_WP_566.pdf)

### Inferences
- Putting Ni and Coval-Shumway together: theory says a call is leveraged stock and should earn more than the stock on average; in practice in-the-money and at-the-money one-month calls did earn positive average returns at mid prices, while out-of-the-money ones lost money on average. So the damage is concentrated in exactly the corner (short-dated, far out-of-the-money, high implied volatility, wide spreads) that feels cheapest.
- A deep in-the-money LEAP is the opposite corner: low skewness, mostly intrinsic value, small time premium relative to price. By the logic of these papers it should carry little of the "lottery" overpricing. This is a reasoned extension, not a tested result: none of the studies I found measured LEAP returns.
- "Negative average return" is an average over many trades; individual short-dated out-of-the-money calls can and do pay off many times over, which is precisely what makes them attractive and overpriced.
- My model tables (section 2) give the mechanical reason in plain numbers: a 20% out-of-the-money call with 1-3 months left loses about 2% to 11% of its value per day if the stock stands still, against about 0.04% a day for a 24-month deep in-the-money call.
- A LEAP call still loses 100% of the premium if the stock is below the strike at expiry, and a deep in-the-money LEAP falls nearly dollar-for-dollar with the stock. Slower decay is not lower risk.

### Gaps
- No academic paper or reputable back-test was found (in this search budget) that directly compares buying LEAP calls with buying short-dated calls, or deep in-the-money LEAP "stock replacement" with owning the stock, over a long sample. This is the biggest hole in these notes. Claims online that LEAPs "outperform" are, as far as I could verify, unsupported by published evidence.
- I did not read the full text of Boyer-Vorkink or Coval-Shumway (abstracts and summaries only), so the per-week figures should be quoted as abstract-level results.
- The -28% figure from Ni is for one-month calls more than 15% out-of-the-money in 1996-2005; I did not see the paper's full table of returns by moneyness, and I have no update for more recent years.
- tastylive research on long-dated versus short-dated long calls was not found.

---

## 6. US tax note (brief)

### Takeaway
A LEAP call sold after being held more than one year gives a long-term capital gain or loss; one year or less is short-term. Exercising the call does not carry the holding period over: the option's cost is added to the stock's cost and the stock's holding period starts fresh the day after exercise.

### Cited Findings
- "If you hold investment property more than 1 year, any capital gain or loss is a long-term capital gain or loss." "If you hold the property 1 year or less, any capital gain or loss is a short-term capital gain or loss." Counting begins "on the date after the day you acquired the property." — [IRS Publication 550](https://www.irs.gov/publications/p550)
- Call buyers: "If you buy a put or a call, you may not deduct its cost. It is a capital expenditure." If it expires, "its cost is either a long-term or short-term capital loss", and "you are considered to have sold or traded the option on the date it expired." — [IRS Publication 550](https://www.irs.gov/publications/p550)
- Exercise: "If you exercise a call, add its cost to the basis of the stock you bought." "Your holding period for property you acquire when you exercise an option begins the day after you exercise the option." — [IRS Publication 550](https://www.irs.gov/publications/p550)
- Wash sales reach options: "The wash sale rules apply to losses from sales or trades of contracts and options to acquire or sell stock or securities", and one trigger is to "Acquire a contract or option to buy substantially identical stock or securities." — [IRS Publication 550](https://www.irs.gov/publications/p550)
- **(snippet only)** Secondary sources put short-term gains at ordinary rates up to 37% and long-term gains at up to 20%. — [search result, QuantWheel "How are options taxed"](https://quantwheel.com/learn/how-are-options-taxed)

### Inferences
- To get long-term treatment and still sell with 6-12 months of life left, the option has to be bought with roughly 18-24+ months to expiry. A 12-month LEAP cannot do both.
- Rolling (selling one LEAP and buying a later one) is a sale for tax purposes and starts a new holding period on the new option. Rolling at a loss into a similar call on the same stock within 30 days may fall under the wash-sale rule quoted above.
- Broad-based index options (for example SPX) are taxed under different rules (Section 1256, a 60/40 long-term/short-term split regardless of holding period); Publication 550 defines "nonequity option" under Section 1256 contracts, but I did not extract the detailed text, so treat this bullet as general knowledge to verify.
- None of this applies inside tax-advantaged accounts such as IRAs.

### Gaps
- Current-year (2026) rate brackets and the 3.8% net investment income tax were not verified.
- The straddle rules and "qualified covered call" rules (relevant if short calls are sold against a LEAP) were not researched.
- This is a summary of general rules, not tax advice; state taxes not covered.
