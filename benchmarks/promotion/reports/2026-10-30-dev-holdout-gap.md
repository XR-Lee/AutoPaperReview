# Dev versus private holdout, offline

Status: diagnosis only. No new model call. No prompt change and no scoring change. The accept-anchor prompt stays in pre-release, and main stays where it is. This file has aggregates only. It does not name a paper, a title, a forum id, or a per-paper row.

The accept-anchor arm is #48, commit `76b9ff5bb392c463269b7c4f5af0cc6c03e32c5c` on the holdout and the stored dev run of that same prompt. The baseline is the pre-change forecast prompt: dev cache of `8300b8b`, holdout cache of main `52aa237`. Both arms are the stored forecast-k1 judgments (grok-4.7, xhigh, one sample, `apr-fusion-v1`). The dev set is the locked 48, twelve in each tier. The holdout is the private 12, three in each tier.

Rank metrics below use `rank_score`. The venue bin is the stored 1–6 decision. A lift is accept-anchor minus baseline. A bias is score minus the mean official rating.

## 1. What the gap is

On the dev set the accept-anchor prompt raises Spearman from 0.644 to 0.739 (difference +0.095, paired interval [−0.036, +0.242]) and accept/reject AUC from 0.806 to 0.856 (difference +0.050). Tier means stay in order, and the gap between clear accept and clear reject grows from 1.55 to 2.88.

On the holdout the same prompt lowers Spearman from 0.483 to 0.056 (difference −0.427, interval [−0.964, −0.050]) and AUC from 0.781 to 0.531 (difference −0.250). The accept-anchor tier means are not in order. Clear reject, borderline reject, and borderline accept sit on top of each other (5.89, 5.89, 5.84) and clear accept is 6.66. The baseline means on the same papers stay in order (5.02, 5.30, 5.82, 6.18).

The gap between those two differences is −0.52 Spearman and −0.30 AUC. A permutation that keeps all 60 stored score pairs and redraws which three papers per tier are the holdout (4000 redraws, seed `20261029`) puts that Spearman gap at two-sided p = 0.007 and the AUC gap at p = 0.018. A random three-per-tier slice of this pool does not produce a reversal this large. The holdout result is not the ordinary noise of "any 12 papers."

It is also not the area cap. The dev draw never put four papers of one primary area into a tier (the maximum is three). No holdout paper sits in an area that the dev draw had already filled to the cap of four. Reviewer-count and within-paper rating SD do not separate the slices (permutation p = 0.56 for mean rating SD).

### Per tier

Human means are the mean official rating. User characters are the baseline forecast user message, which is the manuscript packet plus the task text. A cut is the 240000-character cap. Cuts match across the two arms on every paper.

| Slice | Tier | n | human mean (SD) | rating SD | reviews | user chars | cuts | baseline score | #48 score | lift | #48 bias |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| dev | clear reject | 12 | 2.979 (0.645) | 1.192 | 3.75 | 99500 | 0 | 4.361 | 4.153 | −0.209 | +1.174 |
| dev | borderline reject | 12 | 4.696 (0.404) | 1.508 | 3.50 | 103000 | 0 | 4.914 | 5.377 | +0.464 | +0.682 |
| dev | borderline accept | 12 | 5.976 (0.399) | 1.610 | 3.67 | 124300 | 0 | 5.606 | 6.368 | +0.763 | +0.392 |
| dev | clear accept | 12 | 7.875 (0.528) | 0.924 | 4.17 | 165600 | 2 | 5.914 | 7.033 | +1.119 | −0.842 |
| holdout | clear reject | 3 | 3.739 (0.067) | 1.720 | 4.00 | 130000 | 0 | 5.016 | 5.892 | +0.877 | +2.153 |
| holdout | borderline reject | 3 | 4.472 (0.411) | 1.595 | 3.67 | 170900 | 1 | 5.304 | 5.893 | +0.589 | +1.421 |
| holdout | borderline accept | 3 | 5.883 (0.465) | 0.991 | 4.33 | 166700 | 1 | 5.819 | 5.843 | +0.024 | −0.040 |
| holdout | clear accept | 3 | 7.467 (0.503) | 1.579 | 4.33 | 147300 | 0 | 6.179 | 6.663 | +0.484 | −0.803 |

