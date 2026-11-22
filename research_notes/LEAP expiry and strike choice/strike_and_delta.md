# LEAP call strike and delta choice: deep in-the-money vs at-the-money vs out-of-the-money

Scope notes for the report writer:
- General education only, US-listed equity and ETF options, written November 2026. Not personalized advice, no stock picks.
- "Cited Findings" are what a named source says. "Inferences" are my own reasoning or my own model calculations and are labelled as such.
- Source quality is uneven. The strongest sources are OIC (optionseducation.org), Cboe, OCC, The Options Playbook (Brian Overby, formerly TradeKing/Ally), tastylive, Option Alpha, McMillan's optionstrategist.com, and peer-reviewed papers (read through abstracts and secondary summaries, not full text). Many "0.80 delta" pages on the web are low-quality blogs repeating one another; I cite them only as evidence of the convention.
- Schwab's LEAPS article (schwab.com/learn/story/leaps-options-basics-strategies-risks) refused automated access, and no Fidelity LEAPS page surfaced in search. Nothing in these notes is attributed to Schwab's or Fidelity's LEAPS pages.
- Plain-language glossary: delta = how many cents the option moves when the stock moves $1 (0.80 delta moves about 80 cents). Intrinsic value = how much the option is already in the money (stock price minus strike). Time value / extrinsic value = the rest of the premium; it is the part that melts to zero by expiry. Break-even at expiry = strike + premium paid.

## 1. What delta do practitioner sources recommend for LEAP calls used as stock replacement, and why?

### Takeaway
The practitioner convention is a deep in-the-money call with delta about 0.80 or higher (roughly a strike 20% or more below the stock price), because most of what you pay is intrinsic value rather than time value, so the break-even sits close to today's price and the option tracks the stock. This is a convention from educational sources, not a research result.

