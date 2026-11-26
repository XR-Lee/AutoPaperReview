# Auto Proposal Review

Proposal review runs through the optional `autopaperreview proposal-review` subcommand. With the base APR installation and a configured model CLI, start with `score` for a single idea or `import-apd`, `run` and `export` for a JSON candidate batch.

Use `autopaperreview proposal-review prioritize` to rank a standalone research idea or all candidates in an AutoPaperDraft file with a frozen profile. The six-dimension assessment covers problem importance, conceptual novelty, conditional value, mechanism and assumptions, testability and community reach. A profile selects the judgments that enter its overall score. The output retains the individual judgments and their reasons.

The lightweight `proposal-review score` command assesses four dimensions independently. These cover problem importance, conceptual novelty, conditional value and community impact. Its scale runs from 1 (substantive weakness) through 3 (modest or mixed merit) to 5 (exceptional merit). A dimension with insufficient evidence receives a JSON `null` score.

## Review an idea

The default backend uses local Claude Code with tools disabled, in a fresh temporary directory, and transmits the current stage's input to the configured model provider. A standalone proposal can be Markdown, plain text or an exported five-section proposal object. Use `--model` to select the model. A command backend can also be supplied via `--command` with `{model}` and `{system_file}` placeholders. It must consume stdin, return one JSON object on stdout and run without retrieval or access to other stage inputs.

```bash
autopaperreview proposal-review score proposal.md --work workdir/idea --output workdir/idea/review.json
```

For APD `contribution.yaml`, install `pip install -e '.[proposal-yaml]'` in the APR environment. JSON works with the base install. Every candidate goes through compression, including candidates outside APD's selected entry. The importer reads the shared motivation, contribution, novelty, link to motivation and proposed tests. Fields containing existing evidence, measured table cells, selections and provenance stay out of the model input. The compressor reconstructs five sections: problem, approach, difference from prior work, intended value and validation plan. Each nonempty section carries its source-unit IDs.

```bash
autopaperreview proposal-review import-apd /path/to/contribution.yaml --work workdir/candidates
autopaperreview proposal-review run --work workdir/candidates --jobs 2
autopaperreview proposal-review export --work workdir/candidates --output workdir/candidates/reviews.json
```

For the APR-style probabilistic forecast, run `run --stage forecast` after compression, then `export --reviewer forecast`. For a standalone file use `score --reviewer forecast`. One model call simulates a specialist, a reviewer from a neighboring area, and a prospective user. Each gives a distribution over scores 1–5 for each assessable dimension. The score averages their expected values. The raw distributions remain available in the export. These are simulated perspectives from one model.

## Run a public ICLR evaluation

