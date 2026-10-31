# Promotion benchmark v1

This file is the fixed benchmark for promoting `pre-release` to `main`. It was written before either arm was answered. The model, the inputs, the metrics, and the decision rule below are the rule for this comparison.

## Decision rule

Compare `pre-release` `3147dce790eb59fab137614fcbe878902d4d1ba5` with `main` `386566945bc45054e80f9720778cdb84000071ea`.

Both arms use the same model: **grok-4.7** at reasoning effort **xhigh**, through Cursor. The transport is the replay file-handoff. Each pending packet is answered in a fresh pass that does not read the other arm's answer. Papers are interleaved: the two arms of one paper are answered in the same step, before the next paper. No xAI API key and no other external model API is used.

**PASS** when all three of the following hold. Otherwise **FAIL**.

1. **No primary metric is worse beyond its paired 95% interval.** For each primary metric, draw a paired stratified bootstrap of `(pre-release − main)`. Higher-is-better metrics fail when the entire interval is below zero. Lower-is-better metrics (MAE only) fail when the entire interval is above zero. An interval that contains zero is unchanged for this rule, including when the point estimate moves the worse way inside the interval. A missing interval fails the metric.
2. **No new parse or pipeline failure.** A paper, variant, or check that fails on pre-release also fails on main. Pre-release's parse-failure count and pipeline-failure count are each at most main's.
3. **Synthetic review and schema checks pass on pre-release.** `autopaperreview validate` and two `run`s of `examples/synthetic/review.toml` succeed, the second run is a full cache hit, and `autopaperreview schema` matches `src/autopaperreview/schemas`.

Sealed sets `S24-C1` and `S24-C2` are out of this benchmark. They are not fetched, opened, or scored.

## Bootstrap

- Paired: one draw is applied to both arms.
- Stratified: inside each stratum, draw that stratum's own papers with replacement until the stratum size is restored. Stratum names are walked in sorted order.
- 4000 resamples, seed `20261029`.
- Interval: linear percentiles 0.025 and 0.975, the same percentile as `rank_metrics._linear_percentile`.
- Implementation: `benchmarks/promotion/paired_bootstrap.py`.

For a new ICLR comparison the strata are the four fixed-eval tiers (`clear_reject`, `borderline_reject`, `borderline_accept`, `clear_accept`). The earlier 37-paper subset used the five calibration strata (`clear_reject`, `low`, `borderline`, `high`, `strong`). CVPR strata are the program tiers (`oral`, `highlight`, `poster`). Stratum labels used for resampling stay in the gitignored cache. This file and the report do not attach a tier, a decision, or a rating to a paper id.

## Primary metrics

### ICLR, locked `forecast-k1`

The run is `calibrate-eval` with `--cheap --holistic-mode forecast --holistic-samples 1 --coefficient-set apr-fusion-v1 --related-work off`. The rank is `rank_score` (the clamped mean expected rating). The venue bin is the 1–6 decision from that same call, mapped by `decision_bin`.

Locked rank metrics (`rank_metrics.agreement` on `rank_score`):

| Metric | Direction |
| --- | --- |
| Spearman vs mean official rating | higher |
| Kendall tau-b vs mean official rating | higher |
| Accept/reject AUC | higher |
| MAE vs mean official rating | lower |

Locked calibration-report metrics (`public_calibration.summarize` on the venue bin):

| Metric | Direction |
| --- | --- |
| Spearman | higher |
| Kendall tau-b | higher |
| Accept/reject AUC | higher |
| MAE | lower |
| Bin agreement (nearest) | higher |
| Snap bin agreement | higher |
| Reviewer bin agreement | higher |

Signed error, bias, spread, and the confusion table are reported and are not primary. They have no monotone pass direction in this rule.

### CVPR S24-P, `forecast-k1`

Variants **A0** and **A1**. A1 is in scope because this pre-release accepts `--variant A1`. Each variant has its own primary block. The score is `forecast_mean`. Tier rank is poster = 1, highlight = 2, oral = 3. Macro-AUC is the unweighted mean of the three pairwise AUCs on the same rows. In a resample, macro-AUC is the mean of the three pairwise AUCs computed on that resample.

| Metric | Direction |
| --- | --- |
| Macro-AUC | higher |
| Oral vs poster AUC | higher |
| Highlight vs poster AUC | higher |
| Oral vs highlight AUC | higher |
| Spearman | higher |

Parse failures are the count of answers that still fail `parse_verdict` after the one allowed retry. They are not a scored metric. They are gate 2 above.

A leak-scan failure while preparing A1 is a pipeline failure for that variant. The anonymizer is part of the shared input when both refs emit the same manuscript bytes. If both refs fail the same scan, A1 is blocked for both arms and gate 2 records the shared failure. A scan that fails on only one ref fails the promotion.

## What reaches the model

For every benchmark paper and every mode below, both refs build the prompt that the model reads. The report records SHA-256 of those bytes and whether the two refs match.

