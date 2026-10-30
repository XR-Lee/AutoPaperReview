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

## Fixed dev set

`benchmarks/fixed_eval/manifest.json` is the proposed ICLR dev set (48 papers, 12 in each mean-rating tier) and the CVPR registration for S24-P and S24-D2. Status is `proposed`. `scripts/rank_eval.py` defaults to this ICLR set and requires `--model-id` and `--effort`. `calibrate-eval run` stays on the whole cache unless you pass `--fixed-set iclr-dev`. The private holdout is not in git. A dev-set number does not replace the published README rank. See [fixed evaluation set](fixed-eval.md).

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

## CVPR accepted-tier calibration

`calibrate-eval cvpr-tier` asks whether a review mode's existing rank separates CVPR oral, highlight, and poster papers. The sample is accepted papers only. The loop parses the mode's own rank (`forecast_mean`, `panel_mean`, the rebuttal final-rating mean, or `predicted_mean`) and a `tier` label the model adds to that JSON (`reject`, `poster`, `highlight`, `oral`, ordered reject < poster < highlight < oral). It does not fit a cutoff, and it does not write a weight back into `rate`.

The ordinal Spearman uses poster = 1, highlight = 2, oral = 3. Pairwise AUCs treat the higher tier as the positive class: oral versus poster, highlight versus poster, and oral versus highlight. The primary metric is macro-AUC, the unweighted mean of those three AUCs. Cpop uses the 2024 weights 0.211, 0.760, and 0.030. AUC oral-versus-rest treats oral as the positive class. Its 2024 weighted form is `(324 * AUC oral-versus-highlight + 2302 * AUC oral-versus-poster) / 2626`. Horvitz–Thompson weights are the public population of a year and tier divided by the number of sampled papers in that cell (2024 oral 90, highlight 324, poster 2302; 2025 oral 95, non-oral 2776). The report also writes quadratic-weighted kappa, DeLong tests (single and paired) with Holm and Benjamini–Hochberg adjustments on the three pairwise p-values, year-by-tier stratified bootstrap intervals, a recognised/unrecognised split when probes exist, a tier confusion matrix, per-tier precision and recall, a score-spread diagnostic (clustered when the coarse unique values are under 3 or the sample standard deviation is under 1), and paper-level bootstrap 95% intervals (4000 resamples, seed `20261029`). An analysis CSV is written beside the markdown. The 2025 oral-versus-rest number is an optional contrast and stays empty when the sample has no 2025 non-oral paper.

### What the public sources label

Counts below were read from the public virtual JSON on 2026-10-29. They are program totals. The committed fixtures are synthetic and are not these papers.

| Year | Source | What is labeled | Papers |
| --- | --- | --- | --- |
| 2024 | `cvpr-2024-orals-posters.json` field `decision`, collapsed on `uid`. Missing Open Access URLs are filled by an exact title join to `openaccess.thecvf.com/CVPR2024?day=all` | oral, highlight, poster | oral 90, highlight 324, poster 2302 (2716 papers). 2715 have a CVF Open Access PDF. One paper has no exact title match and is left out of the sample |
| 2025 | `cvpr-2025-orals-posters.json` field `eventtype`. `decision` is empty. An oral paper also has a poster slot | oral only | 95 oral papers. The other 2776 papers stay unlabeled, so they are not drawn as posters. Highlight is not a field in this file |
| 2023 | `cvpr-2023-orals-posters.json` | no oral or highlight split | every row is Accept and Poster. The year is parsed and not sampled |
| 2026 | the static export is one page (`count` 5163, 200 rows, `next` set) | not a sampling frame | the following API page returned 403. A partial page contributes no papers |

The CVPR 2025 program guide states 2,878 accepted papers, 96 orals, and 387 highlights. A two-column extract does not attach those marks to papers reliably, so the guide is not a label source. OpenReview notes for the main conference are not used. Default years are 2024 and 2025. Highlight and poster draws come from 2024. The 2025 orals can enter the oral stratum.

Tier labels also reflect area-chair and program-chair presentation decisions. This loop cannot measure accept versus reject.

### Sample, labels, and cache

The draw keeps papers whose tier is oral, highlight, or poster and whose PDF is on CVF Open Access (or `fixture://` in the synthetic fixture). Each tier is shuffled with `Random(f"{seed}:{tier}")`. The default is 20 papers per tier and seed `20261029`. A short stratum keeps every available paper. The manifest records available, requested, and drawn counts.

Per-paper tiers are written only to `heldout/tiers.json`. `prepare` and `run` refuse to open that file, `catalog.json`, `confirmation/`, `probe_keys/`, `meta/`, and a custody directory recorded for the cache. The sample manifest has no per-paper tier. PDFs and extracted text stay under the cache. Replay prompts and answers go to a handoff directory outside the cache and outside the checkout. The cache and the report must be a gitignored directory (the usual place is `workdir/cvpr-tier/`) or a path outside the checkout.