The default intake downloads two SHA-256-pinned OpenReview API v2 snapshots from the [public dataset releases](https://github.com/ranpox/iclr2024-openreview-submissions/releases). Together they require about 1.3 GB. The October submission snapshot supplies abstracts and the November review snapshot supplies official reviewer comments. The default requires the stored submission modification time to precede the earliest official review creation time for that forum. The API v2 `--source api --year YEAR --pages N` alternative reads the current public notes through the shared OpenReview client. That client waits at least one second between calls. HTTP 403, or a browser check, stops the intake and does not fall through to search. Later revisions can leave few eligible forums. `--allow-late` opts into that retrospective setting and records it in the manifest. Follow the [OpenReview notes documentation](https://docs.openreview.net/how-to-guides/data-retrieval-and-modification/how-to-get-all-notes-for-submissions-reviews-rebuttals-etc) for the underlying data model.

```bash
apr=autopaperreview
p=workdir/proposal-pilot
"$apr" proposal-review fetch --work "$p" --limit 36 --seed prospective-v1 --area 'learning theory' --area optimization
"$apr" proposal-review run --work "$p" --stage ideas --jobs 4
"$apr" proposal-review run --work "$p" --stage label --model sonnet --jobs 4
"$apr" proposal-review run --work "$p" --stage audit --jobs 4
"$apr" proposal-review fit --work "$p"
"$apr" proposal-review report --work "$p"
"$apr" proposal-review run --work "$p" --stage forecast --split dev --jobs 4
"$apr" proposal-review fit --work "$p" --reviewer forecast
```

To reuse local snapshots supply both `--papers raw_paperlist.json` and `--forums raw_paper_reviews.jsonl`. Source reference and file hashes are recorded. `--limit` selects eligible forums by seeded hash before the independent, forum-level dev/holdout split. `--holdout-after` excludes every forum from an earlier manifest and reserves the entire new sample for testing. The following commands compare frozen configurations on fresh papers.

```bash
q=workdir/proposal-replication
"$apr" proposal-review fetch --work "$q" --limit 24 --seed prospective-v1 --area 'learning theory' --area optimization \
  --papers "$p/downloads/raw_paperlist.json" --forums "$p/downloads/raw_paper_reviews.jsonl" --holdout-after "$p/manifest.json"
"$apr" proposal-review run --work "$q" --stage ideas --jobs 4
"$apr" proposal-review run --work "$q" --stage forecast --jobs 4
"$apr" proposal-review run --work "$q" --stage label --model sonnet --jobs 4
"$apr" proposal-review run --work "$q" --stage audit --jobs 4
"$apr" proposal-review report --work "$q" --calibration "$p/calibration.json"
"$apr" proposal-review report --work "$q" --calibration "$p/forecast-calibration.json"
```

## Interpret the evidence

The ICLR compressor receives filtered abstract units. Before those units are built, the abstract is passed through the same ICLR anonymizer used for calibration manuscripts: emails, non-anonymous GitHub links, and camera-ready banners are removed, and the scientific sentences stay. The scoring call sees only reconstructed proposal text. Review coding uses the latest official review per signer in the selected snapshot, direct replies in the correct forum and invitation, and opinion-bearing fields. Overall rating text, numeric paper subscores, decisions, rebuttals and manuscript experiments stay outside the packets. Each official rating is parsed with `parse_rating` on the year scale in `profiles/rating_scales.json`. ICLR 2024 and 2025 use {1, 3, 5, 6, 8, 10}. ICLR 2026 uses {0, 2, 4, 6, 8, 10}. A year without a registered discrete scale uses the inclusive integer range 0–10. The local provenance record stores the mean and the parse counts. One off-scale rating leaves the mean unset instead of dropping that rating. The raw rating string is not stored. A separate audit call requires exact excerpts from the cited source units, compatible with the proposed dimension and score. A reviewer contributes at most one score per dimension. A paper's reference score is the mean across reviewers with retained evidence. A missing opinion stays missing. These reference scores are model-coded human opinions. Reviewers wrote them after reading whole papers, and publicly known work may also be present in model pretraining.

`fit` reads the development split only. Each dimension's offset is the sum of paired target-minus-prediction residuals divided by the number of pairs plus four, shrinking toward zero. Calibrated values are clipped to 1–5. Supply the resulting file to `score` or `export` with `--calibration`. The reviewer and rubric must match. The baseline is the mean development reference per dimension, or the scale midpoint if none is available. Offsets adjust the score level and generally preserve ranks.

The previously reported comparison of these 1–5 scores with official final means is in [Published metrics](metrics.md). Fixed test n=100: Spearman 0.677, accept/reject AUC 0.812. Independent confirm n=100: Spearman 0.597, AUC 0.794. Two replications n=199: Spearman 0.522, AUC 0.805. On 84 pre-experiment NLP proposals, Spearman against expert idea scores was 0.273 and the AUC at 6.625/10 was 0.681. Those fixed100, confirm100, replications199, top-20%, and 84-NLP headline numbers are committed aggregates. Per-paper rows stay outside git. The cohorts are stratified by the final score, so the correlation is a within-sample association. The unit tests do not call a model.

`metrics.json` and `forecast-metrics.json` report coverage, raw and calibrated MAE, the constant baseline on matching pairs, Spearman correlation, uncertainty and disagreement between human reviewers with usable evidence. The percentile interval uses 1,000 paper-level bootstrap draws and is omitted if fewer than five cases or fewer than 900 draws yield a defined correlation. A constant prediction has undefined correlation. Compare modes on the same papers when abstention differs. `holdout.json` and `forecast-holdout.json` join predictions to retained excerpts and their original review IDs. Source and score caches are local run artifacts. Failed runs can reuse validated content-addressed calls. Changes to the model identifier, command, prompt or input create new cache entries.

## Rank proposals with a frozen profile

For the calibrated ranking, unpack your calibration bundle into a local directory such as `workdir/calibration/`. Keep `profile.json` beside the assets it lists, including the literature index and comparison bank for profiles that use them. These data files are distributed separately from the Python package.

The `prioritize` command scores an authored proposal or every candidate in an APD export. Supply the calibration bundle's profile and keep its assets in the same directory. The output contains one continuous priority score, the component judgments, their rationales and the profile fingerprint. Higher scores indicate higher predicted priority. Batch output is ordered by descending score and equal scores share a rank. A score's absolute scale depends on the profile. The full idea assessment supports six dimensions: problem importance, conceptual novelty, conditional value, mechanism and assumptions, testability and reach. A frozen profile specifies which judgments determine the overall ranking.

```bash
autopaperreview proposal-review prioritize proposal.md --profile workdir/calibration/profile.json \
  --work workdir/priority --output workdir/priority/review.json
autopaperreview proposal-review prioritize contribution.yaml --profile workdir/calibration/profile.json \
  --work workdir/priority --output workdir/priority/candidates.json
```

Profiles using historical papers, calibration banks, or learned criteria require both `--target-year` and `--cutoff-ms` for `prioritize`. The cutoff is an exclusive Unix timestamp in milliseconds. Each historical record must come from development, have a venue year before the target year, and have a dated revision. Every known availability time must precede the cutoff. Missing or null times cannot supply evidence of availability. The prior index computes term frequencies and ranks from eligible records after excluding matching target forum, title, abstract, or arXiv identifiers. Recognized arXiv aliases include DOIs, URLs, and archive paths.

Match, slate, and contrast use a dated grade snapshot, such as `target_ms` or `opinion_ms`, or a full set of individually dated excerpts from before the cutoff. Plain-text excerpts need an `opinion_ms` snapshot, and individually dated opinions use their own availability times. A nonempty `lessons.json` must give the development split, latest source venue year, and latest source revision or publication time as `split`, `year`, and `revision_ms`.

Each proposal model call uses a sealed packet. Authored sections have an allowlist, and stages permit only their own nested context and bounded calibration metadata. The sender rechecks keys and text for explicit scores, decisions, awards and identifying material. Default scoring also checks for stated results of completed experiments. The judge considers target-project results across fields, including claims expressed without numbers or past tense. Dedicated outcome screening and attribution requests may contain completed results so the screen can remove them. Reference label, audit, extraction and grading requests all use the same stricter reference audit on their full source units.

The judge reviews each exact UTF-8 user payload, including retry text. Proposal and leak-audit commands fail closed. Their stdout must be exactly one JSON value, a successful CLI envelope is accepted only as that JSON value, and duplicate keys are rejected before the envelope or its `result` string is merged. Any stderr content fails the call before a verdict is read. The paper-review command backend still returns process stdout, including a fenced reply or a Claude envelope, for the adapter to recover. The sender accepts a verdict object with the exact field `"clean"`. The verdicts `"leak"`, `"reported_result"` and `"unsure"` block the request. Each receipt binds the payload, judge prompt, model, command and reply protocol. A receipt written before stderr was part of that contract is not a cache hit. A stored reply is parsed again and must still be exactly the cached verdict. Keep write access to the receipt directory limited to trusted callers: a caller who can rewrite the directory can replay a coordinated entry. Route, prompt and asset checks detect changes to a frozen profile's dependencies.

## Score a locked benchmark

`predict-rank` scores the sealed 100-forum list. `evaluate-rank` scores any locked list. Both require one record per forum and the SHA-256 digest of the canonical sorted test forum IDs. Versioned manifests require an explicit split. Predictions carry a forum and an overall score. References carry prospective merit, the official final mean, and a decision encoded as integer 0 or 1 when available. The schema is documented in [the benchmark README](../benchmarks/proposal_rank/README.md).

`scripts/reproduce_proposal_rank.py` prints the committed aggregates by default. Set `APR_PROPOSAL_REPRO_DIR` or pass `--root` with a checksum-verified Raw or HMAC bundle to recompute the metrics and intervals. `--output` must be outside the repository, the checksum directory and the input directory. See [the reproduction README](../benchmarks/proposal/repro/README.md).

Pass the array of anonymous proposal packets to `predict-rank`. Each record supplies a forum ID and proposal. Historical retrieval always excludes the target record key and any prior row or calibration anchor whose normalized text is exactly the proposal, including when title and abstract were omitted. The index, the eligibility check and the lookup use one key normalization: Unicode NFC and trimmed whitespace. The same text normalization covers titles and abstracts, so an NFD copy matches the NFC original. Rebuild a literature index written by an older release. An index without a key column returns no rows when a target key is set. Nested opinion metadata is part of the check: a declared test partition, or a snapshot time at or after the cutoff, drops that opinion. Optional title and abstract add further exact self-matches. For a profile using historical retrieval, the cohort lock must specify `year` and `cutoff_ms` for each test case. A locked `submission_ms` can supply the cutoff. Profiles using the short forecast can also take a `short_proposal` field. With `--cached-only`, each exact model request needs a matching judgment and a strict leak-audit receipt. The byte-based cache keys require fresh judgments and audits for workspaces created with earlier protocols. To obtain new judgments, omit `--cached-only`.

```bash
autopaperreview proposal-review predict-rank --manifest benchmark/lock.json \
  --proposals benchmark/blind-proposals.json --profile calibration/profile.json \
  --work benchmark/runtime --output workdir/predictions.json --cached-only
autopaperreview proposal-review evaluate-rank --manifest benchmark/lock.json \
  --predictions workdir/predictions.json --references benchmark/references.json \
  --output workdir/metrics.json
```

Primary Spearman uses the mean of each paper's assessable idea-stage reviewer opinions. Primary ROC AUC treats a mean of at least 3.5 on the five-point scale as positive. The same predictions are compared with the final paper-level mean rating (Spearman) and acceptance (ROC AUC). The reference's `year` and `rating_scale` must agree with each other and with the lock's effective scale. If the reference omits both fields, it inherits the locked scale. The fallback when neither record specifies a scale is 1 through 10. A range such as `range:-2-4` has inclusive endpoints. A 2026 mean of 0 is allowed. The official mean can fall between discrete scale points. The output includes percentile intervals from 2,000 paper-level bootstrap draws and a 3,000-draw stratified percentile interval inside final-score strata (seed 61124). The intervals printed in the published table are a separate draw, regenerated by `scripts/reproduce_proposal_rank.py`: numpy Generator seeds 241125, 241126, and 241127 inside the original final-score tiers. The 84-proposal interval uses `random.Random(11752)` and 2,000 draws, with the positive class at 6.625/10. Papers with no accept bit stay in the Spearman and drop out of the decision AUC. An AUC based on few positive papers has substantial uncertainty. Keep test labels sealed during development and report any reuse of the same test cases in subsequent iterations.

A profile can use paired historical examples to calibrate novelty and prospective value. The scorer selects disjoint pairs of topically comparable development proposals whose human concept opinions differ, then asks how their differences apply to the target. Scores and quoted opinions belong only to the examples. Exact and near duplicate proposal inputs are excluded from comparison. Keep the local example bank beside the matching profile. The output retains the comparative argument, the strongest merit, the key objection and the probabilities for each example. One research group may disagree with the reference reviewers even on a well-specified proposal.

### Screening input for completed outcomes

Use `proposal-review prioritize --screen-outcomes` when an input was reconstructed from a completed project. The independent screen checks the compressed candidate before any scientific ranking call and caches its evidence in the same work directory. It covers APD exports, free text and structured proposals. In screened APD batches, the source contribution is checked before compression when lexical filtering would suppress it. A candidate whose contribution is entirely retrospective receives an abstention, and other candidates still run. Each exclusion identifies a numbered statement and its field. Unaffected fields retain their original wording. If the screen removes all available prospective text, the entry has a null score, a null batch rank and the status `insufficient_prospective_text`. Screened runs use one additional local Grok judgment per distinct input. If a surviving statement contains comparative language, a local Claude judgment checks whether the evidence belongs to the proposed intervention. An independent contextual check also resolves lexical matches such as a planned ablation or a prior method’s reported result. The output records each check’s cache key. Historical benchmark scores identify the input processing they used.

### Code initial review opinions for ranking calibration

Use the same reference pipeline for training, qualification and fixed-test data. The command runs Sonnet extraction followed by Opus grading separately for each paper, with a leak audit of every coding packet. It averages the available reviewer grades. Each extracted quote must match one contiguous retained range of the original unit before grading.

Prepare a local JSON array of records. Each record has a unique `forum` and an ordered `reviews` array. Each review contains its unique `id` and a `units` object mapping source unit identifiers to original initial-review sentences. Include `final_rating`, `accept`, `year`, and `rating_scale` when the references will also be used for an APR comparison. The coded output keeps those fields. Before extracting or grading evidence, the reference route removes sentences flagged by the factual-label and reported-result rules. The leak judge also checks the remaining coding payloads for indirect disclosures of completed results. Preserve the release-time version of the review when building these inputs.

```bash
autopaperreview proposal-review code-idea-references --inputs inputs.json --work workdir/reference-coding --output references.json
```

Add `--cached-only` to replay saved judgments. Each output records the coding protocol, source digest, per-review evidence offsets and the two model request keys when applicable. A paper with no codable opinions has a null reference grade. The local work directory stores the matching model and prompt configuration. On annotated references, `evaluate-rank` reports whether every test paper shares the same protocol.

Add `--include-independent-grades` to audit how the grading context affects the reference. The extractor first selects the same source evidence used for the benchmark. Opus then grades the extracted passages of each reviewer separately, assigning that reviewer the alias `r1` for the call. The top-level `prospective` is the benchmark reference. The additional `independent_reference` contains the mean of available individual grades, each reviewer's evidence offsets, its grading request key when available, and the identifier for this coding protocol. Both outputs are suitable for inspecting grading differences. Each distinct nonempty individual packet can require one additional native grading call.


Both `prioritize` and `predict-rank` accept `--outcome-screen-provider claude` to screen proposals with Claude Sonnet. Selecting a provider enables screening. `--screen-outcomes` uses the existing Grok first pass. For APD batches the chosen provider also screens candidate fields when the importer flags them for review. An explicitly selected first pass records its requested model in `screen_model` and its cache receipt in `cache_key`. Each provider has its own cached judgments. Context and attribution checks use their existing configured routes.

For a sealed batch evaluated with result screening, pass `--screen-outcomes` to `predict-rank`. This uses the same proposal and short-proposal checks as `prioritize` and includes their receipts in each prediction. If all assessable text is removed, that forum is retained with a null prediction. The complete-test evaluator will flag the missing score.

### Check compressed statements against the source

Pass `--include-source-audit` to `prioritize` for an APD export, or for a JSON record containing both a numbered `source` mapping and a `proposal` mapping. A JSON array can contain multiple paired records. The option also runs outcome screening. Each candidate's output has a `source_audit` with the source passages, sentence-level classifications and cited passage IDs. Its nested `proposal` contains the sentences the auditor judged fully supported. Inferences, additions and ambiguous details are available in the claim log. The main `overall` and `rank` use the screened proposal and the configured scoring profile.

```bash
autopaperreview proposal-review prioritize contribution.yaml --include-source-audit \
  --profile calibration/profile.json --work workdir/priority --output workdir/audited.json
```

### Clarify an audited proposal

Run the optional evidence check on the output of `prioritize --include-source-audit`. The reviewer first poses a question about the proposed design and gives a conditional grade for each possible answer. A separate reader searches the submitted source and a second reviewer checks its exact quotation. An unresolved question can be returned to the author.

```sh
autopaperreview proposal-review clarify workdir/audited.json --work workdir/clarification --output workdir/questions.json
```

Each record carries the original priority as `deployed_overall`. The question includes its axis, its alternatives, and the probability used for the conditional grades. A verified author passage includes its original source IDs. `experimental_source_score` records the conditional adjustment for evaluation. Add `--cached-only` to replay from prior responses offline.

### Compare two proposals on a shared research question

Install the `proposal-pair` extra and provide the local expert-preference model ZIP or its extracted directory. Provide the candidate array produced by `prioritize --include-source-audit` and the two candidate IDs.

```sh
autopaperreview proposal-review compare workdir/audited.json --left candidate-one --right candidate-two --model workdir/pair-model.zip --output workdir/comparison.json
```

The local tokenizer produces equal-length segments in both orders. Before local inference, the leak judge checks each final sequence as decoded text and as vocabulary tokens resolved from the actual token IDs. The audited packet and the comparison result include separate SHA-256 digests for the token IDs, attention masks, and segment IDs. If a vocabulary token cannot be mapped back to its original ID, comparison stops. Use `--work` to keep the audit receipts and add `--cached-only` to reuse them offline. `probability_prefer_left` estimates a preference between the two proposals. `training_stability90` gives the fifth and ninety-fifth percentiles across fits to resampled training topics. A range spanning one half yields `unresolved`. The output retains both original priority grades. See the model bundle for training provenance, evaluation, and license terms.

### Review the claims in an audited APD proposal

Use the trained claim-event parameters from the local proposal-review study archive.
The command reads the output of `prioritize --include-source-audit` and estimates
how often an initial reviewer will explicitly support or question each selected
claim. It also reports an independence-based estimate of opposing reviews.
The six scientific dimensions and the existing overall score come from the earlier
proposal review. Each added statement requires an exact earlier source excerpt
and a second reader's confirmation of full prospective support. The output keeps
the original review score and records the source decisions separately.

```sh
autopaperreview proposal-review claims workdir/audited.json \
  --model workdir/claim-model.json --work workdir/claim-cache \
  --output workdir/claims.json
```

Use `--cached-only` to rerun the same inputs and model instructions offline.
A live run uses the configured local Claude and Grok commands.

### Source-checked reviewer panels

Run `autopaperreview proposal-review panel audited.json --work panel-cache --output panel.json`
on the candidate JSON from `prioritize --include-source-audit`. Each admitted statement
has up to two exact spans in the supplied author material. The command omits unsupported,
partial and result-dependent statements, and records an explicit state if none qualify.
The second call sees only admitted statements. It estimates a distribution of reviewer
reactions, each with its strongest scientific benefit, concern and prospective grade.
Masses are simulated proportions. The candidate's existing overall grade is reported
separately. The six dimensions cover importance, novelty, conditional value, mechanism
and feasibility, testability, and community reach. Use `--cached-only` to replay locally.
The supplemental research bundle includes fitted ordinal models and held-out diagnostics.
