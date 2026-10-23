# Changelog

## Unreleased

- Annotated manuscript PDFs now carry a role-colored highlight, a visible margin tag such as `E7`, and a PDF comment from `APR reviewer` for every cited evidence id. An appended evidence-index page links each id to that spot. A quote that is not in the PDF becomes a page-level note and is still listed in the index. `autopaperreview annotate` rewrites that file from a finished run without calling the model. The report stage writes the same file when a sibling PDF and PyMuPDF are present. Report handler version is 12, and the report signature records whether PyMuPDF is installed, so a later `run` refreshes the report export only.
- Dimension scoring now appends the packaged `sar_bands_v1` clause list. The schema example is not a default clause. Excerpt checks still prefer an exact copy and also accept a passage whose only difference is whitespace or a line break. The stored quote is the manuscript span.
- A fatal class is stored only when its excerpt resolves and a second judgment, which sees that excerpt and the class definition, says the excerpt states the class. `missing_closest_neighbor` is still owned by related-work search.
- The OpenAI-compatible adapter timeout defaults to 900 seconds. The same default covers visual inspection and related-work confirmation.
- The holistic judgment is K independent ratings on a 1.0–10.0 scale (default 3, temperature 0.7). The stored rating is their mean. Excerpts are optional. A schema failure retries once and then fails the paper; the dimension term is not a substitute. `apr-fusion-v1` ranks by that mean. A verified venue cap can only lower it. The pre-registered candidate is `holistic-capped`. Dimension bands stay diagnostic. `calibrate-eval run --cheap` skips contributions, ledger, dimensions, and fatal. Excerpt matching also accepts Unicode normalization, line-break hyphens, ligatures, and a fuzzy word window at similarity 0.90. Drop counts are on the review metadata. `scripts/rank_eval.py` selects on the tune slice by Spearman then AUC, prints an exact tie, and breaks it by a declared priority rather than list order.
- `calibrate-eval run` accepts `--coefficient-set`, `--with-holistic`, and `--only-ids`. Camera-ready venue banners are removed from the project manuscript before prompting. `scripts/rank_eval.py` prints aggregate Spearman and AUC for the declared candidates and writes per-paper rows only under a gitignored path.
- `apr-iclr-cap-v2` snaps to the largest ICLR bin that does not exceed the capped raw score. A raw 7 stays in 6, a non-fatal band-4 paper can reach 3, and claims plus experimental soundness at 8 no longer pin every weaker profile in bin 8. Weights and the `min(claims, soundness) + 1` gate are unchanged. `rate` handler version is 4. This is still a declared formula, not a fitted regression.
- `scripts/select_public_calibration.py` can score extra rejected submissions with `--extend-rejected`, honours `Retry-After` on HTTP 429, and sets a 45-second socket timeout before PDF downloads.
- Added `scripts/select_public_calibration.py`. It draws a seeded 100-paper public ICLR 2024/2025 sample stratified by official-rating mean and primary area, and it writes a 60/40 tuning split. The manifest, PDFs, and ratings stay in a gitignored cache. `calibrate-eval report` now also prints Kendall tau-b and a human-bin by venue-bin confusion table. Neither the sample nor the table updates the rating formula.
- Added `calibrate-eval`, a report-only comparison of the declared ICLR formula to public official ratings (Spearman, MAE, bin agreement, accept/reject AUC, and score spread). It does not fit weights, caps, or bins. The run includes `related_work_search` before `rate`. The default search mode is `off`, so a missing retrieval does not move the bin. Agent and live search are opt-in. `run --mode adapter` can select `OpenAICompatibleAdapter` and passes `base_url` and `model` through. Manuscripts, forum ids, and numeric reports stay in a gitignored cache. `AgentJudgmentAdapter` loads a review drop that must echo prompt-packet digests.
- `ingest` and `render_pages` accept an optional `supplement` path. The supplement is rendered as a second page set (`S1`, `S2`, ...) beside the main pages. `format_check` still applies page limits to the main PDF only and records those supplement ids. `visual_inspect` sees both page sets.
- The run directory is the project id. A stage signature covers that stage's parameters, the files it reads, and upstream output hashes. Changing an export-gate flag reuses ingest, render, search, and judgments. A manuscript edit still reruns stages that read the manuscript.
- Added `OpenAICompatibleAdapter` and `OpenAICompatibleVisionInspector`. `execute_review` can call an OpenAI-compatible chat endpoint for contributions, ledger and agenda items, the seven rating dimensions, and fatal classification. `visual_inspect` can send rendered page images to the same API. The pipeline validates the JSON, retries a schema failure once, then fails the stage. An excerpt or page hash that does not resolve rejects that contribution, ledger item, or visual finding. There is no silent fallback to the deterministic adapter.
- A rating dimension whose excerpt does not resolve is retried once with that unresolved-anchor error. If any of the seven dimensions is still missing, `execute_review` fails the stage.
- Contributions and ledger judgments require at least one item. An empty list is a schema failure and uses the same one-retry path.
- Connection errors, timeouts, HTTP 429, and HTTP 5xx are retried up to two times with a 0.5s then 1s backoff. Any other HTTP 4xx fails immediately. Transport retries are separate from the schema retry.
- `execute_review` accepts `bilingual = true`. That adds one `zh-Hans` judgment over the accepted English fields, with the same schema retry and stored hashes, so bilingual export can pass.
- A hostname that resolves only to loopback addresses is loopback. Resolution failure, or any non-loopback address, is remote. The API key value stays out of the stage signature; replacing the key does not rerun a cached stage.
- Each raw request and response is stored under the stage directory with its SHA-256 on the stage record. A matching stage signature reuses the result and does not call the endpoint again.
- Loopback endpoints are allowed by default. A remote endpoint requires `allow_remote`, `network_policy = allow`, and an environment-variable API key. The run manifest records `manuscript_left_machine`. Keys are not written to requests, manifests, or errors.
- Fixture JSON and `AgentFindingsInspector` remain equal options for an agent that writes schema JSON outside the process.
- Added a public-repo confidentiality policy ([CONFIDENTIALITY.md](CONFIDENTIALITY.md), [AGENTS.md](AGENTS.md)) and a tracked-file check that rejects review work directories, document and image files outside an allowlist, submission-id patterns, and SHA-256 digests of blocked strings.
- Replaced contribution-list fixtures with synthetic sentences.