### Cited Findings
- The Options Playbook: "look for a delta of .80 or more at the strike price you choose." — [Options Playbook, Buying LEAPS Calls as a Stock Substitute](https://optionsplaybook.com/rookies-corner/buying-leap-options)
- Same source, starting point for strike: consider a LEAPS call at least 20% of the stock price in the money; for a $100 stock, a strike of $80 or lower; for volatile stocks you may need to go deeper to reach the same delta. — [Options Playbook](https://optionsplaybook.com/rookies-corner/buying-leap-options)
- Same source, reason: the higher the delta, the more the option behaves as a stock substitute; deeper strikes cost more because they hold more intrinsic value. — [Options Playbook](https://optionsplaybook.com/rookies-corner/buying-leap-options)
- Same source, expiry and sizing: buy a call that will not expire for a year or more; trade about the same number of contracts as the hundreds of shares you would normally buy (one contract = 100 shares) and do not over-size; set a profit target and stop-loss before buying; if the calls expire out of the money the whole investment can be lost. — [Options Playbook](https://optionsplaybook.com/rookies-corner/buying-leap-options)
- tastylive describes stock replacement as buying "a deep in-the-money (ITM) LEAPS call" and gives the example of a $100 stock: instead of $10,000 for 100 shares, a $70-strike LEAPS call at a $35 premium ($3,500 per contract). It does not name a delta. — [tastylive, LEAPS](https://www.tastylive.com/concepts-strategies/leaps)
- tastylive's listed drawbacks: higher premiums than short-dated options, wider bid-ask spreads, time decay that is slow at first and accelerates near expiry, no dividends or voting rights, and the LEAP can underperform the stock if the stock moves slowly. — [tastylive, LEAPS](https://www.tastylive.com/concepts-strategies/leaps)
- McMillan (optionstrategist.com) frames the substitute as selling the stock, putting a small part of the proceeds into an in-the-money call and the rest into a T-bill or CD matching the LEAP's life. He counts the cost of switching as the call's time value premium plus commissions plus lost dividends, and says there "generally is a net cost of switching." Loss is limited to the cost of the call if the stock falls dramatically. — [McMillan, LEAPS As A Stock Substitute](https://www.optionstrategist.com/node/2681)
- McMillan's risks: the call owner misses any dividend increase or special dividend; tax effects (selling stock can trigger gains; buying a call on stock sold at a loss can be a wash sale). — [McMillan](https://www.optionstrategist.com/node/2681)
- Convention across secondary/blog sources (lower quality, consistent with each other): 70-80 delta "usually cost between 20-30% of the underlying stock price"; an at-the-money LEAP has delta near 0.55 and a large share of its premium is pure time value; a call bought at 0.80 delta can drift to 0.95 after a large rally. — [search-surfaced examples: optionstradingiq.com](https://optionstradingiq.com/leap-options/), [Options Playbook blog on the poor man's covered call](https://www.optionsplaybook.com/blogs/poor-mans-covered-call-how-to-run-a-covered-call-strategy-for-less-capital)

### Inferences
- The four reasons, in plain terms (my synthesis of the sources above plus the model numbers in section 2):
  1. Extrinsic value paid: a 0.85-delta 1-year call is about 77% intrinsic and 23% time value in my model; an at-the-money or out-of-the-money call is 100% time value. Time value is the only part guaranteed to melt.
  2. Break-even distance: deep ITM needs about +6% (1 year) to break even at expiry; at-the-money needs about +14%; a 0.24-delta call needs about +39%.
  3. Leverage: deep ITM gives modest leverage (about 3x), OTM gives more (about 6.5x at 1 year). More leverage per dollar is the price of a much lower chance of being paid.
  4. Probability of finishing in the money: about 77% (deep ITM) vs about 49% (ATM) vs about 15% (0.24-delta) in the model. Finishing in the money is still not the same as making money; the stock also has to clear the break-even.
- Deep ITM is not "safe": in the model a 20% drop in the stock wipes out 100% of a 1-year $80-strike call at expiry. The dollar loss is capped at the premium, but the premium is large ($2,587 per contract in the example).
- For someone used to short-dated OTM calls, the key mental shift is that a deep ITM LEAP is sized like a stock position (one contract per 100 shares you would otherwise buy), not like a lottery ticket.

### Gaps
- I found no peer-reviewed study that tests "0.80 delta" against other deltas for LEAP stock replacement. The 0.80 figure is a practitioner rule of thumb.
- Could not retrieve Schwab's or Fidelity's LEAPS pages, so their exact wording on strike choice is not confirmed. Natenberg's and McMillan's books were not consulted directly (only McMillan's website article).

## 2. Numbers: $100 stock, 12- and 24-month calls at high, middle and low delta

### Takeaway
On a model $100 stock, the deep ITM call loses the least if the stock is flat (about -23% to -28% of premium at expiry) while ATM and OTM calls lose 100%; the OTM call needs a move of roughly +39% (1 year) or +65% (2 years) just to break even at expiry.

### Cited Findings
- Sourced worked example for comparison (tastylive, OTM LEAP): stock at $50, $60-strike call two years out, $8 premium ($800). At expiry: stock $90 = +$2,200; stock $69 = +$100; stock $55 = option expires worthless, $800 lost. Break-even is $68, a +36% move. — [tastylive, LEAPS](https://www.tastylive.com/concepts-strategies/leaps)
- Sourced worked example (tastylive, deep ITM): $100 stock, $70-strike LEAP at $35 ($3,500 vs $10,000 for shares). That is $30 intrinsic and $5 time value; break-even $105. — [tastylive, LEAPS](https://www.tastylive.com/concepts-strategies/leaps) (the intrinsic/time split and break-even are my arithmetic on their numbers)
- Options Playbook's rule (strike $80 or lower on a $100 stock for 0.80+ delta) matches the model below, where the $80 strike has delta 0.85. — [Options Playbook](https://optionsplaybook.com/rookies-corner/buying-leap-options)

### Inferences
All figures below are MY OWN Black-Scholes calculations, not quotes from a source. Assumptions: stock $100, implied volatility 30% for every strike (real chains have different volatility at each strike), interest rate 4%, no dividends, European-style pricing, no commissions or bid-ask cost. Per share; multiply by 100 for one contract. Real quotes will differ, and higher-volatility stocks will show much more time value.

Important quirk: for long-dated calls the at-the-money strike has delta well above 0.50 (0.61 at 1 year, 0.66 at 2 years in this model), because of interest rates and time. A true 0.50-delta LEAP strike is already above the stock price. So "ATM" and "0.50 delta" are not the same thing on a LEAP chain.

12-month calls:

| Strike | Delta | Premium | Intrinsic | Time value | Break-even at expiry | Move needed | Model chance of finishing ITM | Leverage (delta x stock / premium) |
|---|---|---|---|---|---|---|---|---|
| $80 (deep ITM) | 0.85 | $25.87 | $20.00 | $5.87 | $105.87 | +5.9% | 77% | 3.3x |
| $100 (ATM) | 0.61 | $13.75 | $0 | $13.75 | $113.75 | +13.8% | 49% | 4.4x |
| $135 (OTM) | 0.24 | $3.62 | $0 | $3.62 | $138.62 | +38.6% | 15% | 6.5x |

12-month calls, profit or loss at expiry (per share, and as % of premium):

| Strike | Stock -20% ($80) | Stock flat ($100) | Stock +20% ($120) |
|---|---|---|---|
| $80 | -$25.87 (-100%) | -$5.87 (-23%) | +$14.13 (+55%) |
| $100 | -$13.75 (-100%) | -$13.75 (-100%) | +$6.25 (+45%) |
| $135 | -$3.62 (-100%) | -$3.62 (-100%) | -$3.62 (-100%) |

24-month calls:

| Strike | Delta | Premium | Intrinsic | Time value | Break-even at expiry | Move needed | Model chance of finishing ITM | Leverage |
|---|---|---|---|---|---|---|---|---|
| $75 (deep ITM) | 0.86 | $34.48 | $25.00 | $9.48 | $109.48 | +9.5% | 74% | 2.5x |
| $100 (ATM) | 0.66 | $20.28 | $0 | $20.28 | $120.28 | +20.3% | 49% | 3.2x |
| $160 (OTM) | 0.24 | $4.93 | $0 | $4.93 | $164.93 | +64.9% | 13% | 4.9x |

24-month calls, profit or loss at expiry:

| Strike | Stock -20% ($80) | Stock flat ($100) | Stock +20% ($120) |
|---|---|---|---|
| $75 | -$29.48 (-86%) | -$9.48 (-28%) | +$10.52 (+30%) |
| $100 | -$20.28 (-100%) | -$20.28 (-100%) | -$0.28 (-1%) |
| $160 | -$4.93 (-100%) | -$4.93 (-100%) | -$4.93 (-100%) |

Halfway through the option's life (mark-to-market, same volatility), for readers who would sell before expiry:

| Option | Stock -20% | Stock flat | Stock +20% |
|---|---|---|---|
| 12m $80 | -71% | -12% | +61% |
| 12m $100 | -88% | -32% | +75% |
| 12m $135 | -98% | -71% | +55% |
| 24m $75 | -61% | -14% | +40% |
| 24m $100 | -79% | -32% | +39% |
| 24m $160 | -97% | -75% | -6% |

What the tables show:
- Flat stock: only the deep ITM call keeps most of its value. ATM and OTM calls go to zero at expiry.
- Stock +20% by expiry: the deep ITM call makes money in both tenors; the 2-year ATM call roughly breaks even (-1%); both OTM calls still expire worthless. A +20% move is a good year for a stock and it is not enough for a 0.24-delta LEAP held to expiry.
- OTM LEAPs can pay if the move comes early and the position is sold: the 12-month $135 call is +55% if the stock is +20% at the six-month mark. That is a bet on the timing of the move, not just the direction.
- Stock -20%: every call in the table loses most or all of its premium. Dollar loss is biggest on the deep ITM call ($2,587 to $2,948 per contract) and smallest on the OTM call ($362 to $493). Percent-of-premium loss and dollar loss point in opposite directions, which is why contract count matters (section 6).
- Compared with shares: 100 shares would lose $2,000 on a -20% move and gain $2,000 on +20%. The 12-month $80 call loses $2,587 and gains $1,413. At expiry the deep ITM LEAP is worse than stock in a flat or modestly down market by exactly its time value; its advantage is that the loss stops at the premium in a crash and less cash is tied up.
- Contrast with a short-dated OTM call (same model, 30 days, $110 strike): premium $0.66, delta 0.15, about 13% model chance of finishing in the money, 100% loss if flat. The LEAP OTM call has a similar probability profile but costs several times more.

### Gaps
- These are model numbers at one volatility (30%). No sourced table covering the same six cases was found. Real chains have volatility skew (deep ITM and far OTM strikes are priced at different volatilities), dividends and American exercise, all of which shift the numbers.
- No sourced historical data on how often a stock moves +39% in a year or +65% in two years was collected here; that depends heavily on the stock.

## 3. Evidence on low-delta, long-dated calls as lottery tickets

### Takeaway
Academic studies of US stock options find that out-of-the-money calls earned poor, often negative, average returns and that the more lottery-like the option, the worse the average return; delta is a usable rough guide to the chance of finishing in the money but it overstates that chance for calls, and the overstatement is largest for long-dated and high-volatility options. Almost all of this evidence is from short-dated options, so applying it to LEAPs is an extrapolation.

### Cited Findings
- Ni, "Stock Option Returns: A Puzzle": over 1996-2005, across US stocks with listed options, (1) out-of-the-money calls have negative average returns and (2) average returns of high-strike calls are lower than those of low-strike calls. The proposed explanation is risk-seeking (lottery-style) buying. — [HKUST research portal](https://researchportal.hkust.edu.hk/en/publications/stock-option-returns-a-puzzle/); secondary summary with the caveat that results assume buying at the bid-ask midpoint, which is optimistic: [CXO Advisory](https://www.cxoadvisory.com/equity-options/returns-for-call-options-on-individual-stocks/)
- Boyer and Vorkink, "Stock Options as Lotteries" (Journal of Finance, 2014): options with higher expected skewness (more lottery-like payoff) earn lower average returns. — [IDEAS/RePEc record](https://ideas.repec.org/a/bla/jfinan/v69y2014i4p1485-1527.html)
- Secondary summary of Boyer and Vorkink: sample is US individual stock options about 1996-2009, held to expiration; moneyness is the main driver of skewness (far OTM is the most lottery-like); the low-minus-high skewness return gap for 7-day options was about +36.8% for calls; the effect is strongest near expiration; bid-ask spreads rise sharply with skewness and reported returns ignore fees. — [CXO Advisory](https://www.cxoadvisory.com/equity-options/sell-lottery-tickets-for-their-fun-and-your-profit)
- Coval and Shumway, "Expected Option Returns" (Journal of Finance, 2001): theory says expected call returns exceed the underlying's and increase with strike, because calls are leveraged positions in the stock. This is the textbook benchmark the two studies above contradict for individual-stock OTM calls. — [EconPapers record](https://econpapers.repec.org/RePEc:bla:jfinan:v:56:y:2001:i:3:p:983-1009)
- Delta as probability: delta is "sometimes used as a proxy" for the chance of expiring in the money (0.36 delta treated as about 36%); the rule "doesn't work 100% of time, especially for deltas very close to 0.50 and options with longer time to expiration"; it assumes random price moves and unbiased option pricing, conditions "rarely met in practice." — [Macroption](https://macroption.com/delta-calls-puts-probability-expiring-itm)
- In Black-Scholes, delta is N(d1) while the (risk-neutral) probability of finishing in the money is N(d2); for calls delta is the larger of the two, and the gap grows with time to expiry and volatility. — [Wikipedia, Greeks (finance)](https://en.wikipedia.org/wiki/Greeks_(finance))
- Delta says nothing about how far in the money the option finishes, so it is not a probability of profit. — [search-surfaced, MoneyShow](https://moneyshow.com/articles/optionsidea-20708)

### Inferences
- The size of the delta-vs-probability gap on LEAPs, from my model in section 2 (30% volatility): 0.24 delta corresponds to about 15% (1 year) and 13% (2 years) chance of finishing in the money; ATM delta of 0.61-0.66 corresponds to about 49%; 0.85 delta corresponds to about 74-77%. For a 30-day option the gap is small (0.15 delta vs 13%). So "delta is roughly the chance" is a decent shortcut for short-dated options and noticeably too optimistic for LEAPs, by roughly 8 to 17 points in this example.
- Probability of profit at expiry is lower again than probability of finishing in the money, because the stock must clear strike plus premium.
- These are risk-neutral (market-implied) probabilities, not forecasts. Real-world odds for a stock with positive expected drift are somewhat better than risk-neutral odds; but Ni's and Boyer-Vorkink's realized results suggest buyers of OTM calls on individual stocks have, on average, overpaid anyway.
- Why low-delta calls disappoint on average, in plain terms: buyers like small-cost, big-payoff bets and pay up for them, sellers demand extra to carry the risk, and the bid-ask cost is largest in percentage terms on cheap options.
- Fair statement for a beginner: a 0.10-0.30 delta LEAP is a bet that loses the whole premium most of the time and occasionally pays several times over; the research says the occasional big wins have not, on average, made up for the frequent losses on individual-stock OTM calls. It does not say such a trade can never work.

### Gaps
- The academic papers were read through abstracts and secondary summaries, not full text. Exact return figures by moneyness bucket from Ni were not verified; one secondary source cites average returns of -96% or worse for the deepest OTM calls held to expiry, which I could not confirm in the paper and have left out of the findings.
- The studies cover mostly options with weeks to a few months of life, ending 2005-2009. I found no study isolating OTM LEAP calls (1-2+ years). Boyer and Vorkink report the lottery effect weakens with longer maturity, so the penalty on OTM LEAPs is likely smaller than on OTM weeklies, but that is not measured here.
- No reliable published "probability of profit" statistics for retail LEAP buyers were found.

## 4. Is high open interest at a LEAP strike evidence of direction, or a reason to pick that strike?

### Takeaway
Open interest counts contracts outstanding; every contract has both a buyer and a seller, so by itself it is neither bullish nor bearish. Educational sources treat it as a rough liquidity check, and the industry's own education body (OIC) warns it is not even a reliable liquidity measure: look at the actual bid-ask spread and quoted size instead.

### Cited Findings
- OIC: "Open interest only reflects the total number of open (long or short) option contracts for a given option series" and it "indicates neither a bullish nor bearish outlook." It shows "which options were previously the most active." — [OIC FAQ, General Information](https://www.optionseducation.org/referencelibrary/faq/general-information)
- OIC: "A common misconception is that volume and open interest equate with liquidity." Market makers quote every series; "Don't let the open interest or volume fool you into thinking that there is or is not liquidity in that specific contract." A liquid market "is evidenced by a tight (or small) spread between bid and offer." OIC suggests asking for a two-sided market with size. — [OIC FAQ](https://www.optionseducation.org/referencelibrary/faq/general-information)
- OIC: open interest is only updated once a day, after OCC clears and pairs opening and closing trades. — [OIC FAQ](https://www.optionseducation.org/referencelibrary/faq/general-information)
- Option Alpha: "Higher open interest doesn't necessarily mean smart traders are active in the contract"; it just shows interest concentrated at that strike. High open interest "typically means it's easier to trade the option." — [Option Alpha, Volume vs Open Interest](https://optionalpha.com/learn/options-volume-vs-open-interest)
- Option Alpha: round strikes attract more activity: "The $100 strike price for a security likely has more volume and open interest than the $99 strike price." — [Option Alpha](https://optionalpha.com/learn/options-volume-vs-open-interest)
- tastylive: "High open interest often translates to increased liquidity"; liquidity is judged by bid-ask spread, open interest, volume and market depth together. — [tastylive, LEAPS](https://www.tastylive.com/concepts-strategies/leaps), [tastylive, Options Liquidity](https://www.tastylive.com/concepts-strategies/options-liquidity)
- Interactive Brokers education calls "higher open interest means more liquidity" a rule of thumb. — [IBKR Campus, Open Interest](https://ibkrcampus.com/trading-lessons/open-interest)
- Conflicting view (broker education, weaker): Moomoo says high open interest in an OTM call "could be interpreted as a bullish signal." — [Moomoo, Option Volume vs Open Interest](https://www.moomoo.com/us/learn/detail-options-volume-vs-open-interest-117044-240246026); contradicted by [OIC](https://www.optionseducation.org/referencelibrary/faq/general-information)
- One blog suggests a floor of "at least a few hundred contracts" of open interest at the chosen strike and expiry; this is an unsourced rule of thumb. — [Ryan O'Connell Finance](https://ryanoconnellfinance.com/open-interest-options/)

### Inferences
- Why big open interest at a LEAP call strike is not a direction signal: you cannot see whether the opening side was a buyer or a seller, and large call positions are often one leg of something else (covered calls written against shares, call spreads, hedges against short stock, collars). A large call open interest can just as easily be institutions that SOLD those calls.
- On LEAP chains, open interest also piles up for mechanical reasons: positions sit for a long time, and round strikes and January expiries collect activity (section 5).
- Practical reading for strike choice: decide the delta/strike from the trade-offs in sections 1-2 first; then use open interest and, more importantly, the live spread and size, only to choose between neighbouring strikes that are otherwise similar (for example the round strike over an odd one).
- This matches the project owner's own back-test note in memory (walls / magnet / put-call open interest tested as a coin flip on direction); that is internal evidence, not a published source.

### Gaps
- No academic evidence was collected here on whether open interest predicts stock direction. There is academic literature on signed option volume (who initiated the trade) predicting returns, but that needs trade-direction data that public open interest does not provide; it was not researched in this pass.
- No authoritative minimum open-interest threshold exists; the "few hundred contracts" figure is one blogger's rule.

## 5. Liquidity of far-dated options

### Takeaway
Sources agree LEAPs trade less and have wider bid-ask spreads than near-dated options, so entry and exit cost more; the standard advice is a limit order rather than a market order, and to prefer the contracts where activity concentrates (round strikes, January expiries). I found no reliable published statistics on typical LEAP spread widths.

### Cited Findings
- tastylive: "At times, LEAPS options may not be as liquid as their shorter-term counterparts. Bid-ask spreads may also be wider," which raises entry and exit costs. — [tastylive, LEAPS](https://www.tastylive.com/concepts-strategies/leaps)
- OIC: a wider-than-usual spread usually means market makers are unsure where they can hedge; a liquid option usually has a liquid underlying stock. — [OIC FAQ](https://www.optionseducation.org/referencelibrary/faq/general-information)
- Schwab (international site): irregular order flow and low liquidity lead to wider bid/ask spreads because market makers face more risk and less competition. — [Schwab, Large Bid/Ask Options Spreads in Volatile Markets](https://international.schwab.com/story/large-bidask-options-spreads-volatile-markets) (seen via search summary, not fetched in full)
- Cboe product specifications: equity LEAPS expire about two to three years from initial listing (up to 39 months), "January expiration only." — [Cboe, Equity LEAPS Specifications](https://cboe.com/tradable_products/equity_indices/leaps_options/specifications/)
- OCC: equity and ETF LEAPS expire on the third Friday in January. — [OCC, Equity and ETF LEAPS](https://www.theocc.com/clearance-and-settlement/clearing/equity-and-etf-leaps)
- New January LEAP series are added each year around September (Cboe notice, 2018; the exact current schedule may differ). — [Cboe, January LEAP Series Listing Schedule](https://cdn.cboe.com/resources/release_notes/2018/January-LEAP-Series-Listing-Schedule.pdf)
- OIC has a dedicated FAQ on LEAPS and expiration cycles. — [OIC, LEAPS and Expiration Cycles](https://www.optionseducation.org/referencelibrary/faq/leaps-and-expiration-cycles) (listed, not fetched)
- Round strikes draw more volume and open interest than odd strikes. — [Option Alpha](https://optionalpha.com/learn/options-volume-vs-open-interest)
- One data point of how wide LEAP quotes can be: a search-surfaced example quoted a far-dated call at $88.25 bid / $89.75 ask ($1.50 wide) and an adjacent January 2029 $150 call with an $8-wide spread. Single anecdote, source quality unverified. — [search-surfaced; likely allinvestview.com LEAPS guide](https://www.allinvestview.com/articles/leaps-options-guide/)

### Inferences
- Why January is usually the most liquid far-dated month: by exchange rule the long-dated series for a stock are January series, so for dates more than about a year out January is often the only or the oldest-listed choice, and open interest has had the longest time to build there. Other far months, where listed, are newer and thinner. This is reasoning from the listing rules, not a measured statistic.
- Why limit orders near the mid matter more on LEAPs: the premium is large, so a spread that looks small in percent is large in dollars. Example arithmetic (mine): a deep ITM LEAP quoted $25.00 bid / $26.50 ask costs $150 per contract more at the ask than at the bid; buying at the ask and later selling at the bid gives up $150, about 6% of the premium and more than a quarter of the $5.87 time value in the section 2 example. Working an order at or slightly above the mid and letting it sit is the usual practitioner approach; a market order pays the full ask.
- Deep ITM strikes often have wider quoted spreads in dollars than ATM strikes (they are expensive and less traded), though the spread is small relative to the stock-equivalent exposure. Check the live quote; do not assume.
- OIC's warning cuts both ways: low open interest does not prove an option is untradeable, and high open interest does not guarantee a tight spread. The live bid, ask and size are the evidence.
- Practical checks before buying a LEAP (general education): spread as a percent of the mid; quoted size on each side; whether the neighbouring round strike is tighter; whether the January expiry is tighter than other far months.

### Gaps
- No authoritative statistics on typical LEAP bid-ask spreads versus near-dated spreads (in cents or percent) were found. All sources state the direction (wider) without numbers.
- No source measured whether January LEAPs are systematically tighter than other far-dated months; the claim rests on listing rules and practitioner convention. Some heavily traded stocks and ETFs now list additional far-dated months (for example June or December) and this was not researched.
- Strike-interval rules for LEAPs (how far apart strikes are listed) were not found.
- Schwab's and Fidelity's guidance on limit orders for options was not retrieved directly.

## 6. Position sizing guidance for long options (general education)

### Takeaway
For a long call the most you can lose is the premium, so educational sources size by capping premium at risk as a small percent of the account (commonly quoted ranges run from 1-2% up to 5% per trade) and by matching contract count to the share position you would otherwise hold. These percentages are teaching conventions, not tested rules.

### Cited Findings
- Options Playbook, for LEAPs as stock substitutes: trade the same number of contracts as hundreds of shares you would normally buy; "Don't go too crazy"; the entire investment can be lost if the calls expire out of the money; set a profit target and stop-loss in advance. — [Options Playbook](https://optionsplaybook.com/rookies-corner/buying-leap-options)
- tastylive: a LEAP buyer's maximum loss is the premium paid. — [tastylive, LEAPS](https://www.tastylive.com/concepts-strategies/leaps)
- McMillan's framing: put a small portion of the money into the in-the-money call and keep the rest in T-bills/CDs for the life of the LEAP (i.e. do not spend the cash freed up on more calls). — [McMillan](https://www.optionstrategist.com/node/2681)
- Percent-of-account conventions reported across educational pages: 1-2% per trade, 2-5% per trade, and 5% as a popular options guideline; for long calls and debit spreads the cost of the trade is the risk. Worked example: $10,000 account at 5% = $500 maximum risk; an option at $2.50 costs $250 per contract, so two contracts. — [Option Alpha, Options Trading Risk Management](https://optionalpha.com/options-trading-risk-management-19292.html), [Robinhood Learn, Risk Management](https://robinhood.com/us/en/learn/articles/risk-management/) (both seen via search summary, not fetched in full; the attribution of each number to a specific page is not verified)

### Inferences
- Deep ITM LEAPs break the simple "risk X% of the account in premium" rule, because one contract can cost $2,500-$3,500 on a $100 stock. Two ways educational sources reconcile this: (a) size by share-equivalent exposure, as Options Playbook does (one 0.85-delta contract behaves like about 85 shares), and (b) decide in advance the stock price or option loss at which you exit, and treat that planned loss, not the full premium, as the amount at risk, while accepting that a gap down can exceed it.
- OTM LEAPs are the opposite trap: each contract is cheap ($362-$493 in the section 2 model), so it is easy to buy many. Since the model chance of finishing in the money is about 13-15%, the full premium should be treated as likely lost when sizing.
- The leverage warning in plain numbers (my arithmetic): $10,000 buys 100 shares, or about 3.9 of the 12-month $80 calls (about 330 share-equivalents), or about 27 of the $135 calls. Spending the same dollars on calls as you would on stock multiplies the exposure and makes a total loss possible; McMillan's "small portion in the call, rest in T-bills" is the guard against that.
- A long horizon does not remove the need to size: the section 2 tables show a 20% fall in the stock takes 86-100% of the premium of even the deep ITM calls at expiry.
- General-education phrasing for the report: only use money you can afford to lose entirely; decide the maximum loss per trade before entering; spread purchases over time or across positions rather than one large LEAP; these are conventions, not guarantees.

### Gaps
- No OIC, Cboe, Fidelity or Schwab page giving a specific percent-of-account rule for long options was retrieved. The percentages come from broker blogs and course pages and vary from 1% to 5%; treat them as conventions.
- No evidence-based (tested) sizing rule for LEAPs was found. Kelly-style sizing for options was not researched.
