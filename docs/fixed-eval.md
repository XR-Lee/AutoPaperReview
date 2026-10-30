# Fixed evaluation set

One ICLR dev set and one CVPR dev registration. Rank metrics, the promotion benchmark, and regression tests use these lists. A new benchmark does not draw a fresh sample.

Status is **proposed**. The paper list in [benchmarks/fixed_eval/PROPOSAL.md](../benchmarks/fixed_eval/PROPOSAL.md) is open for review. Change `status` to `locked` only after that table is accepted, then recompute `content_sha256`. A run is allowed while the status is `proposed`. The output records the status. The published README rank stays the previous holdout number until a locked private-holdout run replaces it.

## ICLR tiers

The tier is the mean of the official ratings (1, 3, 5, 6, 8, 10), with at least two ratings. The venue decision is stored beside the tier and is not a second cut.

| Tier | Mean rating | Dev | Private holdout |
| --- | --- | ---: | ---: |
| clear reject | 0 ≤ mean < 4 | 12 | 3 |
| borderline reject | 4 ≤ mean < 5.5 | 12 | 3 |
| borderline accept | 5.5 ≤ mean < 7 | 12 | 3 |
| clear accept | 7 ≤ mean ≤ 10 | 12 | 3 |

4.0 is the top of the historical clear-reject stratum. 5.5 sits between the official borderline-reject rating (5) and the official borderline-accept rating (6). 7.0 sits between borderline accept (6) and accept (8). The top bound is inclusive.

Selection seed `20261030`. Inside a tier, forum ids are sorted and shuffled with `Random(f"fixed-eval-v1:{seed}:{tier}")`. The walk takes at most 4 papers from one primary area, then relaxes that cap if the quota is short. The dev quota is taken first. The private holdout is the next 3 papers in that same order. The parent pool is the materialized 93-paper sample (seed `20261021`) plus the README holdout-15 papers that were not already in it. That pool has 15 papers with mean below 4, so the holdout is 12 papers, not 16.

The dev set has 48 papers. The manifest digest is the SHA-256 of the manifest with `content_sha256` removed (`hash_json`). The id-list digest is the SHA-256 of the sorted forum ids plus a trailing newline, the same bytes as `cvpr_sets.id_list_digest`.

## Forecast-k1 and the rank report

A fixed-set model run uses the locked command:

```bash
--cheap --holistic-mode forecast --holistic-samples 1 --related-work off --coefficient-set apr-fusion-v1
```

The rank target is `rank_score`. The rank bootstrap is the paper-level linear percentile in `rank_metrics.py`: 4000 resamples, seed `20261021`, percentiles 0.025 and 0.975. The promotion contrast, once [PR #40](https://github.com/XR-Lee/AutoPaperReview/pull/40) is merged, is the paired stratified bootstrap in `benchmarks/promotion/paired_bootstrap.py` (4000 resamples, seed `20261029`) with these four tiers as strata.

`scripts/rank_eval.py` defaults to `--fixed-set iclr-dev`. It requires `--model-id` and `--effort` (short tokens of letters, digits, `.`, `_`, and `-`). It refuses a cache that does not contain every dev paper, and it refuses `--resamples` other than 4000. The gitignored JSON output records the model id, the effort, the manifest status, and the content digest. Stdout prints the same fields and does not print the paper list.

```bash
PYTHONPATH=src python3 scripts/rank_eval.py \
  --cache workdir/fixed-eval-cache \
  --output workdir/fixed-eval-cache/rank-metrics.json \
  --mode locked \
  --model-id grok-4.7 \
  --effort xhigh
```

`--fixed-set none` scores the cache as given. That is the path for an older full-cache comparison. It does not record a fixed-set digest.

`calibrate-eval run` still defaults to `--fixed-set none`, so an existing full-cache run does not change. Pass the fixed set explicitly:

```bash
autopaperreview calibrate-eval run \
  --cache workdir/fixed-eval-cache \
  --mode adapter \
  --fixed-set iclr-dev \
  --model-id grok-4.7 \
  --effort xhigh \
  --cheap \
  --holistic-mode forecast \
  --holistic-samples 1 \
  --related-work off \
  --coefficient-set apr-fusion-v1
```

Do not pass `--only-ids` with `--fixed-set iclr-dev`. The command writes `fixed-eval-run.json` in the cache with the model id, the effort, the status, and the content digest. A flag set other than forecast-k1 is refused before any paper is scored.

`promotion_iclr_ids()` returns this dev list. PR #40 (`cursor/promotion-benchmark-v1-9a3c`) is not merged and is not copied into this branch. After it lands, its ICLR arm should call that function instead of drawing again.

## Private holdout

The holdout is 12 papers, 3 in each tier, disjoint from the dev set. It is the slice for a release or a README number change. It is not the slice for an ordinary benchmark.

The committed manifest stores HMAC-SHA256 only:

- one HMAC per forum id, sorted
- one HMAC of the sorted ids joined by newlines, plus a trailing newline

The salt is at least 16 bytes, hex-encoded, and is not in the repository. Forum ids and titles are not in the committed holdout block. The operator file is `workdir/fixed-eval/private-holdout.json` (mode `0600`, gitignored). A copy for this environment is kept outside the checkout. Copy that file off the machine before the environment is destroyed. `assert_holdout_file` recomputes both HMACs and refuses a mismatch or any overlap with the dev set.

```bash
PYTHONPATH=src python3 -m autopaperreview.fixed_eval holdout-check workdir/fixed-eval/private-holdout.json
PYTHONPATH=src python3 scripts/rank_eval.py \
  --cache workdir/fixed-eval-holdout \
  --output workdir/fixed-eval-holdout/rank-metrics.json \
  --holdout-file workdir/fixed-eval/private-holdout.json \
  --model-id grok-4.7 \
  --effort xhigh
```

`--holdout-file` cannot be combined with `--ids`. Do not put the salt or the holdout ids in a pull request, a commit message, or the README.

## CVPR

S24-P and S24-D2 are registered in the same manifest and are not mixed with the ICLR papers. S24-P is the existing pilot of 50 papers. Its id-list digest is `81b04fa5031fcc594d0074702c8280d855f36c40835d05969205a26cdb04d29f`.

S24-D2 was drawn at 50 (seed `20261031`, quotas 15 oral / 15 highlight / 20 poster, excluding S24-P and sealed S24-C1). One paper, `cvpr2024-e71e5cd119bbc5797164fb0cd7fd94a4`, fails the A1/A2 body venue-marker scan and is excluded, leaving 49 valid papers. Those 49 ids are not in git. Reconstructing them would mean reading sealed S24-C1, which this set does not do. Until an id-list hash is added, a scored run is checked by exclusion and count: 49 ids, the excluded id absent, no overlap with S24-P. If a hash is set later, the run must match it too.

`calibrate-eval cvpr-tier fetch --set S24-P` must return the registered 50. A fetch of S24-D2 may still contain the excluded paper; the command then checks that the other ids are a valid 49. `cvpr-tier run --set S24-D2` refuses a sample manifest that still contains the excluded paper, so drop that paper from `sets/S24-D2/sample_manifest.json` before scoring. `run --set S24-C1` and `run --set S24-C2` are refused. Those sets stay sealed.

## What a mismatch does

`assert_manifest` refuses a digest, tier count, forecast-k1 flag, bootstrap block, or CVPR registration that does not match. `rank_eval` and `calibrate-eval run --fixed-set iclr-dev` call it before scoring. Exit code 2 means the run did not match. Do not edit the manifest to make a one-off sample pass.