`cvpr2025` and `cvpr2026` are venue profiles for format and deadlines. CVPR main text is 8 pages excluding references, with no total-page cap. The profiles do not map a score onto a tier.

### Models

The model id is a free-form label of letters, digits, `.`, `_`, and `-`, up to 64 characters. `grok-4.7-cursor`, `opus-local`, and `astra-local` are ordinary labels. With no `--variant` and no `--round`, each label has its own directory under `runs/<model-id>/`. Setting either flag uses `runs/<model-id>/<variant or default>/<round or r0>/`. A registered set still lives under `sets/<set-id>/`. The report scores every run on the papers that run parsed, compares runs on the papers they both scored, and pairs variants of the same model and round.

Grok, Opus, and Astra run as local CLIs through `--backend command`. There is no API adapter and no API key. `--backend adapter` is refused. Replay remains available for a file handoff: the command writes `<handoff-id>/prompt.txt` and `packet.json` under a directory outside the cache, then waits. The directory name is 12 hex characters from HMAC-SHA256 of the catalog id. The salt stays in the cache (`handoff_salt`, mode 0600) or in `APR_CVPR_HANDOFF_SALT` and is not copied into the packet. A returned answer is checked against the packet hash and against `canaries.txt`. Exit code 3 means at least one answer is still missing. `--repeats N` (command backend, 1 to 10) stores every parsed score and the report gives Shrout–Fleiss ICC(2,1) and ICC(3,1). `--round` appends that candidate to `rounds.jsonl`. The report lists every line in that file.

The command backend passes the prompt on stdin. `{prompt_file}` still writes the prompt beside the process, outside the empty working directory. `{paper_id}` and `{handoff_id}` are the opaque id, not the catalog id. The process starts in a fresh empty temporary directory with a scrubbed environment. The run records the CLI version (`argv0 --version`), flags, prompt characters, estimated tokens (characters divided by 4), latency, and a `usage` object when the CLI JSON includes one. A timeout or a schema failure is retried once. `usage.json` and `failures.jsonl` live under that model run.

`cvpr-tier probe` runs three contamination templates. P-A shows an abstract and asks for the title, authors, venue, year, and tier. P-T shows a title and asks for the authors, venue, year, and tier. P-D asks four questions, each with one verbatim passage and three paraphrases. When `--passages` is omitted, P-D takes one sentence from the manuscript and builds three paraphrases locally. A batch mixes sample targets, optional arXiv controls, synthetic decoys, and other-year items, then shuffles them under opaque handoff ids. The default batch size is 10. Every item is placed in some batch. Answers are written after each batch, and a later run skips a batch whose stored prompt digest and seed still match. Scoring is title token-F1, surname overlap (first, last, or at least two), venue/year, DE-COP accuracy, and a tier-QA map (oral 3, highlight or spotlight 2, poster 1, unknown 2, reject 0). A paper is recognised when the abstract probe hits the title, either metadata probe hits an author, or at least 3 of 4 verbatim choices are right. Probe output is `probes/<model>/`. It does not contain program tiers. `probe_keys/` holds the operator answer key and is hidden from the model. The report joins tiers when it builds the recognised and unrecognised tables.

An abstract heading is accepted in either case, on a shared line with the other column, or inline as `Abstract.` or `Abstract—`. A layout extract is tried before a raw extract. Papers that still yield no abstract are listed by id in the probe output and in `sample_manifest.json`. The probe does not call the model when that share is above 5%.

`cvpr-tier controls` fetches arXiv metadata for a category whose first version falls in a date window (default `cs.CV` from 2026-07-15). That command is for a real run. The unit tests parse a fixture feed and do not call the network. `cvpr-tier decoys` writes plausible stand-in records with no network. Titles are recombined from a caller-supplied list or drawn from a fixed lexicon, then rejected when they match a catalog title or a hash in `title_digests.json`. `list` writes that digest file and does not store the titles in it. `cvpr-tier round` appends a candidate line before or after a run.

### Sets, anonymization, and the lock

`benchmarks/cvpr_tier/sets.json` registers `S24-P` (pilot 50, dev), `S24-C1` (existing 120, sealed), `S24-D2` (15/15/20, dev, excluding P and C1), and `S24-C2` (30/40/50, sealed, excluding P, C1, and D2). Membership that already exists is stored as an id-list hash. `S24-D2` and `S24-C2` are not drawn in this change, so their hashes are empty. `fetch --set` refuses a sealed or opened set. A dev fetch filters to the set's year, applies the exclusion lists, and writes `sets/<id>/`.