## 0.1.9 - 2026-10-21

- Added `related_work_search`. Before novelty or prior-work scores, the stage derives the task, inputs, outputs, assumptions, and setting, then builds keyword queries for the same problem, the same technique, alternative formulations, standard baselines, and adjacent tasks. Manuscript text is not sent to a provider.
- Live retrieval uses the arXiv API, OpenAlex, and Semantic Scholar, and logs offline, timeout, or rate-limited providers without inventing papers. Agent mode reads a findings drop and keeps a candidate only when the provider payload resolves the same id and title.
- Candidates record venue, year, link, a one-line summary, whether the bibliography cites them (normalized title or arXiv/DOI id), and whether they are prior, concurrent, or undated relative to the venue submission deadline. Tiers are must-compare, should-discuss, and optional. Must-compare requires both task overlap and technique or setting overlap. Surveys, reviews, datasets, benchmarks, and generators stay should-discuss at most. Mentioning an input modality is not enough. At most three confirmed neighbours stay must-compare, ranked by relevance. The judging agent confirms each one with a one-line reason before a retrieval cap can cite it. Unconfirmed neighbours are discussion only.
- Experiment comparison flags missing must-compare baselines, privileged inputs such as ground-truth scale, and baselines that solve a different task. Clause advice follows `apr-iclr-cap-v1`. The search stage does not write an overall score.
- `FatalClass.missing_closest_neighbor` caps `apr-iclr-cap-v1` at 6 (`prior_work_contextualization` ceiling 2, `originality` ceiling 4). The existing `unfair_baseline` signal is a blocking issue and keeps its cap of 5. `rate` applies both through `active_caps()`. A missing-neighbor cap cites each verified prior candidate. `unfair_baseline` may cap at 5 from the manuscript's own privileged or different-task baselines without a verified candidate, and it always cites the resolvable manuscript anchor `E-manuscript-baselines`.
- Only a confirmed candidate dated on or before the venue deadline can support `missing_closest_neighbor`. Concurrent work is discussion only. If every provider fails, times out, or no provider is available, `status=degraded` and no cap is emitted, so the score does not drop. If at least one provider succeeds and another fails, `status=partial` lists the failed providers and the report says the search may be incomplete. That partial search may emit `missing_closest_neighbor` only for a confirmed pre-deadline neighbour verified by a provider that succeeded and absent from the baselines. The same partial search still emits `unfair_baseline` when the manuscript anchor resolves. A failed provider never creates or strengthens a retrieval cap.
- Harness cap issues carry a bilingual `FirstReadNote` (what the paper said, a collapsed quote, why it matters) so `require_first_read_on_major` can pass. The baseline quote is the labeled experiment lines only, collapsed to one line and rendered as a blockquote. A section ends at the next real heading, so body prose does not leak into the labeled block.
- Named methods from the manuscript's related work, baselines, and references are extra name-only queries, so a cited closest method is retrieved and checked. Keyword queries stay generic tokens. Names are ordered by an explicit closest or most-related mention, then methods cited in the body, then experiment-table baselines, then related-work sentences nearest the paper's own method, then mention frequency. Hyphenated, camel-case, and digit-containing names such as `Foo-BAR`, `Abc3D`, and `XYZ-Net` are kept. The default cap is 15 (`named_method_limit`) and does not drop an explicit closest method or a reference cited in the body.
- Bibliography entries are resolved as metadata. Numeric markers (`[n]`, `n.`, `(n)`) and unnumbered author-year blocks are split even when a line wraps, starts with a year, or is only a year. The title is the quoted string, the sentence after the authors, or the sentence after the year. In-body cites include `[24]`, `[3, 24]`, ranges such as `[20–25]`, and author-year forms such as `Name et al. 2025` and `Name and Name 2025`. Entries cited in related work, the experiments, or the method section come first, then the citation nearest the method description. The default budget is 60 (`bibliography_lookup_limit`). An entry with no plausible title does not use a slot. arXiv titles use `ti:`. arXiv ids are batched with `id_list`. OpenAlex receives a sanitized title filter and, when the entry has a DOI, a DOI filter. A resolved cited reference is a candidate with `cited=true` and can be must-compare, which is the missing-neighbour case when the experiments do not compare it. Manuscript prose is not sent.
- Live retrieval and confirmation compose. After the search, the stage writes `confirmation_packet.json` (provisional must-compare candidates plus the manuscript baselines). Confirmation uses a `confirm_must_compare` hook, a `confirmation_adapter` only when that parameter is set, or an agent-written JSON file (`confirmation_path`, or a `confirmations` list on the findings drop). The `execute_review` adapter is not bound automatically. The retrieval cap cites only confirmed items. With no confirmation, the judgment is `awaiting_confirmation` and `require_related_work_confirmation` fails the export gate, instead of demoting those neighbours. The judge's confirmation or rejection reason is stored and reported verbatim. A generic sentence is used only when the judge gave no reason.
- The same paper returned by two providers is one candidate. Records merge on a shared arXiv, DOI, OpenAlex, or Semantic Scholar id, and on a title with punctuation, hyphens, and whitespace removed when the years differ by at most one.
- HTTP 429 is retried and does not abort the rest of that provider's queries. Semantic Scholar with `S2_API_KEY` uses a longer exponential backoff with jitter, honours `Retry-After`, and paces requests to about one per second. The key is not written into URLs, logs, or the cache. Without that key, a 429 that remains after 2 retries skips the provider inside an 8-second budget, and the search status lists it as failed. Successful provider metadata and HTTP 400 responses are cached in memory and on disk under the project `.autopaperreview/related_work_cache` directory, keyed by the provider, lookup kind, and query hash, for 7 days, so a resume after confirmation does not repeat the sweep. OpenAlex title filters drop commas, colons, quotes, and other characters that make the filter invalid. The candidate set is sorted so identical queries stay stable.
- The dimension table shows the assigned score and clause beside the after-ceiling band.
- `novelty` reads baselines against closest neighbours before it assigns tags. In one config, `novelty` and `rate` must depend on `related_work_search`. `rate` reads that comparison before it scores originality.
- Reports gain a bilingual Related Work section, and the stage writes `related_work.md` with suggested weakness and rebuttal sentences.
- Venue deadline sidecars: ICLR 2027 abstract 2026-09-18 and submission 2026-09-25; AAAI-27 abstract 2026-07-21 and submission 2026-07-28. See [related-work search](docs/related-work-search.md).

