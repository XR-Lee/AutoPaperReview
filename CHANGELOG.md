# Changelog

## 0.1.6 - 2026-08-30

- Evidence citations such as `[E2, E3]` are Markdown (and PDF) hyperlinks into the Evidence Index. Each id is independently clickable.
- The report stage copies `review_report.md` next to `review.toml`. With the optional `pdf` extra (`reportlab`) it also writes `review_report.pdf` with the same internal links.
- PDF layout: CJK wrapping, Heiti/Noto sans, bilingual English/中文 cards, compact header, and colored dimension scores and venue outcomes.
- `pip install 'autopaperreview[pdf]'` enables PDF export. Without it, Markdown and JSON reports still run.
- Figure-grounded review: `figure_grounded_v1` is a visual route. Prompt packets require PDF page inspection for every numbered figure; caption paraphrase is not inspection. Export can require figure citations (`min_figure_citations` / `require_figures_if_visual_route`).

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
