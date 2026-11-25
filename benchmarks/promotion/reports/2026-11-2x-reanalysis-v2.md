# Dev192 reanalysis v2

Date: 2026-11-25. Sample: the locked ICLR dev set of 192 papers, 48 in each tier. This file records aggregates only. It does not name a paper, a title, an excerpt, or a per-paper score. The private holdout was not read. No new model call was made.

Forecast numbers were read back from the Gate 1 worker transcripts (decision, six probabilities, chair mean). `rank_score` is the clamped mean of the three expected ratings, the same `forecast-k1` definition as Gate 1. One candidate transcript and two main transcripts did not yield a numeric forecast, so model metrics below use 191 and 190 papers. Human-only figures use all 192. The recovered rank Spearman, Kendall, AUC, and MAE sit within about 0.01 of the published 192-paper Gate 1 point estimates. That is a sanity check on the recovery, not a second experiment.

Bootstrap: stratified by the four tiers, 4000 resamples, seed 20261029, linear percentiles 0.025 and 0.975. An interval is omitted when the resample is undefined. IPW weights are fixed by the tier, not re-estimated inside a resample.

## Target distribution

Paper Copilot's ICLR summary, retrieved 2026-11-25 from https://papercopilot.com/statistics/iclr-statistics/iclr-2025-statistics/. The same page lists 2024 and 2025. Mean and std are of per-submission reviewer-average scores. Reject counts are opt-in public releases and can be smaller than every reject. Accept + reject + withdraw + desk reject does not equal the stated total (2025: 11755 against 11672; 2024: 7407 against 7304). Desk rejects are left out of the mixture because the table does not give them a reviewer-average mean. The three scored statuses are renormalized.

| Year | Stated n | Stated mean (std) | Accept n, mean, std | Reject n, mean, std | Withdraw n, mean, std |
| --- | ---: | --- | --- | --- | --- |
| 2025 | 11672 | 5.12 (1.25) | 3703, 6.43, 0.68 | 4911, 4.77, 0.92 | 2993, 4.05, 0.81 |
| 2024 | 7304 | 5.11 (1.26) | 2260, 6.44, 0.70 | 3438, 4.73, 0.94 | 1655, 4.02, 0.80 |

The dev set mixes years: 119 papers from 2024 and 73 from 2025. The headline population is the count-pooled 2024+2025 accept/reject/withdraw mixture. Tier shares are the normal mixture integrated over (-inf, 4), [4, 5.5), [5.5, 7), [7, inf). That is an approximation, not a public histogram.

| Population | Tier shares | Mixture mean | Mixture std |
| --- | --- | ---: | ---: |
| 2024+2025, with withdraw | clear reject 0.210, borderline reject 0.402, borderline accept 0.320, clear accept 0.068 | 5.107 | 1.255 |
| 2024+2025, accept+reject only | clear reject 0.122, borderline reject 0.375, borderline accept 0.413, clear accept 0.090 | 5.454 | 1.177 |
| 2025 only, with withdraw | clear reject 0.208, borderline reject 0.401, borderline accept 0.324, clear accept 0.067 | 5.114 | 1.253 |
| 2024 only, with withdraw | clear reject 0.213, borderline reject 0.404, borderline accept 0.315, clear accept 0.069 | 5.096 | 1.259 |

Equal-tier sampling uses share 0.25. Headline weight = population share / 0.25: clear reject 0.839, borderline reject 1.607, borderline accept 1.282, clear accept 0.272. Kish effective n is 152.6 on the candidate recovered set and 152.2 on main. The dev human-mean SD is 1.762, against a headline mixture SD of 1.255.

## Rank metrics and IPW

Published Gate 1 on all 192, for reference: candidate Spearman 0.6467, AUC 0.8446, MAE 1.1247; main Spearman 0.6002, AUC 0.8494, MAE 1.1987.

Candidate, n=191.

| Metric | point [95% interval] |
| --- | --- |
| Spearman | 0.6396 [0.5510, 0.7189] |
| Kendall tau-b | 0.4655 [0.3925, 0.5377] |
| Accept/reject AUC | 0.8432 [0.7841, 0.8949] |
| MAE | 1.1202 [1.0236, 1.2215] |
| IPW Spearman | 0.5459 [0.4432, 0.6391] |
| IPW Kendall tau-b | 0.3885 [0.3118, 0.4657] |
| IPW AUC | 0.8452 [0.7849, 0.8987] |
| IPW MAE | 0.9186 [0.8300, 1.0138] |
| Cohen kappa, venue bin | 0.2108 [0.1340, 0.2902] |
| Quadratic weighted kappa, venue bin | 0.5065 [0.4014, 0.6055] |
| Cohen kappa, rank nearest bin | 0.2177 [0.1282, 0.3081] |
| Quadratic weighted kappa, rank nearest bin | 0.5307 [0.4393, 0.6169] |
| Nearest-bin agreement | 0.4136 [0.3541, 0.4764] |
| MAE improvement vs constant median | 0.2272 [0.1586, 0.2944] |
| Adjacent AUC, clear reject vs borderline reject | 0.6895 [0.5793, 0.7946] |
| Adjacent AUC, borderline reject vs borderline accept | 0.7326 [0.6211, 0.8299] |
| Adjacent AUC, borderline accept vs clear accept | 0.6500 [0.5312, 0.7593] |

