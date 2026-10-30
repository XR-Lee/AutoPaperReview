# Proposed ICLR dev set (fixed-eval-v1)

Status: **locked** (accepted by Geek on 2026-10-30, tiers by mean official rating as proposed). The manifest digest refuses any run that uses a different id list. Changing membership requires a new manifest version.

The tier is the mean of the official ratings (1, 3, 5, 6, 8, 10), at least two ratings. The venue decision is recorded beside the tier and is not a second cut, except that every clear-reject and borderline-reject paper in this draw is in fact a reject, and every clear-accept paper is an accept. Four borderline-accept papers were rejected; their means sit between 5.5 and 6.0. Those rows are marked in the note column.

## Tier definitions

| Tier | Mean rating | Dev papers | Private holdout |
| --- | --- | ---: | ---: |
| clear reject | 0 ≤ mean < 4 | 12 | 3 |
| borderline reject | 4 ≤ mean < 5.5 | 12 | 3 |
| borderline accept | 5.5 ≤ mean < 7 | 12 | 3 |
| clear accept | 7 ≤ mean ≤ 10 | 12 | 3 |

4.0 is the top of the historical `clear_reject` stratum. 5.5 is halfway between the official borderline-reject rating (5) and the official borderline-accept rating (6). 7.0 is halfway between borderline accept (6) and accept (8). The top bound is inclusive.

Selection seed `20261030`. Inside a tier, forum ids are sorted and shuffled with `Random(f"fixed-eval-v1:20261030:{tier}")`. The walk takes at most 4 papers from one primary area, then relaxes that cap if the quota is short. The dev quota is taken first. The private holdout is the next 3 papers in that same order. The parent pool is the 93-paper materialized sample (seed `20261021`, no `--extend-rejected`) plus the README holdout-15 papers that were not in that sample (subset seed `20261022`). That pool has 15 papers with mean below 4, so the private holdout is 3 per tier (12 papers) rather than 4. Every dev paper is from that materialized pool, so an existing answer cache can be reused.

The promotion run on the 37-paper redraw cut 2 packets at 240000 characters. Those two ids were not stored. 18 of the 37 are in this dev set, so a truncated paper may be included. Drop it before lock if the id is recovered. Three clear-reject rows carry an older full-review flag (a fatal class, or a dimension quote that failed once). The locked forecast-k1 command does not use that path. The promotion forecast-k1 run had 0 parse failures on the 37, including `zhJDD85QHD`.

## Counts and overlap

| | n |
| --- | ---: |
| Dev set | 48 |
| Dev papers also in the promotion 37 | 18 |
| Dev papers also in README holdout-15 | 7 |
| Dev papers from the 93-paper sample | 46 |
| Private holdout | 12 |
| Private holdout also in the promotion 37 | 6 |
| Private holdout also in README holdout-15 | 2 |

Private holdout ids are not listed here. See `docs/fixed-eval.md`.

Rank-only cost for one pass over the dev set, using the README per-paper figures: Grok about $5.28 (48 × $0.11), Opus about $31.68 (48 × $0.66). The private holdout is about $1.32 on Grok and $7.92 on Opus. This is one `forecast-k1` call per paper, not a full review.

## Papers

### clear reject (12)

