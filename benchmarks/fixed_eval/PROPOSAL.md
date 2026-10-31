# Proposed ICLR dev set (fixed-eval-v1)

Status: **proposal**. The enlarged list is open for review. It is not locked. No model evaluation was run for this enlargement. Change `status` to `locked` only after this table is accepted, then recompute `content_sha256`.

The tier is the mean of the official ratings (1, 3, 5, 6, 8, 10), at least two ratings. The venue decision is recorded beside the tier and is not a second cut. The four boundaries are unchanged: below 4, 4 up to but not including 5.5, 5.5 up to but not including 7, and 7 through 10.

## Tier definitions

| Tier | Mean rating | Dev papers | Private holdout | Eligible pool | Pool 2024 | Pool 2025 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| clear reject | 0 ≤ mean < 4 | 48 | 6 | 115 | 71 | 44 |
| borderline reject | 4 ≤ mean < 5.5 | 48 | 6 | 293 | 161 | 132 |
| borderline accept | 5.5 ≤ mean < 7 | 48 | 6 | 283 | 197 | 86 |
| clear accept | 7 ≤ mean ≤ 10 | 48 | 6 | 185 | 144 | 41 |

No tier is short of 48. The smallest pool is clear reject, with 115 eligible papers.

The parent universe is the public OpenReview listings for ICLR 2024 and ICLR 2025: 14,301 submissions with an accept or reject decision (5,690 from 2024, 8,611 from 2025). A paper is eligible with at least two official ratings. The rated pool is 876 papers. Ratings were fetched in listing order seeded by `Random(f"fixed-eval-enlarge:20261101:{kind}:{year}")` and stopped once every tier had at least 72 papers and at least 36 of them were from 2025.

The previous 48 dev papers stay in the dev set, so judgments already stored for those papers can be reused. The previous 12-paper holdout is in the new dev set and is not in the new holdout. Those 12 ids are not marked in the table below. The new holdout is 24 papers, 6 per tier, disjoint from the previous dev set and from the previous holdout.

Selection seed `20261101`. Inside a tier, the remaining forum ids are sorted and shuffled with `Random(f"fixed-eval-v1:20261101:{tier}")`. The walk takes at most 16 papers from one primary area (the same one-third share as the previous cap of 4 in a tier of 12) and at most 30 papers from one year, so each year has at least 18 papers in the dev tier. A cap is relaxed only if the quota is still short. On this draw no cap had to be relaxed. The private holdout is the next 6 papers, with at most 4 from one year.

## Per-tier summary

Abstract length is the public abstract, in characters, and it covers every selected paper. Manuscript length is extracted text characters. It is measured only where a manuscript was already cached or an arXiv pdf matched the title, so that column is partial and mostly the papers that were already on disk. Reviewer disagreement is the within-paper rating spread. "Both sides" means at least one rating at or below 5 and at least one at or above 6.

### Dev set

| Tier | n | Mean of means | Mean range | Accept / reject | Years 2024 / 2025 | Manuscript characters | Abstract characters | Mean rating SD | Mean rating range | Both sides | Range ≥ 5 |
| --- | ---: | ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| clear reject | 48 | 3.14 | 1.50–3.80 | 0 / 48 | 30 / 18 | n=25, median 68271, p25 54076, p75 96973, min 21225, max 188944 | n=48, median 1205, p25 1091, p75 1436, min 786, max 1978 | 0.98 | 2.31 | 7 | 4 |
| borderline reject | 48 | 4.68 | 4.00–5.40 | 1 / 47 | 30 / 18 | n=19, median 91464, p25 74128, p75 117208, min 44414, max 457418 | n=48, median 1244, p25 1094, p75 1446, min 846, max 2004 | 1.21 | 2.90 | 35 | 10 |
| borderline accept | 48 | 5.94 | 5.50–6.80 | 20 / 28 | 29 / 19 | n=15, median 99233, p25 87688, p75 135294, min 74484, max 287945 | n=48, median 1340, p25 1114, p75 1526, min 775, max 2170 | 1.08 | 2.50 | 42 | 11 |
| clear accept | 48 | 7.72 | 7.00–9.00 | 47 / 1 | 30 / 18 | n=15, median 138075, p25 102132, p75 190960, min 59573, max 2849914 | n=48, median 1305, p25 1176, p75 1490, min 641, max 2161 | 0.87 | 2.00 | 2 | 1 |

Manuscript length is measured for 74 of the 192 dev papers.

### Private holdout, aggregates only

Ids, titles, and the salt are not in this file. The committed manifest stores HMAC-SHA256 only.

