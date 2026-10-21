# Public calibration eval

`apr-iclr-cap-v2` is a declared formula. The venue bin is the largest of 1/3/5/6/8/10 that does not exceed the capped raw score. This command measures how far that formula sits from public human scores. It does not fit weights, caps, or bins, and it does not write the metrics back into the rating code.

The comparison is not a claim about any hosted reviewer's published correlation.

## What is measured

For each public submission the tool stores official ratings separately from the manuscript, runs prompt packets, then `execute_review` and `rate`.

| Metric | Definition |
| --- | --- |
| Spearman | Rank correlation between the venue bin and the mean official rating |
| MAE | Mean absolute error of the venue bin against that mean |
| Bin agreement | Fraction of papers whose bin equals the nearest bin to the mean on 1/3/5/6/8/10. A distance tie stays on the lower bin |
| Snap bin agreement | Same fraction using `snap_down` on the mean |
| Reviewer bin agreement | Average fraction of official ratings equal to our bin |
| Accept/reject AUC | Mann-Whitney AUC of the venue bin, accept versus reject |
| Score spread | Sample standard deviation, range, and the set of bins. `clustered` is a diagnostic when fewer than 3 bins appear or the standard deviation is below 1 |

Official ratings are the latest note per reviewer signature on the public review invitation, parsed only when the leading number is 1, 3, 5, 6, 8, or 10. A paper needs two such ratings and an accept or reject decision. Human ratings are not copied into the prompt packets.

## Cache

Fetch, prepare, and the report write only under a gitignored directory or a path outside the checkout. The default place is `workdir/calibration-eval/`. Forum ids, PDFs, extracted text, reviews, judgments, and `calibration_report.md` stay there. Do not commit them.

`heldout/human.json` is the only file with official ratings. The judgment step should not open it.

## Commands

```bash
PYTHONPATH=src python3 -m autopaperreview calibrate-eval list \
  --listing "ICLR 2024 poster" --limit 20

PYTHONPATH=src python3 -m autopaperreview calibrate-eval fetch \
  --manifest workdir/calibration-manifest.json \
  --cache workdir/calibration-eval

PYTHONPATH=src python3 -m autopaperreview calibrate-eval prepare \
  --cache workdir/calibration-eval

PYTHONPATH=src python3 -m autopaperreview calibrate-eval run \
  --cache workdir/calibration-eval --mode agent

PYTHONPATH=src python3 -m autopaperreview calibrate-eval report \
  --cache workdir/calibration-eval \
  --output workdir/calibration-eval/calibration_report.md
```

The manifest is a local JSON or TOML file:

```json
{
  "papers": [
    {"forum_id": "FORUM_ID", "decision": "accept", "listing": "ICLR 2024 poster"}
  ]
}
```

`decision` is optional when the public note already says accept or reject. `listing` is one of the built-in ICLR 2024/2025 oral, spotlight, poster, or rejected labels. It limits the search used when the direct notes route asks for a browser check.

## Judgment

Agent mode is the default. `prepare` writes prompt packets for routes M0, M2, M5, and M8. `run` then waits until `project/judgment.json` exists. The drop is a legacy review JSON or a canonical review package, including all seven `sar_bands_v1` dimension scores. It must include `packet_sha256`, mapping each route id to the digest in `packet_index.json`. An overall score in the drop is discarded. `rate` assigns the venue bin.

The generated run places `related_work_search` before a consensus stage, and `rate` depends on that consensus. `--related-work` defaults to `off`, which builds the queries locally and does not apply a retrieval cap. `--related-work agent` reads `project/related_work_findings.json`. `--related-work live` calls the metadata providers and needs `--network-policy metadata_only` or `allow`. A file named `project/related_work_confirmation.json` is used when it is present. This command does not change the declared caps or snap rules.

Exit code 3 means at least one paper is still waiting for that drop.

Adapter mode runs a declared `module:attribute` review adapter. The bundled chat adapter is `autopaperreview.model_exec:OpenAICompatibleAdapter`. It needs `--base-url` and `--model`. Loopback is allowed while the project network policy stays `deny`. A remote endpoint also needs `--allow-remote --requires-network --network-policy allow`. When that adapter is selected and related-work mode is `agent` or `live`, the same endpoint confirms must-compare candidates.

```bash
PYTHONPATH=src python3 -m autopaperreview calibrate-eval run \
  --cache workdir/calibration-eval \
  --mode adapter \
  --adapter autopaperreview.model_exec:OpenAICompatibleAdapter \
  --base-url http://127.0.0.1:8000/v1 \
  --model local-reviewer

PYTHONPATH=src python3 -m autopaperreview calibrate-eval run \
  --cache workdir/calibration-eval \
  --mode adapter \
  --adapter some.module:ReviewAdapter
```

A missing adapter is an error. If that adapter calls a network API, pass `--requires-network --network-policy allow`.

## PDFs

The tool requests the public PDF. If that route answers with a browser check, `--pdf-fallback arxiv` (the default) downloads an arXiv PDF only when the title matches exactly. `--pdf-fallback none` leaves the paper unscored. The report records which source supplied the bytes. Text extraction uses `pdftotext`, `pypdfium2`, or `pypdf`, whichever is installed.

## Benchmark sample

`scripts/select_public_calibration.py` draws a 100-paper sample from the public ICLR 2024 and ICLR 2025 listings. The seed is `20261021`. A paper needs two official ratings on 1/3/5/6/8/10 and an accept or reject decision. Strata are the mean of those ratings:

| Stratum | Mean | Quota |
| --- | --- | --- |
| clear_reject | below 4 | 20 |
| low | 4 up to but not including 5 | 15 |
| borderline | 5 up to but not including 6.5 | 25 |
| high | 6.5 up to but not including 8 | 20 |
| strong | 8 and above | 20 |

Inside a stratum the walk alternates years and round-robins primary areas, taking at most 4 papers from one area before relaxing that cap. Each area-year group is sorted by forum id and shuffled with `Random(seed:stratum:area:year)`. After the PDFs are in, a second shuffle (`Random(seed:split:stratum)`) holds out 40 percent inside each stratum. The tuning split is not chosen from model scores.

The script writes the manifest, the split, the PDFs, and the ratings under the cache directory only. The direct notes route is not required. Search pages the public listings. When the venue PDF route asks for a browser check, the script uses the arXiv title fallback. Listing fetches honour `Retry-After` on HTTP 429 and cap the backoff at 8 seconds. PDF downloads use a 45-second socket timeout.

Rejected submissions are often absent from arXiv, so a stratum can stay short after the first PDF walk. `--extend-rejected N` scores N further rejected submissions that are not in the scored pool yet. The shuffle is `Random(f"{seed}:extend-rejected")`, and `fill_to_surplus` is off for that pass so the extra papers are actually fetched. The run that filled every quota used two extra passes, `--extend-rejected 280` and then `--extend-rejected 220`, each followed by materialize. Those counts are part of the selection procedure. They were not chosen from model scores.

```bash
PYTHONPATH=src python3 scripts/select_public_calibration.py --cache workdir/calibration-bench
```

The report metrics include Kendall tau-b and a confusion table of the nearest human bin against the venue bin. Those numbers still do not update the formula.

## Tests

`examples/synthetic/calibration/` is an invented manuscript and invented venue notes. It is not a public submission. The unit test drives fetch, prepare, the agent drop, `rate`, and the report through a fake client.
