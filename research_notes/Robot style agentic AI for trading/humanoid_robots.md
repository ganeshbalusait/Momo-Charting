# How Humanoid Robot AI Systems Work (2025-2026): Tesla Optimus, Figure Helix, 1X NEO, Physical Intelligence, Gemini Robotics, NVIDIA GR00T

Research date: 2026-11-04. Scope: the architecture and learning loop behind general-purpose robot agents, written so the patterns can be mapped onto other domains. Labels used below: **[CLAIM]** means the company said it about itself, **[PAPER]** means a technical report or arXiv paper (written by the company, usually not peer-reviewed), and **[INDEP]** means independent journalism or an outside expert. Almost nothing here has been independently replicated. Most numbers come from the companies.

## System design: VLA models and the System 2 / System 1 split

### Takeaway
Every leading stack in 2025-2026 converged on a layered design. A big, slow vision-language model (the "System 2", about 7-10 Hz) understands the scene and the instruction and produces a compact intent: a latent vector, a subtask in text, or a step-by-step plan. A small, fast policy (the "System 1", 100-200 Hz) turns that intent plus fresh sensor data into motor commands. Figure's Helix 02 adds a third reflex layer (a "System 0" at 1 kHz) for balance. The two layers run asynchronously: the fast loop always uses the *latest* intent the slow loop has published, so it never waits for it.

