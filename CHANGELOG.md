# Changelog

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
