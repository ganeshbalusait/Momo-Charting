# How Professional Trading Firms Use AI/ML (2023-2026), and What Transfers from Robotics / Self-Driving

Research date: 2026-11-04. Firm statements are marked **[FIRM CLAIM]**. Peer-reviewed or regulator facts are marked **[EVIDENCE]**. Secondary or aggregator reports are marked **[SECONDARY]**.

## 1. How quant and market-making firms structure ML, and where LLMs/agents are (and are not) used

### Takeaway
Firms that publish anything describe ML in two places: (a) price forecasting and market making at very large scale (XTX, Jane Street), and (b) agentic LLMs as *research assistants* that generate hypotheses, write code and run tests (Man Group AlphaGPT, Bridgewater AIA Labs). No primary source found says an LLM places orders directly. Every published LLM workflow sends its output through the same human review committee as human research. Citadel's founder says publicly that GenAI helps productivity but not alpha.

### Cited Findings
- **Man Group AlphaGPT [FIRM CLAIM]:** an LLM-based "agentic AI research workflow" described as "a digital three-person research team". An *idea* agent generates hypotheses, an *implementer* agent writes Python against Man's research tools and databases, and an *evaluator* agent runs significance tests, risk checks and economic-rationale checks. A workflow orchestrator coordinates them, with safeguards against hallucination — [Man Group, "What AI Can (and Can't Yet) Do for Alpha"](https://www.man.com/insights/what-ai-can-do-for-alpha)
- Man Group's controls on AlphaGPT **[FIRM CLAIM]**:
  - AI-generated signals must pass the same thresholds as human research.
  - The Investment Committee reviews the hypothesis, economic rationale and results.
  - Technology teams do code review, unit and integration tests, scenario analysis and implementation-risk assessment.
  - A log records every step from hypothesis to implementation.
  - Humans "make final decisions". The authors say they "can't leave it unsupervised just yet".
  - Source: [Man Group](https://www.man.com/insights/what-ai-can-do-for-alpha)
- Man names multiple testing and p-hacking as the main statistical risk: fast testing of many variations produces "lucky" results that fail live. It also names implementation drift (the AI codes something different from the stated idea). The article gives **no performance numbers, backtest statistics or sample sizes**, and does not discuss data leakage. Most success so far is in systematic equity research — [Man Group](https://www.man.com/insights/what-ai-can-do-for-alpha). Hedgeweek covers it as agentic AI for quant *signal discovery* — [Hedgeweek](https://www.hedgeweek.com/man-group-deploys-agentic-ai-for-quant-signal-discovery/)
- A Chinese-language report says dozens of AlphaGPT signals passed committee review and were about to trade. I could not confirm this in English or primary sources — [cnyes](https://hao.cnyes.com/post/181974) **[SECONDARY, unverified]**
- Name confusion: the academic "Alpha-GPT: Human-AI Interactive Alpha Mining" (arXiv 2308.00016) is a separate project, not Man's system — [arXiv](https://arxiv.org/html/2308.00016v2)
- **Bridgewater AIA Labs:**
  - Bloomberg (via Fortune), July 2024: Bridgewater launched a ~$2B fund that uses machine learning for decisions, run by co-CIO Greg Jensen, using models from OpenAI, Anthropic and Perplexity — [Fortune](https://fortune.com/2024/07/01/bridgewater-2-billion-fund-machine-learning-decision-making-openai-anthropic-perplexity) **[EVIDENCE of launch; strategy details are firm claims]**
  - Reported 2025 return of 11.9% and AUM above $5B by March 2026: both appear only in secondary sources — [LongTermWiki](https://www.longtermwiki.com/wiki/bridgewater-aia-labs); [HedgeCo, Mar 2026](https://www.hedgeco.net/news/03/2026/bridgewater-dalios-principles-to-algorithmic-intelligence-the-road-to-5billion.html) **[SECONDARY, unverified]**
- **Citadel / Ken Griffin [FIRM CLAIM / opinion]:** at the JPMorgan Robin Hood conference (October 2025), Griffin said "With GenAI there are clearly ways it enhances productivity, but for uncovering alpha it just falls short" — [Hedgeweek](https://www.hedgeweek.com/generative-ai-fails-to-deliver-alpha-for-hedge-funds-says-griffin/); [Bloomberg Gov](https://news.bgov.com/new-york-brief/griffin-says-genai-fails-help-hedge-funds-beat-markets-1). He made similar earlier remarks on GenAI hype — [Fortune, Jul 2024](https://fortune.com/2024/07/02/ken-griffin-citadel-generative-ai-hype-openai-mira-murati-nvidia-jobs)
- **XTX Markets [FIRM CLAIM]:** describes itself as using ML to produce price forecasts for over 50,000 instruments.
  - February 2024: launched the ML division XTY Labs, with a research cluster of 100,000 cores, 20,000 A/V100 GPUs, 390 PB of storage and 7.5 PB of RAM — [Kyodo / XTX press release](https://kyodonewsprwire.jp/release/202402226932)
  - Later job postings say more than 25,000 GPUs and 650 PB — [Greenhouse job board](https://job-boards.greenhouse.io/xtxmarketstechnologies/jobs/6778222003)
- **Jane Street [FIRM CLAIM]:**
  - Ron Minsky's podcast *Signals and Threads* covers machine learning in noisy markets (episode with In Young Cho, March 2025) — [Signals and Threads](https://signalsandthreads.com/finding-signal-in-the-noise/)
  - Dwarkesh Patel interview (May 2026) on GPUs and trading horizons; I saw only a summary — [summary](https://songkwwwwwww.github.io/en/jane%20street/jane-street-gpus-trading-hiring/)
  - Job postings describe "tens of thousands" of GPUs — [Jane Street ML Researcher posting](https://www.janestreet.com/join-jane-street/position/8069434002)
- **Regulators' view of agents [EVIDENCE]:**
  - FINRA's 2026 Annual Regulatory Oversight Report (9 Dec 2025) added a GenAI section covering AI agents ("systems… capable of autonomously performing and completing tasks on behalf of a user").
  - Risks it names: hallucination and "unsupervised execution of regulated actions" by agents.
  - Its rules are technology-neutral, and it expects agent-specific supervision, human oversight, testing and monitoring.
  - Sources: [Debevoise](https://www.debevoise.com/insights/publications/2025/12/finras-2026-regulatory-oversight-report-continued); [Snell & Wilmer](https://swlaw.com/publication/finras-2026-oversight-report-signals-a-supervisory-reckoning-for-autonomous-ai); [Covington](https://www.covingtonblogs.com/2025/12/22/finra-highlights-trends-and-risks-in-member-firms-use-of-genai/)

### Inferences
- The pattern that holds across firms: **LLM agents sit upstream (idea, code, test, evaluation). Downstream, a human committee and conventional statistical gates stand before anything trades.** Order decisions at forecasting firms come from specialized numeric models on price and order-book data, not LLMs. This rests on what firms say publicly. No firm says an LLM decides orders, but firms also do not disclose their production systems.
- XTX and Jane Street run tens of thousands of GPUs on data from tens of thousands of instruments. That is the scale at which "Tesla-style" end-to-end learning is even attempted in markets. A retail app cannot copy the data or compute side of that.
- For a retail app, the transferable part is Man's *process*: separate agents for idea, implementation and evaluation; one fixed evaluation bar; full logging; and a human sign-off before deployment.

### Gaps
- No primary 2023-2026 source found for Renaissance, Two Sigma, Optiver or Man AHL ML architecture; these firms publish little. Nothing here should be presented about them as fact.
- No independent audited performance for AlphaGPT signals or Bridgewater AIA. The figures are firm or secondary claims.
- No primary source on how firms split ML between alpha, execution algorithms and risk. Separation is the industry's common description, but I did not source it here.

## 2. The research -> backtest -> paper/shadow -> small capital -> scale pipeline; overfitting statistics

### Takeaway
Academic evidence says backtests predict live results poorly, especially once many variants have been tried. In 888 Quantopian algorithms, the in-sample Sharpe explained only about 1-2% of the variance in out-of-sample Sharpe. Published anomalies lose about 26% of their return out-of-sample and about 58% after publication. Bailey and López de Prado's Deflated Sharpe Ratio and Probability of Backtest Overfitting (via CSCV) are the standard corrections for multiple testing.

### Cited Findings
- **Deflated Sharpe Ratio** (Bailey & López de Prado, *Journal of Portfolio Management*, 2014): corrects the Sharpe ratio for selection bias under multiple testing and for non-normal returns. The same paper gives Minimum Track Record Length and Minimum Backtest Length — [SSRN 2460551](https://papers.ssrn.com/abstract=2460551)
- **Probability of Backtest Overfitting** (Bailey, Borwein, López de Prado, Zhu, *Journal of Computational Finance*): estimates the probability that the strategy chosen in-sample underperforms out-of-sample, using combinatorially symmetric cross-validation (CSCV) — [SSRN 2326253](https://papers.ssrn.com/abstract=2326253); [eScholarship PDF](https://escholarship.org/content/qt4w1110bb/qt4w1110bb.pdf). An R implementation exists (`pbo`) — [CRAN vignette](https://archive.linux.duke.edu/cran/web/packages/pbo/vignettes/pbo.html)
- **López de Prado, "The 10 Reasons Most Machine Learning Funds Fail"** (JPM 2018). Later talks list seven pitfalls:
  - the "Sisyphus" lone-researcher paradigm (vs. an assembly line)
  - integer differentiation (losing memory to get stationarity)
  - inefficient sampling (time bars instead of information bars)
  - wrong labeling
  - weighting of non-IID samples
  - cross-validation leakage
  - backtest overfitting
  - Sources: [GARP whitepaper](https://www.garp.org/white-paper/the-10-reasons-most-machine-learning-funds-fail); [Quantpedia summary](https://quantpedia.com/why-machine-learning-funds-fail/)
- He describes the most common error as running data through ML, backtesting, and "repeat[ing] the sequence until a good-looking result appears" — [Oxford Business Law Blog](https://blogs.law.ox.ac.uk/business-law-blog/blog/2018/03/machine-learning-funds-and-investment-malpractice)
- **Live vs. backtest decay [EVIDENCE]:** Wiecki et al. (Quantopian), "All that Glitters Is Not Gold", studied 888 algorithms with at least 6 months out-of-sample:
  - The in-sample Sharpe had R² of about 0.01-0.02 for the out-of-sample Sharpe.
  - More backtests per author meant a bigger in-sample/out-of-sample gap, which points to overfitting.
  - An ML model over backtest *features* reached R² of about 0.17.
  - Sources: [CXO Advisory](https://www.cxoadvisory.com/?p=28573); [Quantpedia](https://quantpedia.com/quantopians-academic-paper-about-in-vs-out-of-sample-performance-of-trading-alg/); [paper PDF](https://c.mql5.com/forextsd/forum/208/All%20that%20Glitters%20Is%20Not%20Gold%20-%20Comparing%20Backtest%20and%20Out-of-Sample%20Performance%20on%20a%20Large%20Cohort%20o.pdf)
- **Alpha decay after publication [EVIDENCE]:** McLean & Pontiff (*Journal of Finance*, 2016) studied 97 anomalies:
  - Returns were 26% lower out-of-sample (their upper bound on data mining).
  - Returns were 58% lower post-publication, implying about 32% from investors trading on the published result.
  - Source: [summary/abstract](https://typeset.io/papers/does-academic-research-destroy-stock-return-predictability-3rinmhqstl)
- A 2023 *JFQA* replication finds about 50% post-publication decay, rising to about 72% with post-2005 data only — [Cambridge Core JFQA](https://core-prod.cambridgecore.org/core/services/aop-cambridge-core/content/view/945133D5A3ECEEAF466AEE91551FD225/S0022109022000874a.pdf/zeroing-in-on-the-expected-returns-of-anomalies.pdf). Jacobs & Müller (JFE 2020, 241 anomalies, 39 markets) find a reliable post-publication decline only in the US — [EconPapers](https://econpapers.repec.org/RePEc:eee:jfinec:v:135:y:2020:i:1:p:213-230)
- **Industry pipeline gates [FIRM CLAIM]:** at Man Group, AI-generated research goes through committee review, code review, scenario analysis and logging before it is "considered for deployment" — [Man Group](https://www.man.com/insights/what-ai-can-do-for-alpha)

### Inferences
- An agentic system that generates and tests many rule variants automatically is a **multiple-testing machine**. Without DSR/PBO-style deflation, and without counting *every* variant tried (including ones the agent discarded), it will reliably surface lucky rules. Man Group names exactly this as its main risk.
- The Quantopian R² of about 0.02 suggests a backtest Sharpe alone should not move a strategy from paper to real money. A forward (paper/shadow) record of adequate length is the gate that counts. DSR's Minimum Track Record Length gives a principled number for how long.
- Expect even a "real" edge to shrink a lot after discovery: about 26% out-of-sample and up to 50-72% once widely known. Size live expectations below the backtest.
- The paper -> small capital -> scale ladder is standard advice, but I found no primary firm document giving stage thresholds (see Gaps).

### Gaps
- No published firm data on paper-to-live conversion rates or typical capital-ramp schedules.
- No primary statistics on live decay specifically for intraday options momentum strategies.
- Walk-forward vs. CPCV comparisons: López de Prado argues CPCV is better than walk-forward, but I did not fetch a primary source on it in this pass.

## 3. Why markets differ from driving or robotics

### Takeaway
Financial RL researchers themselves name two blockers that robotics mostly does not face: **non-stationarity** (the "physics" of the market changes) and **no faithful simulator** (simulated markets do not react like real ones, so there is a sim-to-real gap). On top of that: profitable patterns decay once traded (adversarial adaptation, per McLean-Pontiff), and in-sample performance barely predicts out-of-sample performance (low signal-to-noise, per Quantopian).

### Cited Findings
- A review of RL in finance: simulator quality is critical, and a simulator far from reality causes a sim-to-real gap, so a policy that does well in simulation can fail when deployed — [arXiv 2411.12746](https://arxiv.org/pdf/2411.12746)
- RL in economics and finance review: the two main drawbacks are building a realistic simulator and the lack of stationarity in financial dynamics, which undermines strategies learned from the past — [arXiv 2003.10014](https://arxiv.org/pdf/2003.10014)
- Vyetrenko et al., "Get Real": many limit-order-book simulators fail to reproduce the stylized facts of real markets, and robust simulation may need adaptive agents — [arXiv 1912.04941](https://www.arxiv.org/pdf/1912.04941)
- A 2025 market-making RL review: all prior work trains in a simulator, sim-to-real transfer is still an open requirement, and live learning needs "safe RL" — [arXiv 2507.18680](https://arxiv.org/pdf/2507.18680)
- Lalor & Swishchuk (CME data): naive simulations overstate performance because adverse fills are often left out of backtests — [arXiv 2409.12721](https://arxiv.org/pdf/2409.12721)
- **Adversarial adaptation / alpha decay:** publication-driven decay of 32 to 58 percentage points of anomaly returns — [McLean & Pontiff](https://typeset.io/papers/does-academic-research-destroy-stock-return-predictability-3rinmhqstl)
- **Low signal-to-noise:** in-sample Sharpe R² of about 0.02 for out-of-sample — [CXO Advisory](https://www.cxoadvisory.com/?p=28573). Jane Street's own podcast framing is "finding signal in the noise" — [Signals and Threads](https://signalsandthreads.com/finding-signal-in-the-noise/)
- López de Prado: financial data violates IID assumptions (overlapping labels, non-IID weighting) and needs special labeling. Standard ML cross-validation leaks — [Quantpedia](https://quantpedia.com/why-machine-learning-funds-fail/)

### Inferences (robotics/FSD patterns: transfer or not)
| Pattern from robotics / FSD | Transfers to retail options momentum? | Why |
|---|---|---|
| Massive end-to-end imitation/RL on fleet data | **No** | No stationary "physics". One retail account yields few, noisy, delayed labels (P&L). Edges decay once exploited. |
| Train in a simulator, then deploy (sim-to-real) | **Mostly no** | No simulator reacts like real markets. Backtests omit adverse fills (Lalor & Swishchuk). |
| Shadow mode (model runs alongside, no control) | **Yes, strongly** | This is the paper/forward test, the only gate the evidence supports (Quantopian R²). |
| Perception stack vs. planning vs. hard-coded safety layer | **Yes** | It maps onto signal model -> sizing/execution -> pre-trade risk checks that cannot be overridden (Rule 15c3-5 style). |
| Disengagement metrics, fleet telemetry, incident logs | **Yes** | It maps onto logging every hypothesis and trade (Man Group's step log) and to live-vs-backtest drift monitoring. |
| Continuous online learning from live outcomes | **Risky** | Labels are noisy and delayed. Retraining on recent P&L chases noise and adds multiple-testing risk. |
| "More data and compute solves it" scaling laws | **Weak** | Signal-to-noise is low and the data is non-stationary. Firms with 20k+ GPUs (XTX) still describe the work as forecasting, not autonomy. |
| Human driver takeover / ODD limits | **Yes** | It maps onto a human-in-the-loop approval, a market-hours/regime envelope, and kill switches (FINRA expectations). |

### Gaps
- No quantitative study found comparing the "effective sample size" of trading labels with that of driving datasets. That is an inference, not a sourced fact.
- No primary source found quantifying how fast intraday options momentum edges decay after adoption.

## 4. Execution realism for options; evidence on retail options traders' returns

### Takeaway
Retail options traders lose money on average. Bryzgalova, Pavlova and Sikorskaya (JF 2023) show retail is over 60% of options volume and favors cheap weekly options with an **average bid-ask spread of 12.6%**. de Silva, Smith and So find retail losses of about 5-9% around earnings, rising to 10-14% for high-expected-volatility events. Limit orders face the "winner's curse": they fill more often when the price moves against you, and backtests that skip this overstate results.

### Cited Findings
- **Bryzgalova, Pavlova & Sikorskaya**, "Retail Trading in Options and the Rise of the Big Three Wholesalers", *Journal of Finance* 78(6), 3465-3514 (Dec 2023):
  - Retail recently exceeded 60% of total options volume.
  - About 90% of payment for order flow comes from three wholesalers.
  - Retail favors cheaper, weekly options with an average bid-ask spread of 12.6%, and loses money on average.
  - Source: [IDEAS/RePEc abstract](https://ideas.repec.org/p/cpr/ceprdp/17688.html)
- Media coverage of that paper: more than $1B lost Nov 2019-Jun 2021 (10-day holding assumption), about $5B including trading costs. Pavlova blames repeated spread-paying from frequent trading and lottery-like contracts — [DailyWealth](https://dailywealth.com/articles/how-traders-got-stuck-with-a-5-billion-tuition-bill); [EliteTrader repost of Bloomberg](https://www.elitetrader.com/et/threads/mom-and-pop-investors-took-a-billion-dollar-bath-trading-options-during-the-pandemic.366697) **[SECONDARY]**. Note: one later write-up cites 48% retail share, not 60%+, probably a different measure or version — [lungo](https://lungo.pages.dev/article/b3bbcb35-f386-4a11-8443-dae72e95934e)
- **de Silva, Smith & So**, "Losing is Optional: Retail Option Trading and Expected Announcement Volatility" (*Review of Finance*, 2025):
  - Retail loses about 5-9% on average around earnings, and 10-14% for high-expected-volatility announcements.
  - Retail overpays before volatile events and is slow to close afterwards as the option's value decays.
  - Market makers take the other side.
  - Sources: [Stanford GSB](https://www.gsb.stanford.edu/faculty-research/publications/losing-optional-retail-option-trading-expected-announcement); [Quantpedia summary](https://quantpedia.com/how-retail-losses-money-in-option-trading/); [Advisor Perspectives](https://www.advisorperspectives.com/articles/2023/02/19/the-wealth-destroying-behavior-of-retail-option-trading)
- **Adverse selection on limit orders ("winner's curse"):**
  - Hollifield, Miller & Sandås estimate fill probability and winner's-curse costs. Traders trade off price against fill probability and the risk of being filled just before an adverse move — [CEPR DP 2843](https://repec.cepr.org/repec/cpr/ceprdp/Dp2843.pdf)
  - Fill probability can be split into favorable-move and adverse-move components, with spread capture measured against the post-fill mid — [arXiv 1409.1442](https://arxiv.org/pdf/1409.1442)
  - Execution outcomes diverge from what order-book snapshots imply, more so in high volatility and thin liquidity. Even marketable limit orders often fail to fill immediately — [Albers et al., Quantitative Finance 2025](https://ideas.repec.org/a/taf/quantf/v25y2025i6p919-947.html)
  - Backtests usually leave out adverse fills and so overstate performance — [arXiv 2409.12721](https://arxiv.org/pdf/2409.12721)

### Inferences
- For short-dated OTM calls (the app's use case), a 12.6% average spread means a round trip at the far side costs about one full spread. A strategy scored at the mid with no spread cost overstates P&L by roughly that much per trade. This is consistent with the project's own note that the Bot scores at the mid.
- "Fill at mid" assumptions are biased upward twice: mid limits fill less often, and the fills they get skew toward cases where the option then moves against you. A realistic simulator should fill at the far side, or fill mid limits only with a probability *conditioned on the subsequent price move*.
- The two academic papers bear directly on the app's niche: cheap, short-dated retail options on momentum and event names. The base rate for this population is negative after costs.

### Gaps
- No academic study found that directly measures **fill rates of mid-price limit orders on retail options** with adverse-selection conditioning. This would need the user's own fill logs.
- No primary figure verified for exact round-trip cost by moneyness or DTE in Bryzgalova et al. beyond the 12.6% average.
- Did not fetch the full de Silva et al. paper. The 5-9% and 10-14% figures come from summaries.

## 5. Risk and governance: kill switches, limits, Knight Capital, SEC/FINRA on algorithms and AI

### Takeaway
The governance baseline is hard pre-trade controls (SEC Rule 15c3-5, the Market Access Rule), tested code deployment, and human supervision of automated or agentic systems. Knight Capital (2012) is the canonical case: a bad deployment sent over 4 million orders in 45 minutes and lost over $460M. The SEC's AI-specific predictive-analytics proposal was withdrawn in June 2025, but conflict-of-interest and supervision duties still apply. FINRA's 2026 report targets agentic AI specifically.

### Cited Findings
- **Knight Capital [EVIDENCE, SEC order]:**
  - On 1 Aug 2012, a router running discontinued code sent more than 4 million orders while trying to fill 212 small retail orders in the first 45 minutes of trading. The loss was more than $460M (press often says $440M).
  - Knight lacked adequate controls for code deployment and testing.
  - 97 automated warning emails flagged the error before the open; no one acted on them.
  - This was the SEC's first enforcement under Rule 15c3-5 (Market Access Rule, adopted 2010). Penalty $12M, plus an independent consultant.
  - Sources: [SEC press release 2013-222](https://www.sec.gov/news/press-release/2013-222); [WilmerHale](https://www.wilmerhale.com/insights/client-alerts/knight-capital-settles-rule-15c3-5-violations-with-sec-agrees-to-pay-12-million); [SEC order via Justia](https://contracts.justia.com/companies/kcg-holdings-inc-18985/contract/555902)
- **SEC predictive data analytics proposal [EVIDENCE]:** proposed July 2023 to make broker-dealers and advisers "eliminate or neutralize" conflicts from predictive technologies used with investors. Formally withdrawn with 14 other proposals in June 2025 (announced about 12 June, Federal Register notice 17 June). The SEC said it does not intend to finalize them. Law firms note that existing conflict-of-interest duties still apply — [Paul Hastings](https://www.paulhastings.com/insights/client-alerts/sec-withdraws-14-pending-rule-proposals); [Ropes & Gray](https://www.ropesgray.com/en/insights/alerts/2025/06/sec-clears-unfinished-rulemakings-from-regulatory-agenda); [WealthManagement](https://www.wealthmanagement.com/regulation-compliance/sec-withdraws-ai-and-esg-proposed-rules-from-gensler-s-tenure)
- **FINRA 2026 Oversight Report [EVIDENCE]:** flags agents executing regulated actions unsupervised, and hallucination. Expects agent-specific supervisory procedures, human oversight, output testing and monitoring, and vendor cybersecurity. Commentators tie this to Rule 3110 (Supervision), Rule 3120 (supervisory control systems) and books and records, and warn against "set-it-and-forget-it" automation — [DLA Piper](https://www.dlapiper.com/en-fr/insights/publications/2025/12/finra-flags-generative-ai-risks-and-governance-expectations); [Snell & Wilmer](https://swlaw.com/publication/finras-2026-oversight-report-signals-a-supervisory-reckoning-for-autonomous-ai); [Debevoise](https://www.debevoise.com/insights/publications/2025/12/finras-2026-regulatory-oversight-report-continued)
- **Human-in-the-loop at firms [FIRM CLAIM]:** Man Group keeps humans as final decision-makers for AI-generated signals — [Man Group](https://www.man.com/insights/what-ai-can-do-for-alpha)

### Inferences
- Lessons from Knight that apply directly to a retail auto-trader:
  1. Pre-trade hard limits outside the strategy code: maximum orders per minute, maximum contracts and notional, maximum daily loss, symbol allow-list.
  2. Deployment discipline: no dead code paths reachable by flags, staged rollout.
  3. Alerts that *halt* rather than email.
  4. A kill switch one click away.
- Rule 15c3-5 and the FINRA rules bind broker-dealers, not a personal app trading the user's own account. They are still the best reference design. The user's broker applies its own 15c3-5 checks, but those are coarse.
- An LLM agent should not hold the authority to send orders without an independent deterministic risk layer. That matches both FINRA's "unsupervised execution of regulated actions" concern and Man's practice.

### Gaps
- Did not fetch the primary SEC Rule 15c3-5 text or the FINRA Regulatory Notice 15-09 on algorithmic trading supervision. Specific control requirements are not quoted here.
- No source found on any regulator rule specific to *retail self-directed* algorithmic or AI trading tools.
- EU AI Act / MiFID II RTS 6 (algo trading kill-switch requirements) not researched in this pass.