Dev human means run from 1.50 to 3.75 inside clear reject, and 8 of 12 are at least 3. Holdout clear-reject means sit in a tight band at the top of that tier: all 3 are at least 3.5, and the SD of the three means is 0.07. The permutation p-value for that clear-reject mean gap (+0.76) is 0.069. Clear-accept means go the other way (7.88 on dev, 7.47 on holdout, p = 0.26). Nine of 12 dev clear accepts have a mean of at least 8; 1 of 3 holdout clear accepts does.

Length is the other systematic difference. Holdout user messages are about 31000 characters longer on average (p = 0.059). The extra length is in the three lower tiers. Both dev cuts are in clear accept. The holdout cuts are one borderline reject and one borderline accept. The holdout clear-reject papers are uncut, so the cap is not what inflates them.

On the dev set the lift follows the human mean (Spearman of lift versus human mean +0.58). Higher-rated papers move up, and clear-reject papers move down. Seat mass at 8 or above is 0.03 on dev clear reject and 0.58 on dev clear accept. No dev clear-reject venue bin is 8, and none of those 12 scores reach 6.

On the holdout the lift does not follow the human mean (Spearman −0.29). Clear reject moves up by 0.88. Two of its three venue bins are 8 and one is 3. Seat mass at 8 or above is 0.36, against 0.13 on the baseline. Borderline reject moves up by 0.59 on every paper in the tier (lift SD 0.19): two venue bins are 8 and one is 3, against a baseline of one 3 and two 6s. Borderline accept barely moves (lift +0.02). Clear accept moves up by 0.48, less than the dev lift of 1.12, and only one of the three scores reaches 7.

Swapping stored scores, with no new call, locates the damage. Holdout Spearman is 0.483 on the baseline and 0.056 on #48.

| Holdout scores | Spearman | AUC |
| --- | ---: | ---: |
| baseline everywhere | 0.483 | 0.781 |
| #48 on clear accept only | 0.664 | 0.875 |
| #48 on every tier except clear reject | 0.497 | 0.719 |
| #48 everywhere | 0.056 | 0.531 |

Using #48 only on clear accept would have raised holdout Spearman. Using it on borderline reject as well gives that gain back. Using it on clear reject removes the remaining agreement. The scores on the two arms still agree with each other (Spearman 0.83 on dev, 0.74 on holdout). #48 reweights; it does not scramble the baseline order at random.

### Matched dev papers do not show the clear-reject failure

The holdout clear-reject mean, about 3.7, is inside the dev tier. The four dev clear rejects with mean at least 3.5 have human mean 3.60, #48 score 4.64, lift −0.04, and no venue bin of 8. The six longer dev clear rejects (user message about 129000, the same length as the holdout tier) score 4.17 under #48, with lift −0.27 and no bin of 8. Length and a mild mean do not make the dev prompt score a clear reject as an 8.

Borderline reject is different, and the dev judgments already contain the pattern. Split the dev tier by user-message length:

| Dev borderline reject | n | user chars | #48 score | lift | venue bin 8 |
| --- | ---: | ---: | ---: | ---: | ---: |
| shorter half | 6 | 77100 | 4.56 | +0.15 | 1 |
| longer half | 6 | 128900 | 6.20 | +0.78 | 3 |

The holdout borderline-reject papers are longer still (user message about 171000, one of them cut) and land on the same lift (+0.59, two bins at 8). Inside the dev tier, lift tracks length (Spearman +0.39) and reviewer SD (Spearman +0.59). The published dev tier mean of 5.38 is the average of a short half that stays near 4.6 and a long half that moves to 6.2. That average hid the inflation.

### Reading