| Tier | n | Mean of means | Mean range | Accept / reject | Years 2024 / 2025 | Manuscript characters | Abstract characters | Mean rating SD | Mean rating range | Both sides | Range ≥ 5 |
| --- | ---: | ---: | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: |
| clear reject | 6 | 3.33 | 3.00–3.75 | 0 / 6 | 4 / 2 | not measured | n=6, median 1502, p25 1400, p75 1560, min 872, max 1874 | 0.68 | 1.67 | 2 | 1 |
| borderline reject | 6 | 4.68 | 4.25–5.33 | 0 / 6 | 4 / 2 | not measured | n=6, median 1032, p25 977, p75 1094, min 900, max 1583 | 1.30 | 3.00 | 6 | 1 |
| borderline accept | 6 | 5.91 | 5.50–6.50 | 3 / 3 | 4 / 2 | not measured | n=6, median 1421, p25 1338, p75 1476, min 1307, max 2361 | 0.90 | 2.17 | 5 | 0 |
| clear accept | 6 | 7.71 | 7.25–8.00 | 5 / 1 | 4 / 2 | not measured | n=6, median 1170, p25 968, p75 1354, min 911, max 1639 | 0.87 | 2.17 | 1 | 0 |

Manuscript length is measured for 0 of the 24 holdout papers.

## Cost

One forecast-k1 call, grok-4.7 at xhigh, is $0.11 per paper in the README (the holdout run note records $0.1135; the recent 48-paper runs were about $5, or $0.104). The figures below use $0.11.

The previous 48 dev papers already have judgments on both arms where the commits match: the accept-anchor prompt now on pre-release, and the baseline prompt on main. Those 48 calls per arm are reused and are not in the new-call cost. The new holdout has no reusable judgments.

| Slice | New calls per arm | Cost per arm | Both arms |
| --- | ---: | ---: | ---: |
| Dev set, 192 papers, 48 reused | 144 | $15.84 | $31.68 |
| Private holdout, 24 papers | 24 | $2.64 | $5.28 |
| Dev plus holdout | 168 | $18.48 | $36.96 |

The previous 12 holdout papers also have stored judgments on both of those commits. If a later dev run reuses them as well, the dev new-call count falls from 144 to 132 per arm ($14.52 per arm, $29.04 for both). The table above does not take that credit. It counts reuse of the previous 48 only.

Opus validation before a push to pre-release is a separate screen and stays under $100. See [OPUS_VALIDATION.md](OPUS_VALIDATION.md). The recommended check is one candidate arm on a fixed 36-per-tier slice (144 papers) after a one-time baseline cache of that same slice. Each of those steps is $95.04. The minimum detectable effect on that slice is 0.115 Spearman and 0.084 AUC.

## Papers

The private holdout is not listed. `Promotion 37` marks overlap with `benchmarks/promotion/manifest.json`.

### clear reject (48)

