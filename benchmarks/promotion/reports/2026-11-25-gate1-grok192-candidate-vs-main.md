# Gate 1: Grok forecast-k1, candidate versus main, locked 192

Status: **PASS.** No primary metric is worse beyond its paired 95% interval. Rank MAE is better: that interval lies entirely below zero. Parse failures are 0 on both arms. Pipeline failures are 0 on both arms. The synthetic review and schema checks passed on this candidate before the forecasts. The private holdout was not scored. This file records aggregates only. It does not name a paper, a title, an excerpt, or a per-paper score.

## Refs

| Arm | Commit | Role |
| --- | --- | --- |
| candidate (`cursor/apr-grok-quality-combine-d373`, PR #64) | `7f93da9d9ce64e4f09bbe9ca7f2684411bceea3b` | `--grounded-weaknesses` |
| main | `481c694021fcd6386d7157b7d58f1ab0a03b41e7` | default forecast prompt |

The set is the locked fixed dev manifest: status `locked`, 192 papers, 48 in each tier (`clear_reject`, `borderline_reject`, `borderline_accept`, `clear_accept`). `content_sha256` is `3dadef447e93452fc3d21ad0ff6c51f3bf042e93ec233fd01a4c75691dc51440`. The private holdout and the sealed CVPR sets `S24-C1` and `S24-C2` were not scored.

## Protocol

Model: grok-4.7, reasoning effort xhigh, through Cursor cloud-agent file handoff. No xAI API key and no other model API. Each answer is a fresh pass over that arm's user message. An answer already written and accepted by `ForecastJudgment` in this run was kept. The two arms of a paper do not read each other.

Locked `forecast-k1`: rank is `rank_score`, the clamped mean expected rating from one forecast sample. The venue bin is the 1–6 decision from that same call, mapped by `decision_bin`. Ratings inside a review are chosen from 1, 3, 5, 6, 8, 10. Probabilities are six numbers that sum to 1. Expected ratings are clamped to 1.0–10.0.

The candidate arm adds `--grounded-weaknesses`. The main arm uses the default forecast task text. Temperature in the locked chat body is 0.7. Cursor grok-4.7 xhigh does not take that knob. Both arms use the same Cursor effort.

Calls: 384 answer files, 192 on each arm. Parse retries: 0. `ForecastJudgment` accepted every file. Off-scale answers: 0. Renormalized probability vectors: 0. Mode mismatches: 0. One pair has the same rank score on both arms.

The forecast system prompt is 1185 characters on both arms, SHA-256 `f20e596dacc9157cb695e29c63ab59e487a5c5f6dd4510c8ad285f5f9745edcd`. The main task text is 7302 characters, SHA-256 `3ff44530081b79911122a6618b5d5bf18fced30f0b192da0376dffee8eddcccd`. The candidate task text is 8240 characters, SHA-256 `a800dab4491b02422c1a83e4c0dad56ba22438c4eada430efd44d41185e29f55`. User messages match on 0 of 192 papers, because that task text differs by the grounded-weakness paragraph. The shown manuscript span matches on all 192 papers. The 240000-character cap cuts 9 papers on each arm, and the cut flag matches on every paper. One candidate packet has one extra source-coverage notice line. That line is outside the shown manuscript span.

`fusion.py`, `rank_metrics.py`, `rating.py`, and `benchmarks/promotion/paired_bootstrap.py` match across the two refs. `public_calibration.py` differs only in the grounded-weaknesses flag plumbing. `summarize` is unchanged. `model_exec.py` differs in `forecast_task_text()` and the grounded-weakness handoff. `forecast_feedback.py` is on the candidate only.

The decision intervals are the paired stratified bootstrap: one draw applied to both arms, the four dev tiers restored with replacement in sorted stratum order, 4000 resamples, seed `20261029`, linear percentiles 0.025 and 0.975. All 4000 resamples were defined on every primary comparison below. A higher-is-better metric is worse beyond the interval when the whole interval is below zero. MAE is worse beyond the interval when the whole interval is above zero. An interval that contains zero is unchanged for this rule.

## Primary rank metrics

Point estimates on all 192 papers. Bias is `rank_score` minus the mean official rating.

| Arm | Spearman | Kendall tau-b | Accept/reject AUC | MAE | Bias |
| --- | ---: | ---: | ---: | ---: | ---: |
| candidate | 0.6467 | 0.4720 | 0.8446 | 1.1247 | -0.2656 |
| main | 0.6002 | 0.4367 | 0.8494 | 1.1987 | -0.3306 |

Paired differences, candidate minus main.

| Metric | difference | 95% interval | worse beyond interval |
| --- | ---: | --- | --- |
| Spearman | +0.0466 | [-0.0082, 0.1029] | no |
| Kendall tau-b | +0.0352 | [-0.0112, 0.0821] | no |
| Accept/reject AUC | -0.0048 | [-0.0410, 0.0302] | no |
| MAE | -0.0740 | [-0.1271, -0.0187] | no |

The rank MAE interval lies entirely below zero. That is the better direction for MAE. The other three rank intervals contain zero. The accept/reject AUC point estimate is slightly lower on the candidate, inside an interval that contains zero.

## Venue-bin metrics

These are the locked calibration metrics on the 1–6 decision. Signed error is -0.8541 on the candidate and -0.9374 on main. Spread (standard deviation of the bin) is 1.7274 on the candidate and 1.7048 on main. Signed error, spread, and the confusion table are not primary. Both arms see 68 accepts and 124 rejects.

Nearest official bin against our bin. Rows are the nearest human bin. Columns are our bin. Empty cells are zero.

| Human bin | arm | 1 | 3 | 5 | 6 | 8 | 10 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | candidate | 0 | 5 | 0 | 0 | 0 | 0 |
| 1 | main | 0 | 5 | 0 | 0 | 0 | 0 |
| 3 | candidate | 0 | 44 | 4 | 3 | 1 | 0 |
| 3 | main | 0 | 41 | 7 | 3 | 1 | 0 |
| 5 | candidate | 0 | 30 | 13 | 6 | 2 | 0 |
| 5 | main | 0 | 33 | 12 | 4 | 2 | 0 |
| 6 | candidate | 0 | 10 | 14 | 13 | 9 | 0 |
| 6 | main | 0 | 14 | 14 | 10 | 8 | 0 |
| 8 | candidate | 0 | 8 | 5 | 16 | 9 | 0 |
| 8 | main | 0 | 7 | 10 | 11 | 10 | 0 |

| Arm | Spearman | Kendall tau-b | Accept/reject AUC | MAE | Bin agreement | Snap bin agreement | Reviewer bin agreement |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| candidate | 0.5909 | 0.4776 | 0.8076 | 1.4211 | 0.4115 | 0.4844 | 0.3477 |
| main | 0.5465 | 0.4375 | 0.8231 | 1.5131 | 0.3802 | 0.4271 | 0.3226 |

| Metric | difference | 95% interval | worse beyond interval |
| --- | ---: | --- | --- |
| Spearman | +0.0443 | [-0.0336, 0.1236] | no |
| Kendall tau-b | +0.0401 | [-0.0230, 0.1065] | no |
| Accept/reject AUC | -0.0154 | [-0.0607, 0.0286] | no |
| MAE | -0.0920 | [-0.2057, 0.0226] | no |
| Bin agreement (nearest) | +0.0312 | [-0.0260, 0.0885] | no |
| Snap bin agreement | +0.0573 | [-0.0052, 0.1198] | no |
| Reviewer bin agreement | +0.0252 | [-0.0056, 0.0553] | no |

Every venue-bin interval contains zero. No primary interval lies entirely on the worse side.

## Per tier

Human means are the mean official rating on that tier of 48. Adjacent AUC treats the higher tier as the positive class and is not a primary metric. Tier means are strictly increasing on both arms, in the same order as the human means.

| Tier | human mean | candidate mean (bias, MAE) | main mean (bias, MAE) |
| --- | ---: | --- | --- |
| clear reject | 3.136 | 4.257 (+1.1211, 1.146) | 4.356 (+1.2199, 1.222) |
| borderline reject | 4.678 | 4.782 (+0.1041, 0.768) | 4.688 (+0.0100, 0.769) |
| borderline accept | 5.943 | 5.448 (-0.4952, 0.790) | 5.298 (-0.6453, 0.897) |
| clear accept | 7.722 | 5.929 (-1.7925, 1.795) | 5.815 (-1.9071, 1.907) |

| Adjacent pair | candidate AUC | main AUC |
| --- | ---: | ---: |
| clear reject vs borderline reject | 0.696 | 0.634 |
| borderline reject vs borderline accept | 0.733 | 0.715 |
| borderline accept vs clear accept | 0.650 | 0.658 |

## Quote check and grounded fixes

These rates are not primary. A missing paired interval here does not fail the promotion rule. Main's task text does not ask for a quote tag, so a quote-resolution rate is undefined on main and the paired interval for located-per-quoted is undefined (0 of 4000 resamples).

Both arms wrote 576 seats and 1152 weaknesses, exactly two weaknesses per seat. On main, all 1152 weaknesses are unquoted diagnoses. On the candidate, each seat wrote one unquoted diagnosis and one quoted weakness, and every quote is on the last weakness.

A quote resolves when `locate_weakness` finds one continuous passage in the shown source and exactly one match in the manuscript. Whitespace may differ. A changed number, comparison, or negation does not match.

| Rate | candidate | main |
| --- | ---: | ---: |
| Unquoted diagnoses / weaknesses | 576/1152 = 0.5000 | 1152/1152 = 1.0000 |
| Quoted weaknesses / weaknesses | 576/1152 = 0.5000 | 0/1152 = 0.0000 |
| Resolved quotes / quotes | 490/576 = 0.8507 | undefined |
| Unresolved quotes / quotes | 86/576 = 0.1493 | undefined |
| Resolved quotes / weaknesses | 490/1152 = 0.4253 | 0/1152 = 0.0000 |
| Seats whose last weakness quote resolves | 490/576 = 0.8507 | 0/576 = 0.0000 |

The comment before each of the 490 resolved quotes is at least two sentences. The paired difference for seats with a resolved last-weakness quote is +0.8507 [0.8160, 0.8854]. The paired difference for resolved quotes per weakness is +0.4253 [0.4080, 0.4427].

## Decision

PASS under `benchmarks/promotion/benchmark-v1.md` for this Gate 1 comparison: no primary metric is worse beyond its interval, the candidate has no new parse or pipeline failure, and the synthetic validate plus the second `run` cache hit passed on this candidate. The branch is not merged. The private holdout was not started.