## 0.1.8 - 2026-10-20

- Added a pinned AAAI-27 main-track format profile (`profiles/aaai2027.json`): 7 content pages, 9 pages total, US Letter, and the anonymous-submission template marker. `format_check` reads `template_markers`, `max_total_pages`, `page_limit_fatal`, and `anonymity_metadata_fatal` from the profile. The ICLR 2027 profile is unchanged.

## 0.1.7 - 2026-10-19

- Added a declared ICLR rating, `apr-iclr-cap-v1`. `rate` snaps seven dimension scores onto 1/3/5/6/8/10 after a `min(claims, soundness)` gate. It is not a fitted regression and it does not call a model.
- Fatal classes cap that bin: invalid protocol, leakage, and wrong metric at 3; unfair baseline and unsupported headline claim at 5; anonymity or page-limit break at 1. A cap is active only when the issue is decision-relevant and blocking, or when a format/visual record says so. Exemptions are records and cannot cite excluded evidence as their basis.
- Dimension scores may carry `rubric_id` and `clause_id` from `sar_bands_v1` (bands 2/4/6/8/10). The export gate can require them.
- Added `calibrate_batch` and `autopaperreview calibrate`. Same-bin neighbors require `same_bin_justified`. Different active caps cannot share a bin.
- Added optional page rendering (`render_pages`, extra `visual` / pypdfium2), a pinned ICLR 2027 format checklist (`format_check`), and a visual-adapter contract (`visual_inspect`) whose findings must carry the page-image hash and a visible mark that is not the caption. Those findings affect the rating only through fatal classes or clause ceilings.
- `init` mounts `visual_qa_v1` as route M9. Reports and the PDF footer show the declared formula when `rate` has run, and still say that no overall score was assigned otherwise.
- Schema stays `1.0`. New fields are optional, so existing packages still load.
- `visual_inspect` now has two adapters. `AgentFindingsInspector` checks a JSON drop written after every rendered page was opened (page-image hash, bbox, visible mark, severity, fatal class or clause). `HostedVisionInspector` can POST a page PNG to a declared endpoint only when `network_policy = allow` and `APR_VISION_API_KEY` is set. A missing key does not crash import; the status is `key_absent` and no pixels are sent.

