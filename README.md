# AutoPaperReview

AutoPaperReview is a local-first, evidence-traceable harness for repeatable scholarly manuscript review. It separates durable review data and execution records from replaceable parsers, model providers, literature services, workflow engines, and journal platforms.

Version: `0.1.8`

## Code harness, not a prompt-only skill

AutoPaperReview is an executable Python harness. The TOML DAG, Pydantic schemas, artifact hashing, cache/resume logic, migration, plugin registry, and CLI are code. Files under `src/autopaperreview/prompts/` are packaged, versioned route assets that can be replaced without changing the canonical data model.

The core does not parse PDF/DOCX by itself. It calls a model only when a stage selects the bundled OpenAI-compatible adapter. Fixture JSON and the deterministic adapter remain equal options. Any other model provider, parser, retrieval worker, or journal platform is connected through a stage plugin or adapter and must convert its output into AutoPaperReview records.

## What v0.1 provides

- Strict Pydantic models for artifacts, sources, evidence, issues, review packages, stages, and run manifests.
- A TOML-defined DAG with validation, resumable execution, content-hash caching, and atomic stage promotion.
- Built-in stages for source ingest, deterministic commands, prompt packets, legacy issue import, consensus, and Markdown/JSON reports. A report can emit one Markdown file with both `en` and `zh-Hans` (`bilingual = true` or `languages = ["en", "zh-Hans"]`). Missing translations are filled into `LocalizedText.translations` only for short fixture phrases; bilingual rendering does not silently fall back to the primary language, and incomplete pairing is refused before Markdown/PDF export.
- A deny-by-default network declaration gate. Commands never use a shell, and network-requiring stages must declare their scope.
- Python entry-point plugins under `autopaperreview.stages`.
- Migration support for the earlier bare issue-array and review-package JSON formats.
- Generated JSON Schema, a synthetic end-to-end example, unit tests, CI, and an open-source reference-stack snapshot.
- Optional SAR-shaped records: seven dimension scores on a 1–10 scale, narrative claims that require evidence IDs, related-work query sets, retrieved-source snapshots, and venue-specific conclusions for conferences and journals. No overall score is emitted by default. Report Markdown (and PDF, if `reportlab` is installed) links each `[E2]` to the Evidence Index.
- A `literature_grounding` stage that emits multi-perspective queries and optional offline snapshots behind the network declaration gate.
- P0–P2 contract stages: `execute_review` (fixture JSON, deterministic checks, or an OpenAI-compatible chat adapter), snapshot excerpts, typed `Anchor`s, a report export gate, `ledger` / `agenda`, read-only `workspace_inspect`, `integrity`, and matched-setting `novelty`.
- Figure-grounded review: a visual route (`figure_grounded_v1`) plus prompt packets that require inspecting every numbered figure on the sibling PDF page. Caption paraphrase is not inspection.
- Listed contributions: the 3–4 author-numbered items are extracted, retrieved against, and audited one by one for evidence completeness. A single paper-level novelty paragraph is not sufficient.
- First-read review writing: issues can carry a `FirstReadNote` (what the paper said, a quote, why it matters). Prompt packets prepend a shared writing contract. Reports lead with a how-to-read note and inline quotes so a first-time reader can follow the argument without already knowing the paper.
- Declared ICLR rating (`rate`, coefficient set `apr-iclr-cap-v1`): seven anchored bands (`sar_bands_v1`, scores 2/4/6/8/10), fatal-flaw caps, and a snap-down onto 1/3/5/6/8/10. The gate is `min(claims, soundness)`. Caps and exemptions are stored on the score. This is not a fitted regression. `calibrate` orders several packages and requires `same_bin_justified` when two neighbors share a bin.
- Visual and format review (`pip install 'autopaperreview[visual]'`): `render_pages` writes hashed page PNGs, `format_check` applies a pinned venue profile (`iclr2027` or `aaai2027`), and `visual_inspect` accepts an adapter whose findings cite the page-image hash and a non-caption locator. `AgentFindingsInspector` reads a JSON drop after the pages were opened. `OpenAICompatibleVisionInspector` sends those page images to an OpenAI-compatible chat model. `HostedVisionInspector` can POST page images only when the network policy is `allow` and an API key is set; a missing key sends nothing. Findings move the rating only through fatal classes or clause ceilings. `init` mounts `visual_qa_v1` as route M9.

See [Stanford SAR vs AutoPaperReview](docs/stanford-sar-comparison.md) for an honest comparison with paperreview.ai, [feature gaps versus stronger open experiments](docs/oss-experiment-gaps.md) for a prioritized review against DeepReviewer 2.0, ResearchArena, AgentReview, MARG, OpenReviewer, PaperQA2, and related work, and [first-read review writing](docs/first-read-review.md) for the ICLR / ARR / Reviewer2 / Conventional Comments strategy this slice implements. This repository does not claim Stanford's ICLR Spearman numbers.

