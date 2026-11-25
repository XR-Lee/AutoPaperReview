# Published metrics

Release `0.1.11`, dated 2026-11-25. These are the numbers this release reports. The review code and the prompts are the promoted tree `c82aeeb` except `__version__`. Gate 1 and Gate 2 already passed on that review code. This page does not retune a prompt, a coefficient, or a scoring path.

The earlier 15-paper public comparison stays in the [README](../README.md#benchmark-scope). It is not the release number.

No table here names a paper, a title, a forum id, or a per-paper score. The private holdout ids are not in the repository.

## Paper review

Both gates use locked `forecast-k1`: `--cheap --holistic-mode forecast --holistic-samples 1 --related-work off --coefficient-set apr-fusion-v1`. One call per paper. The rank is `rank_score`, the clamped mean of three expected ratings. The venue bin is the 1–6 decision from that same call, mapped by `decision_bin`. Contributions, the ledger, dimension scores, the fatal-flaw check, and related-work search did not run.

The candidate arm is this release's code with `--grounded-weaknesses`. The comparison arm is the previous `main` (`481c694`) with the default forecast task text. Differences are candidate minus that arm. Intervals are the paired stratified bootstrap in `benchmarks/promotion/paired_bootstrap.py`: 4000 resamples, seed `20261029`, linear percentiles 0.025 and 0.975, four fixed-eval tiers as strata. These reports do not publish a separate interval around each absolute Spearman. A higher-is-better metric is worse beyond the interval only when the whole interval is below zero. MAE is worse only when the whole interval is above zero. An interval that contains zero is not a change under that rule.

`--grounded-weaknesses` keeps each reviewer's diagnosis. Up to two weaknesses stay. The first sentence of each is only that diagnosis. One short fix, grounded in a single quoted passage, is added to the last weakness. A weakness with no quote is kept. A quote counts as resolved when `locate_weakness` finds one continuous passage in the shown source and exactly one match in the manuscript. Whitespace may differ. A changed number, comparison, or negation does not match.

### Gate 1: locked dev set, n=192

Date 2026-11-25. Model grok-4.7, reasoning effort xhigh, through Cursor cloud-agent file handoff. No xAI API key. Fresh passes. 48 papers in each tier (`clear_reject`, `borderline_reject`, `borderline_accept`, `clear_accept`). Manifest status `locked`, `content_sha256` `3dadef447e93452fc3d21ad0ff6c51f3bf042e93ec233fd01a4c75691dc51440`. Parse failures 0. Pipeline failures 0. Off-scale answers 0. Renormalized probability vectors 0. Status **PASS**. Rank MAE is better: its interval lies entirely below zero. The other primary rank intervals contain zero. The accept/reject AUC point estimate is slightly lower, inside an interval that contains zero.

Report: [benchmarks/promotion/reports/2026-11-25-gate1-grok192-candidate-vs-main.md](../benchmarks/promotion/reports/2026-11-25-gate1-grok192-candidate-vs-main.md).

| Metric | This release | Previous main | Difference | 95% interval |
| --- | ---: | ---: | ---: | --- |
| Spearman | 0.6467 | 0.6002 | +0.0466 | [−0.0082, 0.1029] |
| Kendall tau-b | 0.4720 | 0.4367 | +0.0352 | [−0.0112, 0.0821] |
| Accept/reject AUC | 0.8446 | 0.8494 | −0.0048 | [−0.0410, 0.0302] |
| MAE | 1.1247 | 1.1987 | −0.0740 | [−0.1271, −0.0187] |

Bias (`rank_score` minus the mean official rating) is −0.2656 on this release and −0.3306 on the previous main. Both arms see 68 accepts and 124 rejects.

Venue bin, same 192 papers. Every venue-bin interval contains zero.

| Metric | This release | Previous main | Difference | 95% interval |
| --- | ---: | ---: | ---: | --- |
| Spearman | 0.5909 | 0.5465 | +0.0443 | [−0.0336, 0.1236] |
| Kendall tau-b | 0.4776 | 0.4375 | +0.0401 | [−0.0230, 0.1065] |
| Accept/reject AUC | 0.8076 | 0.8231 | −0.0154 | [−0.0607, 0.0286] |
| MAE | 1.4211 | 1.5131 | −0.0920 | [−0.2057, 0.0226] |
| Bin agreement (nearest) | 0.4115 | 0.3802 | +0.0312 | [−0.0260, 0.0885] |
| Snap bin agreement | 0.4844 | 0.4271 | +0.0573 | [−0.0052, 0.1198] |
| Reviewer bin agreement | 0.3477 | 0.3226 | +0.0252 | [−0.0056, 0.0553] |

Both arms wrote 576 seats and 1152 weaknesses, two per seat. On this release, 576 of those weaknesses are unquoted diagnoses and 576 are quoted, and every quote is on the last weakness. Resolved quotes: 490/576 = 0.8507. Unresolved: 86/576 = 0.1493. The previous main did not ask for a quote, so its quote-resolution rate is undefined (0/576 seats). The paired difference for seats with a resolved last-weakness quote is +0.8507 [0.8160, 0.8854]. Quote rates are not a primary promotion metric.

### Gate 2: private holdout, n=24

Date 2026-11-25. Model `claude-opus-5-5`, reasoning effort xhigh, run locally through the Claude Code CLI behind a loopback proxy. No Anthropic API key and no xAI API key. Six papers in each of the same four tiers. This is the one-time private holdout. It is not a rerun of the 192-paper dev set. The holdout was not used to change a prompt, a coefficient, or a scoring path. Sealed sets `S24-C1` and `S24-C2` were not opened.

Calls: 24 per arm. Parse failures after the one allowed retry: 0. Pipeline failures: 0. Off-scale answers 0. Renormalized probability vectors 0. Status **PASS**. Every primary rank interval and every venue-bin interval contains zero.

Report: [benchmarks/promotion/reports/2026-11-25-gate2-opus-holdout-candidate-vs-main.md](../benchmarks/promotion/reports/2026-11-25-gate2-opus-holdout-candidate-vs-main.md).

| Metric | This release | Previous main | Difference | 95% interval |
| --- | ---: | ---: | ---: | --- |
| Spearman | 0.6168 | 0.6282 | −0.0114 | [−0.1021, 0.0680] |
| Kendall tau-b | 0.4371 | 0.4871 | −0.0499 | [−0.1485, 0.0449] |
| Accept/reject AUC | 1.0000 | 1.0000 | +0.0000 | [0.0000, 0.0000] |
| MAE | 1.1033 | 1.1112 | −0.0079 | [−0.0933, 0.0720] |

Bias is −0.7422 on this release and −0.7737 on the previous main. Both arms see 8 accepts and 16 rejects. AUC 1.0000 on 24 papers is a small-sample result: the score separated the 8 accepts from the 16 rejects on this draw. It is not a claim that a later holdout will do the same.

Venue bin, same 24 papers.

| Metric | This release | Previous main | Difference | 95% interval |
| --- | ---: | ---: | ---: | --- |
| Spearman | 0.6909 | 0.7173 | −0.0265 | [−0.0934, 0.0000] |
| Kendall tau-b | 0.5842 | 0.6101 | −0.0259 | [−0.0849, 0.0000] |
| Accept/reject AUC | 1.0000 | 1.0000 | +0.0000 | [0.0000, 0.0000] |
| MAE | 1.1993 | 1.2826 | −0.0833 | [−0.2500, 0.0000] |
| Bin agreement (nearest) | 0.4583 | 0.4167 | +0.0417 | [0.0000, 0.1250] |
| Snap bin agreement | 0.5833 | 0.5417 | +0.0417 | [0.0000, 0.1250] |
| Reviewer bin agreement | 0.4424 | 0.4007 | +0.0417 | [0.0000, 0.1250] |

Both arms wrote 72 seats and 144 weaknesses. On this release, resolved last-weakness quotes are 62/72 = 0.8611. The paired difference against the previous main, which did not ask for a quote, is +0.8611 [0.7778, 0.9444].

## Proposal review

The figure and the table below are not a result of the code in this release.

The harness is on branch `feat/proposal-review-harness` at `3a1fd15` (a patch on `406ef6c`). It is not merged. It scores ICLR 2024 public OpenReview submissions after they are compressed to an early proposal: the title plus a short description, with no results and no PDF. APR scores idea dimensions on a 1–5 scale. The comparison is the official final mean reviewer score.

Cohorts are stratified by that final score. The correlation is a within-sample association, not a sample drawn without regard to the outcome. Intervals are 3000-draw percentile bootstraps inside those final-score strata. There is no prospective trace: the proposals were scored after the conference decisions, not before the work was done.

![Higher proposal scores sit next to higher final review scores. Three panels, fixed test n=100 Spearman 0.677, independent confirm n=100 Spearman 0.597, two replications n=199 Spearman 0.522. Dots are unlabeled. The caption states that each cohort is stratified by the final score.](assets/proposal_score_vs_final_review.png)

| Cohort | n | Spearman | 95% interval | Accept/reject AUC |
| --- | ---: | ---: | --- | ---: |
| Fixed test | 100 | 0.677 | [0.559, 0.772] | 0.812 |
| Independent confirm | 100 | 0.597 | [0.465, 0.713] | 0.794 |
| Two replications | 199 | 0.522 | [0.400, 0.637] | 0.805 |

The two-replication AUC uses the 141 papers that have a decision. On those 199 papers, the top 20% by the APR score is 40 papers. That slice holds 23 papers whose final mean is at least 7. A random 40 of the same 199 would hold about 10.05.

On 84 real pre-experiment NLP proposals from the AI-Researcher study, Spearman against expert idea scores was 0.273.

### Shipped proposal path, and ClaimNov

This release does ship `prompt_packet` with `input_mode = "proposal"` and the prompt `proposal_audit_v1`. That path audits a title and a short description against supplied prior-work records. It uses the paper originality bands in `sar_bands_v1`. It is not the harness behind the figure, and it does not emit the 1–5 idea score in the table above.

On the ClaimNov later-realized 50 ([issue #33](https://github.com/XR-Lee/AutoPaperReview/issues/33)), a local grok-4.7-build run finished 50 of 50 items. Originality is clause `or-6` on 49 of 50 and `or-8` on the remaining 1. The other dimensions vary. False-alarm rate on those novel items: 0.000 at a cutoff of 4, and 0.980 [0.940, 1.000] at a cutoff of 6 (4000-draw bootstrap). `missing_closest_neighbor` is 0.000. The model chose `or-6` itself. Those bands are written for full papers, and `or-6` ("a specific combination whose pieces already exist") is easy to award to a short proposal sitting next to a large prior-work pool. A comparison with ClaimNov's reported rate is not possible from this collapse. The committed pilot does not store that run. See [benchmarks/claimnov_pilot/README.md](../benchmarks/claimnov_pilot/README.md).
