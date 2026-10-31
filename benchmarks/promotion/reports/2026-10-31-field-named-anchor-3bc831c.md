# Field-named accept anchor on the locked 192-paper dev set

Status: **Grok gate PASS against main and against pre-release `9487ee6`.** No primary metric is worse beyond its paired 95% interval on either comparison. The Opus screen did not finish, so this report does not clear the candidate for promotion. This file records aggregates only. It does not name a paper, a title, an excerpt, or a per-paper score.

## Refs

| Arm | Commit | Role |
| --- | --- | --- |
| candidate | `3bc831c228863679fe22c268099775445676ffc2` | field-named accept anchor, on pre-release `6da03a0` |
| pre-release Grok baseline | `9487ee67c9b82e389713858e069d4a327d09eae3` | stored forecast-k1 scores |
| main | `52aa2379abf7ae18c8b2f215d301b7e564b19e41` | stored Grok baseline, and the Opus main arm |

The set is the locked fixed dev manifest: status `locked`, 192 papers, 48 in each tier (`clear_reject`, `borderline_reject`, `borderline_accept`, `clear_accept`). `content_sha256` is `3dadef447e93452fc3d21ad0ff6c51f3bf042e93ec233fd01a4c75691dc51440`. The private holdout and the sealed CVPR sets were not scored.

The only scoring change from current pre-release is the accept-anchor paragraph in `forecast_task_text()`. Each sentence names its field. On the review field `rating`, and on the probabilities for that rating, an accept's official rating is 8. On `predicted_mean`, an accept sits near 7 or 8. On the 1-to-6 decision, a `predicted_mean` near 7 or 8 is decision 5, and decision 6 stays for a rare strong accept. Cutoffs, the bin map, and the coefficients are unchanged.

## Grok protocol

Model: grok-4.7, reasoning effort xhigh, through Cursor cloud-agent file handoff into a loopback chat server on `127.0.0.1`. No model API key. The server indexes the SHA-256 of the user message.

Locked `forecast-k1`, coefficient set `apr-fusion-v1`: `--cheap --holistic-mode forecast --holistic-samples 1 --related-work off --fixed-set iclr-dev --model-id grok-4.7 --effort xhigh`. The rank is `rank_score`. The venue bin is the 1–6 decision from the same call.

The candidate task text is 7302 characters, SHA-256 `3ff44530081b79911122a6618b5d5bf18fced30f0b192da0376dffee8eddcccd`. The stored pre-release task text is 7229 characters, SHA-256 `3611121d0cee15e7f503e2dd90557bbbcaf4b3ea48f487b5cd7ba127a53889e4`. The stored main task text is 6780 characters, SHA-256 `4374696de5c606470815b72c53ac5382afbeab79501f651c86608241fe1800b7`. `full_text_packet` matches main. The user message differs on all 192 papers because the task text differs. The 240000-character cap cuts 9 papers.

Calls: 192 fresh answers. Reused calls: 0. `ForecastJudgment` accepted every answer. Parse failures: 0. Pipeline failures: 0. The review stage ran again on all 192 papers (0 cache hits). The loopback returned 192 known-prompt hits and 0 misses. At $0.11 per paper, the Grok arm is $21.12.

The paired intervals use `benchmarks/promotion/paired_bootstrap.py`: one draw applied to both arms, strata restored with replacement in sorted stratum order, 4000 resamples, seed `20261029`, linear percentiles 0.025 and 0.975. All 4000 resamples were defined on every primary comparison below. A higher-is-better metric is worse beyond the interval when the whole interval is below zero. MAE is worse beyond the interval when the whole interval is above zero. Differences are candidate minus the named baseline.

Stored candidate scores are in the agent-store baseline cache, `fixed-eval/baseline-cache/field-3bc831c-grok` (`metadata.json` and `papers/<id>/our_score.json`, n=192). The comparison baselines are `pre-9487ee6-grok` and `main-52aa237-grok`.

## Grok rank metrics

Point estimates on all 192 papers. Bias is `rank_score` minus the mean official rating.