| Forum id | Title | Year | Mean | Decision | Promotion 37 |
| --- | --- | ---: | ---: | --- | --- |
| RFJGFrMvYj | TCIG: Two-Stage Controlled Image Generation with Quality Enhancement through Diffusion | 2024 | 1.5000 | reject |  |
| WRxCuhTMB2 | Experimental methodology to evaluate the effectiveness of uncertainty disentanglement on regression models | 2024 | 1.6667 | reject |  |
| yldBrD4nYB | CI-VAE: a Generative Deep Learning Model for Class-Specific Data Interpolation | 2024 | 1.6667 | reject |  |
| 2DD4AXOAZ8 | Inference-Friendly Models With MixAttention | 2025 | 2.0000 | reject |  |
| JNZ3Om6NPS | On inherent limitations of GPT/LLM \\ Architecture | 2025 | 2.0000 | reject |  |
| f6GMwpxXHG | ZEPHYR GAN: REDEFINING GAN WITH FLEXIBLE GRADIENT CONTROL | 2025 | 2.2000 | reject |  |
| 2NwHLAffZZ | Weak Correlations as the Underlying Principle for Linearization of Gradient-Based Learning Systems | 2024 | 2.3333 | reject | yes |
| IoonroIpfD | A Federated Graph Learning Framework With Attention Mechanism and Clustering Algorithm | 2025 | 2.5000 | reject |  |
| eRAXvtP0gA | Unsupervised Cognition | 2024 | 2.5000 | reject |  |
| ga4LyaucKr | Learning-based Mechanism Design: Scalable, Truthful, and Continuum Approaches for Utility Maximization | 2025 | 2.5000 | reject |  |
| 1P92J25hdf | Going Deeper with General and Specific Inductive Bias for Real-Time Stereo Matching | 2024 | 2.6000 | reject |  |
| VyWv7GSh5i | A Novel Variational Lower Bound For Inverse Reinforcement Learning | 2024 | 2.7500 | reject |  |
| FOcleL0ltt | UniComposer: Band-Level Music Composition with Symbolic and Audio Unification | 2025 | 3.0000 | reject |  |
| PoB6QGAM38 | Neural Networks Decoded: Targeted and Robust Analysis of Neural Network Decisions via Causal Explanations and Reasoning | 2024 | 3.0000 | reject |  |
| TkP2RtR4hr | Regulating the level of manipulation in text augmentation with systematic adjustment and advanced sentence-embedding | 2024 | 3.0000 | reject |  |
| V8cMqUZT8o | Text Attributed Graph Node Classification Using Sheaf Neural Networks and Large Language Models | 2025 | 3.0000 | reject |  |
| fTdhM7q1o2 | Reward Learning From Preference With Ties | 2025 | 3.0000 | reject |  |
| gS0XOu0JKs | Improving the Reliability of Large Language Models by Leveraging Uncertainty-Aware In-Context Learning | 2024 | 3.0000 | reject |  |
| llXCyLhOY4 | Bias Resilient Multi-Step Off-Policy Goal-Conditioned Reinforcement Learning | 2024 | 3.0000 | reject | yes |
| pq3RANvCZC | Binary Hypothesis Testing for Softmax Models and Leverage Score Models | 2025 | 3.0000 | reject |  |
| zhJDD85QHD | CEIR: Concept-based Explainable Image Representation Learning | 2024 | 3.0000 | reject | yes |
| 4y3GDTFv70 | A Latent Space Theory for Emergent Abilities in Large Language Models | 2024 | 3.2500 | reject |  |
| 4qRCiEZGKd | Neural Description Logic Reasoning over Incomplete Knowledge Bases | 2025 | 3.4000 | reject |  |
| 0TZs6WOs16 | Hyperbolic Embeddings in Sequential Self-Attention for Improved Next-Item Recommendations | 2024 | 3.5000 | reject |  |
| HvTJLthEGQ | Zero-shot Clustering of Embeddings with Pretrained and Self-Supervised Learning Encoders | 2024 | 3.5000 | reject |  |
| MpWRCiw8g5 | JOSENet: A Joint Stream Embedding Network for Violence Detection in Surveillance Videos | 2024 | 3.5000 | reject |  |
| Z2QPJj52m3 | PISA: Compressive Sensing Adaptation of Large Language Models | 2025 | 3.5000 | reject |  |
| bUv5gJAAxH | Relating Implicit Bias and Adversarial Attacks through Intrinsic Dimension | 2024 | 3.5000 | reject |  |
| f4HohsyNEk | NeuManifold: Neural Watertight Manifold Reconstruction with Efficient and High-Quality Rendering Support | 2024 | 3.5000 | reject |  |
| gVWEq7LITG | TF-HOT: Training-Free Hand-Object Pose Tracking and Optimization for Dexterous Manipulation | 2025 | 3.5000 | reject |  |
| kaZAKvjLro | Semi-supervised Long-tailed Recognition using Alternate Sampling | 2024 | 3.5000 | reject |  |
| kn3GT7LbxT | Value Residual Learning For Alleviating  Attention Concentration In Transformers | 2025 | 3.5000 | reject |  |
| pogJXugbN8 | BAFFLE: A Baseline of Backpropagation-Free Federated Learning | 2024 | 3.5000 | reject |  |
| zV2cgXk2aY | Sentinel: Multi-Patch Transformer with Temporal and Channel Attention for Time Series Forecasting | 2025 | 3.5000 | reject |  |
| 2Ed7b52z53 | On the Matrix Form of the Quaternion Fourier Transform and Quaternion Convolution | 2024 | 3.6667 | reject |  |
| IS7kW28VVt | AlphaQCM: Alpha Discovery with Distributional Reinforcement Learning | 2025 | 3.6667 | reject |  |
| Oo5spZRpH6 | HAct: Out-of-Distribution Detection with Neural Net Activation Histograms | 2024 | 3.6667 | reject |  |
| UqEI76CKgO | Amphibian: A Meta-Learner for Rehearsal-Free Fast Online Continual Learning | 2024 | 3.6667 | reject |  |
| jD1sU2vLOn | Learning Counterfactually Invariant Predictors | 2024 | 3.6667 | reject |  |
| lgvOSEMEQS | Lightweight Unsupervised Federated Learning with Pretrained Vision Language Model | 2024 | 3.6667 | reject |  |
| 0y0yOpI4wx | General-Purpose In-Context Learning by Meta-Learning Transformers | 2024 | 3.7500 | reject | yes |
| 9k4Yvb75ED | EquiAV: Single-modal Equivariance Promotes Audio-Visual Contrastive Learning | 2024 | 3.7500 | reject |  |
| TvvT4wjEPf | Towards Practical Large-Scale Privacy-Preserving Recurrent Neural Networks | 2025 | 3.7500 | reject |  |
| lvjz7Bm3Ea | ChronoGAM: An End-to-End One-Class Time Series Gaussian Mixture Model | 2024 | 3.7500 | reject |  |
| xw3fStKCwm | Tensor-Train Point Cloud Compression and Efficient Approximate Nearest Neighbor Search | 2024 | 3.7500 | reject | yes |
| 6CfJp9NG6Q | STUDY: Socially Aware Temporally Causal Decoder Recommender Systems | 2024 | 3.8000 | reject |  |
| E6rpTruK4v | CodeUnlearn: Amortized Zero-Shot Machine Unlearning in Language Models Using Discrete Concept | 2025 | 3.8000 | reject |  |
| fJ1hON2r2u | A Single Swallow Does Not Make a Summer: Understanding Semantic Structures in Embedding Spaces | 2025 | 3.8000 | reject |  |

