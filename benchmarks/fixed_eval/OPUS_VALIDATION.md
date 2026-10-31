# Opus validation before a push to pre-release

Opus is a check on a candidate before it is pushed to `pre-release`. One validation costs at most **$100**, counting both arms when that validation runs both. The official rule is locked in [benchmark-v1.md](../promotion/benchmark-v1.md). This file keeps the cost and power notes behind that rule. It does not change the Grok promotion benchmark. No Opus call was made to write it.

The unit cost is the published forecast-k1 figure for APR Opus 5.5: **$0.66 per paper** (the holdout run note records $0.6645; the README rounds it to $0.66). At $0.66, 192 papers on two arms is 384 calls and **$253.44**. That does not fit.

## What the stored logs can and cannot say

Every forecast-k1 answer stored for the recent Grok arms records `prompt_tokens: 0` and `completion_tokens: 0`. The loopback that served those answers does not meter tokens. There is no input/output split to scale. The $0.66 figure is the measured per-paper cost of one full forecast-k1 call, not a token bill. Option (c) below is therefore a character-ratio bound, and it is marked as one.

Grok's published figure is $0.11 per paper ($0.1135 in the same run note). The recent 48-paper Grok runs were about $5 each, which is $0.104 per paper. The Grok cost in [PROPOSAL.md](PROPOSAL.md) uses $0.11.

## Power model

The standard errors are the paired stratified bootstrap on the 48-paper dev set, from `benchmarks/promotion/reports/2026-10-30-dev-holdout-gap.md`. At 12 papers per tier the Spearman SE of the arm difference is 0.071 and the AUC SE is 0.052. Both scale as `1/sqrt(k)` for a larger equal-tier draw from the same noise. The 80% minimum detectable effect is `2.80 × SE`, the normal approximation that report uses. Power at a named effect is `Φ(|effect| / SE − 1.96)`. The report's own bootstrap powers sit a few points off this normal approximation (0.24 versus 0.29 at 12 per tier for a Spearman effect of 0.10). The rows below use the normal approximation so the intermediate sizes are comparable.

| Papers per tier | Papers | Spearman SE | Spearman MDE | Power at 0.10 | AUC SE | AUC MDE | Power at AUC 0.05 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 12 | 48 | 0.071 | 0.199 | 0.29 | 0.052 | 0.146 | 0.16 |
| 18 | 72 | 0.058 | 0.162 | 0.41 | 0.042 | 0.119 | 0.22 |
| 24 | 96 | 0.050 | 0.141 | 0.51 | 0.037 | 0.103 | 0.27 |
| 36 | 144 | 0.041 | 0.115 | 0.68 | 0.030 | 0.084 | 0.38 |
| 48 | 192 | 0.036 | 0.099 | 0.80 | 0.026 | 0.073 | 0.49 |

The full dev set (48 per tier) is what the Grok screen was enlarged to see: about 80% power at a Spearman difference of 0.10. One Opus arm of that set is already $126.72. Two arms are $253.44. Opus cannot run the full set inside $100.

## (a) Cache the baseline, rerun only the candidate

Main's prompt changes rarely. A candidate changes often. Run the baseline arm once on the standing slice and reuse it. The cache is valid only while the main commit, the model id and effort, and the scoring path match. The scoring path is the forecast-k1 command and the code that builds the packet. Refresh the cache when any of those change.

| What runs | Papers | Calls | Cost |
| --- | ---: | ---: | ---: |
| Full dev set, both arms, no cache | 192 | 384 | $253.44 |
| Full dev set, candidate only, baseline already cached | 192 | 192 | $126.72 |
| Full dev set, the one-time baseline cache itself | 192 | 192 | $126.72 |

Caching does not bring the full set under $100. The baseline cache of all 192 papers is itself over the cap, and so is every later candidate arm. (a) alone is not enough. It is worth doing on whatever subset does fit, because the second and later validations then cost one arm instead of two.