| Arm | Spearman | Kendall tau-b | Accept/reject AUC | MAE | Bias |
| --- | ---: | ---: | ---: | ---: | ---: |
| candidate | 0.6842 | 0.5025 | 0.8923 | 1.0376 | -0.1006 |
| pre-release `9487ee6` | 0.6326 | 0.4641 | 0.8606 | 1.1261 | -0.1436 |
| main `52aa237` | 0.6597 | 0.4872 | 0.8564 | 1.1816 | -0.4959 |

Paired differences, candidate minus pre-release `9487ee6`.

| Metric | difference | 95% interval | worse beyond interval |
| --- | ---: | --- | --- |
| Spearman | +0.0516 | [-0.0178, 0.1228] | no |
| Kendall tau-b | +0.0383 | [-0.0216, 0.0987] | no |
| Accept/reject AUC | +0.0317 | [-0.0082, 0.0740] | no |
| MAE | -0.0885 | [-0.1738, -0.0027] | no |

Paired differences, candidate minus main.

| Metric | difference | 95% interval | worse beyond interval |
| --- | ---: | --- | --- |
| Spearman | +0.0245 | [-0.0415, 0.0926] | no |
| Kendall tau-b | +0.0153 | [-0.0392, 0.0732] | no |
| Accept/reject AUC | +0.0358 | [-0.0042, 0.0789] | no |
| MAE | -0.1440 | [-0.2230, -0.0676] | no |

Rank MAE is better beyond the interval against both baselines. One paper has the same rank score as pre-release. No paper has the same rank score as main.

## Grok venue-bin metrics

Signed error is -0.5155 on the candidate, -0.5989 on pre-release, and -1.1353 on main. Spread (standard deviation of the bin) is 1.9061, 1.9414, and 1.5926. Signed error, spread, and the confusion table are not primary.

Nearest official bin against our bin. Rows are the nearest human bin. Columns are our bin. Empty cells are zero. No arm used bin 1 or bin 10.

| Human bin | arm | 3 | 5 | 6 | 8 |
| --- | --- | ---: | ---: | ---: | ---: |
| 1 | candidate | 4 | 1 | 0 | 0 |
| 1 | pre-release | 5 | 0 | 0 | 0 |
| 1 | main | 5 | 0 | 0 | 0 |
| 3 | candidate | 39 | 10 | 3 | 0 |
| 3 | pre-release | 42 | 6 | 1 | 3 |
| 3 | main | 45 | 5 | 2 | 0 |
| 5 | candidate | 32 | 9 | 8 | 2 |
| 5 | pre-release | 33 | 8 | 3 | 7 |
| 5 | main | 38 | 8 | 4 | 1 |
| 6 | candidate | 7 | 10 | 15 | 14 |
| 6 | pre-release | 7 | 15 | 12 | 12 |
| 6 | main | 14 | 14 | 12 | 6 |
| 8 | candidate | 3 | 7 | 8 | 20 |
| 8 | pre-release | 4 | 10 | 8 | 16 |
| 8 | main | 8 | 10 | 13 | 7 |

| Arm | Spearman | Kendall tau-b | Accept/reject AUC | MAE | Bin agreement | Snap bin agreement | Reviewer bin agreement |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| candidate | 0.6544 | 0.5180 | 0.8875 | 1.2874 | 0.4323 | 0.4271 | 0.3754 |
| pre-release | 0.6038 | 0.4782 | 0.8572 | 1.4289 | 0.4062 | 0.4688 | 0.3618 |
| main | 0.5793 | 0.4641 | 0.8059 | 1.5567 | 0.3750 | 0.4271 | 0.3216 |

Candidate minus pre-release.

| Metric | difference | 95% interval | worse beyond interval |
| --- | ---: | --- | --- |
| Spearman | +0.0506 | [-0.0190, 0.1251] | no |
| Kendall tau-b | +0.0398 | [-0.0209, 0.1026] | no |
| Accept/reject AUC | +0.0303 | [-0.0103, 0.0731] | no |
| MAE | -0.1415 | [-0.2662, -0.0198] | no |
| Bin agreement (nearest) | +0.0260 | [-0.0417, 0.0885] | no |
| Snap bin agreement | -0.0417 | [-0.1042, 0.0208] | no |
| Reviewer bin agreement | +0.0136 | [-0.0245, 0.0517] | no |

