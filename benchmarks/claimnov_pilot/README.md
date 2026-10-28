# ClaimNov novelty pilot

This directory is the harness for a proposal-level novelty comparison with ClaimNov (arXiv 2610.36511, code at `emmatliu/claimnov`). It does not store proposals, abstracts, or review prose. Those stay in a gitignored directory.

## Sample

`sample_ids.json` is a seeded draw of 50 items from each of the four published sets (200 items). Seed `20261028`.

| Dataset | ClaimNov items | Strata | Stored id |
| --- | --- | --- | --- |
| disguised | source in the pool (`B_in`, label `non_novel`) and source removed (`B_out`, label `src_out`), then rung | condition, then rung | `seedN_rR_B_*` |
| claim_disputed | published profile rows whose decision is accept or reject | decision, largest remainder | `n2f_<index>` |
| later_realized | ideas in the published later-realized set that have a ClaimNov profile row | `bucket` on the published sort file | sha256 of the ClaimNov item id |
| hybrids | the 100 spliced proposals | uniform | sha256 of the ClaimNov item id |

Title-bearing ids are stored only as sha256. Rebuild the file from a local checkout:

```bash
PYTHONPATH=. python3 -m benchmarks.claimnov_pilot.catalog \
  --claimnov /path/to/claimnov \
  --output benchmarks/claimnov_pilot/sample_ids.json
```

## What APR runs

Each item is a title plus a short description (`prompt_packet` `input_mode = "proposal"`). Related work is `mode = "pool"`: the supplied prior-work file only, no arXiv, OpenAlex, or Semantic Scholar call. `cutoff_date` is `2026-03-01` (exclusive, the date ClaimNov uses). Ids in `blocked_ids` are removed and recorded on `target_leakage`. A row with a target leak is dropped from the rates. A disguise whose source stays in the pool (`B_in`) does not block that source. Block the source only when the protocol removed it (`B_out`). Block the future paper for a later-realized idea, and the anchor paper for a hybrid, when that id shows up in the pool.

The proposal verdict is an originality cutoff on the bands 2/4/6/8/10, or `missing_closest_neighbor`. The cutoff is chosen on one stratified half (seed `20261028`) by informedness, catch minus false-alarm rate. Ties keep the lower false-alarm rate, then the smaller cutoff. Catch and false alarms are reported on the other half, with a 4000-draw item bootstrap, percentile 95% interval, seed `20261028`. The sweep on that half is reported too. False alarms are later-realized items (`label = novel`). Source-removed disguises are `src_out`, not that false-alarm rate. Hybrid keep rate is one minus the rejection rate on the spliced items, which are not used to choose the cutoff.

Claim-level agreement aligns APR contribution strings to published claim annotations by token Jaccard of at least 0.2. `established` agrees with tag `overlap`. No alignable APR claims is an unusable profile.

## Run

```bash
PYTHONPATH=. python3 -m benchmarks.claimnov_pilot.run \
  --claimnov /path/to/claimnov \
  --workdir workdir/claimnov_pilot
```

The adapter reads `APR_MODEL_API_KEY`. The model string is `grok-4.7` and `APR_MODEL_BASE_URL` defaults to `https://api.x.ai/v1`.

The public ClaimNov checkout stores pointers for disguises, hybrids, and claim-disputed proposals, and pool files without abstracts. Filling abstracts uses `S2_API_KEY` (optional but the unauthenticated batch is easy to exhaust) and, for some submissions, `OPENREVIEW_USERNAME` and `OPENREVIEW_PASSWORD`. Rebuilding proposal text uses `ANT_API_KEY` or `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`. Rebuilt text does not match the published per-item rows, so those rows must not be paired with it.

## Published rows on this sample

`results/claimnov_on_sample.json` is ClaimNov and the published baselines on these 200 ids. `novel = false` is a rejection. Hybrid keep is `novel = true`. `test_half` is the stratified half from `metrics.split_rows` (seed `20261028`). No originality cutoff is fit, because those rows are already pass or fail. The file has rates and counts only.

```bash
PYTHONPATH=. python3 -m benchmarks.claimnov_pilot.published \
  --claimnov /path/to/claimnov \
  --sample benchmarks/claimnov_pilot/sample_ids.json \
  --output benchmarks/claimnov_pilot/results/claimnov_on_sample.json
```

## Status

`results/status.json` records that this pilot did not call the model. No APR comparison is stored, and no issue was opened.

`run` exits 3 when `APR_MODEL_API_KEY` is unset, 4 when the secret is set but proposal text or pool abstracts are missing, and 2 when both are present. This revision does not call the model on that last path. Rebuilt disguise, hybrid, or claim-disputed text must not be paired with the published rows.