### borderline reject (48)

| Forum id | Title | Year | Mean | Decision | Promotion 37 |
| --- | --- | ---: | ---: | --- | --- |
| 1uHTIjXjkk | Potential Based Diffusion Motion Planning | 2024 | 4.0000 | reject | yes |
| Dr4qD9bzZd | Functional Geometry Guided Protein Sequence and Backbone Structure Co-Design | 2024 | 4.0000 | reject | yes |
| EX7AxKgc46 | Improved Generalization of cGAN using Vicinal Estimation and Early Stopping | 2024 | 4.0000 | reject |  |
| JVBR1ud4lL | MergePrint: Robust Fingerprinting against Merging Large Language Models | 2025 | 4.0000 | reject |  |
| PfZekmXRjN | Understanding When and Why Graph Attention Mechanisms Work via Node Classification | 2025 | 4.0000 | reject |  |
| ihwRfc4RNw | MatText: Do Language Models Need More than Text & Scale for Materials Modeling? | 2025 | 4.0000 | reject |  |
| k8Y71G7Xpz | FORKS: Fast Second-Order Online Kernel Learning using Incremental Sketching | 2024 | 4.0000 | reject |  |
| q3MYZQ3es8 | tBen: Benchmarking and Testing the Rule-Based Temporal Logic Reasoning Ability of Large Language Models with DatalogMTL | 2025 | 4.0000 | reject |  |
| y2D8aW4son | Capturing The Channel Dependency Completely Via Knowledge-Episodic Memory For Time Series Forecasting | 2024 | 4.0000 | reject |  |
| 1SO93f7sVf | Training Neural Networks from Scratch with Parallel Low-Rank Adapters | 2024 | 4.2500 | reject |  |
| MNwXif6AWA | Periodic Set Transformer: Material Property Prediction from Continuous Isometry Invariants | 2024 | 4.2500 | reject |  |
| WnEnU2K3Rb | Beyond the Benchmark: Detecting Diverse Anomalies in Videos | 2024 | 4.2500 | reject |  |
| JsM46OZix7 | BiMix: Bivariate Data Mixing Law for Language Model Pretraining | 2025 | 4.3333 | reject |  |
| E4Ero36Zr4 | Rethinking Teacher-Student Curriculum Learning under the Cooperative Mechanics of Experience | 2024 | 4.4000 | reject |  |
| jXrXTuvA3L | NF-MKV Net: A Constraint-Preserving Neural Network Approach to Solving Mean-Field Games Equilibrium | 2025 | 4.5000 | reject |  |
| lBlHIQ1psv | ADOPD-Instruct: A Large-Scale Multimodal Dataset for Document Editing | 2025 | 4.5000 | reject |  |
| lo9HMoGNwQ | Towards Interpretable, Sequential Multiple Instance Learning: An Application to Clinical Imaging | 2025 | 4.5000 | reject |  |
| Ng7OYC3PT8 | ATraDiff: Accelerating Online Reinforcement Learning with Imaginary Trajectories | 2024 | 4.6000 | reject | yes |
| 4lqo5Jwfnq | Class-Incremental Learning with Parameter-Efficient Cross-Task Prompts | 2024 | 4.6667 | reject |  |
| 50P9TDPEsh | Critique Ability of Large Language Models | 2024 | 4.6667 | reject | yes |
| Ge0GEOvifh | Better Safe than Sorry: Pre-training CLIP against Targeted Data Poisoning and Backdoor Attacks | 2024 | 4.6667 | reject |  |
| MD4ifad9v5 | Adaptive higher order reversible integrators for memory efficient deep learning | 2025 | 4.6667 | reject |  |
| SJPUmX4LXD | Brain2Music: Reconstructing Music from Human Brain Activity | 2024 | 4.6667 | reject |  |
| sNycNM577m | Effective LLM Knowledge Learning Requires Rethinking Generalization | 2025 | 4.6667 | reject |  |
| xsts7MRLey | DEEP UNSUPERVISED DOMAIN ADAPTATION FOR TIME SERIES CLASSIFICATION: A BENCHMARK | 2024 | 4.6667 | reject | yes |
| zIJFG7wW2d | Agent Instructs Large Language Models to be General Zero-Shot Reasoners | 2024 | 4.6667 | reject |  |
| KgN0mo6pLo | Compositional Scene Modeling with An Object-Centric Diffusion Transformer | 2025 | 4.7500 | reject |  |
| SR8LFpmVun | UncertaintyRAG: Span Uncertainty Enhanced Long-Context Modeling for Retrieval-Augmented Generation | 2025 | 4.7500 | reject |  |
| nATTIkte9f | LMO-DP: Accurately Fine-Tuning Language Models with Stronger Differential Privacy | 2024 | 4.7500 | reject |  |
| uDZ9d4UAUh | Exposing the Achilles' Heel: Evaluating LLMs Ability to Handle Mistakes in Mathematical Reasoning | 2025 | 4.7500 | reject |  |
| WdhtdjoaVw | Functional Wasserstein Bridge Inference for Bayesian Deep Learning | 2024 | 4.8000 | reject |  |
| 4nU3BLG1ni | Multi-player Multi-armed Bandits with Delayed Feedback | 2025 | 5.0000 | reject |  |
| 50UzaXh0gC | One Wave to Explain Them All: A Unifying Perspective on Post-hoc Explainability | 2025 | 5.0000 | reject |  |
| QFo2wxQEW6 | Autonomous Catheterization with Open-source Simulator and Expert Trajectory | 2024 | 5.0000 | reject |  |
| bjFJrdK0nO | Integrating View Conditions for Image Synthesis | 2024 | 5.0000 | reject |  |
| hZ3QE0rUt1 | How to distill task-agnostic representations from many teachers? | 2025 | 5.0000 | reject |  |
| j0KjevdhkH | SIG: Self-Interpretable Graph Neural Network for Continuous-time Dynamic Graphs | 2025 | 5.0000 | reject |  |
| rSAPrQzoQa | Subject Clustering by an Improved IF-PCA Algorithm | 2025 | 5.0000 | reject |  |
| zLwCT9srfo | H-Rockmate: Hierarchical Approach for Efficient Re-materialization of Large Neural Networks | 2024 | 5.0000 | reject |  |
| 8ohamFnX14 | The (co)limit of metabeliefs | 2024 | 5.2000 | reject |  |
| NTWtNjlThd | Explicitly Disentangled Representations in Object-Centric Learning | 2024 | 5.2500 | reject | yes |
| mnHpxTxnYg | Black-Box Privacy Attacks Against GANs via Detector Networks | 2024 | 5.2500 | reject |  |
| 48CXLrx7K3 | Revealing Unintentional Information Leakage in Low-Dimensional Facial Portrait Representations | 2024 | 5.3333 | reject |  |
| 7jWiBAWG0b | Learning Guarantees for Non-convex Pairwise SGD with Heavy Tails | 2024 | 5.3333 | reject |  |
| CgPs04l9TO | Butterfly Effects of SGD Noise: Error Amplification in Behavior Cloning and Autoregression | 2024 | 5.3333 | accept |  |
| HXZK1Z8tHa | ShareFormer: Share Attention for Efficient Image Restoration | 2024 | 5.3333 | reject |  |
| C5sxQsqv7X | SELECTFORMER: PRIVATE AND PRACTICAL DATA SELECTION FOR TRANSFORMERS | 2024 | 5.4000 | reject |  |
| tlqmkftgpw | DBRNet: Advancing Individual-Level Continuous Treatment Estimation through Disentangled and Balanced Representation | 2024 | 5.4000 | reject |  |

