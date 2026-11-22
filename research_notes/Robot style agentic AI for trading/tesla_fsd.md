# Tesla Full Self-Driving (FSD) as an AI system, end to end (as of Nov 2026)

Notes written 2026-11-04. Labels used throughout:
- **[Tesla claim]**: said by Tesla or Musk and not independently checked.
- **[Independent]**: regulators, trackers, or reporters with their own data.
- **[Older]**: from 2019-2022 and possibly outdated; Tesla's stack has since been rebuilt.
- **[Background]**: widely reported context from before this session that I did not re-check against a source here. Treat it as unverified.

Source quality caveat: much of the 2026 detail comes from enthusiast sites (Not a Tesla App, Teslarati, Basenor, Teslamagz) and translated coverage of talks. I found no official transcripts. Primary sources I could not open this session include Tesla shareholder decks, the NHTSA ODI (Office of Defects Investigation) files and the Standing General Order (SGO) crash database.

## 1. Architecture evolution: HydraNet / occupancy / planner, then v12+ "end-to-end"

### Takeaway
Before 2024, FSD was a modular pipeline: neural-network perception (HydraNet multi-task heads, then an occupancy network) fed a planner and controller written largely in C++ (about 300k lines). From v12 (employee release Nov 2023), "end-to-end" means one neural network trained on human driving video. Camera pixels, map/route and kinematics go in; the driving trajectory and controls come out. Most of the hand-written planning code was replaced by learned behaviour. By v14 (Oct 2025 onward) the model is larger and has a reinforcement-learning (RL) stage after imitation training, and from v14.3 one model serves FSD, Summon and Robotaxi.

