# Changelog

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
