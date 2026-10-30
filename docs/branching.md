# Branching

`pre-release` is the integration branch. `main` is the stable branch.

## Pull requests

All pull requests target `pre-release`. Merge a pull request into `pre-release` only when CI is fully green.

Dependabot opens dependency updates against `pre-release`.

## Promotions into main

`main` only takes promotions from `pre-release`. A promotion is a pull request whose head ref is `pre-release` and whose head repository is this repository. The `main-source-guard` job enforces that source on pull requests that target `main`. A pull request that targets any other base passes that job without a source check.

A promotion is allowed only when a regression report shows that `pre-release` scores higher than or equal to `main` on the same fixed benchmark. Both branches run with the same model, grok-4.7 at reasoning_effort `xhigh`, via Cursor.

## Fixed benchmark

The regression report uses the fixed ICLR dev set in `benchmarks/fixed_eval/manifest.json` (protocol `fixed-eval-v1`). Status is `proposed` until that list is accepted. Both branches score it with `scripts/rank_eval.py`, which defaults to this set and records the model id and the effort. The CVPR arms are the registered S24-P and S24-D2 dev sets in the same manifest. They are not mixed with the ICLR papers. Sealed sets S24-C1 and S24-C2 stay closed. See [fixed evaluation set](fixed-eval.md). The promotion harness in PR #40 (`cursor/promotion-benchmark-v1-9a3c`) should read these ICLR ids from `promotion_iclr_ids()` once it lands. This branch does not copy that harness.