### Cited Findings
- **[Older] Occupancy network (AI Day 2022):**
  - Uses all eight cameras (12-bit images) to build one unified 3D volumetric occupancy space.
  - Tesla had three in-house supercomputers with 14,000 GPUs for training and auto-labelling.
  - Source: [Analytics India Mag, "The brilliant language of lanes"](https://analyticsindiamag.com/ai-trends/the-brilliant-language-of-lanes)
- **[Older] Code size before v12:** Karpathy reportedly said at AI Day 2022 that the hand-written planning/control codebase had grown to about 300,000 lines of C++. A research report attributes a similar Musk quote about v11. Both are secondary sources; I found no primary transcript. Sources: [Analytics India Mag](https://analyticsindiamag.com/the-brilliant-language-of-lanes); [kvynlim FSD production report](https://kvynlim.github.io/industry-research/80-industry-intel/companies/tesla/fsd-production)
- **What replaced the C++ (v12):** Several secondary sources say v12 replaced the rule-based planner with an end-to-end network. A claim that only "2,000–3,000 lines" remain is unsourced, and other sources mark their line counts as estimates. Source: [kvynlim FSD production report](https://kvynlim.github.io/industry-research/80-industry-intel/companies/tesla/fsd-production)
- **v12 rollout timing:**
  - Employees first in Nov 2023, which Musk confirmed on X.
  - v12.1 went to more than 15,000 employee-owned cars ("Wave1") in Dec 2023.
  - Sources: [Not a Tesla App](https://www.notateslaapp.com/news/1713/tesla-fsd-v12-rolls-out-to-employees-with-update-2023-38-10); [ev-a2z](https://ev-a2z.com/news/tesla-deploys-fsd-beta-v12-1-to-15000-employee-evs)
- **v13 vs v14:** One commentary calls v13 the version that "finally meant a single model from camera pixels to steering commands" and v14 "the version that ships the homework." A tracker lists v14 (Oct 2025) as a roughly 10x larger network. Both are secondary sources. Source: [autonomy-news "FSD v14: the quiet rewrite"](https://autonomy-news.pages.dev/article/fsd-v14-the-quiet-rewrite/)
- **v14.x changes (release notes, as reported):**
  - v14.2.2 upgraded the vision encoder with higher-resolution features (emergency vehicles, obstacles, human gestures).
  - v14.3.2 (Apr 2026): "Actually Smart Summon, FSD, and Robotaxi now share a unified neural network model."
  - v14.3.7 "upgraded the Reinforcement Learning (RL) stage of training."
  - HW3 cars get a reduced "FSD 14 Lite."
  - Sources: [Not a Tesla App 2026.2.9.8 notes](https://www.notateslaapp.com/de/software-updates/version/2026.2.9.8/versionshinweise); [TeslaNorth v14.3](https://teslanorth.com/2026/04/07/tesla-fsd-14-3-launches-with-2026-2-9-6-update-release-notes); [Not a Tesla App 2026.20.6.11](https://www.notateslaapp.com/cs/software-updates/version/2026.20.6.11/release-notes)
- **[Tesla claim] v15:** On the Q1 2026 call Musk said v15 will run on HW4 and is targeted for end of 2026 or early 2027. Tesla wants v15 trained and validated before scaling Unsupervised FSD and Robotaxi. Source: [Not a Tesla App](https://www.notateslaapp.com/news/4035/tesla-confirms-fsd-v15-will-run-on-hw4-shares-release-date)
- **[Tesla claim] What "end-to-end" means concretely (Ashok Elluswamy, ICCV 2025 WDFM-AD workshop, Oct 2025):**
  - Inputs are video, maps/route and kinematic data. Elluswamy calls a single network "the only scalable path."
  - Rationale: "mini-trolley problems" (drive through a large puddle, or briefly enter a clear oncoming lane?) and "soft intent" (waiting for chickens, driving around geese) are hard to hard-code. He says they are "best communicated in an end-to-end latent fashion" rather than through a fixed perception-to-planning interface.
  - Source: [Humanoids Daily, 2025-10-24](https://www.humanoidsdaily.com/news/tesla-ai-chief-details-unified-world-simulator-for-fsd-and-optimus)
- **[Tesla claim] Scale of the input (same talk, translated write-up):**
  - Input context is about 2 billion tokens: 7 cameras × 36 FPS × 5 MP × 30 s history ÷ (5×5-pixel patches), plus maps and kinematics.
  - The talk lists this "curse of dimensionality" as one of three core challenges. The others are interpretability/safety and evaluation.
  - The model can emit interpretable intermediate outputs, which can serve as "reasoning tokens" when needed.
  - Source: [captaincaptain Notion write-up of ICCV25 talk](https://captaincaptain.notion.site/A-Peek-into-Tesla-s-Autonomous-Future-Core-Tech-Revealed-by-VP-Ashok-Elluswamy-at-ICCV25-WDFM-AD-2d7b1f9d11a08092aaa3c6c31589ad72); a podcast summary also mentions Gaussian-splatting debugging ([podwise](https://podwise.ai/dashboard/episodes/7101406))

### Inferences
- "End-to-end" does not mean the network has no internal structure. Tesla still pulls out intermediate outputs (3D scene/occupancy, Gaussian-splat reconstructions, reasoning tokens) for debugging and for the on-screen display. What changed is that gradients flow from the driving output back through perception. The intermediate outputs are side outputs, not hand-defined interfaces the planner must use.
- [Background] AI Day 2021 described HydraNet as a shared backbone with many task heads, plus a multi-camera fusion step into bird's-eye-view (BEV) space. Planning was a search/optimisation in C++. I did not re-fetch the AI Day 2021 transcript this session.
- Order of the training recipe, as reported: imitation learning on curated human driving first, then an RL stage (v14.3.7 notes) using the learned world simulator for closed-loop reward. This matches the LLM recipe of pretraining followed by RL fine-tuning.

### Gaps
- No primary Tesla transcript or slides for the 300k-line figure or the post-v12 code size.
- No official parameter count or model size for v12, v13 or v14. The "10x" figure is secondary.
- The exact output of the network (trajectory waypoints vs direct steering/acceleration) is not documented in sources I found.

## 2. The data engine: triggers, campaigns, auto-labelling, curation, shadow mode

### Takeaway
Tesla's data engine is a closed loop:
1. Deploy a model.
2. Run lightweight on-car "triggers" or classifiers that flag interesting or failing moments.
3. Upload only those clips.
4. Auto-label them offline with large slow networks, with human cleanup.
5. Add the hard cases to the training and test sets.
6. Retrain and redeploy.

Shadow (ghost) mode runs a candidate network silently next to the human driver or the active model, so its predictions can be scored against what actually happened. Disagreements and wrong predictions become new training examples and regression "unit tests."

### Cited Findings
- **[Older] Triggers and dataset (Karpathy, CVPR 2021):**
  - 221 hand-written triggers ran on the fleet.
  - The dataset was about 1.5 PB: roughly 1 million 10-second clips and about 6 billion labelled objects.
  - Labels came first from large offline networks, then human cleanup.
  - The loop was: deploy in ghost mode, observe predictions, tune triggers, turn wrong predictions into unit tests, add similar examples, retrain.
  - The dataset for that release went through the loop seven times.
  - Source: [Dynamically Typed summary of Karpathy CVPR 2021](https://dynamically-typed.netlify.app/stories/2021/karpathy-autopilot-cvpr)
- **[Older] Shadow mode definitions conflict:**
  - Tesla's Q2 2019 letter described comparing the software's outputs with real driver behaviour while not controlling the car.
  - Karpathy (Autonomy Day 2019) described running an in-development network in the car that predicts events such as cut-ins without acting on them.
  - Outside analysts note that shadow mode captures only a small fraction of driving time.
  - Sources: [TMC thread quoting the Q2 2019 letter](https://teslamotorsclub.com/tmc/threads/were-hw3-gen-nns-running-passively-since-july.179647/); [NextBigFuture](https://www.nextbigfuture.com/?p=171803)
- **Patent: trigger classifier.** Tesla's patent (WO 2020/056331 / US 2021/0271259 A1 / EP4586218; Karpathy named as inventor) describes:
  - A lightweight "trigger classifier" applied to an intermediate layer of the driving network, which scores incoming sensor data.
  - Data that scores over a threshold is sent back as training data.
  - Classifiers can be selected dynamically on the vehicle, so engineers can push a new "campaign" to the fleet.
  - Sources: [Electrek on the patent](https://electrek.co/?p=127323); [Patsnap drafting analysis](https://www.patsnap.com/resources/blog/ip-blog/patent-drafting-analysis-of-teslas-autonomous-vehicle-training-data-collection-system-us-2021-0271259-a1/); [EPO EP4586218](https://data.epo.org/publication-server/rest/v1.2/patents/EP4586218NWA2/document.html)
- **Patent: offline auto-labelling.** "Generating ground truth for machine learning from time series elements" (US20200250473A1 / US12223428B2) labels a moment using the frames that come after it. In hindsight you know what the object turned out to be and where it went. Sources: [Google Patents US12223428B2](https://patents.google.com/patent/US12223428); [Patsnap Eureka](https://eureka.patsnap.com/patent-US20200250473A1)
- **[Tesla claim] Fleet data volume now:** Elluswamy (Oct 2025) described the fleet as able to provide "500 years of driving data every single day" and called it a "Niagara Falls of data." Sources: [futunn](https://news.futunn.com/en/post/63836230/tesla-s-world-simulator-arrives-1-day-of-learning-equals); [Humanoids Daily](https://www.humanoidsdaily.com/news/tesla-ai-chief-details-unified-world-simulator-for-fsd-and-optimus)
- **[Tesla claim] Cumulative supervised miles:**
  - About 10 billion by May 4, 2026.
  - "Approaching 12 billion" on the Q2 2026 call, with 1.48 million active subscriptions.
  - 1.74 million subscriptions at Q3 2026 (Oct 21, 2026).
  - Sources: [Not a Tesla App Q2 2026 call](https://notateslaapp.com/news/4481/summary-of-teslas-2026-q2-earnings-call-cybercab-fsd-ai4-and-more); [TeslaNorth Q3 2026](https://teslanorth.com/2026/10/21/tesla-q3-2026-earnings/); [Basenor 10B](https://www.basenor.com/blogs/news/tesla-fsd-hits-10-billion-miles-what-the-numbers-really-mean)

### Inferences
- With end-to-end training, the label becomes **what the human actually did next**. Fleet video plus the driver's steering and pedal inputs is self-labelling demonstration data, so per-object hand labelling matters much less than in 2021.
- An intervention (the driver takes over) is the most valuable signal. It marks exactly where the model's behaviour was not acceptable to a human, and the following seconds show the correct behaviour.
- For a trading app, the analogues are:
  - Triggers become cheap on-line detectors of "interesting" market moments (model-vs-trader disagreement, large error between expected and actual move).
  - Campaigns become targeted queries pushed to collect a specific scenario type.
  - Hindsight auto-labelling becomes labelling each signal with what price actually did over the next N bars.
  - Shadow mode becomes running a new strategy version silently alongside the live one and logging every decision difference.

### Gaps
- Current (2024-2026) trigger counts, upload volumes and curation rules are not public. Everything concrete on triggers is from 2019-2021.
- No public detail on how Tesla weights interventions vs ordinary driving in v12+ training, or on how it filters out bad human driving (rolling stops, speeding) from the imitation data. The 2023 recall over rolling stops and traffic-law behaviour suggests this is a real issue (see section 5).

## 3. Safety and validation before release

### Takeaway
Tesla's validation has five parts:
1. Offline open-loop and closed-loop evaluation, now largely in a learned "neural world simulator."
2. Replaying historical failure clips as regression tests.
3. Staged rollouts: internal ADAS testers, then employees, then early-access customers, then wide release.
4. Fleet metrics: miles per intervention, crashes per mile.
5. For Robotaxi, geofenced deployment with safety monitors who are removed gradually.

The only independent quality data comes from community trackers and NHTSA crash reports. Both are noisy, and they disagree with Tesla's framing.

### Cited Findings
- **[Tesla claim] Neural world simulator (ICCV 2025):**
  - Trained on fleet data.
  - Generates multi-camera video (reportedly 8 camera feeds) in response to the policy's actions.
  - Used to replay historical clips and let the new model "diverge" to show what it would have done differently.
  - Runs closed-loop evaluation and injects synthetic adversarial or corner cases.
  - Supports large-scale RL toward "superhuman performance."
  - Elluswamy noted that "loss on open-loop predictions might not correlate to great performance in the real-world."
  - Sources: [Humanoids Daily](https://www.humanoidsdaily.com/news/tesla-ai-chief-details-unified-world-simulator-for-fsd-and-optimus); [itiger](https://www.itiger.com/news/1146546403)
- **[Older] Regression tests:** In the 2021 loop, wrong predictions were turned into unit tests before retraining. Source: [Dynamically Typed](https://dynamically-typed.netlify.app/stories/2021/karpathy-autopilot-cvpr)
- **Staged rollout:**
  - Versions go to dedicated internal ADAS testers, then employees, then early-access customers.
  - Wide release follows only after those groups sign off and major bugs are fixed.
  - Example: v12.4.3 went to more than 500 owners first, after the prior wide release of v12.3.6.
  - Source: [Not a Tesla App v12.4.3](https://www.notateslaapp.com/news/2118/tesla-releases-fsd-v1243-to-employees)
- **[Independent] FSD Community Tracker (crowd-sourced, self-reported):**
  - Piper Sandler cited a jump in miles to critical disengagement from 441 to more than 9,200 after v14.1. That figure was an outlier.
  - City miles per critical disengagement peaked at 4,109 (v14.1, Oct 2025), then fell to 809 after v14.2.
  - Other readings: about 732 miles (Electrek, early v14); about 1,454 overall and 834 city (vs 443 and 217 on v13.2).
  - Sources: [Yahoo/Piper Sandler](https://finance.yahoo.com/news/tesla-unsupervised-fsd-milestone-very-close-piper-sandler-says-185014902.html); [AOL "sharply deteriorating"](https://www.aol.com/articles/teslas-fsd-safety-metrics-sharply-043107359.html); [electrek](https://electrek.co/?p=441026); [eletric-vehicles.com](https://eletric-vehicles.com/tesla/tesla-fsd-v14-data-shows-major-improvement-in-miles-between-interventions/)
- **[Independent] Tracker metric fragility:**
  - One analysis argues the v14.1-to-v14.2 "collapse" is a metric artifact. 76% of v14 testers record no critical disengagement at all, so a few drivers dominate the ratio. It estimates about 1,950 miles per critical disengagement, and says the "% No Critical DE" figure barely moved.
  - Commenters say about 80% of critical disengagements are traffic-control or emergency-vehicle issues.
  - Sources: [Dominik Roth blog](https://blog.dominik-roth.eu/posts/fsd_14.html); [tracker site](https://sites.google.com/view/fsdbetacommunitytracker)
- **[Independent/Tesla] Robotaxi timeline:**
  - Launched in Austin in June 2025 with safety monitors.
  - Limited unsupervised rides from Jan 22, 2026.
  - Expanded to Dallas and Houston in Apr 2026.
  - Unsupervised service across the whole Austin metro announced Jun 3, 2026.
  - Tracker counts were about 20 unsupervised cars in Austin in early June, and 28 across six Texas/Florida markets in mid-August.
  - Unsupervised miles were about 380k, versus roughly 220M for Waymo.
  - Reports say chase cars with monitors follow some "unsupervised" vehicles.
  - No permit for driverless operation in California.
  - Sources: [driveteslacanada](https://driveteslacanada.ca/news/tesla-begins-public-unsupervised-robotaxi-rides-in-austin/); [Quartz](https://qz.com/tesla-robotaxi-austin-metro-expansion-060326); [SlashGear](https://www.slashgear.com/2086725/tesla-unsupervised-robotaxis-austin-texas); [Jalopnik](https://jalopnik.com/2122796/tesla-unsupervised-robotaxis-texas-cut-to-4-cars/); [S&P Global](https://autotechinsight.spglobal.com/news/5288754/tesla-launches-unsupervised-robo-taxi-operations-across-austin-metro-area-in-texas)
- **[Independent] Robotaxi crashes (NHTSA SGO):**
  - 14 crashes were reported in the first eight months of the Austin service, and 17 from July 2025 to March 2026.
  - Tesla's narratives were fully redacted as confidential business information until May 2026, when they were unredacted. Two of the crashes involved teleoperator takeover at under 10 mph.
  - Electrek-based analyses claim a crash rate 3-4x worse than humans. The comparison is contested: the human baseline counts only police-reported crashes.
  - Mid-2026 filings show zero at-fault crashes for a month.
  - Sources: [Claims Journal](https://amp.claimsjournal.com/news/national/2026/02/18/335739.htm); [TechCrunch 2026-05-15](https://www.techcrunch.com/2026/05/15/tesla-reveals-two-robotaxi-crashes-involving-teleoperators/); [electrek via reader](https://hn.jakechampion.name/reader/https://electrek.co/2026/02/17/tesla-robotaxi-adds-5-more-crashes-austin-month-4x-worse-than-humans); [eletric-vehicles.com](https://eletric-vehicles.com/tesla/tesla-robotaxi-shows-0-at-fault-crashes-in-nhtsa-latest-report/)

### Inferences
- Tesla's release gate is mainly empirical and statistical: simulator scores plus fleet intervention rates across staged cohorts. There is no published, formally verified safety case. Waymo, by contrast, publishes peer-reviewed safety analyses (Background, not verified here).
- A metric dominated by rare events (critical disengagements) gives a very noisy ratio, and per-version comparisons can swing 5-20x on small samples. This is the same problem as judging a trading strategy on a few large losses. Use survival-style measures ("% of sessions with no critical event") alongside ratios.
- Robotaxi supervision was removed gradually: safety monitor in the passenger seat, then a chase car, then remote teleoperation. That is a staged-autonomy pattern: one human fallback layer at a time, inside a geofence.

### Gaps
- Tesla does not publish its internal miles-per-intervention, its release-gate thresholds, or the size of its regression suite.
- I could not open NHTSA SGO raw data or Tesla's Vehicle Safety Report directly. Crash counts are from secondary sources.
- Robotaxi status after August 2026 (fleet size, new cities, Cybercab production) is not confirmed in sources I found.

## 4. Compute and infrastructure: Dojo, Cortex, data size, HW3/HW4/AI5

### Takeaway
- **Training:** mostly on Nvidia GPUs in Tesla's Cortex clusters at Giga Texas. About 50k H100s were live by Q4 2024, with a reported 120k H100-equivalents by Sept 2025. Cortex 2 came online in 2026.
- **Dojo** (the in-house training chip) was shut down in Aug 2025. Musk called Dojo 2 an "evolutionary dead end," and the effort converged on the AI5/AI6 chips.
- **In the car:** inference runs on HW3 (needs a reduced "Lite" model), HW4/AI4 (current main target, including for v15) and, from 2027, AI5. Musk claims AI5 is up to 40x better than AI4 on some metrics and 8x on raw compute.
- **Latency:** not published. Cameras reportedly run at 36 FPS.

### Cited Findings
- **Cortex 1:** About 50,000 H100 cluster completed in Q4 2024. AI-related capex over $5B per the CFO on the Q4 2024 call. Source: [DatacenterDynamics](https://datacenterdynamics.com/en/news/teslas-50000-gpu-cortex-supercomputer-went-live-in-q4-2024)
- **[Tesla claim] Q1 2025 compute:** About $1B AI-infrastructure capex, and training compute up more than 130% in the quarter. Source: [SDxCentral](https://www.sdxcentral.com/news/tesla-spent-1bn-on-ai-infrastructure-in-q1-elon-musk-posits-using-cars-as-distributed-compute/)
- **[Tesla claim, unverified] 120k GPUs:** A Tesla chart reportedly shows about 120,000 H100-equivalents for FSD by Sept 2025. I could not check it against the shareholder deck. Source: [aigazine](https://aigazine.com/markets/tesla-expands-ai-training-power-to-120000-gpus--v)
- **Cortex 2:** Online at Giga Texas, with a first phase of about 250 MW. Source: [Converge Digest](https://convergedigest.com/tesla-activates-cortex-2-data-center-at-gigatexas/?amp=1)
- **Dojo shut down (Aug 2025):**
  - Bloomberg reported it first. Musk said that once "all paths converged to AI6" he shut Dojo down and called Dojo 2 "an evolutionary dead end."
  - AI5 is to be made by TSMC (and Samsung per some reports); AI6 by Samsung.
  - Musk: it "doesn't make sense for Tesla to divide its resources and scale two quite different AI chip designs."
  - Sources: [TechCrunch 2025-08-11](https://techcrunch.com/2025/08/11/elon-musk-confirms-shutdown-of-tesla-dojo-an-evolutionary-dead-end); [Tom's Hardware](https://www.tomshardware.com/tech-industry/artificial-intelligence/tesla-scraps-custom-dojo-wafer-level-processor-initiative-dismantles-team-musk-to-lean-on-nvidia-and-amd-more)
- **[Tesla claim] AI5:**
  - Up to 40x better than AI4 on some metrics and 8x on raw compute. Part of the 40x comes from native softmax, which AI4 emulates in about 40 steps.
  - Taped out Apr 15, 2026; volume production expected mid-2027.
  - Musk also said AI5 has 5x the compute of two AI4 chips.
  - Reported AI4 TOPS figures conflict (about 50 vs about 500).
  - Sources: [Not a Tesla App AI5 specs](https://www.notateslaapp.com/news/3115/musk-calls-teslas-ai5-chip-a-monster-reveals-specs-for-next-gen-fsd-hardware); [doolpa tape-out](https://doolpa.com/news/tesla-ai5-chip-tape-out-musk-40x-april-2026); [Not a Tesla App 5x two AI4](https://www.notateslaapp.com/news/3979/musk-tesla-ai5-has-5x-the-compute-power-of-2-ai4-chips); [TechRadar](https://www.techradar.com/vehicle-tech/hybrid-electric-vehicles/elon-musk-reveals-teslas-powerful-new-ai5-chip-but-dont-expect-it-to-improve-full-self-driving-anytime-soon)
- **HW3 split:** v14.3 targets HW4. HW3 gets "FSD 14 Lite." Source: [Not a Tesla App 2026.20.6.11](https://www.notateslaapp.com/cs/software-updates/version/2026.20.6.11/release-notes)
- **[Tesla claim] Camera rate:** 36 FPS input, from the ICCV token arithmetic. Source: [captaincaptain Notion](https://captaincaptain.notion.site/A-Peek-into-Tesla-s-Autonomous-Future-Core-Tech-Revealed-by-VP-Ashok-Elluswamy-at-ICCV25-WDFM-AD-2d7b1f9d11a08092aaa3c6c31589ad72)
- **[Older] Data size:** about 1.5 PB, roughly 1M clips in 2021. Source: [Dynamically Typed](https://dynamically-typed.netlify.app/stories/2021/karpathy-autopilot-cvpr)

### Inferences
- The fleet's hardware sets a hard ceiling on model size. Tesla now has to ship two model sizes (full and Lite) as older hardware falls behind, so model size is tied to the oldest hardware still supported. The trading analogue is latency and compute budgets on the machine that executes trades.
- The Dojo-to-Nvidia move suggests owning the data loop matters more than owning the training chip.

### Gaps
- No published end-to-end inference latency (photon to actuation) or model FPS on HW4.
- Current training-set size (hours or PB) is not disclosed. "500 years per day" is fleet capacity, not what Tesla uploads.
- The 120k H100-equivalent figure and the $10B capex numbers come from secondary summaries.

## 5. Known weaknesses, criticisms and how they are handled

### Takeaway
Recurring failures are in the long tail and in traffic-law compliance: red lights, railroad crossings, wrong-way lane changes, emergency vehicles, and low visibility (glare, fog, dust) for a camera-only system. Regulators handle these through NHTSA investigations and software recalls. Tesla handles them through over-the-air (OTA) model updates and targeted data campaigns, as release notes show (for example, emergency-vehicle handling in v14.2.2). Imitation learning also copies human bad habits, which was the root of the 2023 recall.

### Cited Findings
- **Feb 2023 recall:** About 362,758 FSD Beta vehicles were recalled because FSD did not adequately follow traffic laws at intersections, risking crashes. The fix was an OTA update. Tesla said it did not agree with NHTSA's analysis. Source: [Fortune](https://fortune.com/2023/02/16/tesla-recalls-all-362758-vehicles-with-full-self-driving-beta-may-cause-crashes-ota-software-update-elon-musk/)
- **Low-visibility probe:**
  - Preliminary evaluation opened Oct 2024 covering about 2.4M vehicles, after 4 crashes in sun glare, fog or dust, one of them fatal.
  - Escalated to an Engineering Analysis (the step before a recall request) by Mar 2026.
  - NHTSA was concerned that the cameras did not detect these conditions and did not warn the driver when camera performance was degraded.
  - Sources: [Automotive Dive](https://automotivedive.com/news/nhtsa-opens-investigation-tesla-fsd-odi-crashes-autopilot/730353); [Insurance Journal 2026-03-20](https://www.insurancejournal.com/news/national/2026/03/20/862650.htm)
- **Traffic-violation probe:**
  - Preliminary evaluation opened Oct 7, 2025 covering about 2.88-2.9M vehicles.
  - Scope: running red lights and lane changes into opposing traffic, plus railroad crossings.
  - 58 reports, including 14 crashes and 23 injuries.
  - Several incidents occurred repeatedly at one intersection in Joppa, Maryland.
  - Sources: [Insurance Journal](https://www.insurancejournal.com/news/national/2025/10/13/843431.htm); [Fast Company](https://www.fastcompany.com/91419342/nhtsa-launching-probe-into-nearly-2-9-million-teslas-heres-why); [CarComplaints](https://www.carcomplaints.com/news/2025/tesla-fsd-traffic-violations-investigated-by-government.shtml)
- **Disengagement causes:** Tracker commenters say about 80% of critical disengagements are traffic-control or emergency-vehicle issues. Source: [Dominik Roth blog](https://blog.dominik-roth.eu/posts/fsd_14.html)
- **Transparency criticism:** Robotaxi crash narratives were redacted until May 2026. Source: [TechCrunch](https://www.techcrunch.com/2026/05/15/tesla-reveals-two-robotaxi-crashes-involving-teleoperators/)
- **Version regressions:** City miles per critical disengagement fell from 4,109 to 809 between v14.1 and v14.2. This may be a real regression or an artifact of the metric. Sources: [AOL](https://www.aol.com/articles/teslas-fsd-safety-metrics-sharply-043107359.html); [Dominik Roth](https://blog.dominik-roth.eu/posts/fsd_14.html)

### Inferences
- **Distribution shift:** one intersection producing repeated failures (Joppa) shows that a model trained on fleet averages can fail systematically on a single rare layout. Local, repeatable failures need targeted data campaigns, not just more general data.
- **Imitation inherits the teacher's bias:** learning from humans reproduced rolling stops. Demonstration data needs a rules or constraint layer, or filtering, for hard constraints (laws there, risk limits for trading). An RL stage with a reward that includes rule compliance is one way to add that.
- **Missing self-check:** NHTSA's main criticism of the visibility issue is that the system does not detect when its own inputs are degraded. The trading analogue is detecting stale or missing data feeds and refusing to act, rather than acting confidently on bad inputs.

### Gaps
- Outcomes of the Oct 2025 traffic-violation probe and of the low-visibility Engineering Analysis after Mar 2026 (any recall?) are not confirmed.
- No data on how Tesla prioritises and tracks these failure categories internally.

## 6. Which design principles generalise (to an AI options-trading app)

### Takeaway
Six patterns transfer:
1. A perception → prediction → planning → control pipeline that is later merged end-to-end only once there is enough data.
2. A closed-loop data engine where deployment produces the next round of training data.
3. Human as teacher (imitation), with interventions or overrides as the most valuable label.
4. Shadow mode for safe evaluation against reality.
5. A failures-become-regression-tests discipline plus staged rollouts with cheap on-line triggers.
6. A learned simulator for closed-loop evaluation and RL, with the caveat that good open-loop scores do not guarantee good live results.

### Cited Findings
- **Loop structure:** deploy in shadow, observe, tune triggers, wrong predictions become unit tests, add similar data, retrain, repeated 7 times per release in 2021. Source: [Dynamically Typed](https://dynamically-typed.netlify.app/stories/2021/karpathy-autopilot-cvpr)
- **Trigger classifiers on intermediate network outputs** select which data to upload. Source: [Electrek patent](https://electrek.co/?p=127323)
- **Hindsight labelling:** future frames are used to label the current one. Source: [Google Patents US12223428B2](https://patents.google.com/patent/US12223428)
- **Human values via imitation:** learning from human data aligns behaviour with human values that are hard to hard-code ("mini-trolley problems"). Source: [Humanoids Daily](https://www.humanoidsdaily.com/news/tesla-ai-chief-details-unified-world-simulator-for-fsd-and-optimus)
- **Open-loop vs closed-loop warning:** "loss on open-loop predictions might not correlate to great performance in the real-world." The answer is closed-loop evaluation in a world simulator plus replay of historical failures. Source: [Humanoids Daily](https://www.humanoidsdaily.com/news/tesla-ai-chief-details-unified-world-simulator-for-fsd-and-optimus)
- **Staged cohorts:** internal testers, then employees, then early access, then wide. Source: [Not a Tesla App](https://www.notateslaapp.com/news/2118/tesla-releases-fsd-v1243-to-employees)
- **One shared model across products** (FSD, Summon, Robotaxi), and a world simulator shared with Optimus. Sources: [Not a Tesla App 2026.2.9.8](https://www.notateslaapp.com/de/software-updates/version/2026.2.9.8/versionshinweise); [Humanoids Daily](https://www.humanoidsdaily.com/news/tesla-ai-chief-details-unified-world-simulator-for-fsd-and-optimus)

### Inferences (mapping to trading; these are my analysis, not sourced facts)

| FSD pattern | Trading-app analogue | Caution |
|---|---|---|
| Perception (cameras → 3D scene) | Market-state features: price/volume bars, option chain, open interest, IV, news tags | Data-feed health check comes first (the "camera blinded by glare" lesson) |
| Prediction (other agents' futures) | Forecast of the underlying's path or distribution over the next N bars | Score it with hindsight labels |
| Planning / control | Entry, strike/expiry choice, sizing, exits | Keep hard constraints (risk limits, max loss) as non-learned guardrails, like traffic law |
| Human as teacher | The trader's own historical trades and overrides as demonstrations | Imitation copies bad habits too; filter or reweight by outcome |
| Intervention = signal | Every time the trader overrides or ignores a bot signal, log it with context and outcome | The highest-value training rows |
| Triggers / campaigns | Cheap detectors: model-vs-trader disagreement, large forecast error, rare regime, slippage spikes | Store full context snapshots only for triggered moments |
| Shadow mode | A new strategy version paper-trades alongside the live version; diff every decision | Shadow fills are not real fills (spread and slippage), unlike a car where "would have steered" is directly comparable |
| Regression suite | Every past failure day/setup becomes a fixed replay test the new version must pass | Watch for overfitting to the suite |
| World simulator / closed-loop | A market replay or simulator for back-tests in which the strategy's actions affect fills | Markets are non-stationary and adversarial in a way roads are not; open-loop back-test wins often fail live (the same warning Elluswamy gives) |
| Staged rollout | Back-test, then shadow, then paper account, then small real size, then full size | Promote on a pre-set metric, not anecdotes |
| Metric fragility | A miles-per-critical-disengagement-style ratio is dominated by rare events; pair it with "% sessions with no critical loss" | Small samples swing 5-20x |

- **The biggest difference:** Tesla's loop works because labels (what the human did, what happened next) are plentiful and the road's physics is stable. In trading, the "teacher" is often wrong, the environment shifts with regime, and other participants adapt. The data-engine discipline (triggers, hindsight labels, regression suite, shadow mode, staged promotion) transfers more safely than the end-to-end "one big network" idea. That idea needed billions of miles to work.

### Gaps
- No public Tesla documentation of exactly how interventions are weighted in the v12+ loss or the RL reward. The "intervention as learning signal" principle is clearly documented only for the 2019-2021 era, and is otherwise inferred.
- [Background] Karpathy's "Software 2.0" essay (2017) frames neural networks as code written by data and optimisation rather than by humans. I did not re-fetch it this session.