`prepare --variant A1` writes `manuscripts/A1/`. It removes the title, authors, affiliations, emails, footnotes, acknowledgements, URLs, and the multi-line CVF watermark. Acknowledgement and funding headings (singular or plural, numbered or inline) are removed through the references heading. Title 4-grams are masked after line breaks, hyphenation, ligatures, and case are normalized. A residual acknowledgement heading or title 4-gram is a leak count. `A2` also masks method names. `meta/` keeps the title and authors for anonymization checks and is not a prompt. The leak scan writes counts only. A1 and A2 fail closed when any count is above zero. Omitting `--variant` keeps the historical one-line banner strip at `manuscripts/<paper-id>.txt`.

`prepare` also saves per-paper category counts in the `papers` mapping of `manuscripts/<variant>/leak_counts.json`, including when the scan fails. For A1/A2, `run` reads that mapping before building prompts, reusing answers, or exporting replay packets. Each failed manuscript gets a `parsed/<handoff-id>.json` record with `status: "skipped"`, `reason: "leak_failed"`, paper id, variant, and positive `leak_counts`; it has no score or predicted tier. The run result lists skipped records, usage and rounds count them separately, and reports count them outside scoring and the scored-only analysis CSV. Skips use no model calls or estimated tokens. Missing or invalid per-paper counts fail closed: rerun `prepare` with the same cache, set (if any), and variant to upgrade an older aggregate-only cache. A0 and the no-variant path do not consult these counts.

`cvpr-tier lock` writes `lock.json` (protocol `cvpr-tier-lock-v1`): analysis defaults, model transports, task hashes, and set hashes. It does not contain catalog ids and it does not start a run. A sealed run requires `--confirm --lock` and checks those hashes. `report --confirm` reads labels from the custody directory once, writes `opened.json`, and appends `opened.log`.

```bash
PYTHONPATH=src python3 -m autopaperreview calibrate-eval cvpr-tier list \
  --cache workdir/cvpr-tier --years 2024,2025

PYTHONPATH=src python3 -m autopaperreview calibrate-eval cvpr-tier fetch \
  --cache workdir/cvpr-tier --set S24-D2

PYTHONPATH=src python3 -m autopaperreview calibrate-eval cvpr-tier prepare \
  --cache workdir/cvpr-tier --set S24-D2 --variant A1

PYTHONPATH=src python3 -m autopaperreview calibrate-eval cvpr-tier lock \
  --output workdir/cvpr-tier/lock.json --holistic-mode forecast --holistic-samples 1

PYTHONPATH=src python3 -m autopaperreview calibrate-eval cvpr-tier run \
  --cache workdir/cvpr-tier \
  --model-id grok-4.7-cursor \
  --backend command \
  --set S24-D2 --variant A1 \
  --command-template 'grok --output {answer_file} --model {model}'

PYTHONPATH=src python3 -m autopaperreview calibrate-eval cvpr-tier run \
  --cache workdir/cvpr-tier \
  --model-id opus-local \
  --backend command \
  --command-template 'opus --prompt {prompt_file} --output {answer_file} --model {model}'

PYTHONPATH=src python3 -m autopaperreview calibrate-eval cvpr-tier run \
  --cache workdir/cvpr-tier \
  --model-id opus-local \
  --backend command \
  --variant A1 --round r1 --repeats 2 \
  --command-template 'opus --prompt {prompt_file} --output {answer_file} --model {model}'

PYTHONPATH=src python3 -m autopaperreview calibrate-eval cvpr-tier decoys \
  --output workdir/cvpr-tier/decoys.json --count 4

PYTHONPATH=src python3 -m autopaperreview calibrate-eval cvpr-tier controls \
  --output workdir/cvpr-tier/controls.json --since 2026-07-15 --category cs.CV

PYTHONPATH=src python3 -m autopaperreview calibrate-eval cvpr-tier probe \
  --cache workdir/cvpr-tier \
  --model-id grok-4.7-cursor \
  --backend command \
  --decoys 3 \
  --controls workdir/cvpr-tier/controls.json \
  --command-template 'grok --output {answer_file} --model {model}'

PYTHONPATH=src python3 -m autopaperreview calibrate-eval cvpr-tier round \
  --cache workdir/cvpr-tier --round r1 --model-id grok-4.7-cursor --variant A1

PYTHONPATH=src python3 -m autopaperreview calibrate-eval cvpr-tier report \
  --cache workdir/cvpr-tier \
  --output workdir/cvpr-tier/cvpr_tier_report.md
```

The command names above are the local binaries. Substitute the argv those CLIs actually accept. A template that starts with `bash -lc` is still an argument vector; the harness does not pick a shell for you. A sealed set is not fetched or run by the examples above. `S24-D2` still needs a full 2024 catalog before a draw is valid; the synthetic fixture is too small and the fetch refuses it.

`examples/synthetic/cvpr_tier/` is an invented catalog. The unit tests run list, fetch, prepare, command, replay, and report from that directory and do not call the network. A real model sweep is not part of this change.
