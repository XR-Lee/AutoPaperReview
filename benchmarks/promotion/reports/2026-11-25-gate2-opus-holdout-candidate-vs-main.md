# Gate 2: Opus forecast-k1 on the 24-paper private holdout, candidate versus main

Status: **PASS.** No primary rank metric or venue-bin metric is worse beyond its paired 95% interval. Parse failures after the one allowed retry are 0 on both arms. Pipeline failures are 0 on both arms. The Gate 3 synthetic review and schema checks passed on this pre-release commit. This file records aggregates only. It does not name a paper, a title, an excerpt, a forum id, or a per-paper score.

This is the one-time private holdout: 24 papers, 6 in each fixed-eval tier (`clear_reject`, `borderline_reject`, `borderline_accept`, `clear_accept`). It is not a rerun of the locked 192-paper dev set. Sealed sets `S24-C1` and `S24-C2` were not opened. The holdout was not used to change a prompt, a coefficient, or a scoring path.

Gate 1 (192-paper Grok forecast-k1 comparison) passed separately and is recorded in `benchmarks/promotion/reports/2026-11-25-gate1-grok192-candidate-vs-main.md`.

## Refs

| Arm | Commit | Role |
| --- | --- | --- |
| candidate (`pre-release`, after #64) | `331f05648d0c4ac85449613366facae306eff4bd` | `--grounded-weaknesses` |
| main | `481c694021fcd6386d7157b7d58f1ab0a03b41e7` | default forecast prompt |

The operator file matched the committed HMAC list (n=24, 6 per tier) and does not overlap the dev set. That file and the staged manuscripts stay outside the checkout on the box only.

## Protocol

Model: `claude-opus-5-5`, reasoning effort `xhigh`, run locally on the box via the Claude Code CLI (`claude.ai` subscription) behind a loopback OpenAI-compatible proxy (`127.0.0.1`). No Anthropic API key, no xAI API key, and no external API billing were used. Each answer is a fresh pass over that arm's user message, and the two arms of a paper do not read each other.

Locked `forecast-k1`, coefficient set `apr-fusion-v1`: `--cheap --holistic-mode forecast --holistic-samples 1 --related-work off`. The rank is `rank_score`, the clamped mean expected rating from one forecast sample. The venue bin is the 1–6 decision from that same call, mapped by `decision_bin`. Candidate adds `--grounded-weaknesses`; `main` uses the default forecast task text.

Calls: 48 Gate 2 calls (24 per arm), 0 parse retries, 0 errors, 0 off-scale answers, 0 renormalized probability vectors, 0 mode mismatches. Parse-failure count after retry is 0 on each arm; pipeline-failure count is 0 on each arm. An additional 16 subscription calls were run as an offline main-vs-main repeat noise check on the first 16 papers without touching the candidate or tuning anything. Total CLI-reported notional subscription cost was $17.31 for the 48 Gate 2 calls ($18.11 including the 16 noise-check calls); actual API spend was $0.00 (under the $100 cap).

The forecast system prompt is 1185 characters on both arms, SHA-256 `f20e596dacc9157cb695e29c63ab59e487a5c5f6dd4510c8ad285f5f9745edcd`. The main task text is 7302 characters, SHA-256 `3ff44530081b79911122a6618b5d5bf18fced30f0b192da0376dffee8eddcccd`. The candidate task text is 8240 characters, SHA-256 `a800dab4491b02422c1a83e4c0dad56ba22438c4eada430efd44d41185e29f55`. User messages match on 0 of 24 papers because the task text differs by the grounded-weakness paragraph. The shown manuscript span matches on all 24 papers. The 240000-character cap cuts 1 paper on each arm, and the cut flag matches on that paper; 23 papers are uncut on each arm.

Sixteen manuscripts came from an exact arXiv title match. Eight had no exact arXiv match and were downloaded from OpenReview via the box browser before scoring the remaining 8 papers per arm.

The decision intervals are the paired stratified bootstrap: one draw applied to both arms, the four fixed-eval tiers restored with replacement in sorted stratum order, 4000 resamples, seed `20261029`, linear percentiles 0.025 and 0.975 (`benchmarks/promotion/paired_bootstrap.py`). All 4000 resamples were defined on every primary comparison below. A higher-is-better metric is worse beyond the interval when the whole interval is below zero. MAE is worse beyond the interval when the whole interval is above zero. An interval that contains zero (including an endpoint of zero) is unchanged for this rule.

## Primary rank metrics

Point estimates on all 24 papers (8 accepts and 16 rejects). Bias is `rank_score` minus the mean official rating.

| Arm | Spearman | Kendall tau-b | Accept/reject AUC | MAE | Bias |
| --- | ---: | ---: | ---: | ---: | ---: |
| candidate | 0.6168 | 0.4371 | 1.0000 | 1.1033 | -0.7422 |
| main | 0.6282 | 0.4871 | 1.0000 | 1.1112 | -0.7737 |

Paired differences, candidate minus main.

| Metric | difference | 95% interval | worse beyond interval |
| --- | ---: | --- | --- |
| Spearman | -0.0114 | [-0.1021, 0.0680] | no |
| Kendall tau-b | -0.0499 | [-0.1485, 0.0449] | no |
| Accept/reject AUC | +0.0000 | [0.0000, 0.0000] | no |
| MAE | -0.0079 | [-0.0933, 0.0720] | no |

Every primary rank interval contains zero. No paper has the exact same `rank_score` on both arms. Twenty-three papers have the same venue bin.

## Venue-bin metrics

These are the locked calibration metrics on the 1–6 decision. Signed error is -0.9910 on the candidate and -1.0743 on main. Spread (standard deviation of the bin) is 1.7173 on the candidate and 1.8337 on main. Signed error, spread, and the confusion table are not primary.

Nearest official bin against our bin. Rows are the nearest human bin. Columns are our bin.

| Human bin | arm | 1 | 3 | 5 | 6 | 8 | 10 |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 3 | candidate | 0 | 5 | 1 | 0 | 0 | 0 |
| 3 | main | 1 | 4 | 1 | 0 | 0 | 0 |
| 5 | candidate | 0 | 6 | 1 | 0 | 0 | 0 |
| 5 | main | 0 | 6 | 1 | 0 | 0 | 0 |
| 6 | candidate | 0 | 1 | 1 | 3 | 0 | 0 |
| 6 | main | 0 | 1 | 1 | 3 | 0 | 0 |
| 8 | candidate | 0 | 1 | 0 | 3 | 2 | 0 |
| 8 | main | 0 | 1 | 0 | 3 | 2 | 0 |

| Arm | Spearman | Kendall tau-b | Accept/reject AUC | MAE | Bin agreement | Snap bin agreement | Reviewer bin agreement |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| candidate | 0.6909 | 0.5842 | 1.0000 | 1.1993 | 0.4583 | 0.5833 | 0.4424 |
| main | 0.7173 | 0.6101 | 1.0000 | 1.2826 | 0.4167 | 0.5417 | 0.4007 |

Paired differences, candidate minus main.

| Metric | difference | 95% interval | worse beyond interval |
| --- | ---: | --- | --- |
| Spearman | -0.0265 | [-0.0934, 0.0000] | no |
| Kendall tau-b | -0.0259 | [-0.0849, 0.0000] | no |
| Accept/reject AUC | +0.0000 | [0.0000, 0.0000] | no |
| MAE | -0.0833 | [-0.2500, 0.0000] | no |
| Bin agreement (nearest) | +0.0417 | [0.0000, 0.1250] | no |
| Snap bin agreement | +0.0417 | [0.0000, 0.1250] | no |
| Reviewer bin agreement | +0.0417 | [0.0000, 0.1250] | no |

Every venue-bin interval contains zero (no interval lies entirely on the worse side).

## Per tier

Human means are the mean official rating on that tier of 6. Adjacent AUC treats the higher tier as the positive class and is not a primary metric.

| Tier | human mean | candidate mean (bias, MAE) | main mean (bias, MAE) |
| --- | ---: | --- | --- |
| clear reject | 3.333 | 3.834 (+0.5011, 0.752) | 3.817 (+0.4839, 0.698) |
| borderline reject | 4.681 | 3.958 (-0.7222, 0.722) | 3.930 (-0.7506, 0.751) |
| borderline accept | 5.908 | 5.097 (-0.8117, 1.003) | 5.022 (-0.8861, 1.054) |
| clear accept | 7.708 | 5.772 (-1.9361, 1.936) | 5.766 (-1.9422, 1.942) |

| Adjacent pair | candidate AUC | main AUC |
| --- | ---: | ---: |
| clear reject vs borderline reject | 0.417 | 0.417 |
| borderline reject vs borderline accept | 0.778 | 0.778 |
| borderline accept vs clear accept | 0.667 | 0.639 |

## Quote check and grounded fixes

These rates are not primary. Main's task text does not request a quote tag, so quote resolution is undefined on main. Both arms wrote 72 seats and 144 weaknesses (2 weaknesses per seat). On main, all 144 weaknesses are unquoted diagnoses. On the candidate, each seat wrote one unquoted diagnosis and one quoted weakness on the last weakness.

| Rate | candidate | main |
| --- | ---: | ---: |
| Unquoted diagnoses / weaknesses | 72/144 = 0.5000 | 144/144 = 1.0000 |
| Quoted weaknesses / weaknesses | 72/144 = 0.5000 | 0/144 = 0.0000 |
| Resolved quotes / quotes | 62/72 = 0.8611 | undefined |
| Unresolved quotes / quotes | 10/72 = 0.1389 | undefined |
| Resolved quotes / weaknesses | 62/144 = 0.4306 | 0/144 = 0.0000 |
| Seats whose last weakness quote resolves | 62/72 = 0.8611 | 0/72 = 0.0000 |

The paired difference for seats with a resolved last-weakness quote is +0.8611 [0.7778, 0.9444]. The paired difference for resolved quotes per weakness is +0.4306 [0.3889, 0.4722].

## Gate 3

On `pre-release` (`331f05648d0c`), from a clean `examples/synthetic/.runs`: `python -m autopaperreview validate examples/synthetic/review.toml` returned status `pass` (0 errors, 6 routes, 12 stages); the first `run` completed with all 12 stages `success`; the second `run` completed with all 12 stages `skipped` (full cache hit); `python -m autopaperreview schema` output matched `src/autopaperreview/schemas` with an empty diff; `python3 scripts/check_confidentiality.py` passed.

## Decision

**PASS** under `benchmarks/promotion/benchmark-v1.md` for this Gate 2 private-holdout comparison: no primary rank or venue-bin metric is worse beyond its paired 95% interval, candidate has 0 parse and 0 pipeline failures, and Gate 3 synthetic and schema checks pass on `331f05648d0c`. Combined with Gate 1 PASS (`benchmarks/promotion/reports/2026-11-25-gate1-grok192-candidate-vs-main.md`), `pre-release` is cleared for promotion to `main`.
