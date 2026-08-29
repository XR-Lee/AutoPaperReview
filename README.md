# AutoPaperReview

AutoPaperReview is a local-first, evidence-traceable harness for repeatable scholarly manuscript review. It separates durable review data and execution records from replaceable parsers, model providers, literature services, workflow engines, and journal platforms.

Version: `0.1.3`

## Code harness, not a prompt-only skill

AutoPaperReview is an executable Python harness. The TOML DAG, Pydantic schemas, artifact hashing, cache/resume logic, migration, plugin registry, and CLI are code. Files under `src/autopaperreview/prompts/` are packaged, versioned route assets that can be replaced without changing the canonical data model.

The core deliberately does not call a model or parse PDF/DOCX by itself. A model provider, parser, retrieval worker, or journal platform is connected through a stage plugin or adapter and must convert its output into AutoPaperReview records.

## What v0.1 provides

- Strict Pydantic models for artifacts, sources, evidence, issues, review packages, stages, and run manifests.
- A TOML-defined DAG with validation, resumable execution, content-hash caching, and atomic stage promotion.
- Built-in stages for source ingest, deterministic commands, prompt packets, legacy issue import, consensus, and Markdown/JSON reports. A report can emit one Markdown file with both `en` and `zh-Hans` (`bilingual = true` or `languages = ["en", "zh-Hans"]`). Missing translations are filled into `LocalizedText.translations`; bilingual rendering does not silently fall back to the primary language.
- A deny-by-default network declaration gate. Commands never use a shell, and network-requiring stages must declare their scope.
- Python entry-point plugins under `autopaperreview.stages`.
- Migration support for the earlier bare issue-array and review-package JSON formats.
- Generated JSON Schema, a synthetic end-to-end example, unit tests, CI, and an open-source reference-stack snapshot.
- Optional SAR-shaped records: seven dimension scores on a 1–10 scale, narrative claims that require evidence IDs, related-work query sets, retrieved-source snapshots, and venue-specific conclusions for conferences and journals. No overall score is emitted by default.
- A `literature_grounding` stage that emits multi-perspective queries and optional offline snapshots behind the network declaration gate.

See [Stanford SAR vs AutoPaperReview](docs/stanford-sar-comparison.md) for an honest comparison with paperreview.ai. This repository does not claim Stanford's ICLR Spearman numbers.

The core does not depend on LangChain, LangGraph, Prefect, Docling, GROBID, PaperQA, LiteLLM, or a specific model API. Those systems belong behind adapters so their release cadence does not become the review database schema.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

For development without installation:

```bash
PYTHONPATH=src python3 -m autopaperreview --version
```

## Run the synthetic example

```bash
PYTHONPATH=src python3 -m autopaperreview validate examples/synthetic/review.toml
PYTHONPATH=src python3 -m autopaperreview run examples/synthetic/review.toml
```

The synthetic example sets `bilingual = true` on the report stage so `review_report.md` contains English and Simplified Chinese section by section, including dimension rationales.

The run ID is derived from the project ID, source hash, and configuration hash. Repeating the command reuses successful stages only when their signatures and output hashes still match.

Each run records a normalized configuration snapshot, source hash, handler version, stage signature, command input hashes, and relative output artifacts under the configured run root.

## Start a review project

```bash
autopaperreview init reviews/my-paper --project-id my-paper --title "My paper review"
autopaperreview validate reviews/my-paper/review.toml
autopaperreview run reviews/my-paper/review.toml
```

## Migrate an existing review package

```bash
autopaperreview migrate old_review_package.json canonical_review_package.json \
  --source manuscript.docx \
  --project-id manuscript-2026
```

Migration is non-destructive: the input is not modified, bilingual fields are normalized, unknown legacy fields are retained under metadata, and the original source is bound by SHA-256.

The migration command does not copy an unpublished manuscript beside the output JSON. The package records its logical source name and hash; use an ingest stage with source copying when a self-contained local run bundle is required.

## CLI

```text
autopaperreview init       Create a minimal project
autopaperreview validate   Validate TOML, DAG, plugins, and local paths
autopaperreview run        Run or resume the pipeline
autopaperreview inspect    Print a run manifest
autopaperreview migrate    Import legacy review JSON
autopaperreview schema     Export canonical JSON Schema
autopaperreview templates  Export packaged prompt templates
autopaperreview plugins    List installed stage types
autopaperreview doctor     Inspect optional adapter availability
```

## Design principles

1. Deterministic checks run before model-based judgments.
2. Every released claim must bind to an immutable manuscript or external-source snapshot.
3. Model prompts and responses are versioned artifacts, not hidden application state.
4. Semantic deduplication never happens silently. The v0.1 consensus stage performs only explicit-key or exact normalized grouping.
5. Manuscript text is local by default. Metadata-only or full-text network use requires an explicit policy change.
6. Third-party objects are converted immediately into canonical AutoPaperReview models.

The built-in network policy validates declared requirements but is not an operating-system network sandbox. Stage plugins and commands are trusted local code; use containers or another sandbox when executing untrusted integrations.

Command stages inherit an explicit environment-variable allowlist rather than the complete parent environment. Add secret variable names with `inherit_env`; their values affect the cache signature but are not written to command records. Never put secret values in the TOML `env` table because configuration is snapshotted.

See [Architecture](docs/architecture.md), [Stanford SAR comparison](docs/stanford-sar-comparison.md), [Open-source reference stack](docs/oss-reference-stack.md), and [Maintenance policy](docs/maintenance.md).