Main, n=190.

| Metric | point [95% interval] |
| --- | --- |
| Spearman | 0.5917 [0.4889, 0.6839] |
| Kendall tau-b | 0.4292 [0.3492, 0.5097] |
| Accept/reject AUC | 0.8469 [0.7850, 0.9004] |
| MAE | 1.1939 [1.0968, 1.2941] |
| IPW Spearman | 0.4829 [0.3687, 0.5880] |
| IPW Kendall tau-b | 0.3414 [0.2579, 0.4242] |
| IPW AUC | 0.8559 [0.7935, 0.9104] |
| IPW MAE | 0.9771 [0.8873, 1.0727] |
| Cohen kappa, venue bin | 0.1687 [0.0903, 0.2506] |
| Quadratic weighted kappa, venue bin | 0.4541 [0.3473, 0.5545] |
| Cohen kappa, rank nearest bin | 0.1672 [0.0826, 0.2538] |
| Quadratic weighted kappa, rank nearest bin | 0.4303 [0.3313, 0.5237] |
| Nearest-bin agreement | 0.3842 [0.3245, 0.4474] |
| MAE improvement vs constant median | 0.1752 [0.1010, 0.2461] |
| Adjacent AUC, clear reject vs borderline reject | 0.6268 [0.5124, 0.7420] |
| Adjacent AUC, borderline reject vs borderline accept | 0.7148 [0.6042, 0.8138] |
| Adjacent AUC, borderline accept vs clear accept | 0.6545 [0.5379, 0.7620] |

Unweighted candidate Spearman 0.6396 moves to IPW Spearman 0.5459. Main moves from 0.5917 to 0.4829. The unweighted and IPW intervals overlap, so the drop is a shift of the point estimate, not a clean separation. It has the same sign under both sensitivities below. Applying the Case II range-restriction formula to the unweighted Spearman, with the dev human SD and the headline mixture SD, gives 0.5100 (candidate) and 0.4635 (main). That formula is a one-number approximation. It is not the IPW estimate.

Sensitivity, point IPW Spearman and IPW MAE only. Candidate under the 2025-only mixture: 0.5459, 0.9174. Candidate under accept+reject without withdraw: 0.5254, 0.9117.

## Trivial baselines

On the scored rows, predicting the sample median of the human means has MAE 1.4495 (candidate comparison set) and 1.4475 (main). Predicting the mean has MAE 1.4503 and 1.4479. Title-character length, leave-one-out linear fit, has MAE 1.4514 and 1.4499. Relative MAE improvement of APR versus the constant median is 0.2272 (candidate) and 0.1752 (main). Versus title length it is 0.2282 and 0.1765.

Page count is not in the locked manifest. Title-plus-abstract text is not cached on this machine. Both baselines are TODO.

## Chance-corrected agreement

Nearest-bin agreement counts a hit when the venue bin equals the nearest official bin of the human mean. Cohen kappa and quadratic weighted kappa use the ordered bins 1, 3, 5, 6, 8, 10. Venue-bin kappa compares the model's decision bin with the human nearest bin. Rank-bin kappa compares the nearest bin of `rank_score` with the human nearest bin.

Adjacent AUC is the Mann-Whitney AUC of `rank_score` between two neighboring tiers, higher tier positive. It is not the accept/reject AUC. The published point estimates were 0.696 / 0.733 / 0.650 (candidate) and 0.634 / 0.715 / 0.658 (main).

## Leave-one-reviewer-out

For each paper with at least three official ratings, each reviewer is held out in turn. The target is the mean of the remaining reviewers. APR's `rank_score` and the held-out rating are both compared to that same target. Papers are the bootstrap units, so a drawn paper contributes every held-out reviewer. This is an Alt-test-style comparison, not a preregistered non-inferiority test. Every dev paper has at least three ratings. Human-only figures use all 192 papers. The model comparison uses the recovered forecasts.

Human only, n=192: Spearman 0.6775 [0.6336, 0.7197], MAE 1.2564 [1.1548, 1.3534].

Candidate, n=191: held-out human Spearman 0.6730 [0.6254, 0.7148], MAE 1.2577 [1.1552, 1.3634]. APR Spearman 0.6214 [0.5307, 0.7009], MAE 1.1671 [1.0671, 1.2689]. APR minus human Spearman -0.0517 [-0.1388, 0.0326]. Human MAE minus APR MAE 0.0906 [-0.0466, 0.2313]. A negative Spearman gap means APR agrees less with the other reviewers than a held-out human does.