| Forum id | Title | Mean | Decision | Promotion 37 | Holdout-15 | Note |
| --- | --- | ---: | --- | --- | --- | --- |
| RFJGFrMvYj | TCIG: Two-Stage Controlled Image Generation with Quality Enhancement through Diffusion | 1.5000 | reject |  |  | Older full-review batch flagged a fatal class. Not a forecast-k1 parse failure. Mean sits at the low end of clear reject. |
| 2NwHLAffZZ | Weak Correlations as the Underlying Principle for Linearization of Gradient-Based Learning Systems | 2.3333 | reject | yes |  |  |
| eRAXvtP0gA | Unsupervised Cognition | 2.5000 | reject |  |  |  |
| VyWv7GSh5i | A Novel Variational Lower Bound For Inverse Reinforcement Learning | 2.7500 | reject |  | yes | Older full-review run missed a dimension quote, then scored on retry. Not a forecast-k1 parse failure. |
| PoB6QGAM38 | Neural Networks Decoded: Targeted and Robust Analysis of Neural Network Decisions via Causal Explanations and Reasoning | 3.0000 | reject |  |  |  |
| llXCyLhOY4 | Bias Resilient Multi-Step Off-Policy Goal-Conditioned Reinforcement Learning | 3.0000 | reject | yes |  |  |
| zhJDD85QHD | CEIR: Concept-based Explainable Image Representation Learning | 3.0000 | reject | yes | yes | Older full-review batch flagged a fatal class. The promotion forecast-k1 run scored this paper with no parse failure. |
| 4y3GDTFv70 | A Latent Space Theory for Emergent Abilities in Large Language Models | 3.2500 | reject |  |  |  |
| MpWRCiw8g5 | JOSENet: A Joint Stream Embedding Network for Violence Detection in Surveillance Videos | 3.5000 | reject |  |  |  |
| kaZAKvjLro | Semi-supervised Long-tailed Recognition using Alternate Sampling | 3.5000 | reject |  | yes |  |
| 2Ed7b52z53 | On the Matrix Form of the Quaternion Fourier Transform and Quaternion Convolution | 3.6667 | reject |  |  |  |
| xw3fStKCwm | Tensor-Train Point Cloud Compression and Efficient Approximate Nearest Neighbor Search | 3.7500 | reject | yes |  |  |

### borderline reject (12)

| Forum id | Title | Mean | Decision | Promotion 37 | Holdout-15 | Note |
| --- | --- | ---: | --- | --- | --- | --- |
| Dr4qD9bzZd | Functional Geometry Guided Protein Sequence and Backbone Structure Co-Design | 4.0000 | reject | yes |  |  |
| 1SO93f7sVf | Training Neural Networks from Scratch with Parallel Low-Rank Adapters | 4.2500 | reject |  |  |  |
| WnEnU2K3Rb | Beyond the Benchmark: Detecting Diverse Anomalies in Videos | 4.2500 | reject |  |  |  |
| Ng7OYC3PT8 | ATraDiff: Accelerating Online Reinforcement Learning with Imaginary Trajectories | 4.6000 | reject | yes |  |  |
| 50P9TDPEsh | Critique Ability of Large Language Models | 4.6667 | reject | yes |  |  |
| Ge0GEOvifh | Better Safe than Sorry: Pre-training CLIP against Targeted Data Poisoning and Backdoor Attacks | 4.6667 | reject |  |  |  |
| SJPUmX4LXD | Brain2Music: Reconstructing Music from Human Brain Activity | 4.6667 | reject |  |  |  |
| xsts7MRLey | DEEP UNSUPERVISED DOMAIN ADAPTATION FOR TIME SERIES CLASSIFICATION: A BENCHMARK | 4.6667 | reject | yes |  |  |
| QFo2wxQEW6 | Autonomous Catheterization with Open-source Simulator and Expert Trajectory | 5.0000 | reject |  |  |  |
| bjFJrdK0nO | Integrating View Conditions for Image Synthesis | 5.0000 | reject |  |  |  |
| NTWtNjlThd | Explicitly Disentangled Representations in Object-Centric Learning | 5.2500 | reject | yes |  |  |
| 48CXLrx7K3 | Revealing Unintentional Information Leakage in Low-Dimensional Facial Portrait Representations | 5.3333 | reject |  | yes |  |

### borderline accept (12)