### borderline accept (48)

| Forum id | Title | Year | Mean | Decision | Promotion 37 |
| --- | --- | ---: | ---: | --- | --- |
| 8GmPLkO0oR | NeRFuser: Diffusion Guided Multi-Task 3D Policy Learning | 2024 | 5.5000 | reject |  |
| E6EbeJR20o | A Large-Scale 3D Face Mesh Video Dataset via Neural Re-parameterized Optimization | 2024 | 5.5000 | reject | yes |
| H6DpBnPCyH | Reinforcement Learning for Finite Space Mean-Field Type Games | 2025 | 5.5000 | reject |  |
| MUTTUDKb3M | DRIMA: Differential Reward Interaction for Cooperative Multi-Agent Reinforcement Learning | 2025 | 5.5000 | reject |  |
| PCXvcULwiI | Benchmarking Structural Inference Methods for Interacting Dynamical Systems with Synthetic Data | 2024 | 5.5000 | reject |  |
| ZN8BaYVFkx | Training Adversarially Robust SNNs with Gradient Sparsity Regularization | 2024 | 5.5000 | reject |  |
| c4w1TqcSi0 | Optima: Optimizing Effectiveness and Efficiency for LLM-Based Multi-Agent System | 2025 | 5.5000 | reject |  |
| mPOVOwsDOO | Talking Models: Distill Pre-trained Knowledge to Downstream Models via Interactive Communication | 2024 | 5.5000 | reject | yes |
| rlsWIBDWhW | Cluster-Driven Adversarial Perturbations for Robust Contrastive Learning | 2025 | 5.5000 | reject |  |
| sehRvaIPQQ | Let Models Speak Ciphers: Multiagent Debate through Embeddings | 2024 | 5.5000 | accept |  |
| x5txICnnjC | Synaptic Weight Distributions Depend on the Geometry of Plasticity | 2024 | 5.5000 | accept |  |
| yr7PjzmkQ6 | On the utility of Equivariance and Symmetry Breaking in Deep learning architectures on point clouds | 2025 | 5.5000 | reject |  |
| py34636XvR | Scalable Simulation-free Entropic Unbalanced Optimal Transport | 2025 | 5.6000 | reject |  |
| 2pvMZKGYDR | Extend Model Merging from Fine-Tuned to Pre-Trained Large Language Models via Weight Disentanglement | 2025 | 5.6667 | reject |  |
| Aemqy6Hjdj | Enhancing Compositional Generalization via Compositional Feature Alignment | 2024 | 5.6667 | reject | yes |
| KS4G94XBo7 | Deviation Ratings: A general, clone invariant rating method | 2025 | 5.6667 | reject |  |
| itGkF993gz | MAPE-PPI: Towards Effective and Efficient Protein-Protein Interaction Prediction via Microenvironment-Aware Protein Embedding | 2024 | 5.6667 | accept |  |
| vo9t20wsmd | Faster Cascades via Speculative Decoding | 2025 | 5.6667 | accept | yes |
| w0jk3L3IjV | Breaking the Detection-Generalization Paradox on Out-Of-Distribution Data | 2025 | 5.6667 | reject |  |
| zsfrzYWoOP | FABRIC: Personalizing Diffusion Models with Iterative Feedback | 2024 | 5.6667 | reject |  |
| 6LKmaC4cO0 | Graph of Records: Boosting Retrieval Augmented Generation for Long-context Summarization with Graphs | 2025 | 5.7500 | reject |  |
| 6NEJ0ReNzr | Learning to Plan and Generate Text with Citations | 2024 | 5.7500 | reject |  |
| UrbTqU2xrb | Clothing-disentangled 3D character generation from a single image | 2025 | 5.7500 | reject |  |
| lNCnZwcH5Z | Non-negative Contrastive Learning | 2024 | 5.7500 | accept |  |
| pZhdz4oyzo | SqueezeLLM: Dense and Sparse Quantization | 2024 | 5.7500 | reject | yes |
| xh0XzueyCJ | Plug-And-Play Controllable Graph Generation With Diffusion Models | 2024 | 5.7500 | reject |  |
| 3LifGYAD0W | Eliciting Black-Box Representations from LLMs through Self-Queries | 2025 | 6.0000 | reject |  |
| DS5qRs0tQz | Grounding DINO: Marrying DINO with Grounded Pre-Training for Open-Set Object Detection | 2024 | 6.0000 | reject |  |
| Pujt3ADZgI | Iterative Nash Policy Optimization: Aligning LLMs with General Preferences via No-Regret Learning | 2025 | 6.0000 | accept |  |
| UfczlMudN6 | GRAM: Generalization in Deep RL with a Robust Adaptation Module | 2025 | 6.0000 | reject |  |
| fWk5Qx0exc | Last-Iterate Convergence Properties of Regret-Matching Algorithms in Games | 2024 | 6.0000 | reject |  |
| jznbgiynus | Language Modeling Is Compression | 2024 | 6.0000 | accept |  |
| vpV7fOFQy4 | When should we prefer Decision Transformers for Offline Reinforcement Learning? | 2024 | 6.0000 | accept |  |
| Z7aq3djHZw | JPEG-LM: LLMs as Image Generators with Canonical Codec Representations | 2025 | 6.2500 | reject |  |
| ZkBg5D2lgT | Bringing robotics taxonomies to continuous domains via GPLVM on hyperbolic manifolds | 2024 | 6.2500 | reject |  |
| rUf9G9k2im | Image Inpainting via Iteratively Decoupled Probabilistic Modeling | 2024 | 6.2500 | accept |  |
| 5xxGP9x5dZ | Unlearn and Burn: Adversarial Machine Unlearning Requests Destroy Model Accuracy | 2025 | 6.3333 | accept |  |
| VtmBAGCN7o | MetaGPT: Meta Programming for A Multi-Agent Collaborative Framework | 2024 | 6.3333 | accept |  |
| iAYIRHOYy8 | Neural Contractive Dynamical Systems | 2024 | 6.3333 | accept | yes |
| yrgQdA5NkI | Equivariant Matrix Function Neural Networks | 2024 | 6.3333 | accept |  |
| hXm0Wu2U9K | Correcting the Mythos of KL-Regularization: Direct Alignment without Overoptimization via Chi-Squared Preference Optimization | 2025 | 6.4000 | accept | yes |
| My7lkRNnL9 | Forward Learning with Top-Down Feedback: Empirical and Analytical Characterization | 2024 | 6.5000 | accept |  |
| 4VIgNuQ1pY | Stable Neural Stochastic Differential Equations in Analyzing Irregular Time Series Data | 2024 | 6.6667 | accept |  |
| PudduufFLa | Geographic Location Encoding with Spherical Harmonics and Sinusoidal Representation Networks | 2024 | 6.7500 | accept |  |
| oQKKlzxV1o | Online Information Acquisition: Hiring Multiple Agents | 2024 | 6.7500 | accept |  |
| v7ZPwoHU1j | Statistically Optimal $K$-means Clustering via Nonnegative Low-rank Semidefinite Programming | 2024 | 6.7500 | accept |  |
| Re4Z3Wt2DS | Variational Mirror Descent for Robust Learning in Schrödinger Bridge | 2025 | 6.8000 | reject |  |
| pxI5IPeWgW | ODE Discovery for Longitudinal Heterogeneous Treatment Effects Inference | 2024 | 6.8000 | accept |  |