## 0.1.6 - 2026-09-06

- Evidence citations such as `[E2, E3]` are Markdown (and PDF) hyperlinks into the Evidence Index. Each id is independently clickable.
- The report stage copies `review_report.md` next to `review.toml`. With the optional `pdf` extra (`reportlab`) it also writes `review_report.pdf` with the same internal links.
- PDF contents page with page numbers, clickable jumps, and a document outline (bookmarks) for sections and issues.
- PDF layout: CJK wrapping, Heiti/Noto sans, bilingual English/中文 cards, compact header, and colored dimension scores and venue outcomes.
- Canned integrity/novelty/ledger notes render as status codes with one bilingual legend, not a repeated English/中文 card on every row. Identical novelty cells collapse to `C1–C3 x RW1–RW7`.
- `pip install 'autopaperreview[pdf]'` enables PDF export. Without it, Markdown and JSON reports still run.
- Figure-grounded review: `figure_grounded_v1` is a visual route. Prompt packets require PDF page inspection for every numbered figure; caption paraphrase is not inspection. Export can require figure citations (`min_figure_citations` / `require_figures_if_visual_route`).
- Listed contributions are audit units: ledger extracts the 3–4 author-numbered items, literature emits per-item retrieval queries, agenda asks evidence-completeness and matched-setting comparator per item, and `contribution_audit_v1` forbids a single paper-level novelty paragraph.
- M7 contribution audits replace the pending C_i risk. Each listed contribution gets Tests / Comparator / Missing content from covering issues; the canned "have not been checked" sentence is only the empty state.
- Bilingual export is fail-closed. `bilingual = true` now refuses unpaired `en` / `zh-Hans` prose, mismatched language-prefix counts, and incomplete strength/gate lists before Markdown or PDF is written. Word-substitution fill is limited to short fixture phrases; long review prose must already be `LocalizedText`.
- PDF tokenization no longer joins `**en:**` / `**zh-Hans:**` into one body run. Issue headings such as `R01` / `C01` are recognized as issue cards.
- Made review comments readable for a first-time reader of the manuscript (area chair, author, or reviewer who has not internalized the paper yet).
- Added optional `FirstReadNote` on `ReviewIssue`: restated claim, explanation, quote, Conventional Comments intent, and blocking flag. Existing packages remain valid.
- Added packaged `first_read_v1.md` writing contract. `prompt_packet` prepends it to every route packet. Specialist prompts now require a self-contained argument, not an evidence ID.
- Reports open with a how-to-read note, human dimension labels, inlined quotes, and an evidence catalog. Internal IDs stay as audit handles.
- Export gate can require first-read notes and quotes on major/critical issues (`require_first_read_on_major`, `require_quote_on_major`, `min_explanation_chars`).
- Deterministic adapter, synthetic fixture, and `init` now emit first-read notes. `init` adds a first-read clarity route (`M8`).
- Documented the open-source strategy in [docs/first-read-review.md](docs/first-read-review.md). This is not a claim that AutoPaperReview matches ICLR, ARR, Reviewer2, or ReviewEval scores.