The reversal is not ordinary holdout noise, and it is not the area cap. Part of it is a composition effect the dev tier mean concealed: long borderline rejects are inflated on the dev set itself, and the holdout drew long ones. The larger part is specific to the holdout clear rejects. Papers on the dev set with the same mean and the same length are not scored that way. Two of the three holdout clear-reject bins move to 8, the tier mean moves to 5.89, and that tier accounts for the drop of holdout Spearman from about 0.50 to 0.06. Choosing the prompt on the dev set makes the dev difference optimistic, but the dev Spearman interval already contained zero. The holdout clear-reject scores are a generalization failure of this prompt, on a slice of three, and the design below is too small to measure a failure of that size cleanly.

## 2. Power

The test is the v1 rule: a paired stratified bootstrap interval, 4000 resamples, seed `20261029`, excludes zero. That is a two-sided 5% test. Strata are the four tiers, drawn with replacement. Critical values are the 2.5 and 97.5 percentiles of the bootstrap differences after recentering them at zero, so the shape comes from the stored pairs and the null is no difference. The noise model for the sample-size curve is the 48 dev pairs, treated as the population. A second row uses the 12 holdout pairs as their own population. No resample in this grid was undefined.

Standard error scales as `1/sqrt(k)` from 12 papers per tier upward (Spearman SE 0.071 at k = 12, 0.036 at k = 48, 0.017 at k = 200). The minimum detectable effect at 80% power is about `2.80 × SE` under a normal approximation. The figures below are the bootstrap powers; the normal approximation lands within a few points of them. At three per tier the bootstrap SE is a bit larger than the `1/sqrt(k)` extrapolation, and the holdout's own pairs are noisier still.

| Papers per tier | Total | Spearman SE | Power at 0.10 | Power at 0.05 | AUC SE | Power at AUC 0.05 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | 12 | 0.157 | 0.06 | 0.05 | 0.112 | 0.05 |
| 6 | 24 | 0.105 | 0.12 | 0.07 | 0.075 | 0.09 |
| 12 | 48 | 0.071 | 0.24 | 0.10 | 0.052 | 0.14 |
| 24 | 96 | 0.050 | 0.48 | 0.15 | 0.036 | 0.30 |
| 48 | 192 | 0.036 | 0.82 | 0.31 | 0.025 | 0.51 |
| 96 | 384 | 0.025 | 0.97 | 0.47 | 0.018 | 0.77 |
| 144 | 576 | 0.020 | 1.00 | 0.69 | 0.014 | 0.94 |
| 200 | 800 | 0.017 | 1.00 | 0.82 | 0.012 | 0.98 |

Papers per tier for about 80% power, four equal tiers, noise as in the dev pairs:

| Effect | Papers per tier | Total |
| --- | ---: | ---: |
| Spearman difference 0.10 | 48 | 192 |
| Spearman difference 0.05 | 190 | 760 |
| Accept/reject AUC difference 0.05 | 100 | 400 |

Minimum detectable effect at 80% power:

| Design | Spearman | AUC |
| --- | ---: | ---: |
| Dev, 12 per tier, dev pairs | 0.21 | 0.15 |
| Holdout size, 3 per tier, dev pairs | 0.48 | 0.34 |
| Holdout size, 3 per tier, the holdout's own pairs | 0.56 | 0.37 |

The minimum is the smallest effect on a 0.005 grid whose bootstrap power is at least 80%. The normal approximation `2.80 × SE` is 0.20 and 0.14 for the dev set. The dev Spearman difference of +0.09 is below that minimum, which is why that interval contains zero. Power at a difference of 0.10 on this dev set is about 0.24. The holdout Spearman difference of −0.43 is below that design's own minimum of about 0.56. The interval excluded zero only at the edge (upper end −0.05) and because the percentile interval is asymmetric (holdout Spearman SE 0.24, wider than the 0.16 a dev-like slice of 12 would have). A 12-paper holdout cannot confirm a 0.05 or 0.10 movement. It can catch a collapse on the order of half a correlation point, and even that only barely.