### Cited Findings
- **Figure Helix (Feb 2025) [CLAIM/blog]:**
  - System 2 is a 7B-parameter open-weight VLM pretrained on internet data, running at 7-9 Hz. Its inputs are a camera image and robot state (wrist pose, finger positions).
  - System 1 is an 80M-parameter cross-attention encoder-decoder transformer running at 200 Hz. Its vision backbone was pretrained entirely in simulation.
  - Output is a 35-DoF upper-body action space: wrists, fingers, torso, and head. — [Figure: Helix](https://www.figure.ai/news/helix)
- **How Helix's two layers talk [CLAIM]:**
  - S2 compresses scene and language into "a single continuous latent vector". That vector is projected into S1's token space and concatenated with S1's visual features.
  - Training is end to end, with gradients flowing from S1 back into S2 through the latent.
  - At runtime, S2 runs asynchronously on its own embedded GPU and writes the latent to shared memory. S1 reads the latest observation and the latest latent on every 200 Hz tick. — [Figure: Helix](https://www.figure.ai/news/helix)
- **Helix completion signal [CLAIM]:** S1 also outputs a synthetic "percentage task completion" action, so the model reports when a behavior is finished. That is a built-in progress or termination signal. — [Figure: Helix](https://www.figure.ai/news/helix)
- **Helix 02 (Jan 27, 2026) [CLAIM via press]:**
  - It adds a "System 0", a learned whole-body controller running at 1 kHz that handles balance, contact forces, and whole-body coordination. S1 stays at 200 Hz.
  - S0 was trained on more than 1,000 hours of human motion data plus sim-to-real reinforcement learning. — [eWeek](https://www.eweek.com/news/figure-helix-02-humanoid-robot-autonomy/); [Interesting Engineering](https://interestingengineering.com/ai-robotics/figure-helix02-upgrades-humanoid-robot-control)
- **NVIDIA GR00T N1 (Mar 2025) [PAPER]:**
  - System 2 is the Eagle-2 VLM, which interprets vision and language. System 1 is a diffusion transformer trained with flow matching. It cross-attends to the VLM's image and text tokens while processing robot state and actions, and outputs closed-loop motor actions at 120 Hz.
  - The two modules are "tightly coupled and jointly trained end-to-end". The released GR00T-N1-2B has 2.2B parameters. — [arXiv 2503.14734](https://arxiv.org/abs/2503.14734); [ar5iv full text](https://ar5iv.labs.arxiv.org/html/2503.14734)
  - Secondary summaries say the VLM runs at about 10 Hz and that features are taken from a middle LLM layer (the 12th) rather than the last. These are unverified against the paper. — [JarvisForResearchers summary](https://davidlai0522.github.io/JarvisForResearchers/posts/2026-05-01-gr00t-n1-an-open-foundation-model-for-generalist-humanoid-ro/)
- **Physical Intelligence π0.5 (Apr 2025) [PAPER]:**
  - One model does the hierarchy *inside itself*. It first predicts a high-level subtask *in text* (for example "pick up the plate"). Then, conditioned on that subtask, a flow-matching "action expert" generates continuous chunks of actions.
  - High-level inference was found to matter: the full model beat ablations, and per one review it even beat a human "oracle" giving the subtasks. — [arXiv 2504.16054](https://arxiv.org/pdf/2504.16054); [PI blog π0.5](https://www.pi.website/blog/pi05); [Moonlight review](https://www.themoonlight.io/review/05-a-vision-language-action-model-with-open-world-generalization)
- **Gemini Robotics 1.5 (Sep 2025) [PAPER/blog]:** The split is between two separate models.
  - Gemini Robotics-ER 1.5 is a VLM "brain". It reasons about the physical world, makes multi-step plans, calls tools, and gives the VLA a natural-language instruction for each step.
  - Gemini Robotics 1.5 is the VLA. It turns vision plus that instruction into motor commands, and "thinks" in natural language before acting. — [DeepMind blog](https://deepmind.google/discover/blog/gemini-robotics-15-brings-ai-agents-into-the-physical-world/); [arXiv 2510.03342](https://arxiv.org/pdf/2510.03342)
- **Tesla Optimus [CLAIM]:** Tesla has published no architecture paper. Its stated direction is end-to-end neural networks trained mostly from camera video, in the style of its FSD driving stack (see Data). — [eWeek](https://www.eweek.com/news/tesla-optimus-robot-training/)

### Inferences
- There are three interface styles between the slow and fast layers:
  - (a) a learned latent vector (Helix, GR00T), which is fast, differentiable, and opaque;
  - (b) a text subtask (π0.5), which a person can read and an LLM can edit;
  - (c) a natural-language step from a separate planner model (Gemini ER to VLA), which is modular, lets the planner be swapped, and allows tool calls.
- The common runtime contract is a "latest-value mailbox". The slow layer publishes an intent at about 10 Hz, and the fast layer samples it on every tick without blocking. Rates differ by 10-100x between layers.
- Action chunking (emitting a short sequence of future actions at once) plus a separate fast reflex layer is how these systems hide the latency of the big model.

### Gaps
- Figure's S2 model identity and Helix 02's exact S2 rate were not confirmed from a primary Helix 02 page. Survey sources give inconsistent rates (7-9 vs 7-10 Hz).
- Tesla Optimus's internal model structure is not public.

## World models: what they are and how they evaluate or "imagine"

### Takeaway
A world model is a learned simulator: given the current frames and a proposed action sequence, it predicts what happens next, usually as video or latents and sometimes with a success score. In 2025-2026 they serve three jobs: (1) **policy evaluation**, ranking checkpoints or architectures without running real robots; (2) **synthetic data**, by generating imagined trajectories and recovering actions from them with an inverse-dynamics model; (3) **acting**, by imagining a video of the task and then converting it to motor commands (1X, 2026). They are still too slow and too unreliable on unseen objects to be the main real-time planner.

### Cited Findings
- **1X World Model (1XWM) for evaluation (2025) [PAPER/CLAIM]:**
  - It is trained on video frames plus robot observations plus action trajectories. It predicts latent encodings of future frames and also the *state value of the final frame*, which estimates whether the task succeeded.
  - It is action-controllable: it follows exact robot trajectories rather than text prompts.
  - To evaluate, candidate policies are rolled out from identical start states and their predicted success is compared. Counterfactual evaluation can start from states where a real policy previously failed. — [1X: Redwood/world model](https://www.1x.tech/discover/redwood-ai-world-model); [1X world model PDF](https://www.1x.tech/1x-world-model.pdf)
- **1XWM accuracy claims [PAPER]:**
  - Predicted success rates correlated with real-world scores.
  - Imitation-learning training loss was *not* correlated with real performance, so it was useless for picking checkpoints.
  - Statistical framing: when two policies truly differ by 15 points of success rate, a world model that is 70% accurate picks the better one 90% of the time.
  - Evaluator accuracy scales with data from autonomous policy rollouts. — [1X page](https://www.1x.tech/discover/redwood-ai-world-model); [1X PDF](https://www.1x.tech/1x-world-model.pdf)
- **1XWM stated limitations [PAPER]:**
  - It struggles with objects not seen in training (for example a plant).
  - With limited data it misrepresents object structure, such as treating an air-fryer tray and body as one part.
  - It is not yet good enough for production-level whole-body manipulation. — [1X page](https://www.1x.tech/discover/redwood-ai-world-model)
- **1XWM as policy (Jan 2026) [CLAIM via press]:**
  - It is a 14B-parameter generative video model fine-tuned for NEO. From a prompt it imagines the task, and an inverse-dynamics model converts the imagined video into motor commands.
  - Inference reportedly takes about 11 s per rollout. — [TestingCatalog](https://www.testingcatalog.com/1x-unveils-1xwm-world-model-for-neo-robot-platform); [Pulse.bot](https://www.pulse.bot/robotics/news/1x-launches-world-model-enabling-neo-robot-to-learn-tasks-by-watching-videos-82917fd3-c059-4179-a4c3-87e2bfdd1548)
- **NVIDIA Cosmos + GR00T-Dreams [PAPER/docs]:**
  - Cosmos Predict 2.5 generates synthetic robot trajectories from one image plus a language prompt. Cosmos Reason 2 (a VLM) checks whether they obey physics.
  - The DreamGen pipeline: fine-tune the video model, generate videos, extract actions with an inverse-dynamics model, train the policy (GR00T N1.5), and benchmark with DreamGenBench.
  - Evaluation scripts use VLM judges (Qwen2.5-VL, GPT-4o) for instruction-following and physics alignment. — [Cosmos cookbook GR00T-Dreams](https://nvidia-cosmos.github.io/cosmos-cookbook/recipes/end2end/gr00t-dreams/post-training.html); [SourcePulse GR00T-Dreams](https://www.sourcepulse.org/projects/11204609)
- **Cosmos-Predict2.5 [PAPER]:** It is positioned for generation, policy evaluation, and closed-loop simulation. A surgical variant (Cosmos-Surg-dVRK) runs simulated policy rollouts and scores success with a video classifier. — [arXiv 2511.00062](https://arxiv.org/pdf/2511.00062); [arXiv 2510.16240](https://arxiv.org/pdf/2510.16240v2)
- **DeepMind Genie 3 (Aug 2025) [CLAIM/INDEP]:**
  - It is a real-time interactive 3D world generator: 24 fps, 720p, several minutes per session, with about 1 minute of visual memory.
  - It was tested with the SIMA agent pursuing multi-step goals. The world model does not know the agent's goal and simply simulates consequences of its actions.
  - Critics note that it is pixel-only and not a robot-physics simulator. Access was limited to select researchers. — [TechRepublic](https://www.techrepublic.com/article/news-google-deepmind-genie-3/); [Labellerr](https://www.labellerr.com/blog/genie-3-google-deepmind-world-model/)

### Inferences
- The most transferable use is the *evaluator*. A learned simulator that scores candidate policies from identical, replayable start states, including the states where the live system failed, is a cheaper and more predictive proxy for "is checkpoint B better than A" than training loss.
- Separating the forward model (what happens if I do X) from the inverse model (which actions produce this outcome) is a recurring pattern.
- World-model outputs get a *second* model as a checker (Cosmos Reason, VLM judges) because generated futures can violate physics.

### Gaps
- No independent replication of the 1XWM evaluator correlation. 1X has not published a quantitative success rate for the January 2026 video-to-action policy.
- No public evidence that Tesla or Figure use a generative world model in their deployment loop.

## Data: teleoperation, human video, simulation, cross-embodiment, flywheel scale

### Takeaway
Data is the bottleneck, and every company builds a "data pyramid". At the base is cheap but indirect data (internet video, egocentric human video). In the middle is synthetic data (simulation and world-model "dreams"). At the top is scarce but exact data (teleoperated robot demonstrations, and the robot's own autonomous rollouts and human corrections). Cross-embodiment training ("motion transfer") lets data from one robot body help another. The own-experience layer (rollouts plus interventions) is what the 2025-2026 RL methods use to keep improving after deployment.

### Cited Findings
- **Figure Helix [CLAIM]:** It used about 500 hours of teleoperated data from multiple robots and operators, which Figure says is under 5% of the size of previous VLA datasets. Language labels were added after the fact by a VLM auto-labeler over segmented clips ("hindsight labeling"). — [Figure: Helix](https://www.figure.ai/news/helix)
- **Figure Helix 02 S0 [CLAIM]:** Trained on more than 1,000 hours of human motion data plus sim-to-real RL. — [eWeek](https://www.eweek.com/news/figure-helix-02-humanoid-robot-autonomy/)
- **GR00T N1 [PAPER]:** Trained on "a heterogeneous mixture of real-robot trajectories, human videos, and synthetically generated datasets". The paper calls this a data pyramid, and the proportions are in the full paper. — [arXiv 2503.14734](https://arxiv.org/abs/2503.14734)
- **Tesla Optimus [INDEP, from Business Insider insiders]:**
  - Around mid-2025 (after program head Milan Kovac left), Tesla shifted from motion-capture suits and teleoperation to camera-only collection. Workers wear helmet and backpack rigs with five cameras while doing chores, and the video trains Optimus, as with FSD.
  - Experts warned that video lacks force and touch information and that inferring 3D actions from 2D video introduces errors. — [eWeek](https://www.eweek.com/news/tesla-optimus-robot-training/); [Sherwood News](https://www.sherwood.news/tech/teslas-robots-get-the-same-treatment-as-its-cars-video-training/)
- **1X NEO [CLAIM/INDEP]:**
  - Home deployment is itself the data source. For tasks NEO does not know, a scheduled "1X Expert" teleoperates it remotely in VR, and those sessions train autonomy.
  - The CEO told the WSJ that teleoperation is temporary while the AI learns, and "You have to be okay with this for the product to be useful". — [Runtimewire](https://runtimewire.com/article/1x-neo-hands-25-dof-teleoperation-autonomy-gap); [Daring Fireball linking WSJ/Stern](https://daringfireball.net/linked/2025/10/31/stern-1x-neo)
- **Physical Intelligence π0.5 [PAPER]:** Co-trains on mobile-robot data from many homes, data from other robot types, web vision-language data, and high-level subtask labels. That co-training is what it credits for generalization to entirely new homes. — [arXiv 2504.16054](https://arxiv.org/pdf/2504.16054); [PI blog](https://www.pi.website/blog/pi05)
- **Gemini Robotics 1.5 Motion Transfer [PAPER/blog]:**
  - Tasks trained only on ALOHA 2 worked on the Apptronik Apollo humanoid and the bi-arm Franka, and vice versa, without per-robot specialization.
  - Per a secondary summary, for data-rich robots motion transfer mainly aligns embodiments, while for data-poor robots (Apollo) the external data gives a large boost. — [DeepMind blog](https://deepmind.google/discover/blog/gemini-robotics-15-brings-ai-agents-into-the-physical-world/); [HackerNoon summary](https://sia.hackernoon.com/how-a-two-brain-system-and-motion-transfer-are-creating-true-physical-agents)
- **Simulation [CLAIM]:** Helix's S1 vision backbone was pretrained entirely in simulation. Helix 02's balance layer was trained with sim-to-real RL. NVIDIA Isaac plus Cosmos supply synthetic trajectories for GR00T N1.5. — [Figure: Helix](https://www.figure.ai/news/helix); [Cosmos cookbook](https://nvidia-cosmos.github.io/cosmos-cookbook/recipes/end2end/gr00t-dreams/post-training.html)

### Inferences
- The flywheel is: deploy, log autonomous rollouts and human takeovers, label in hindsight (often with a VLM), retrain, and re-evaluate (ideally in a world model). Then redeploy.
- Hindsight auto-labeling of raw logs (Figure) is a cheap way to turn unlabeled operating history into instruction-conditioned training data.

### Gaps
- No reliable fleet-scale numbers were found: total teleop hours, Tesla video hours, or PI dataset size. The GR00T synthetic trajectory counts and generation time were not in the abstract and need the full paper.
- Sim-to-real success rates are not quantified publicly.

## Evaluation and safety: deciding a policy is ready, failure detection, recovery, human takeover, staged rollout

### Takeaway
Deployment readiness is judged mostly by real-world task success and throughput on held-out scenes, homes, or objects, increasingly backed by world-model evaluation. Recovery in 2025-2026 is learned from data: human takeovers on the robot's own failure states become training data, and a learned value function spots mistakes early. Rollout is staged: lab, then pilot factory line with humans nearby, then longer unattended shifts, then homes with a remote human "expert" on call. Public reliability numbers are thin, and almost all are self-reported.

### Cited Findings
- **PI π*0.6 / RECAP (Nov 2025) [PAPER/blog]:**
  - Training has three stages: demonstrations, then coaching corrections (an expert teleoperator takes over when the policy errs, which supervises exactly the states the policy reaches and so targets compounding errors), then RL from autonomous experience.
  - A learned **value function** predicts how good a state is (visualized as negative steps-to-completion) and handles credit assignment: a bad grasp is flagged early even if failure shows up later.
  - The policy trains on *all* data, good and bad, with each action tagged by its advantage. At run time it is told to produce high-advantage actions. — [PI blog π*0.6](https://www.pi.website/blog/pistar06); [arXiv 2511.14759](https://arxiv.org/pdf/2511.14759v1)
- **RECAP results [PAPER/CLAIM]:**
  - It more than doubles throughput on some of the hardest tasks and cuts failure rates by 2x or more.
  - Espresso stages reach over 90% success.
  - Claimed runs: espresso made from 5:30am to 11:30pm; 50 novel laundry items folded in a new home for hours; 59 chocolate-packaging boxes assembled in a real factory without interruption.
  - Exact values appear only in charts. PI has not said when its models will be deployed outside controlled settings. — [PI blog](https://www.pi.website/blog/pistar06); [Interesting Engineering](https://interestingengineering.com/ai-robotics/humanoid-robot-do-laundry-coffee)
- **Gemini Robotics safety [CLAIM]:** The safety stack is layered: semantic safety reasoning before acting, alignment with Gemini safety policies, and onboard low-level subsystems such as collision avoidance. DeepMind released an upgraded ASIMOV benchmark for semantic safety, and claims state-of-the-art results for ER 1.5. — [DeepMind blog](https://deepmind.google/discover/blog/gemini-robotics-15-brings-ai-agents-into-the-physical-world/)
- **Figure staged rollout [CLAIM]:**
  - At BMW Spartanburg, Figure 02 ran an 11-month pilot of 10-hour shifts, Monday to Friday. It loaded 90,000+ parts, logged 1,250+ hours of runtime, and Figure says it helped build about 30,000 cars with "minimal hardware failures".
  - Forearm PCB and cabling were cited as failure points that Figure 03 was redesigned to fix.
  - These hours predate Helix 02. — [Interesting Engineering](https://interestingengineering.com/ai-robotics/figure-humanoid-robots-retires-bmw); [Humanoid Robotics Technology](https://humanoidroboticstechnology.com/industry-news/figure-humanoid-robots-contributed-to-the-production-of-30000-cars-at-bmw/)
- **Figure Helix 02 [CLAIM]:**
  - A 4-minute dishwasher unload and reload chained 61 locomotion and manipulation actions without resets or teleoperation, using the hip and foot when the hands were full. There is no error-rate data.
  - Figure says Helix 02 robots can run an 8-hour shift without a remote operator, and a May 2026 8-hour package-sorting livestream was reported. Neither is independently verified. — [Interesting Engineering](https://interestingengineering.com/ai-robotics/figure-helix02-upgrades-humanoid-robot-control); [GadgetReview](https://www.gadgetreview.com/?p=378020)
- **1X evaluation [PAPER]:** The world model is used for checkpoint selection because imitation loss does not predict real-world success (see World models). — [1X page](https://www.1x.tech/discover/redwood-ai-world-model)
- **Human-in-the-loop at home [CLAIM]:** NEO ships with "basic autonomy". Unknown chores are done under a scheduled remote 1X Expert who views and controls the robot in VR. Observers raised privacy concerns. — [Runtimewire](https://runtimewire.com/article/1x-neo-hands-25-dof-teleoperation-autonomy-gap); [Ground News](https://ground.news/article/1x-opens-preorders-for-neo-a-humanoid-home-robot-draws-criticism-for-teleoperation_e52990)

### Inferences
- Failure detection in learned stacks has three parts: (a) a value or progress head (π*0.6's value function, Helix's "percent complete" output, 1XWM's final-state value), (b) a planner VLM checking progress (Gemini ER is described as good at "progress understanding" in the [arXiv abstract](https://arxiv.org/pdf/2510.03342)), and (c) human takeover.
- Takeover is treated as a *data asset*, not just a safety valve. Interventions on the policy's own failure states are the highest-value training data because they fix compounding error.
- Safety is layered by speed: semantic or "should I" checks happen at the slow planner, while hard limits (collision, balance) sit in the fastest, non-negotiable layer.

### Gaps
- No standardized cross-company reliability metric (mean time between interventions, success rate on held-out tasks) was found.
- No published independent audit of any deployment. Formal humanoid safety standards (for example ISO work on dynamically stable mobile robots) were not researched in this pass.

## Agentic behavior: task decomposition, memory, self-correction, tool use

### Takeaway
Agentic behavior lives mostly in the slow layer. A VLM breaks a long instruction into short, executable subtasks (in text), checks progress, and replans. Gemini Robotics-ER 1.5 adds explicit tool calls (Google Search, user functions) and "thinking" before acting. Memory is still short: action chunks, a few seconds of context, Genie 3's about 1 minute of visual memory. Self-correction comes from replanning and from learned value functions, not from a persistent episodic memory.

### Cited Findings
- **Gemini Robotics 1.5 [CLAIM/PAPER]:**
  - ER 1.5 natively calls tools such as Google Search and third-party user-defined functions to get the information a task needs (for example, local recycling rules).
  - It creates multi-step plans and gives the VLA a natural-language instruction for each step.
  - The VLA produces internal natural-language reasoning before acting ("Embodied Thinking") and splits long tasks into shorter segments.
  - ER's performance scales with more thinking tokens. — [DeepMind blog](https://deepmind.google/discover/blog/gemini-robotics-15-brings-ai-agents-into-the-physical-world/); [arXiv 2510.03342](https://arxiv.org/pdf/2510.03342); [ArXivIQ](https://arxiviq.substack.com/p/gemini-robotics-15)
- **ER 1.5 availability [CLAIM]:** ER 1.5 is open to developers via the Gemini API. The VLA is limited to select partners. — [DeepMind blog](https://deepmind.google/discover/blog/gemini-robotics-15-brings-ai-agents-into-the-physical-world/)
- **π0.5 [PAPER]:** The model predicts a text subtask, then acts on it. The text subtask is the decomposition step, and it is re-predicted as the episode progresses. — [arXiv 2504.16054](https://arxiv.org/pdf/2504.16054)
- **Helix [CLAIM]:**
  - Abstract-command grounding: "Pick up the desert item" selected a toy cactus.
  - Two robots ran the same weights to collaborate zero-shot on grocery storage, with language prompts addressing each robot. — [Figure: Helix](https://www.figure.ai/news/helix)
- **Self-correction via value [PAPER]:** π*0.6's value function detects that a state is going badly. Advantage conditioning steers the policy toward better actions, and corrections teach recovery from those states. — [PI blog](https://www.pi.website/blog/pistar06)

### Inferences
- The robotics "agent" pattern is: a planner LLM with tools and scratchpad reasoning, emitting *bounded, verifiable subtasks* to a narrow executor policy, with progress checks between subtasks. It maps directly to LLM-agent designs in software.
- Long-term memory (across episodes or days) is largely absent from the published robot stacks. Persistent learning happens offline through retraining, not through run-time memory.

### Gaps
- No primary source found on persistent episodic memory in any of these humanoid stacks.
- Tool-use evaluation numbers for ER 1.5 were not extracted.

## Honest status in 2026: demo vs deployed, reliability, criticisms

### Takeaway
As of late 2026, humanoids are in pilot deployments, not scaled deployment. Figure has the most concrete factory hours (BMW pilot). PI has the most rigorous public learning-from-experience results, but in controlled settings. 1X's home robot launched leaning heavily on teleoperation. Tesla admitted in January 2026 that no Optimus robots did useful factory work, and in October 2026 said it was only in "early production", with external deliveries around the end of 2027. Reliability numbers are self-reported and rarely include error rates.

### Cited Findings
- **Tesla Optimus [INDEP reporting earnings calls]:**
  - Q4 2025 call (Jan 2026): Musk admitted no Optimus robots are doing useful work in Tesla factories ("not in usage in our factories in a material way"). He had promised in January 2025 that several thousand would be doing useful things by the end of 2025. — [Electrek](https://electrek.co/2026/01/28/musk-admits-no-optimus-robots-are-doing-useful-work-at-tesla-after-claiming-otherwise/); [The Register](https://www.theregister.com/2026/01/29/truth_telling_man_always_tells_truth/)
  - Q1 2026: Fremont production was to start in late July or August with no volume target, on a new line with about 10,000 unique parts. — [TIKR](https://www.tikr.com/blog/tesla-q1-2026-earnings-what-the-call-actually-revealed-about-where-tsla-is-headed)
  - Q3 2026 (Oct 21): "early production on our high-volume line in Fremont". External deliveries are expected around the end of 2027, and the ramp is a "shallow slope" S-curve. — [Motley Fool / search summary](https://fool.com/investing/2026/07/28/elon-musk-says-optimus-could-be-the-biggest-produc); [Gasgoo](https://autonews.gasgoo.com/articles/ev/from-building-cars-to-building-humans-teslas-strategic-bet-and-the-changing-landscape-of-optimus-mass-production-2047304223174164481)
  - Volume targets conflict across reports (a 5,000 to 2,000 unit cut vs a line designed for about 1M per year). — [Gasgoo](https://autonews.gasgoo.com/articles/ev/from-building-cars-to-building-humans-teslas-strategic-bet-and-the-changing-landscape-of-optimus-mass-production-2047304223174164481)
- **1X NEO [INDEP]:**
  - WSJ's Joanna Stern found the $20K NEO "not autonomous at all" in her demo, slow and clumsy even under remote control.
  - Decrypt reported the hype cooled once demos showed full teleoperation.
  - MKBHD called the preorder a bet on a robot that "(maybe) ships next year". — [Daring Fireball/WSJ](https://daringfireball.net/linked/2025/10/31/stern-1x-neo); [Decrypt](https://decrypt.co/346648/humanoid-hype-meets-reality-1x-20k-home-robot-skepticism-memes?amp=1)
- **Figure [CLAIM]:**
  - The BMW pilot is the largest documented real deployment (1,250+ h, 90k parts).
  - The original Helix blog reports no success rates, failure rates, or baselines; its evidence is curated video. — [Interesting Engineering](https://interestingengineering.com/ai-robotics/figure-humanoid-robots-retires-bmw); [Figure: Helix](https://www.figure.ai/news/helix)
- **Physical Intelligence [CLAIM]:** It has not said when models will be deployed outside controlled environments. — [Interesting Engineering](https://interestingengineering.com/ai-robotics/humanoid-robot-do-laundry-coffee)
- **Expert critique, Rodney Brooks (Sep 2025) [INDEP]:**
  - "Why Today's Humanoids Won't Learn Dexterity" argues that "collecting just visual data is not collecting the right data", because human dexterity depends on touch (about 17,000 tactile receptors in a hand) and force feedback.
  - He says humanoid walking is unlike human walking and full-size falling robots are dangerous.
  - He predicts useful robots in 15 years will look like neither today's humanoids nor humans. — [TechCrunch](https://techcrunch.com/2025/09/26/famed-roboticist-says-humanoid-robot-bubble-is-doomed-to-burst); [Robotics & Automation News](https://roboticsandautomationnews.com/2025/10/01/pioneering-roboticist-rodney-brooks-says-humanoid-robots-matching-human-skills-is-pure-fantasy/95069)
- **Genie 3 [INDEP]:** Sessions last a few minutes, and agents cannot yet respond appropriately to all events. — [TechRepublic](https://www.techrepublic.com/article/news-google-deepmind-genie-3/)

### Inferences
- The gap between "impressive 4-minute demo" and "8-hour unattended shift at production quality" is the central honest metric, and few companies publish it.
- Self-reported throughput doubling (PI) and long uninterrupted runs are the strongest evidence that learning from deployment experience works. The evidence is still company-run.

### Gaps
- No independently measured success, failure, or intervention rate for any humanoid deployment was found.
- NEO's actual delivery count and autonomy share in homes as of November 2026 was not confirmed. A secondary profile only says US deliveries were "advertised to begin in 2026" ([Yespress](https://yespress.io/1x.md)).
- Figure 03 / BotQ production numbers were not verified.

## Which design principles generalize to any agent acting in an uncertain, changing world

### Takeaway
The transferable pattern is:
- a slow, broad "understand and plan" model publishing a compact intent at low rate;
- a fast, narrow executor reacting to fresh data at high rate;
- a non-negotiable reflex and safety layer underneath;
- a learned value or progress estimate to detect drift early;
- a world model or simulator to score candidates before risking real outcomes;
- a flywheel in which live rollouts and human overrides on the agent's own failures become the most valuable training data;
- staged rollout gated by out-of-sample real-world metrics rather than training loss.

### Cited Findings
- **Asynchronous slow/fast with a latest-value mailbox:** S2 writes its latent to shared memory at 7-9 Hz, and S1 reads the latest at 200 Hz ([Figure](https://www.figure.ai/news/helix)). The VLM runs at about 10 Hz and the action head at 120 Hz ([GR00T N1](https://ar5iv.labs.arxiv.org/html/2503.14734)).
- **Reflex/safety layer at the highest rate:** Helix 02's S0 runs at 1 kHz for balance and contact ([eWeek](https://www.eweek.com/news/figure-helix-02-humanoid-robot-autonomy/)). Gemini puts collision avoidance in onboard low-level subsystems, below semantic safety reasoning ([DeepMind](https://deepmind.google/discover/blog/gemini-robotics-15-brings-ai-agents-into-the-physical-world/)).
- **Readable intermediate intents:** π0.5 uses text subtasks ([arXiv 2504.16054](https://arxiv.org/pdf/2504.16054)), and Gemini ER passes natural-language instructions per step ([DeepMind](https://deepmind.google/discover/blog/gemini-robotics-15-brings-ai-agents-into-the-physical-world/)).
- **Learn from all outcomes, weighted by advantage:** In RECAP the policy trains on good and bad data, labeled by a value function, then is told to act high-advantage ([PI](https://www.pi.website/blog/pistar06)).
- **Training loss is not deployment quality; use a predictive evaluator:** Imitation loss did not correlate with real success, but the world-model evaluator did ([1X](https://www.1x.tech/discover/redwood-ai-world-model)).
- **Counterfactual replay from failure states:** 1XWM starts evaluations from states where the live policy previously failed ([1X](https://www.1x.tech/discover/redwood-ai-world-model)).
- **Generated futures need an independent checker:** Cosmos Reason vets Cosmos Predict outputs for physical plausibility ([Cosmos cookbook](https://nvidia-cosmos.github.io/cosmos-cookbook/recipes/end2end/gr00t-dreams/post-training.html)).
- **Human takeover as supervised data on the agent's own state distribution:** RECAP corrections ([PI](https://www.pi.website/blog/pistar06)); NEO Expert Mode ([Runtimewire](https://runtimewire.com/article/1x-neo-hands-25-dof-teleoperation-autonomy-gap)).
- **Hindsight auto-labeling of raw logs:** Figure used a VLM to label segmented clips with instructions ([Figure](https://www.figure.ai/news/helix)).
- **Explicit termination and progress signal:** Helix outputs a "percent task completion" signal ([Figure](https://www.figure.ai/news/helix)).
- **Staged rollout with long unattended runs as the gate:** BMW pilot hours, then 8-hour no-operator shift claims ([Interesting Engineering](https://interestingengineering.com/ai-robotics/figure-humanoid-robots-retires-bmw); [GadgetReview](https://www.gadgetreview.com/?p=378020)).
- **Cautionary principle, the right data modality matters:** Brooks argues video alone misses force and touch ([TechCrunch](https://techcrunch.com/2025/09/26/famed-roboticist-says-humanoid-robot-bubble-is-doomed-to-burst)). Experts gave similar warnings about Tesla's video-only pivot ([eWeek](https://www.eweek.com/news/tesla-optimus-robot-training/)).

### Inferences
These mappings onto an options-trading agent are the researcher's synthesis, not sourced claims:
- **System 2:** an LLM regime and thesis layer that refreshes every few minutes, reading news, the chain, and higher-timeframe structure. It publishes a compact intent (direction, allowed setups, risk budget) to a shared "latest intent" slot.
- **System 1:** a deterministic or small-model entry and exit executor that reacts to each tick or bar using that intent plus fresh quotes. It never blocks on the LLM.
- **System 0:** hard risk limits (max loss, stale-quote refusal, position caps, kill switch) that no higher layer can override.
- **Value function:** an estimate of "how is this trade or episode going" that flags a bad entry early (credit assignment), so later failure is not the only signal.
- **World model / evaluator:** replay historical, or "counterfactual from past failure", market states to rank strategy versions. Trust out-of-sample P&L correlation, not fit loss.
- **Flywheel:** log every live decision and every manual override by the trader as labeled data. Overrides on the agent's own bad states are the most valuable examples.
- **Staged rollout:** shadow, then paper, then small live, then scaled. Gate each stage on intervention rate and out-of-sample metrics.
- **Honest-status lesson:** report error and intervention rates, not curated wins. Robotics' credibility problems came from demos without failure statistics.

### Gaps
- No source directly validates transfer of these robotics patterns to financial trading. The mapping above is analytical inference.