| Track | Mode | Bytes that are hashed |
| --- | --- | --- |
| ICLR | forecast-k1, K=1 | Forecast system prompt, then the M0 prompt packet after `full_text_packet` (cap 240000) joined to `forecast_task_text()` |
| CVPR A0 | forecast-k1, K=1 | `build_prompt` on the banner-stripped manuscript, one sample |
| CVPR A1 | forecast-k1, K=1 | `build_prompt` on the A1 anonymized manuscript, one sample |

Temperature in the locked chat body is 0.7. Cursor grok-4.7 xhigh does not take that temperature knob. Both arms use the same Cursor effort, so the knob is shared and is not a branch difference. The report says so.

Parsing and scoring compared across the refs: `ForecastJudgment` / `parse_verdict`, `rating` fusion for `forecast-k1`, `public_calibration.summarize`, `rank_metrics.agreement`, and `cvpr_tier` Spearman / pairwise AUC. The report states whether those paths match.

## ICLR arm

The current ICLR arm is locked. It is the fixed dev set in `benchmarks/fixed_eval/manifest.json`. Status is **locked**. `content_sha256` is `3dadef447e93452fc3d21ad0ff6c51f3bf042e93ec233fd01a4c75691dc51440`.

**Grok** is the full-set benchmark: **192 dev papers, 48 in each mean-rating tier** (`clear_reject`, `borderline_reject`, `borderline_accept`, `clear_accept`), plus the private HMAC holdout of **24 papers, 6 per tier**. Do not draw a fresh subset. `promotion_iclr_ids()` returns the dev list. The paired bootstrap strata for a new ICLR comparison are these four tiers.

## Opus validation

Opus is the screen before a push to `pre-release`. It is not a substitute for the Grok benchmark, and it is not the private holdout. This section is the official rule.

- The baseline arm runs Opus once on the fixed 36-per-tier slice (144 papers) and is cached.
- Each candidate check runs only the candidate arm on that slice, at about $95, under a $100 cap per validation.
- If both arms must run, use the first 18 per tier (72 papers).
- The slice is defined in the manifest. Seed `20261102`. Inside each tier, sort forum ids and shuffle with `Random(f"opus-validation:20261102:{tier}")`. `opus_validation.paper_ids_by_tier` keeps that shuffle order. The standing slice is the first 36 ids. The both-arms slice is the first 18 ids of the same list.
- The baseline cache is valid only while the main commit, the model id and effort, and the scoring path match. Refresh the cache when any of those change. The scoring path is the forecast-k1 command and the code that builds the packet.

Refuse a validation before any call when `papers × arms × 0.66` is over $100. The cost table is in `benchmarks/fixed_eval/OPUS_VALIDATION.md`.

The earlier subset below is the draw this file originally specified. It is not the arm for a new promotion.

Source: the existing public ICLR 2024/2025 calibration sample from `scripts/select_public_calibration.py`.

- Sample seed `20261021`, quotas 20 / 15 / 25 / 20 / 20, tune fraction 0.6 (`benchmark_select.assign_split`).
- The promotion subset is drawn only from that holdout.
- Subset seed `20261029`. Quotas, scaled from the locked quotas by the holdout fraction: `clear_reject` 8, `low` 6, `borderline` 10, `high` 8, `strong` 8 (40 papers).
- Inside a stratum, sort by forum id, shuffle with `Random(f"promotion-v1:20261029:{stratum}")`, and take the quota. A short stratum keeps every holdout paper in it. The manifest records the shortfall counts.

`manifest.json` lists the chosen forum ids and nothing else about those papers. Manuscripts, titles, official ratings, and decisions stay in the gitignored cache (`workdir/`).

## CVPR subset

Set **S24-P** only (role dev, seed `20261029`, quotas 13 oral / 17 highlight / 20 poster). Membership is the pilot id list. Its SHA-256 is `81b04fa5031fcc594d0074702c8280d855f36c40835d05969205a26cdb04d29f`, the same digest as `benchmarks/cvpr_tier/sets.json`.

PDFs and extracted text are fetched with `calibrate-eval cvpr-tier` into the gitignored cache. `prepare` writes A0 and A1. Replay writes `prompt.txt` outside the cache and waits for `answer.json`.

## Synthetic review and schema

On pre-release, from a clean `examples/synthetic/.runs`:

1. `python -m autopaperreview validate examples/synthetic/review.toml`
2. `python -m autopaperreview run examples/synthetic/review.toml` twice
3. The second manifest's stages are `skipped` cache hits
4. `python -m autopaperreview schema` matches `src/autopaperreview/schemas`

## Answers

One call is one answer file for one packet. A parse failure may be retried once; the retry is a second call. The report's call count is the number of answer files written, including retries.

ICLR has no replay backend. That arm still uses file handoff: each ref's `calibrate-eval run` calls `OpenAICompatibleAdapter` on a loopback server that returns the answer file for the request body that ref just built. The server has no API key. CVPR uses `--backend replay`.

## Report

Aggregates only, in `benchmarks/promotion/reports/2026-10-29-pre-release-3147dce.md`: the metrics above, the paired intervals, pass/fail, timing, prompt-identity counts, and call counts. No paper id, title, excerpt, or per-paper score goes in that report.