Candidate minus main.

| Metric | difference | 95% interval | worse beyond interval |
| --- | ---: | --- | --- |
| Spearman | +0.0751 | [-0.0001, 0.1524] | no |
| Kendall tau-b | +0.0539 | [-0.0104, 0.1193] | no |
| Accept/reject AUC | +0.0817 | [0.0354, 0.1327] | no |
| MAE | -0.2693 | [-0.3986, -0.1410] | no |
| Bin agreement (nearest) | +0.0573 | [-0.0156, 0.1302] | no |
| Snap bin agreement | 0.0000 | [-0.0677, 0.0729] | no |
| Reviewer bin agreement | +0.0538 | [0.0149, 0.0930] | no |

Against main, venue-bin accept/reject AUC, venue-bin MAE, and reviewer bin agreement are better beyond the interval. Against pre-release, venue-bin MAE is better beyond the interval. No primary interval on either comparison lies entirely on the worse side.

## Grok tiers

Human means are the mean official rating on that tier of 48. Adjacent AUC treats the higher tier as the positive class. Candidate tier means are strictly increasing, in the same order as the human means.

| Tier | human mean | candidate mean (bias, MAE) | pre mean (bias, MAE) | main mean (bias, MAE) |
| --- | ---: | --- | --- | --- |
| clear reject | 3.136 | 4.309 (+1.1731, 1.173) | 4.331 (+1.1951, 1.197) | 4.148 (+1.0120, 1.012) |
| borderline reject | 4.678 | 4.767 (+0.0893, 0.837) | 4.869 (+0.1905, 0.915) | 4.558 (-0.1197, 0.700) |
| borderline accept | 5.943 | 5.661 (-0.2821, 0.758) | 5.554 (-0.3886, 0.814) | 5.133 (-0.8102, 0.948) |
| clear accept | 7.722 | 6.339 (-1.3825, 1.383) | 6.150 (-1.5715, 1.579) | 5.656 (-2.0658, 2.066) |

| Adjacent pair | candidate AUC | pre AUC | main AUC |
| --- | ---: | ---: | ---: |
| clear reject vs borderline reject | 0.673 | 0.678 | 0.691 |
| borderline reject vs borderline accept | 0.764 | 0.712 | 0.717 |
| borderline accept vs clear accept | 0.680 | 0.667 | 0.675 |

## Opus screen

Opus is the cold-start slice from `opus_validation`: the first 18 papers per tier of the registered shuffle (seed `20261102`), 72 papers, both arms. The planned cost is 144 calls at $0.66, which is $95.04, under the $100 cap.

The candidate arm finished: 72 valid `ForecastJudgment` answers, 0 schema failures. The main arm, built with the `52aa237` task text on the same manuscript packets, has 54 valid answers and 0 schema failures among the files that were written. Main is missing 5 of 18 borderline accepts and 13 of 18 clear accepts. The two lower tiers are complete on both arms.

Twenty-five main-arm calls returned a platform usage-limit error. Seven of those had already written a valid answer. Eighteen papers have no answer. No retry was launched. Eighteen further calls would be $11.88, and the 144 launches already account for $95.04, which would cross $100. The platform also refused those calls with a usage-limit error, so a retry would not have completed the slice.

Paired Opus metrics were not computed. A comparison on the papers that finished would drop most of the clear-accept tier and part of the borderline-accept tier, which is not the registered slice. The main-arm Opus baseline was not cached. A partial arm is not the standing baseline.

## Verdict

**Grok versus main: PASS.** **Grok versus pre-release `9487ee6`: PASS.** No primary rank or venue-bin metric is worse beyond its paired interval. Parse failures and pipeline failures are 0.

**Opus versus main: not scored.** The main arm stopped at 54 of 72 papers. This candidate is not cleared by the Opus screen.
