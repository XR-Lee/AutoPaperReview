# Opus cold-start screen, field-named anchor against main

Status: **PASS** on the primary promotion-benchmark-v1 metrics. Candidate minus main, on all 72 papers, has no primary interval entirely on the worse side of zero. The 54-paper same-path sensitivity check is also **PASS**, and no primary flag changes. This file records aggregates only. It does not name a paper, a title, an excerpt, or a per-paper score.

## Provenance

The candidate arm is the field-named accept anchor at pre-release `6d088b7eea3258d00627dc9306ddb2b9ab703a51`. Current pre-release `251810302e19be649a16b3104f1d0414b469d92a` adds the blocked-finish report and does not change `src/`. The main arm is `52aa2379abf7ae18c8b2f215d301b7e564b19e41`.

All 72 candidate answers, and 54 of the main answers, came from Cursor: model `claude-opus-5-5`, effort xhigh, temperature 0.7. Accounted Cursor spend for those launches is $95.04.

The other 18 main answers came from a different call path, approved for this screen. They were posted to the calmabacus (sub2api) Anthropic-compatible `/v1/messages` API: model `claude-opus-5-5`, `output_config.effort = max`, `max_tokens = 64000`. No temperature was sent, because the model rejects it, so those 18 calls did not use 0.7. No `output_config` schema was set, because the endpoint rejects integer minimum and maximum. The answers were validated afterwards with `ForecastJudgment` from main `52aa237`. The system prompt and the user message are the main-arm request bytes. Estimated cost for these 18 is $85.16 at an assumed $15 / $75 per million input / output tokens. Counted tokens, including trial calls and worst-case placeholders for hung calls, are 1,311,508 input and 873,182 output. The real gateway rate was not available.

Those 18 are 5 papers in `borderline_accept` and 13 papers in `clear_accept`. The two lower tiers are Cursor on both arms.

## Slice and protocol

The slice is the locked cold start: the first 18 forum ids per tier of `opus_validation` (seed `20261102`), 72 dev papers, 18 in each of `clear_reject`, `borderline_reject`, `borderline_accept`, and `clear_accept`. Manifest status `locked`. `content_sha256` is `3dadef447e93452fc3d21ad0ff6c51f3bf042e93ec233fd01a4c75691dc51440`. The private holdout and the sealed CVPR sets were not scored. `main` was not modified.

Locked `forecast-k1`, coefficient set `apr-fusion-v1`: `--cheap --holistic-mode forecast --holistic-samples 1 --related-work off`. The rank is `rank_score`. The venue bin is the 1–6 decision from the same call, mapped by `decision_bin`.

The candidate task text is 7302 characters, SHA-256 `3ff44530081b79911122a6618b5d5bf18fced30f0b192da0376dffee8eddcccd`. The main task text is 6780 characters, SHA-256 `4374696de5c606470815b72c53ac5382afbeab79501f651c86608241fe1800b7`. The user message differs on all 72 papers. The 240000-character cap cuts 3 papers, on both arms.

Each arm was scored from its own tree through a loopback chat server on `127.0.0.1`. The server indexes the SHA-256 of the user message and ignores the model, the temperature, and the system prompt. It has no API key. Each arm returned 72 known-prompt hits and 0 misses. The response body matches the answer file on all 72 papers of each arm. `ForecastJudgment` accepted every served answer. Parse failures: 0. Pipeline failures: 0.

The paired intervals use `benchmarks/promotion/paired_bootstrap.py`: one draw applied to both arms, strata restored with replacement in sorted stratum order, 4000 resamples, seed `20261029`, linear percentiles 0.025 and 0.975. All 4000 resamples were defined on every primary comparison below. A higher-is-better metric fails when the whole interval is below zero. MAE fails when the whole interval is above zero. An interval that contains zero is unchanged for this rule. Differences are candidate minus main.

## Rank metrics

Point estimates on all 72 papers. Bias is `rank_score` minus the mean official rating.

