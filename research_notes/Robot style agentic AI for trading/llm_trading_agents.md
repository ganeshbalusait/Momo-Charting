# LLM-based and Agentic AI Trading Systems (2023-2026): Designs and Honest Evidence

Scope note: 16 tool calls (searches plus full-text fetches of TradingAgents, FINSABER and StockBench). Each result below is labelled **[BACKTEST]**, **[LIVE-SIM]** (live data, simulated money), or **[REAL MONEY]**, and leakage risk is flagged where it applies. Some Alpha Arena figures come from secondary press because nof1's own blog post did not surface in search.

## 1. Notable systems and papers: what has been built?

### Takeaway
The main academic systems are TradingAgents, FinMem, FinCon, FinAgent, AlphaAgents (BlackRock) and StockAgent. They were nearly all judged on short backtests of a few large-cap stocks. Studies that are free of training-data leakage, run longer, or use live money (FINSABER, StockBench, Agent Market Arena, Alpha Arena) find edges that are small, unstable, or absent, and LLMs rarely beat buy-and-hold reliably.

### Cited Findings
**TradingAgents (Xiao, Sun, Luo, Wang; arXiv 2412.20138, AAAI 2025 workshop oral)** [BACKTEST, HIGH leakage risk]
- The design copies a trading firm. Fundamental, sentiment, news and technical analysts write reports. Bull and bear researchers debate for a fixed number of rounds, and a facilitator picks the winning view. A trader turns that into buy/sell/hold. A risk team (risk-seeking, neutral and conservative agents) debates the trade, and a "fund manager" agent gives final approval — [arXiv HTML v4](https://arxiv.org/html/2412.20138v4)
- Models: gpt-4o-mini and gpt-4o do the "quick-thinking" work (summaries, data retrieval). o1-preview does the "deep-thinking" work for analysts, researchers and the trader — [arXiv](https://arxiv.org/html/2412.20138v4)
- Test window: 2024-01-01 to 2024-03-29 (about 3 months) on AAPL, GOOGL and AMZN — [arXiv](https://arxiv.org/html/2412.20138v4)
- Reported cumulative return / Sharpe / max drawdown. AAPL 26.62% / 8.21 / 0.91%, against buy-and-hold -5.23%. GOOGL 24.36% / 6.39 / 1.69%, against buy-and-hold 7.78%. AMZN 23.21% / 5.60 / 2.11%, against buy-and-hold 17.1% — [arXiv](https://arxiv.org/html/2412.20138v4)
- The authors say these Sharpe ratios are above their expected range (above 3 is "excellent"). They blame a period with few pullbacks and publish the numbers anyway — [arXiv](https://arxiv.org/html/2412.20138v4)
- Each decision costs about 11 LLM calls and 20+ tool calls. The authors give this cost as the reason the test was so short. No transaction costs or slippage are mentioned — [arXiv](https://arxiv.org/html/2412.20138v4)
- The paper says agents see only data up to each trading day ("eliminating look-ahead bias"). It does not discuss whether the 2024 test window falls inside the models' training data — [arXiv](https://arxiv.org/html/2412.20138v4). A Hugging Face commenter (informal, not a peer review) criticises its weak leakage safeguards — [HF papers](https://huggingface.co/papers/2412.20138)
- Follow-up work: Trading-R1 (arXiv 2509.11420) applies RL to LLM trading reasoning — [search summary of arXiv listing](https://arxiv.org/abs/2412.20138v6)

**FinMem (Stevens Institute; AAAI Spring Symposium 2024)**
- A single-agent LLM trader with layered memory and a "character" profile. It acts as an autonomous trader, not a question-answering system — [AAAI-SS](https://ojs.aaai.org/index.php/AAAI-SS/article/view/31290); [arXiv 2311.13743](https://arxiv.org/abs/2311.13743)

**FinCon (Stevens/Harvard/The Fin AI; arXiv 2407.06567, NeurIPS 2024)**
- A manager-analyst hierarchy with "conceptual verbal reinforcement," a form of self-reflection that updates beliefs from past P/L, and two layers of risk control — [arXiv](https://arxiv.org/html/2407.06567v2); [alphaXiv overview](https://www.alphaxiv.org/overview/2407.06567v3)

**FinAgent**
- A multimodal trading agent (price charts plus text) that calls external quantitative tools — [FinCon related-work](https://arxiv.org/html/2407.06567v1)

**AlphaAgents (BlackRock; arXiv 2508.11152)**
- Three GPT-4o agents (fundamental, sentiment, valuation) build equity portfolios, and a debate settles disagreements. It is portfolio construction, not live trading — [ar5iv](https://ar5iv.labs.arxiv.org/html/2508.11152)

**StockAgent**
- A multi-agent LLM simulation of how investors trade in a simulated stock market. It is a simulation, not a trading result — [search summary](https://arxiv.deeppaper.ai/papers/2510.02209v1)

**FINSABER (Li, Kim, Cucuringu, Ma; arXiv 2505.07078, accepted KDD 2026)** [BACKTEST, bias-mitigated re-test]
- Re-tests FinMem and FinAgent against buy-and-hold, simple rules (SMA/WMA crossover, Bollinger, ATR, trend following, turn-of-month), ARIMA/XGBoost and RL agents (A2C, PPO, SAC, TD3). It covers about 2004-2024 on S&P 500 stocks, including delisted names — [arXiv v5](https://arxiv.org/html/2505.07078v5)
- **Original window (Oct 2022 to Apr 2023):** FinMem's edge held mainly on TSLA (Sharpe 2.68, cumulative return 61.8%, against buy-and-hold -20.5%). On NFLX, AMZN and MSFT, buy-and-hold matched or beat the LLMs — [arXiv](https://arxiv.org/html/2505.07078v5)
- **20 years, bias-mitigated (Sharpe / annual return):**
  - Random-5 picks: buy-and-hold 0.315 / 6.7%; FinMem -0.253 / -0.1%; FinAgent 0.094 / 4.5%
  - Momentum picks: buy-and-hold 0.384 / 9.9%; FinMem 0.025 / 3.6%; FinAgent 0.104 / 13.95%, with large drawdowns — [arXiv](https://arxiv.org/html/2505.07078v5)
- Paired t-tests: buy-and-hold beats FinMem at p of about 1e-5 to 1e-6, and beats FinAgent at p of about 0.0006 to 0.012. Neither LLM had significant alpha (all p > 0.34) — [arXiv](https://arxiv.org/html/2505.07078v5)
- FinMem's commission ratio was 5-9 times FinAgent's, meaning it overtraded — [arXiv](https://arxiv.org/html/2505.07078v5)
- **By market regime (Sharpe):**
  - Buy-and-hold: 0.61 bull, 0.48 sideways, -0.28 bear
  - FinAgent: 0.12 bull, -0.38 bear
  - FinMem: -0.19 bull, -0.97 bear
  - The authors' verdict: "too cautious when risk is rewarded and too aggressive when it is penalised" — [arXiv](https://arxiv.org/html/2505.07078v5)
- The authors note that leakage from GPT-4o pretraining cannot be ruled out, but would only flatter the LLMs — [arXiv](https://arxiv.org/html/2505.07078v5)

**StockBench (arXiv 2510.02209)** [BACKTEST on a leakage-free window]
- 2025-03-03 to 2025-06-30 (82 trading days), 20 large Dow stocks, 13 LLMs, 3 seeds each. Daily inputs are prices, fundamentals and news. No costs, slippage or intraday trading — [arXiv HTML v2](https://arxiv.org/html/2510.02209v2)
- Results (return / max drawdown): equal-weight buy-and-hold 0.4% / -15.2%. Kimi-K2 1.9% / -11.8% (top composite score). Qwen3-235B-Think 2.5%. GLM-4.5 2.3%. GPT-OSS-20B -2.8% — [arXiv](https://arxiv.org/html/2510.02209v2)
- The abstract says most models "struggle to outperform" buy-and-hold, while the table shows several slightly ahead. The paper contradicts itself, and either way the margins are about 2 percentage points over 4 months — [abs](https://arxiv.org/abs/2510.02209); [HTML](https://arxiv.org/html/2510.02209v2)
- Every agent trailed the baseline in the downturn and beat it in the upturn. Results got worse as the number of stocks grew. Reasoning models were not reliably better than instruct models — [arXiv](https://arxiv.org/html/2510.02209v2)
- **Why the window had to be recent:** GPT-5 described AAPL's 2021 trend accurately without any search — [arXiv appendix A](https://arxiv.org/html/2510.02209v2)

**Agent Market Arena (arXiv 2510.11695)** [LIVE-SIM]
- A continuous live benchmark across crypto and stocks. Initialisation ran May-Jul 2025; live evaluation ran from 2025-08-01 for two months — [search summary](https://arxiv.org/pdf/2510.11695)
- Four agent designs (InvestorAgent single-agent baseline, TradeAgent, HedgeFundAgent, DeepFundAgent with memory) were each run on GPT-4o, GPT-4.1, Claude-3.5-haiku, Claude-sonnet-4 and Gemini-2.0-flash. Key finding: the agent framework drives behaviour, and "model backbones contribute less to outcome variation" — [arXiv abs](https://arxiv.org/abs/2510.11695)

**Alpha Arena (nof1.ai)** [REAL MONEY, but tiny sample]
- **Season 1** (mid-Oct to 2025-11-03): six LLMs got $10k each ($60k total) to trade crypto perpetuals autonomously on Hyperliquid — [ForkLog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/)
- Final balances: Qwen3-Max $12,231; DeepSeek $10,489; Claude Sonnet 4.5 $5,799; Gemini 2.5 Pro $5,445; Grok 4 $4,208; GPT-5 $4,126 — [ForkLog](https://forklog.com/en/ai-model-grok-4-2-triumphs-in-trading-tournament/)
  - My calculation: about -$17.7k combined (-29.5%). Four of six lost about 40-60%.
  - Other press quotes different percentages (for example GPT-5 at -62% or -72% at intermediate points), so the figures conflict across outlets — [besthub](https://www.besthub.dev/articles/why-gpt-5-lost-72-while-chinese-ai-models-gained-32-in-the-nof1-ai-alpha-arena-e458ddc00b1d)
- **nof1's own framing:** early wins may be luck. Season 1 tested default rule-following and risk management. Admitted flaws: prompt bias, small sample, no statistical rigour, short window, no memory of past actions — [besthub summary](https://www.besthub.dev/articles/can-llms-trade-crypto-profitably-inside-the-alpha-arena-competition-ace3b75ceaa5); [Datawallet](https://www.datawallet.com/crypto/alpha-arena-nof1-ai-explained)
- Founder Jay Azhang: "It's not yet feasible to directly hand over money to an LLM and let it trade on its own" — [search result summary, ForkLog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/)
- Behaviour: Grok 4, GPT-5 and Gemini leaned short, and Claude rarely shorted. Holding periods varied widely. One analysis ties GPT-5's losses to trading frequency and weak risk control — [besthub](https://www.besthub.dev/articles/why-gpt-5-lost-72-while-chinese-ai-models-gained-32-in-the-nof1-ai-alpha-arena-e458ddc00b1d)
- **Season 1.5** (late Nov to early Dec 2025, US stocks, about 2 weeks): four simultaneous modes (New Baseline, Monk Mode, Situational Awareness, Max Leverage). Added Kimi 2 and a "mystery model," later revealed as Grok 4.20 — [ForkLog](https://forklog.com/en/ai-model-grok-4-2-triumphs-in-trading-tournament/)
  - Grok 4.20 won, up about 12% on average and profitable in all four modes. GPT-5.1 was second and Gemini 3 third. As of Dec 8 it was the only profitable model — [ForkLog](https://forklog.com/en/ai-model-grok-4-2-triumphs-in-trading-tournament/)

**Field-level review**
- A review of 164 financial-LLM studies (2023-2025) is reported to conclude that earlier alpha gains came from survivorship and look-ahead bias. This is a second-hand characterisation that I did not verify — [arXiv 2603.26797](https://arxiv.org/pdf/2603.26797.pdf)

### Inferences
- The positive results fall into two groups. Some are short backtests inside the models' training period (TradingAgents, FinMem's original paper). The others are tiny-sample live contests (Alpha Arena, about 2 weeks and n=6). Every longer, broader or leakage-free test pulls the edge toward zero.
- FINSABER, StockBench and Alpha Arena S1 agree on regime fragility: LLM agents do badly in downturns or keep missing upside.
- Agent Market Arena suggests the wrapper design (prompting, risk rules, framework) matters more than which LLM sits inside. That favours spending engineering effort on deterministic structure rather than chasing the newest model.

### Gaps
- I did not find Alpha-GPT details (it did not surface), and I did not fetch the full papers for FinMem, FinCon or AlphaAgents performance numbers.
- I did not fetch nof1's official Season 1 or 1.5 blog posts. Fee and overtrading totals for Alpha Arena are unverified.
- I found no study of LLM agents trading **options**, and none on intraday momentum specifically. Every system above trades daily equities or crypto perpetuals.
- I found no practitioner blog with audited real-money P/L from a retail LLM agent over months.

## 2. Common architecture patterns: which parts are deterministic code and which are LLM?

### Takeaway
The dominant pattern is a simulated firm: analyst roles feed a bull/bear debate, then a trader, then a risk-team debate, then a manager who approves, plus memory and reflection. In the published systems the LLM makes the actual trading decision. Data fetching, indicator maths and order execution are tools or plain code. The live evidence shows the LLM-decision parts are where things break: arithmetic, output format, sizing and risk control.

### Cited Findings
- **TradingAgents' flow:** LLM agents produce analysis, debate content, the trade decision, risk adjustments and final approval. Tools fetch data and compute indicators. The paper does not label which parts are deterministic — [arXiv](https://arxiv.org/html/2412.20138v4)
- **Two-tier model use** (cheap fast model for summaries, reasoning model for decisions) as a cost control — [arXiv](https://arxiv.org/html/2412.20138v4)
- **Memory and reflection:** FinMem uses layered memory with a persona — [AAAI-SS](https://ojs.aaai.org/index.php/AAAI-SS/article/view/31290). FinCon uses verbal reinforcement (lessons learned from P/L) plus two layers of risk control — [alphaXiv](https://www.alphaxiv.org/overview/2407.06567v3). DeepFundAgent in Agent Market Arena is memory-based — [arXiv](https://arxiv.org/abs/2510.11695)
- **Debate to settle disagreements** (BlackRock AlphaAgents, TradingAgents' bull/bear and risk debates) — [ar5iv](https://ar5iv.labs.arxiv.org/html/2508.11152); [arXiv](https://arxiv.org/html/2412.20138v4)
- **Failure modes when the LLM decides:**
  - Agents miscalculate share quantities (arithmetic errors) and break the required JSON format (schema errors)
  - Thinking models make fewer arithmetic errors but more schema errors
  - Source: [StockBench](https://arxiv.org/html/2510.02209v2)
- **Missing pieces in Alpha Arena S1** that nof1 named: no memory of past actions, short context, no position scaling. Season 2 was planned to add web search, more data and multi-step reasoning — [Datawallet](https://www.datawallet.com/crypto/alpha-arena-nof1-ai-explained); [search summary](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/)
- **Cost and latency:** about 11 LLM calls plus 20+ tool calls per decision per ticker per day in TradingAgents — [arXiv](https://arxiv.org/html/2412.20138v4)

### Inferences
- For an app that already has a deterministic scanner and BOT, the evidence argues for this split:
  - **Code** owns signal generation, sizing, arithmetic, order construction, risk limits and exits.
  - **LLMs** do what they measurably add: reading text (news and catalysts; StockBench shows news and fundamentals inputs raise returns), structured critique, and nightly review or reflection.
  - Debate and reflection fit offline or nightly review better than tick-time decisions, because of the cost and latency above.
- Any LLM output that drives an action should be checked against a strict schema and have its numbers recomputed in code, because schema and arithmetic errors are documented failure modes.

### Gaps
- I found no published ablation that isolates "LLM as veto/filter on a deterministic signal" against "LLM as primary decider." That is the configuration most relevant to this app.
- I found no latency figures in seconds per decision.

## 3. Measured results: live vs backtest, data leakage, cost, reliability

### Takeaway
Backtests of LLM traders are badly exposed to look-ahead bias: frontier models remember market history, so any test window before the model's training cutoff is suspect. The only clean evaluations are forward-only (post-cutoff windows or live), and those show thin or negative edges with high variance.

### Cited Findings
- **Models remember markets:** GPT-5 accurately described AAPL's 2021 price trend without search, which is why StockBench used a post-cutoff 2025 window — [StockBench](https://arxiv.org/html/2510.02209v2)
- **Leakage can be measured:**
  - Gao, Jiang & Yan's membership-inference diagnostic shows Llama-3.3-70B's recall of outcomes "collapses essentially to zero" in the year after its December 2023 cutoff. They test news-headline return prediction and earnings-call forecasts — [arXiv 2512.23847](https://arxiv.org/html/2512.23847v1)
  - ChronoBERT and ChronoGPT train only on text available at each date, as a leak-free alternative — [arXiv 2502.21206](https://arxiv.org/pdf/2502.21206v2)
- **Other mitigations discussed:** mask firm identifiers, anonymise entities, restrict to post-cutoff data. The trade-off is a short out-of-sample window and low statistical power — [search summary of 2512.23847 and related](https://arxiv.org/pdf/2512.23847)
- **Survivorship and data snooping:** a short horizon and a narrow, hand-picked universe produce survivorship, look-ahead and data-snooping bias at once — [FINSABER](https://arxiv.org/pdf/2505.07078)
- **Backtest vs bias-mitigated comparison:**
  - FinMem: TSLA Sharpe 2.68 in the original window; Sharpe -0.25 to +0.03 over 20 years with a bias-mitigated universe
  - TradingAgents: Sharpe 5.6-8.2 in a 3-month in-sample-era backtest
  - Real money (Alpha Arena S1): 4 of 6 models lost about 40-60% in about 2.5 weeks
  - Sources: [FINSABER](https://arxiv.org/html/2505.07078v5); [TradingAgents](https://arxiv.org/html/2412.20138v4); [ForkLog](https://forklog.com/en/ai-model-grok-4-2-triumphs-in-trading-tournament/)
- **Reliability:** rankings flip between regimes, results degrade with more symbols, and reasoning models are not reliably better — [StockBench](https://arxiv.org/html/2510.02209v2). Rankings across Alpha Arena seasons flipped completely (S1 leader Qwen3-Max; S1.5 leader Grok 4.20) — [ForkLog](https://forklog.com/en/ai-model-grok-4-2-triumphs-in-trading-tournament/)
- **Costs ignored:** StockBench excludes trading costs and slippage. TradingAgents does not mention them. FinMem's high commission ratio dragged it down under FINSABER's cost-inclusive test — [StockBench](https://arxiv.org/html/2510.02209v2); [FINSABER](https://arxiv.org/html/2505.07078v5)

### Inferences
- Any backtest of an LLM agent component in this app must run only on dates after the model's training cutoff. Local LLMs have known cutoffs, which helps. Otherwise the result should be treated as contaminated. Forward paper or shadow trading is the honest evaluation.
- Two weeks of live P/L across six models (Alpha Arena) cannot separate skill from luck. Months of forward records plus a significance test (as FINSABER does with t-tests) are needed before trusting an agent.
- Slippage matters even more for short-dated OTM options than for the equities in these studies, so any evaluation should price fills at bid/ask, not the mid.

### Gaps
- No source gave per-decision dollar costs for frontier-model agents, or tokens per day for a running system.
- I did not find Agent Market Arena's numeric returns (abstract only).

## 4. Reinforcement learning: where it helps (execution) and where it fails (alpha)

### Takeaway
RL has a credible, partly industrial track record for **execution**, meaning how to work an order: how much, at what price, for how long. Here the reward is well defined (implementation shortfall) and simulation is feasible. RL for **alpha**, meaning predicting direction, is dominated by backtest overfitting, and even FinRL's own authors list overfitting and low signal-to-noise as core obstacles.

### Cited Findings
- **JPMorgan LOXM (2017):** deep RL trained on "billions of historic and simulated trades" to execute equity orders, starting in Europe in Q1 2017. The bank reported its own trial results as beating benchmarks. It ran inside the bank's electronic risk framework under internal control and regulator validation. Its actions were how much to place, at what price, and for how long — [Markets Media](https://marketsmedia.com/j-p-morgan-ai-trading-global/); [Informa Connect](https://informaconnect.com/the-latest-in-loxm-and-why-we-shouldnt-be-using-single-stock-algos); [BestPractice.ai](https://bestpractice.ai/ai-use-cases/case-studies/financial-services/jpmorgan-s-new-ai-program-for-automatically-executing-equity-trades-in-real-time-out-performed-current-manual-and-automated-methods-in-trial)
  - Caveat: these are bank-reported figures with no independent audit, and I found no recent status.
- **Lineage:** RL for optimal execution goes back to Nevmyvaka et al. (2006), who used tabular Q-learning to minimise implementation shortfall — [arXiv 2511.15262](https://arxiv.org/html/2511.15262v1)
- **Recent research:**
  - A Double DQN agent in a queue-reactive limit-order-book simulator beat standard execution benchmarks (simulation only, preprint) — [arXiv 2511.15262](https://arxiv.org/pdf/2511.15262)
  - Large-scale deep RL for limit-order placement on crypto exchanges, where PPO cut shortfall against benchmarks — [FAU working paper](https://www.iwf.rw.fau.de/files/2020/04/05_2020.pdf)
- **Alpha side:**
  - Gort, Liu et al. argue many deep-RL trading profits are false positives from backtest overfitting, and propose a hypothesis test that rejects overfitted agents — [arXiv 2209.05559](https://arxiv.org/pdf/2209.05559)
  - FinRL-Meta names low signal-to-noise, survivorship bias and overfitting as the main obstacles — [arXiv 2211.03107](https://arxiv.org/abs/2211.03107v1)
  - In FINSABER's 20-year test, the RL baselines (A2C, PPO, SAC, TD3) appear alongside the LLMs, and the paper's conclusion is that buy-and-hold was strongest or near-strongest on risk-adjusted terms. I did not extract the RL agents' individual numbers — [FINSABER](https://arxiv.org/html/2505.07078v5)

### Inferences
- For this app, the most defensible RL use is narrow: limit-order pricing and timing for option entries and exits (for example, where to work between bid and ask) on paper accounts. The reward there is measurable fill quality. Using RL to pick which tickers or setups to trade is not defensible.
- Retail options lack a realistic order-book simulator, so even execution RL would have to learn from logged paper fills. A simple deterministic rule (start at mid, walk toward the ask on a timer) is the baseline any RL must beat.

### Gaps
- I found no evidence on RL execution for retail-size options orders, and no independent live audit of LOXM.
- I found no direct quantitative FinRL out-of-sample failure study for equities (only the crypto overfitting paper and FinRL-Meta's acknowledgement).

## 5. Best practices for safe agentic trading: guardrails, evaluation, shadow trading, kill switches, audit logs

### Takeaway
Regulators now name AI agents explicitly. FINRA's 2026 report asks for human-in-the-loop points, action logging, constrained permissions and guardrails. The research record adds three more lessons: evaluate only forward or after the training cutoff, include costs, and test significance before trusting P/L.

### Cited Findings
- **FINRA 2026 Annual Regulatory Oversight Report (released 2025-12-09):**
  - First explicit AI-agent guidance. It defines agents as systems "capable of autonomously performing and completing tasks on behalf of a user" — [Debevoise](https://www.debevoise.com/insights/publications/2025/12/finras-2026-regulatory-oversight-report-continued)
  - Recommendations: monitor agent system access and data handling, define where human-in-the-loop review is required, track and log agent actions and decisions, and build guardrails that constrain agent behaviour — [Debevoise](https://www.debevoise.com/insights/publications/2025/12/finras-2026-regulatory-oversight-report-continued); [Mayer Brown PDF](https://www.mayerbrown.com/-/media/files/perspectives-events/publications/2025/12/finra-2026-regulatory-oversight-report.pdf%3Frev=-1)
  - Named risks: agents acting without a human in the loop, acting beyond the user's authority, permission and access scope problems, and misuse of sensitive data — [Pivot Point Security](https://www.pivotpointsecurity.com/latest-finra-report-puts-brokers-on-notice-about-ai-governance/)
  - No new rules: existing technology-neutral rules apply — [Sidley](https://datamatters.sidley.com/2025/12/16/finra-issues-2026-regulatory-oversight-report/)
- **Run inside an existing risk framework:** LOXM operated inside JPMorgan's electronic risk framework with control-group oversight — [Markets Media](https://marketsmedia.com/j-p-morgan-ai-trading-global/)
- **Evaluation-harness practice from the literature:**
  - Post-cutoff data windows — [StockBench](https://arxiv.org/html/2510.02209v2)
  - Survivorship-free universes that include delisted names, 20-year horizons, paired t-tests and alpha significance — [FINSABER](https://arxiv.org/html/2505.07078v5)
  - A test that rejects overfitted agents — [arXiv 2209.05559](https://arxiv.org/pdf/2209.05559)
  - Leakage diagnostics — [arXiv 2512.23847](https://arxiv.org/html/2512.23847v1)
  - Multiple seeds per agent — [StockBench](https://arxiv.org/html/2510.02209v2)
  - Continuous live benchmarks rather than one-off backtests — [Agent Market Arena](https://arxiv.org/abs/2510.11695)
- **Practitioner view:** nof1's founder says LLMs cannot yet be handed money to trade alone, and nof1 framed early wins as possibly luck — [ForkLog](https://forklog.com/en/four-out-of-six-ai-models-suffer-losses-in-trading-tournament/); [besthub](https://www.besthub.dev/articles/can-llms-trade-crypto-profitably-inside-the-alpha-arena-competition-ace3b75ceaa5)

### Inferences
Suggested guardrail stack for this app, my synthesis from the above:
1. LLM agents only propose, veto or annotate. Deterministic code places orders and enforces hard limits (maximum premium per trade, maximum daily loss, maximum open positions, no-trade windows).
2. Every agent output is validated against a strict schema, and its numbers are recomputed in code.
3. Shadow mode first: log what the agent would have done next to the deterministic BOT, then Alpaca paper. Promote only after a pre-registered number of forward trades and a significance test against the BOT baseline, with fills priced at bid/ask.
4. Use a kill switch at two levels: an automatic trip on daily-loss or error-rate thresholds, plus a manual switch.
5. Keep an append-only audit log of inputs, prompt version, model, output and final action (FINRA's "track and log").
6. Watch for regime fragility (worst in downturns) and add regime-aware throttles.
7. Never treat a backtest that runs before the model's training cutoff as evidence.

### Gaps
- I did not open the primary FINRA PDF, so the wording comes from law-firm summaries.
- I found no source for specific kill-switch designs (for example SEC Rule 15c3-5 market-access controls, or the Knight Capital 2012 post-mortem). Those were not researched in this pass and need a separate lookup if they are wanted.
- I found no published audit-log schema for LLM trading agents.