## (b) A fixed stratified subset, sized from the power table

Equal tiers, drawn from the dev set, never from the private holdout.

The largest subset whose **two** arms fit in $100 is 18 papers per tier (72 papers, 144 calls, **$95.04**). At $0.6645 that is $95.69, still under $100. 19 per tier is 76 papers and $100.32, which does not fit. 24 per tier on two arms is $126.72.

At 18 per tier the minimum detectable effect is **0.162 Spearman** and **0.119 AUC**. Power at the effect the full Grok set was built for (Spearman 0.10) is about 0.41. This screen catches a large regression. It does not confirm a small one.

## (c) A cheaper packet

The current packet cap is 240000 characters. On the existing 48 dev papers the user message (packet plus the forecast task) has mean 123080 characters, median 110147, and two papers already sit on the 240000 cap. Capping the packet lower changes the bytes the model reads:

| Packet cap | Papers cut, of the current 48 | Share of characters kept | Optimistic cost if the whole $0.66 scales with characters |
| --- | ---: | ---: | ---: |
| 240000 (current) | 2 | 0.995 | $0.66 |
| 160000 | 10 | 0.922 | $0.61 |
| 120000 | 21 | 0.827 | $0.55 |
| 80000 | 39 | 0.632 | $0.42 |

Even the optimistic 80000-character figure does not bring two full arms under $100: 384 × $0.42 is about $160. One full arm at that figure is about $80. A 120000 cap on two full arms is still about $210.

The fidelity risk is high. A lower cap changes the manuscript on the long tail, so the Opus call is no longer the forecast-k1 command the Grok promotion uses. A pass or a fail on the trimmed packet does not validate the prompt that would ship. The character scaling is also an upper bound on the savings: if part of the $0.66 is output tokens, which this repo cannot measure, the savings are smaller and can be zero. Do not use a trimmed packet to buy a larger Opus set.

## (d) Combinations, and the recommendation

**Locked rule: (a) and (b) together. Do not use (c).** This is the official Opus protocol in [benchmark-v1.md](../promotion/benchmark-v1.md).

Standing gate, once a baseline cache exists for this manifest:

- Slice: **36 papers per tier** (144 papers), drawn from the dev set. The ids are in the manifest under `opus_validation.paper_ids_by_tier`, in shuffle order. The private holdout is not in this slice.
- Draw: inside each tier, sort forum ids and shuffle with `Random(f"opus-validation:20261102:{tier}")`. Take 36.
- Each validation runs **only the candidate arm**.
- Cost: 144 × $0.66 = **$95.04** (144 × $0.6645 = $95.69).
- Minimum detectable effect: **0.115 Spearman**, **0.084 AUC**.
- Power at a Spearman difference of 0.10 is about 0.68. Power at an AUC difference of 0.05 is about 0.38.

The one-time baseline cache is the same 144 papers, one arm, **$95.04**. It is valid only while the main commit, the model id and effort, and the scoring path match. A candidate validation does not rebuild it. Refresh it when the main commit, the model id, the effort, or the scoring path changes. The scoring path is the forecast-k1 command and the code that builds the packet.

Cold start, when a validation has to run both arms in that same $100 because no cache exists yet:

- Slice: **18 papers per tier** (72 papers). Same seed, the first 18 of the same shuffle, so it is a subset of the 36.
- Cost: 144 calls, **$95.04**.
- Minimum detectable effect: **0.162 Spearman**, **0.119 AUC**.

Build the 36-per-tier cache as its own step before using the standing gate. Do not put both arms of the 144-paper slice in one validation: that is $190.08.

What this screen is for: block a candidate that is worse than the cached baseline by about 0.12 Spearman or more. It is not a substitute for the Grok dev set of 192, and it is not the private holdout. A promotion to `main` still uses the Grok rule on the full dev set and, when Geek asks for it, the private holdout.

Refuse the run before any call if `papers × arms × 0.66` is over $100.