| Arm | Spearman | Kendall tau-b | Accept/reject AUC | MAE | Bias |
| --- | ---: | ---: | ---: | ---: | ---: |
| candidate | 0.8544 | 0.6783 | 0.9915 | 0.9308 | -0.6385 |
| main `52aa237` | 0.8633 | 0.6869 | 0.9932 | 0.8896 | -0.5862 |

Paired differences, candidate minus main.

| Metric | difference | 95% interval | worse beyond interval |
| --- | ---: | --- | --- |
| Spearman | -0.0089 | [-0.0308, 0.0120] | no |
| Kendall tau-b | -0.0086 | [-0.0402, 0.0224] | no |
| Accept/reject AUC | -0.0017 | [-0.0096, 0.0035] | no |
| MAE | +0.0412 | [-0.0114, 0.0991] | no |

No paper has the same rank score on both arms. Rank Spearman, Kendall, and AUC point the worse way, and rank MAE points the worse way, and every one of those intervals contains zero.

## Venue-bin metrics

Signed error is -0.8123 on the candidate and -0.6595 on main. Spread (standard deviation of the bin) is 2.0756 and 2.0393. Signed error, spread, and the confusion table are not primary.

Nearest official bin against our bin. Rows are the nearest human bin. Columns are our bin. Empty cells are zero.

| Human bin | arm | 1 | 3 | 5 | 6 | 8 | 10 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | candidate | 2 | 2 | 0 | 0 | 0 | 0 |
| 1 | main | 1 | 3 | 0 | 0 | 0 | 0 |
| 3 | candidate | 0 | 17 | 0 | 0 | 0 | 0 |
| 3 | main | 0 | 15 | 2 | 0 | 0 | 0 |
| 5 | candidate | 0 | 16 | 3 | 0 | 0 | 0 |
| 5 | main | 1 | 13 | 5 | 0 | 0 | 0 |
| 6 | candidate | 0 | 3 | 4 | 8 | 3 | 0 |
| 6 | main | 0 | 2 | 4 | 9 | 3 | 0 |
| 8 | candidate | 0 | 0 | 0 | 5 | 8 | 1 |
| 8 | main | 0 | 0 | 0 | 5 | 8 | 1 |

| Arm | Spearman | Kendall tau-b | Accept/reject AUC | MAE | Bin agreement | Snap bin agreement | Reviewer bin agreement |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| candidate | 0.8579 | 0.7279 | 0.9915 | 1.1437 | 0.5278 | 0.5417 | 0.3940 |
| main | 0.8173 | 0.6794 | 0.9945 | 1.1252 | 0.5278 | 0.5556 | 0.3667 |

Candidate minus main.

| Metric | difference | 95% interval | worse beyond interval |
| --- | ---: | --- | --- |
| Spearman | +0.0406 | [-0.0058, 0.0933] | no |
| Kendall tau-b | +0.0485 | [-0.0093, 0.1131] | no |
| Accept/reject AUC | -0.0030 | [-0.0102, 0.0010] | no |
| MAE | +0.0185 | [-0.1111, 0.1528] | no |
| Bin agreement (nearest) | 0.0000 | [-0.0694, 0.0694] | no |
| Snap bin agreement | -0.0139 | [-0.0972, 0.0556] | no |
| Reviewer bin agreement | +0.0273 | [-0.0093, 0.0685] | no |

## Tiers

Human means are the mean official rating on that tier of 18. Adjacent AUC treats the higher tier as the positive class. Both arms' tier means are strictly increasing, in the same order as the human means.

| Tier | n | Human mean | Candidate mean | Candidate MAE | Candidate bias | Main mean | Main MAE | Main bias |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| clear_reject | 18 | 2.9565 | 3.3061 | 0.4870 | +0.3496 | 3.3556 | 0.5391 | +0.3991 |
| borderline_reject | 18 | 4.7417 | 3.9015 | 0.9846 | -0.8402 | 3.9367 | 0.9446 | -0.8050 |
| borderline_accept | 18 | 5.9213 | 5.1376 | 0.9719 | -0.7837 | 5.1798 | 0.8774 | -0.7415 |
| clear_accept | 18 | 7.7963 | 6.5165 | 1.2798 | -1.2798 | 6.5991 | 1.1972 | -1.1972 |