The core does not depend on LangChain, LangGraph, Prefect, Docling, GROBID, PaperQA, LiteLLM, or a specific model API. Those systems belong behind adapters so their release cadence does not become the review database schema.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[pdf,visual]"
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

The synthetic example sets `bilingual = true` on the report stage so `review_report.md` contains English and Simplified Chinese section by section, including dimension rationales. After a successful run, `review_report.md` (and `review_report.pdf` if `reportlab` is installed) is also copied next to `review.toml`. Evidence citations such as `[E2, E3]` link each id to the Evidence Index. Inspect `stages/report/review_report.md`, `stages/prompt_packets/M3.md`, `stages/integrity/integrity.json`, and `stages/novelty/novelty.json` under the run directory.

`init` writes a deterministic adapter DAG that can review a replaced `manuscript.txt` without a model API. The synthetic example uses `FixtureReviewAdapter` plus a local snapshot so literature, ledger, integrity, and novelty records are visible. Both are equal to the chat adapter below: packets plus externally written JSON, or a model call inside the same `run`.

The run ID is derived from the project ID, source hash, and configuration hash. Repeating the command reuses successful stages only when their signatures and output hashes still match.

Each run records a normalized configuration snapshot, source hash, handler version, stage signature, command input hashes, and relative output artifacts under the configured run root.

## Start a review project

```bash
autopaperreview init reviews/my-paper --project-id my-paper --title "My paper review"
autopaperreview validate reviews/my-paper/review.toml
autopaperreview run reviews/my-paper/review.toml
```

## Closed-loop model review

`OpenAICompatibleAdapter` reads the prompt packets already written for contributions, ledger and agenda items, the seven rating dimensions, and fatal classification. `OpenAICompatibleVisionInspector` reads the visual packet and sends each rendered page image. The pipeline validates the JSON, retries a schema failure once, and rejects an item whose excerpt or page hash does not resolve. It does not fall back to the deterministic adapter.

Loopback is the default. A remote host also needs `allow_remote = true`, `network_policy = "allow"`, and a key in the environment. The key is not written to the run. A remote call sets `manuscript_left_machine` on the run manifest.

```toml
[project]
network_policy = "deny"

[[stages]]
id = "review"
type = "execute_review"
depends_on = ["prompt_packets", "ledger"]
[stages.params]
adapter = "autopaperreview.model_exec:OpenAICompatibleAdapter"
base_url = "http://127.0.0.1:8000/v1"
model = "local-reviewer"
api_key_env = "APR_MODEL_API_KEY"
allow_remote = false
judgments = ["contributions", "ledger", "dimensions", "fatal"]

[stages.params.packet_routes]
contributions = "M7"
ledger = "M0"
dimensions = "M3"
fatal = "M2"
```

Set `include_source_text = true` on `prompt_packet` so the packet contains the manuscript. `base_url` is the API root (`.../v1`), not a URL with a query or a key. For a remote endpoint, set `allow_remote = true`, `requires_network = true`, and `network_policy = "allow"`, then export `APR_MODEL_API_KEY`.

The same client inspects page images:

```toml
[[stages]]
id = "visual"
type = "visual_inspect"
depends_on = ["render_pages", "prompt_packets"]
[stages.params]
adapter = "autopaperreview.model_exec:OpenAICompatibleVisionInspector"
base_url = "http://127.0.0.1:8000/v1"
model = "local-vision"
packet_route = "M6"
allow_remote = false
```

To keep the model outside the process, use `adapter = "autopaperreview.adapters:FixtureReviewAdapter"` with `fixture_path`, or `AgentFindingsInspector` with `findings_path`. `rate` still applies `apr-iclr-cap-v1`. The chat adapter does not emit the overall score.

Raw requests and responses are stored under `stages/<id>/model/` with their SHA-256 on the stage record. A second `run` reuses that stage while the signature matches and does not call the endpoint again.

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
5. Manuscript text is local by default. Metadata-only or full-text network use requires an explicit policy change. Real manuscripts, reviews, and page renders are never committed; see [Confidentiality](CONFIDENTIALITY.md).
6. Third-party objects are converted immediately into canonical AutoPaperReview models.

The built-in network policy validates declared requirements but is not an operating-system network sandbox. Stage plugins and commands are trusted local code; use containers or another sandbox when executing untrusted integrations.

Command stages inherit an explicit environment-variable allowlist rather than the complete parent environment. Add secret variable names with `inherit_env`; their values affect the cache signature but are not written to command records. Never put secret values in the TOML `env` table because configuration is snapshotted.

See [Architecture](docs/architecture.md), [Stanford SAR comparison](docs/stanford-sar-comparison.md), [Open-source reference stack](docs/oss-reference-stack.md), and [Maintenance policy](docs/maintenance.md).