Main, n=190: held-out human Spearman 0.6735 [0.6265, 0.7138], MAE 1.2520 [1.1467, 1.3552]. APR Spearman 0.5812 [0.4862, 0.6680], MAE 1.2350 [1.1383, 1.3321]. APR minus human Spearman -0.0923 [-0.1873, -0.0031]. Human MAE minus APR MAE 0.0170 [-0.1228, 0.1543].

The candidate Spearman gap includes zero. The main Spearman gap does not: its interval lies below zero, so on this sample main agrees less with the other reviewers than a held-out human does. Both MAE gaps include zero. This sample does not show that APR is closer in absolute error than a held-out reviewer.

## Calibration

CORP decomposes MSE of `rank_score` against the human mean after an increasing isotonic fit. MCB is the mean squared gap between the score and the fitted value. DSC is the variance of the fitted value. UNC is the variance of the human mean. Pooling distinct scores into one fitted value leaves a cross term, so MSE = MCB - DSC + UNC + cross. The identity gap is the leftover after that term.

Candidate.

| Term | point [95% interval] |
| --- | ---: |
| MCB (miscalibration) | 0.1812 [0.1425, 0.3641] |
| DSC (discrimination) | 1.4143 [1.1634, 1.9171] |
| UNC (uncertainty) | 3.0410 [2.7620, 3.3554] |
| MSE | 1.9114 [1.6106, 2.2381] |
| cross term | 0.1034 [0.0963, 0.1968] |
| identity gap | 0.000000 |

| Quintile | n | mean score | mean human |
| --- | ---: | ---: | ---: |
| 1 | 38 | 3.819 | 3.734 |
| 2 | 38 | 4.403 | 4.752 |
| 3 | 38 | 4.977 | 5.407 |
| 4 | 38 | 5.678 | 6.264 |
| 5 | 39 | 6.655 | 6.758 |

Main.

| Term | point [95% interval] |
| --- | ---: |
| MCB (miscalibration) | 0.2338 [0.1698, 0.4425] |
| DSC (discrimination) | 1.2748 [0.9888, 1.7982] |
| UNC (uncertainty) | 3.0371 [2.7568, 3.3526] |
| MSE | 2.1017 [1.7842, 2.4487] |
| cross term | 0.1057 [0.1015, 0.2005] |
| identity gap | 0.000000 |

| Quintile | n | mean score | mean human |
| --- | ---: | ---: | ---: |
| 1 | 38 | 3.876 | 4.062 |
| 2 | 38 | 4.275 | 4.590 |
| 3 | 38 | 4.937 | 5.380 |
| 4 | 38 | 5.489 | 6.073 |
| 5 | 38 | 6.629 | 6.793 |

## Leakage

The first OpenReview notes request for this audit returned HTTP 403. No further OpenReview call was made, and no dev manuscript was fetched. Residual leak-marker counts on the dev set are TODO until a client can read the original submission PDFs. The `scripts/audit_submission_leaks.py` counter is in the tree and was checked on synthetic text.

How much of the Gate 1 correlation disappears on anonymized inputs is TODO. It needs a new forecast-k1 run on the anonymized text. That is a paid model call, and this reanalysis does not make one.

## What the current headline numbers overstate

Accept/reject AUC (about 0.84) is mostly the gap between clear rejects and clear accepts. Adjacent-tier AUC is about 0.63 to 0.73. A reader who treats 0.84 as fine-grained ranking is misreading the metric.

Nearest-bin agreement near 0.41 is close to what the base rates allow. Cohen kappa and QWK are the chance-corrected readings. Quote resolution (490/576 on the candidate in the Gate 1 report) says a quote occurs in the source. It does not say the criticism is right.

The middle quintiles sit below the human mean (candidate gaps about 0.35, 0.43, and 0.59). The end quintiles are close. That is the same direction as the Gate 1 negative bias, and it is a monotone under-prediction in the middle of the score range rather than a reversal.

The dev set gives each tier a quarter of the sample. The natural mixture puts more mass in the middle, and the dev human-mean SD (1.762) is wider than the headline mixture SD (1.255). Unweighted Spearman is the correlation inside that widened sample. IPW Spearman is the reweighted estimate. The Thorndike number is only a cross-check.

MAE looks small next to a 1-10 scale and large next to a constant median. The relative improvement versus that median is the comparison that matches the question "did the model beat a trivial guess?"

The Gate 1 forecasts were not run on anonymized submission text. Accepted camera-ready PDFs and rejected anonymous PDFs can leak the decision. Until the anonymized rerun exists, the published Spearman and AUC are not known to be free of that shortcut.

## TODO

- Residual leak-marker audit of the 192 dev manuscripts, after an OpenReview client that is not answered with 403.
- Forecast-k1 rerun on anonymized text, then the same metrics, to measure how much of the signal the leak carried.
- Page-count and title-plus-abstract baselines. Neither input is cached here.
- The one candidate forecast and two main forecasts that were not recoverable. They are omitted, not imputed.