## 0.1.5 - 2026-08-29

- Closed the P0–P2 contract gaps from the open-experiment review.
- Reproducibility attestation treats “No … was reported” as missing, not as a positive mention.
- Added snapshot `excerpt` / `excerpt_hash`, typed `Anchor` locators, and a report export gate.
- Added `execute_review` with bundled `DeterministicManuscriptAdapter` and `FixtureReviewAdapter`. The core still does not call a model API.
- `prompt_packet` can attach literature queries and snapshot excerpts to review routes.
- Added `ledger`, `agenda`, `workspace_inspect`, `integrity`, and matched-setting `novelty` stages.
- `init` now writes specialist prompts, a workspace `artifacts/` folder, and a runnable deterministic review DAG.
- `doctor` distinguishes importable optional modules from bundled adapters.

## 0.1.4 - 2026-08-29

- Added [docs/oss-experiment-gaps.md](docs/oss-experiment-gaps.md): a prioritized feature review against DeepReviewer 2.0, ResearchArena, AgentReview, MARG, OpenReviewer, PaperQA2, reviewer2, and related open experiments. This is not a claim that AutoPaperReview matches their scores.
- Reordered the post-0.1.3 roadmap slices from that review: contract completeness and typed locators before ObservationRecord; an optional integrity family and thin model adapter next.
- Literature query generation now emits distinct narrow / mid / broad variants. Extra queries no longer append a `" 2"` suffix. The `literature_grounding` handler version is now `2` so existing stage caches invalidate.

## 0.1.3 - 2026-08-29

- Added `VenueConclusion` records: conference and journal decisions from the seven 1–10 dimension scores, with no overall numeric score.
- Report Markdown now renders venue conclusions instead of an ICLR-style overall 0–10.
- The synthetic fixture includes ICLR reject and TMLR major-revision conclusions.

## 0.1.2 - 2026-08-29

- Added bilingual review Markdown: one report file can contain both `en` and `zh-Hans` section by section (Summary, Strengths, Weaknesses, Questions, seven dimension rationales, and issues).
- Added the thinnest report-stage options `bilingual = true` and `languages = ["en", "zh-Hans"]`. Switching `default_language` alone still renders one language.
- Missing translations are filled into `LocalizedText.translations` before bilingual render. Silent fallback to the primary language while claiming bilingual is rejected.
- Extended the synthetic fixture so dimension rationales include `zh-Hans` text rather than English-only strings.

## 0.1.1 - 2026-08-29

- Added optional SAR-shaped package fields: seven dimension scores, evidence-bound claims, related-work queries, retrieved-source snapshots, and an overall score that is absent by default and requires explicit provenance.
- Added a `literature_grounding` stage that generates baseline / same-problem / related-technique queries from local manuscript text, records snapshot artifacts, and stays behind the network declaration gate. Tavily, OpenAlex, and PaperQA are not core dependencies.
- Added SAR-shaped prompt and report sections. Every claim in the new records cites evidence IDs.
- Added `docs/stanford-sar-comparison.md`. Do not treat Stanford's published Spearman 0.42 as a number measured in this repository.
- Extended the synthetic example, committed JSON Schemas, and tests. No database, hosted service, agent framework, or vector index was added.

## 0.1.0 - 2026-08-21

- Added the versioned canonical review and run models.
- Added the explicit local DAG runner with resume and content-hash caching.
- Added built-in ingest, command, prompt-packet, issue-import, consensus, and report stages.
- Added deny-by-default network policy and shell-free command execution.
- Added legacy review migration, JSON Schema export, synthetic example, tests, CI, and maintenance documentation.
- Added normalized configuration snapshots, handler versions, command input hashes, portable command records, and atomic stage replacement with rollback.
- Added strict cross-reference checks, source-bound migration, Unicode-safe consensus keys, conflict detection, and deterministic issue-ID disambiguation.
- Packaged the built-in review prompts and added a CLI command to export them into review projects.
- Restricted command environment inheritance to an explicit allowlist and bound inherited-value hashes plus runtime ABI data into command cache signatures.
- Enforced per-route manuscript access in prompt packets and removed attempt-directory paths from command failure messages.