| Forum id | Title | Mean | Decision | Promotion 37 | Holdout-15 | Note |
| --- | --- | ---: | --- | --- | --- | --- |
| E6EbeJR20o | A Large-Scale 3D Face Mesh Video Dataset via Neural Re-parameterized Optimization | 5.5000 | reject | yes |  | Venue decision is reject. The tier is the mean-rating band, which is at or above 5.5 and below 7. |
| x5txICnnjC | Synaptic Weight Distributions Depend on the Geometry of Plasticity | 5.5000 | accept |  |  |  |
| Aemqy6Hjdj | Enhancing Compositional Generalization via Compositional Feature Alignment | 5.6667 | reject | yes |  | Venue decision is reject. The tier is the mean-rating band, which is at or above 5.5 and below 7. |
| itGkF993gz | MAPE-PPI: Towards Effective and Efficient Protein-Protein Interaction Prediction via Microenvironment-Aware Protein Embedding | 5.6667 | accept |  |  |  |
| vo9t20wsmd | Faster Cascades via Speculative Decoding | 5.6667 | accept | yes |  |  |
| DS5qRs0tQz | Grounding DINO: Marrying DINO with Grounded Pre-Training for Open-Set Object Detection | 6.0000 | reject |  | yes | Venue decision is reject. The tier is the mean-rating band, which is at or above 5.5 and below 7. |
| Pujt3ADZgI | Iterative Nash Policy Optimization: Aligning LLMs with General Preferences via No-Regret Learning | 6.0000 | accept |  |  |  |
| fWk5Qx0exc | Last-Iterate Convergence Properties of Regret-Matching Algorithms in Games | 6.0000 | reject |  |  | Venue decision is reject. The tier is the mean-rating band, which is at or above 5.5 and below 7. |
| rUf9G9k2im | Image Inpainting via Iteratively Decoupled Probabilistic Modeling | 6.2500 | accept |  |  |  |
| iAYIRHOYy8 | Neural Contractive Dynamical Systems | 6.3333 | accept | yes |  |  |
| yrgQdA5NkI | Equivariant Matrix Function Neural Networks | 6.3333 | accept |  |  |  |
| pxI5IPeWgW | ODE Discovery for Longitudinal Heterogeneous Treatment Effects Inference | 6.8000 | accept |  |  |  |

### clear accept (12)

| Forum id | Title | Mean | Decision | Promotion 37 | Holdout-15 | Note |
| --- | --- | ---: | --- | --- | --- | --- |
| UpgRVWexaD | Accelerating Data Generation for Neural Operators via Krylov Subspace Recycling | 7.0000 | accept | yes | yes |  |
| kGvXIlIVLM | Toward Guidance-Free AR Visual Generation via Condition Contrastive Alignment | 7.0000 | accept | yes |  |  |
| wcka3bd7P4 | Unleashing the Potential of Fractional Calculus in Graph Neural Networks with FROND | 7.5000 | accept | yes |  |  |
| 6s5uXNWGIh | MLE-bench: Evaluating Machine Learning Agents on Machine Learning Engineering | 8.0000 | accept |  |  |  |
| GRMfXcAAFh | Oscillatory State-Space Models | 8.0000 | accept | yes |  |  |
| NYN1b8GRGS | GIM: Learning Generalizable Image Matcher From Internet Videos | 8.0000 | accept |  |  |  |
| RuP17cJtZo | Generator Matching: Generative modeling with arbitrary Markov processes | 8.0000 | accept |  |  |  |
| esYrEndGsr | Influence Functions for Scalable Data Attribution in Diffusion Models | 8.0000 | accept |  |  |  |
| g3xuCtrG6H | A Computational Framework for Modeling Emergence of Color Vision in the Human Brain | 8.0000 | accept |  |  |  |
| t7P5BUKcYv | MoE++: Accelerating Mixture-of-Experts Methods with Zero-Computation Experts | 8.0000 | accept | yes | yes |  |
| xByvdb3DCm | When Selection Meets Intervention: Additional Complexities in Causal Discovery | 8.0000 | accept |  |  |  |
| Ha6RTeWMd0 | SAM 2: Segment Anything in Images and Videos | 9.0000 | accept |  |  |  |

CVPR dev sets are registered in the same manifest and are not mixed into this table. S24-P is the existing 50-paper pilot. S24-D2 is the existing dev draw with 49 valid papers after excluding `cvpr2024-e71e5cd119bbc5797164fb0cd7fd94a4`. Sealed sets S24-C1 and S24-C2 are not opened.
