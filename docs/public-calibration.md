# Public calibration eval

`apr-iclr-cap-v2` is a declared formula. The venue bin is the largest of 1/3/5/6/8/10 that does not exceed the capped raw score. This command measures how far that formula sits from public human scores. It does not fit weights, caps, or bins, and it does not write the metrics back into the rating code.

The comparison is not a claim about any hosted reviewer's published correlation.

## Locked fusion candidate

`apr-fusion-v1` ships one preregistered rank, `forecast-k1`. One evaluation is one `forecast` call on the full manuscript packet (`model_exec.full_text_packet`; the `## Source Text` section is capped at `anchors.FULL_TEXT_MAX_CHARS` characters and the cut kind is recorded in `main_text`). The call simulates the three reviewers of `anchors.FORECAST_SEATS` (specialist, generalist, senior, each defined in the prompt by what that reviewer weighs). For each reviewer the model writes a one-sentence `summary`, up to two `strengths` and `weaknesses`, the official `rating` that reviewer would most likely submit (1, 3, 5, 6, 8, 10), six `probabilities` (that the reviewer's official rating is 1, 3, 5, 6, 8, 10, in that order, summing to one) and a `confidence` 1–5; it then writes its own `predicted_mean` on 1.0–10.0 and the 1–6 decision, on the same scale text as the other modes. Each distribution is normalised (a sum away from one is counted as `renormalized`, a dictionary keyed by rating is reordered) and its expected rating is `sum(p_i * r_i)` (`anchors.expected_rating`). The per-call sample is the mean of the three expected ratings (`anchors.forecast_mean`), the rank is that sample clamped to 1.0–10.0, and the decision sets the ICLR bin. The mean of the three integer ratings is `vote-k*`, the model's own mean is `chair-k*`, and the per-call highest and lowest expected rating are `high-k*` and `low-k*`; all four are diagnostic. An integer off the official list is counted as `off_scale` and an integer that is not the rating with the most mass as `mode_mismatch`; neither is retried. Nothing in this mode is verified against the manuscript, and no call is conditioned on another. The temperature is 0.7.

The previous candidate `matched-k2` (`--holistic-mode matched --holistic-samples 2`) is two `matched` calls that run together and do not see each other. Each call sees the manuscript's main text: the `## Source Text` section of the prompt packet is cut at the first standalone reference-list heading found at or after `anchors.MAIN_TEXT_MIN_SHARE` of the text and capped at `anchors.MAIN_TEXT_MAX_CHARS` characters, a marker line says where the cut happened, and the other packet sections are kept (`model_exec.main_text_packet`). The call simulates the review panel the venue would assign: four topic-matched reviewers of mixed seniority in the declared seats of `anchors.MATCHED_SEATS` (insider, junior, neighbour, senior), each defined in the prompt by what that reviewer weighs, with the instruction that they read the main text once, that a detail deferred to the appendix is not missing, and that they rate by the venue's norms, which the prompt states in words (about one submission in three accepted; how often each official rating is given; what an accepted and a rejected paper's ratings usually look like; not a quota for one paper). For each reviewer the model writes a one-sentence `summary`, up to two `strengths` and `weaknesses`, the official `rating` that reviewer would submit (the venue's choices 1, 3, 5, 6, 8, 10, with what each means at the venue stated in the prompt; an integer off that list is kept in the mean and counted as `off_scale` rather than retried), and a `confidence` 1–5. It then writes a short `discussion`, its own `predicted_mean` on 1.0–10.0, and the 1–6 decision. The scale and decision text is the same as the `panel`, `expert`, `mean` and `audited` modes. The per-call panel mean is the mean of the four ratings (`anchors.panel_mean`), and the rank is the mean of the first two panel means. The decision sets the ICLR bin and is not the rank. The model's own mean is stored as `chair-k*`, and the per-call highest and lowest rating as `high-k*` and `low-k*`; all three are diagnostic. Nothing in this mode is verified against the manuscript, and no call is conditioned on another. The temperature is 0.7.

`holistic_mode` is a declared switch. `forecast` is the locked mode (`LOCKED_HOLISTIC_MODE`) with K=1 (`LOCKED_HOLISTIC_SAMPLES`); `--holistic-samples 2` runs two concurrent forecast calls and still ranks by the first as `forecast-k1`. `rebuttal` keeps the previous candidate `rebuttal-k2`: K simulated three-reviewer reviews of the full manuscript (`anchors.REBUTTAL_SEATS`: specialist, generalist, senior) carried through the authors' response, ranked by the mean official rating held at the end of the discussion; with `--holistic-samples 2` that is `rebuttal-k2`. `matched` keeps `matched-k2`: K topic-matched panels on the main text as described above; with `--holistic-samples 2` that is `matched-k2`, and `--holistic-samples 1` ranks by the single panel as `matched`. `panel` keeps the candidate before that, `panel-k2`: K mixed-role panels (`anchors.PANEL_ROLES`: specialist, generalist, methodologist, senior) on the full packet; with `--holistic-samples 2` that is `panel-k2`. `expert` keeps `expert-k2`: K expert reads that each name the contribution type, the recalled prior work and the field context, fill the review-form triad (`form-k*`, diagnostic) and then estimate the mean; with `--holistic-samples 2` that is `expert-k2`. `audited` keeps `audited-k1`: one `audit` call answers twelve fixed questions (`anchors.AUDIT_CHECKS`) with pass, fail, or unclear and lists at most four flaws, every pass, fail, and flaw carries an excerpt that must resolve in the manuscript (an unresolved pass or fail becomes unclear, an unresolved flaw is dropped, both counted in `drop_counts`), and one `holistic` call then scores beside the verified audit; the verified pass count is `checks-k1`. `mean` keeps the candidate before that: K venue-mean calls that run together and are averaged; with `--holistic-samples 3` that is `mean-k3`. The prompt text and the packet of those six modes are unchanged. A package with only two simulated discussions still ranks by `rebuttal-k2`, one with only two matched panels by `matched-k2`, one with only two mixed-role panels by `panel-k2`, one with only two expert reads by `expert-k2`, one with only an audited mean by `audited-k1`, and one with three plain venue means by `mean-k3`.

Do not replace `forecast-k1` with another candidate after seeing tune or held-out metrics. `scripts/rank_eval.py` defaults to `--mode locked`, which reports the shipped rank. Dimension bands stay on the package and stay off this rank. A package that has only an older isolated pair, a ladder placement, a shorter venue mean, or a decision still ranks by that older signal. `our_score.json` records `forecast_samples`, `vote_samples`, `chair_samples`, `high_samples`, `low_samples`, a `forecast` summary that holds, per call, the seat id, integer rating, confidence, expected rating, peak probability and the six normalised probabilities of each review and the panel numbers only, and `main_text` (source characters, shown characters, cut kind); the review text stays in the run directory. The rebuttal fields (`rebuttal_samples`, `rebuttal`), the matched fields (`matched_samples`, `matched`), the panel fields (`panel_samples`, `panel`), the expert fields (`expert_samples`, `form_samples`, `expert`) and the audited fields (`audited_samples`, `checks_samples`, `audit`) are still written when those modes run.

```bash
PYTHONPATH=src python3 -m autopaperreview calibrate-eval run \
  --cache workdir/calibration-eval --mode adapter \
  --adapter autopaperreview.model_exec:OpenAICompatibleAdapter \
  --base-url http://127.0.0.1:8000/v1 --model local-reviewer \
  --coefficient-set apr-fusion-v1 --cheap --holistic-mode forecast --holistic-samples 1
```

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