### clear accept (48)

| Forum id | Title | Year | Mean | Decision | Promotion 37 |
| --- | --- | ---: | ---: | --- | --- |
| 0akLDTFR9x | Contrastive Difference Predictive Coding | 2024 | 7.0000 | accept |  |
| 79FVDdfoSR | A Characterization Theorem for Equivariant Networks with Point-wise Activations | 2024 | 7.0000 | accept |  |
| BV1PHbTJzd | Accelerating Distributed Stochastic Optimization via Self-Repellent Random Walks | 2024 | 7.0000 | accept |  |
| S1RKWSyZ2Y | Guiding Instruction-based Image Editing via Multimodal Large Language Models | 2024 | 7.0000 | accept |  |
| UpgRVWexaD | Accelerating Data Generation for Neural Operators via Krylov Subspace Recycling | 2024 | 7.0000 | accept | yes |
| bxfKIYfHyx | AlignDiff: Aligning Diverse Human Preferences via Behavior-Customisable Diffusion Model | 2024 | 7.0000 | accept |  |
| kGvXIlIVLM | Toward Guidance-Free AR Visual Generation via Condition Contrastive Alignment | 2025 | 7.0000 | accept | yes |
| kJ5H7oGT2M | Learning Long Range Dependencies on Graphs via Random Walks | 2025 | 7.0000 | accept |  |
| lR3rk7ysXz | On Diffusion Modeling for Anomaly Detection | 2024 | 7.0000 | accept |  |
| nBZBPXdJlC | Listen, Think, and Understand | 2024 | 7.0000 | accept |  |
| 5FXKgOxmb2 | MAGNet: Motif-Agnostic Generation of Molecules from Scaffolds | 2025 | 7.2500 | accept |  |
| LiUfN9h0Lx | Efficient and Accurate Explanation Estimation with Distribution Compression | 2025 | 7.3333 | accept |  |
| hnrB5YHoYu | Finetuning Text-to-Image Diffusion Models for Fairness | 2024 | 7.3333 | accept |  |
| o2IEmeLL9r | Pre-Training Goal-based Models for Sample-Efficient Reinforcement Learning | 2024 | 7.3333 | accept |  |
| 84pDoCD4lH | Do Vision-Language Models Represent Space and How? Evaluating Spatial Frame of Reference under Ambiguities | 2025 | 7.4000 | accept |  |
| 6N8TW504aa | Graphical Multioutput Gaussian Process with Attention | 2024 | 7.5000 | accept |  |
| FVhmnvqnsI | Multisize Dataset Condensation | 2024 | 7.5000 | accept |  |
| GZ6AcZwA8r | MMD Graph Kernel: Effective Metric Learning for Graphs via Maximum Mean Discrepancy | 2024 | 7.5000 | accept |  |
| VmGRoNDQgJ | Influencer Backdoor Attack on Semantic Segmentation | 2024 | 7.5000 | accept |  |
| WOzffPgVjF | Knowing Your Target: Target-Aware Transformer Makes Better Spatio-Temporal Video Grounding | 2025 | 7.5000 | accept |  |
| le1UUMd45T | Solving Multiobjective Combinatorial Optimization via Learn to Improve Method | 2024 | 7.5000 | reject |  |
| sLdVl0q68X | NuwaDynamics: Discovering and Updating in Causal Spatio-Temporal Modeling | 2024 | 7.5000 | accept |  |
| tVMPfEGT2w | Provable Offline Preference-Based Reinforcement Learning | 2024 | 7.5000 | accept |  |
| wcka3bd7P4 | Unleashing the Potential of Fractional Calculus in Graph Neural Networks with FROND | 2024 | 7.5000 | accept | yes |
| 1vDArHJ68h | Mastering Memory Tasks with World Models | 2024 | 8.0000 | accept |  |
| 51WraMid8K | A Probabilistic Perspective on Unlearning and Alignment for Large Language Models | 2025 | 8.0000 | accept |  |
| 6s5uXNWGIh | MLE-bench: Evaluating Machine Learning Agents on Machine Learning Engineering | 2025 | 8.0000 | accept |  |
| GRMfXcAAFh | Oscillatory State-Space Models | 2025 | 8.0000 | accept | yes |
| IGzaH538fz | GNNCert: Deterministic Certification of Graph Neural Networks against Adversarial Perturbations | 2024 | 8.0000 | accept |  |
| NYN1b8GRGS | GIM: Learning Generalizable Image Matcher From Internet Videos | 2024 | 8.0000 | accept |  |
| QEHrmQPBdd | RM-Bench: Benchmarking Reward Models of Language Models with Subtlety and Style | 2025 | 8.0000 | accept |  |
| RuP17cJtZo | Generator Matching: Generative modeling with arbitrary Markov processes | 2025 | 8.0000 | accept |  |
| SQrHpTllXa | CABINET: Content Relevance-based Noise Reduction for Table Question Answering | 2024 | 8.0000 | accept |  |
| esYrEndGsr | Influence Functions for Scalable Data Attribution in Diffusion Models | 2025 | 8.0000 | accept |  |
| g3xuCtrG6H | A Computational Framework for Modeling Emergence of Color Vision in the Human Brain | 2025 | 8.0000 | accept |  |
| gU58d5QeGv | Würstchen: An Efficient Architecture for Large-Scale Text-to-Image Diffusion Models | 2024 | 8.0000 | accept |  |
| hyfe5q5TD0 | Computationally Efficient RL under Linear Bellman Completeness for Deterministic Dynamics | 2025 | 8.0000 | accept |  |
| pOoKI3ouv1 | Robust agents learn causal world models | 2024 | 8.0000 | accept | yes |
| rmg0qMKYRQ | Intriguing Properties of Generative Classifiers | 2024 | 8.0000 | accept |  |
| t7P5BUKcYv | MoE++: Accelerating Mixture-of-Experts Methods with Zero-Computation Experts | 2025 | 8.0000 | accept | yes |
| w4abltTZ2f | Batched Low-Rank Adaptation of Foundation Models | 2024 | 8.0000 | accept |  |
| xByvdb3DCm | When Selection Meets Intervention: Additional Complexities in Causal Discovery | 2025 | 8.0000 | accept |  |
| ANvmVS2Yr0 | Generalization in diffusion models arises from geometry-adaptive harmonic representations | 2024 | 8.5000 | accept |  |
| HSKaGOi7Ar | Beyond Weisfeiler-Lehman: A Quantitative Framework for GNN Expressiveness | 2024 | 8.5000 | accept |  |
| UyNXMqnN3c | DreamGaussian: Generative Gaussian Splatting for Efficient 3D Content Creation | 2024 | 8.5000 | accept |  |
| Ha6RTeWMd0 | SAM 2: Segment Anything in Images and Videos | 2025 | 9.0000 | accept |  |
| aN4Jf6Cx69 | The mechanistic basis of data dependence and abrupt learning in an in-context classification task | 2024 | 9.0000 | accept |  |
| gc8QAQfXv6 | Unlocking the Power of Function Vectors for Characterizing and Mitigating Catastrophic Forgetting in Continual Instruction Tuning | 2025 | 9.0000 | accept |  |

CVPR dev sets are registered in the same manifest and are not mixed into this table. S24-P is the existing 50-paper pilot. S24-D2 is the existing dev draw with 49 valid papers after excluding `cvpr2024-e71e5cd119bbc5797164fb0cd7fd94a4`. Sealed sets S24-C1 and S24-C2 are not opened.