Adjacent AUC, candidate then main: clear_reject vs borderline_reject 0.7160 and 0.7160; borderline_reject vs borderline_accept 0.8519 and 0.8549; borderline_accept vs clear_accept 0.8611 and 0.8673.

## Sensitivity: 54 papers on the Cursor path

This recomputes the same paired procedure after dropping the 18 calmabacus main answers. Both arms of the remaining papers are Cursor `claude-opus-5-5` xhigh at temperature 0.7. The strata are 18 `clear_reject`, 18 `borderline_reject`, 13 `borderline_accept`, and 5 `clear_accept`. This is a sensitivity check. It is not the registered 18-per-tier slice.

| Arm | Spearman | Kendall tau-b | Accept/reject AUC | MAE | Bias |
| --- | ---: | ---: | ---: | ---: | ---: |
| candidate | 0.7282 | 0.5624 | 0.9901 | 0.8413 | -0.4778 |
| main | 0.7401 | 0.5730 | 0.9901 | 0.8472 | -0.4643 |

| Metric | difference | 95% interval | worse beyond interval |
| --- | ---: | --- | --- |
| Spearman | -0.0119 | [-0.0607, 0.0334] | no |
| Kendall tau-b | -0.0106 | [-0.0590, 0.0371] | no |
| Accept/reject AUC | 0.0000 | [0.0000, 0.0000] | no |
| MAE | -0.0059 | [-0.0510, 0.0394] | no |
| Bin Spearman | +0.0893 | [-0.0224, 0.2100] | no |
| Bin Kendall tau-b | +0.0871 | [-0.0239, 0.2051] | no |
| Bin accept/reject AUC | 0.0000 | [0.0000, 0.0000] | no |
| Bin MAE | +0.0062 | [-0.1605, 0.1728] | no |
| Bin agreement (nearest) | +0.0185 | [-0.0556, 0.0926] | no |
| Snap bin agreement | 0.0000 | [-0.0926, 0.0741] | no |
| Reviewer bin agreement | +0.0364 | [-0.0019, 0.0840] | no |

Venue-bin point estimates on these 54: candidate Spearman 0.7381, Kendall 0.6276, accept/reject AUC 0.9938, MAE 1.1670, bin agreement 0.5556, snap 0.5741, reviewer agreement 0.3741. Main: 0.6488, 0.5405, 0.9938, 1.1608, 0.5370, 0.5741, 0.3377. Spread is 1.6606 and 1.6711. Signed error is -0.8793 and -0.6941.

The verdict stays **PASS**. None of the 11 primary worse-beyond flags change. Rank accept/reject AUC and venue-bin accept/reject AUC differ by 0.0000 on every resample. Tier means on the 54 remain strictly increasing on both arms.

## Baseline cache

The main-arm scores are in the agent-store baseline cache `fixed-eval/baseline-cache/main-52aa237-opus-mixed` (`metadata.json` and `papers/<id>/our_score.json`, n=72). `load_baseline_cache` returns them when the commit is `52aa2379abf7ae18c8b2f215d301b7e564b19e41`, the model id is `claude-opus-5-5`, the effort is `mixed`, and the manifest digest matches. The public copy of that metadata, with no per-paper scores, is `benchmarks/fixed_eval/baselines/main-52aa237-opus-mixed.json`.

The cache is tagged mixed provenance: 54 Cursor xhigh answers at temperature 0.7, and 18 calmabacus max answers with no temperature. It is the 72-paper cold start, not the 144-paper standing slice.

## Cost

| Item | Answers | Accounted cost |
| --- | ---: | ---: |
| Cursor, 72 candidate and 54 main, xhigh, temperature 0.7 | 126 | $95.04 |
| calmabacus, 18 main, effort max, no temperature | 18 | $85.16 |
| Opus screen total | 144 | $180.20 |

The calmabacus figure is an estimate at $15 / $75 per million tokens, and it includes trial calls and worst-case placeholders. The Grok screen on the 192-paper dev set remains the pass recorded in `2026-10-31-field-named-anchor-3bc831c.md`.