These powers describe a new draw from the same 48 paired papers. They do not add variance for a genuinely different slice. The permutation test in section 1 is the check against that pool, and it is the stronger evidence that this holdout is unusual. The interval on 12 papers is not.

## 3. Are mean-rating tiers sound?

On the dev set, three tiers match the official decision exactly. Clear reject is 12 rejects, borderline reject is 12 rejects, clear accept is 12 accepts. Borderline accept does not: 8 accepts and 4 rejects. It is also the disagreement tier. A split review here means one official rating at or below 5 and another at or above 8. A high range is a within-paper range of at least 5.

| Tier | mean rating SD | mean range | high range | split review | accept / reject |
| --- | ---: | ---: | ---: | ---: | --- |
| dev clear reject | 1.19 | 2.42 | 2/12 | 0/12 | 0/12 |
| dev borderline reject | 1.51 | 2.83 | 3/12 | 3/12 | 0/12 |
| dev borderline accept | 1.61 | 3.25 | 4/12 | 9/12 | 8/4 |
| dev clear accept | 0.92 | 2.00 | 0/12 | 0/12 | 12/0 |
| holdout clear reject | 1.72 | 3.67 | 1/3 | 0/3 | 0/3 |
| holdout borderline reject | 1.60 | 3.33 | 0/3 | 0/3 | 0/3 |
| holdout borderline accept | 0.99 | 2.00 | 0/3 | 1/3 | 1/2 |
| holdout clear accept | 1.58 | 3.67 | 1/3 | 1/3 | 3/0 |

Borderline accept on the dev set is dominated by disagreement: 9 of 12 papers have reviewers on both sides of the decision scale. Borderline reject is only partly so (3 of 12). The holdout slice of borderline accept is the opposite, the most agreed of the four holdout tiers, and it is majority reject. Disagreement in the borderline tiers does not explain the holdout failure. The jackknife on the dev set says the same thing about the #48 contrast: dropping one clear-accept paper moves the Spearman difference by 0.012 on average, against 0.005 to 0.007 in the other tiers.

Dropping the 9 dev papers with range at least 5 leaves 10, 9, 8, and 12 papers in the four tiers. The #48 Spearman difference moves from +0.095 to +0.146, and AUC difference from +0.050 to +0.098. The SD of that Spearman difference across random drops of 9 papers is 0.037, so the point estimate moves by about the noise of the drop. Split-half SD of the Spearman difference, 400 splits, falls only from 0.076 to 0.070. High-disagreement papers are not what made #48 look acceptable on the dev set, and dropping them does not make the benchmark meaningfully more stable.

A decision-plus-mean alternative on the dev set is unbalanced: 12 rejects below 4, 12 rejects from 4 up to 5.5, 4 rejects at 5.5 or above, 8 accepts below 7, and 12 accepts at 7 or above. The awkward cell is those 4 rejects inside borderline accept. That is not an equal-n design, and building one would mean a new draw. The locked manifest should stay as it is.

Clear reject has a different structural weakness. The band is four rating points wide. Dev means inside it run from 1.50 to 3.75. The holdout slice landed entirely at the top of the band. Matching the dev papers in that upper band shows that the prompt does not always inflate them, so the band width is not by itself the bug. It does mean a three-paper slice can miss the papers the tier average was describing. Future comparisons of a small slice should report the within-tier human mean and its SD beside the tier label. They should also look at long borderline rejects separately: the dev set already lifts that half toward a 6. Neither of those is a change to the locked list.

## Recommendation

Keep the locked tiers. Do not retier on decision, and do not drop high-range papers from the manifest. Read borderline accept as a mixed, high-disagreement band. When a holdout or a sensitivity slice is small, publish the within-tier human mean, rating SD, user-message length, and cut count with the score means. The accept-anchor failure mode to watch is a mild clear reject, and a long borderline reject, scored as an 8. The dev tier means averaged both of those away: clear reject because the very low papers dominate the mean, borderline reject because the short papers do.
