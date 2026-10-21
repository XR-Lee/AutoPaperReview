# Architecture

AutoPaperReview is a local-first execution harness for repeatable manuscript review. Its durable boundary is the canonical review and run data, not any parser, model API, workflow framework, or journal platform.

This document describes the implemented `0.1.x` architecture. Planned additions are tracked in [Roadmap](roadmap.md).

## Goals

- Bind review claims and generated files to immutable source hashes.
- Run deterministic and human- or model-assisted review routes in an explicit DAG.
- Resume a run only when stage signatures and recorded output hashes still match.
- Keep unpublished manuscripts local unless a project explicitly relaxes the network policy.
- Convert third-party output into strict AutoPaperReview models before it becomes durable state.
- Keep the core small enough to inspect, test, and migrate without a workflow service.

## Non-goals for v0.1

- A hosted review service or multi-tenant database.
- A general agent framework.
- An operating-system network sandbox. The network policy validates declared requirements; adapters and command stages still run as trusted local code.
- Semantic clustering or adjudication by an LLM.
- A general PDF/DOCX layout parser, literature retrieval, journal submission, or a multi-provider model SDK. One OpenAI-compatible chat adapter is bundled. Page PNGs and the ICLR format checklist are a narrow optional stage, not a parser.
- A stable plugin ABI beyond the documented Python entry-point and stage-handler surface.

## System boundary

```text
review.toml
  + manuscript/source file
  + packaged or project-specific prompts
  + imported review input
          |
          v
  config validation and DAG ordering
          |
          v
  stage attempts in isolated scratch directories
          |
          v
  atomic promotion of successful stage outputs
          |
          v
  run_manifest.json + hash-addressed artifacts
          |
          v
  canonical JSON and Markdown report artifacts
```

The core package owns configuration, canonical models, hashing, artifact records, stage registration, execution, migration, reporting, committed JSON Schemas, and versioned route-prompt assets. Optional integrations belong behind adapters or stage plugins.

## Canonical models

The v0.1 models are strict Pydantic models with unknown fields rejected.

| Model | Responsibility |
|---|---|
| `Artifact` | Logical path, SHA-256, size, media type, role, producer, and metadata. |
| `ReviewRoute` | A declared deterministic, executable, prompt, literature, visual, or human review route. |
| `SourceRecord` | A manuscript or external source identity and optional immutable digest. |
| `EvidenceRecord` | A claim and locator tied to a source and, when available, an artifact/excerpt hash. |
| `ReviewIssue` | The current canonical issue record, including localized prose, severity, confidence, references, consensus metadata, and an optional `FirstReadNote` (restated claim, quote, explanation) for a reader seeing the paper for the first time. |
| `ReviewPackage` | A validated collection of manuscript, route, source, evidence, issue, strength, acceptance-gate, optional SAR-shaped claim/score, query, and snapshot records. |
| `DimensionScore` | One of the seven SAR dimensions, a 1–10 value, rationale, and required evidence IDs. |
| `VenueConclusion` | Conference or journal decision label. It is not computed from the seven scores. |
| `OverallScore` | Optional overall score with required method/provenance. Absent until `rate` writes `declared_formula` / `apr-iclr-cap-v1`. Never an implicit LLM 0–10 and never a fitted regression. |
| `RelatedWorkQuery` / `RetrievedSourceSnapshot` | Multi-perspective search queries and retrieved source snapshots (query IDs, excerpt, arXiv ID, date, abstract vs full-text, optional task/dataset/metric). |
| `Anchor` | Typed locator (display, text offset, quote, PDF region, DOCX paragraph, external snapshot) with optional excerpt hash. |
| `LedgerClaim` / `AgendaQuestion` | Manuscript-only claim–evidence–risk ledger and investigation agenda. |
| `IntegrityRecord` | Artifact-aware reference, results, and reproducibility checks. |
| `NoveltyAssessment` | Matched-setting novelty tag. Overlap evidence requires the same task, dataset, and metric. |
| `ReviewClaim` | SAR-shaped summary/strength/weakness/question/comment text that must cite evidence IDs. |
| `StageRecord` | Stage signature, status, attempt number, outputs, timestamps, metadata, and error. |
| `RunManifest` | Source/config identity, immutable configuration-snapshot artifact, framework version, policy, stages, and run lifecycle. |

`ReviewPackage` validates unique issue IDs and route/source/evidence references. Artifacts use relative logical paths when they are inside the project workspace or run directory. Absolute paths are permitted for imported external files but should not be released.

`ReviewIssue.location` and `EvidenceRecord.locator` remain display strings. Prefer the optional `anchor` field for machine-readable coordinates. `ReviewIssue` still represents both an imported route finding and a merged issue; raw immutable observation records are not yet a separate entity.

## Configuration and DAG

`review.toml` defines:

- Project identity, title, source, run root, default language, copy policy, and network policy.
- Review routes and their prompt/model declarations.
- Ordered stage definitions, parameters, and dependencies.

The source distribution ships default route assets under `src/autopaperreview/prompts/`. Routes still reference an explicit path and prompt version, and the prompt file hash participates in the corresponding stage signature. Projects may replace these assets without changing canonical schemas.

Configuration validation rejects duplicate IDs, unknown dependencies, self-dependencies, cycles, disabled dependency chains, unknown stage types, invalid handler parameters, missing prompt routes, incompatible declared network access, and missing local inputs. The runner performs a deterministic topological traversal; no external scheduler is required.

## Stage lifecycle

Each enabled stage follows this lifecycle:

1. Serialize the parsed configuration to `config.snapshot.json`, hash it as an artifact, and bind it to the run manifest.
2. Resolve successful dependency records.
3. Compute a signature from framework version, handler type/version, stage configuration, source hash, dependency artifact hashes, handler-specific material, and network policy.
4. Reuse an earlier result only when its signature matches and every output still exists with the recorded hash.
5. Otherwise execute in a unique `.attempts` directory.
6. Validate that every declared output exists inside that attempt directory.
7. Atomically promote the completed attempt to `stages/<stage-id>`, preserving the previous completed directory if promotion fails.
8. Record success or failure in `run_manifest.json` through an atomic file replacement.

The run ID is derived from project ID, source hash, and configuration hash. It identifies a source/configuration combination; stage signatures provide the finer cache boundary.

## Built-in stages

| Stage | Current behavior |
|---|---|
| `ingest` | Records the source and optionally copies it into the run. |
| `command` | Executes an argument array with `shell=False` and an explicit environment allowlist; records stdout/stderr, logical placeholder paths, declared outputs, environment-key names, and hashes of command input files, then imports declared outputs. |
| `prompt_packet` | Builds versioned local prompt packets for declared review routes. It does not call a model. Every packet prepends the first-read writing contract. Literature routes can attach query and snapshot excerpts. When a sibling `manuscript.pdf` exists, packets tell the reviewer to inspect every numbered figure on the PDF page; caption paraphrase is not inspection. |
| `import_issues` | Migrates legacy issue/package JSON into the canonical package. |
| `execute_review` | Calls a declared `module:attribute` adapter and validates a `ReviewPackage`. Bundled adapters are fixture import, deterministic manuscript checks, and `OpenAICompatibleAdapter` (OpenAI-compatible chat). |
| `ledger` / `agenda` | Builds a claim–evidence–risk ledger and investigation questions from local text. When the paper lists numbered contributions, those items are the ledger units; agenda asks evidence completeness and a matched-setting comparator per item. |
| `workspace_inspect` | Read-only inventory of declared workspace paths. Refuses path escape. |
| `integrity` | Reference, results, and reproducibility records from snapshots and workspace files. |
| `novelty` | Matched-setting novelty tags. A snapshot is not overlap evidence unless task, dataset, and metric match. |
| `consensus` | Groups by explicit `consensus_key` or exact normalized category/title/location, then selects one representative record. |
| `literature_grounding` | Generates multi-perspective, multi-specificity related-work queries from local text and optional agenda questions, plus one same-problem and one related-technique query per listed contribution. Optionally attaches a snapshot fixture or a declared retriever. Live retrieval requires a network declaration. |
| `render_pages` | Optional `visual` extra. Rasterizes each PDF page to a hashed PNG and a manifest. Fails closed when a visual route is enabled and the sibling PDF is missing. |
| `format_check` | Deterministic ICLR 2027 checklist from `profiles/iclr2027.json`: 9-page main text, US Letter, margin floor, anonymity metadata and page-1 email, template marker, font embedding, raster size, blank caption pages. Desk-reject classes set `format_desk_reject`. Other failures set a `writing_clarity` clause ceiling. |
| `visual_inspect` | Adapter contract. `AgentFindingsInspector` reads a findings drop produced after the pages were opened. `OpenAICompatibleVisionInspector` sends each page PNG to an OpenAI-compatible chat endpoint. `HostedVisionInspector` POSTs page PNGs only under `network_policy = allow` with an optional API key; a missing key sends nothing. Findings carry the page-image hash, a bbox, a non-caption visible mark, and an optional severity, fatal class, or clause. |
| `rate` | Pure function. Anchored bands, fatal-flaw caps, and snap-down onto 1/3/5/6/8/10 under `apr-iclr-cap-v1`. Records `cap_ids` and the weighted/gate terms. Does not call a model. |
| `calibrate_batch` | Orders several packages by that formula. Neighbors in the same bin need `same_bin_justified`. Different active caps cannot share a bin. |
| `report` | Emits canonical JSON, Markdown with SAR-shaped sections when present, and a small summary. Reports open with a first-read how-to-read note and inline quotes. Evidence citations such as `[E2, E3]` link each id to the Evidence Index. Copies `review_report.md` next to `review.toml`. With the optional `pdf` extra, also writes `review_report.pdf` with the same internal links. Optional export-gate budgets refuse an unauditable package, including optional first-read/quote and figure-citation budgets. `bilingual = true` or `languages = ["en", "zh-Hans"]` writes both languages into one Markdown file after filling missing translations, then refuses export if the package or Markdown still has unpaired English or Chinese prose. A declared formula replaces the "no overall score" sentence and the PDF footer. |

## Model execution

`execute_review` can run the bundled chat adapter instead of an external JSON drop. The model makes the judgment. The pipeline checks it before the stage is promoted.

The adapter reads the `prompt_packet` files already produced for the run. It calls an OpenAI-compatible `POST /chat/completions` endpoint once per judgment:

| Judgment | What the model returns | What the pipeline enforces |
|---|---|---|
| `contributions` | One item per listed contribution, with an exact excerpt | The excerpt must occur in the manuscript or a retrieved snapshot. Otherwise the item is rejected. |
| `ledger` | Ledger claims and agenda questions | Same excerpt rule. An agenda item that cites a rejected claim is rejected. |
| `dimensions` | All seven SAR dimensions, each with a `sar_bands_v1` clause id and an excerpt | The numeric score is the clause. An excerpt that does not resolve drops that dimension. `rate` still requires all seven. |
| `fatal` | `fatal_class` for issue ids the pipeline assigned (`I-<contribution id>`) | An unknown issue id is rejected. `rate` applies the cap. |
| visual page | `VisualFinding` records, with the page image attached for a multimodal model | The page-image hash and bbox must match the rendered PNG. A caption restatement fails validation. |

`OpenAICompatibleVisionInspector` is the visual path. It uses the same client from `visual_inspect`, after `render_pages`, and reads the visual route's prompt packet.

Invalid JSON or a schema mismatch is retried once. The retry appends the validation error. A second failure fails the stage. The adapter does not substitute deterministic output.

Every request and response is stored under `stages/<id>/model/` as the bytes that were sent and received. Each file's SHA-256 is listed on the stage record in `model_exchanges`. The API key is not part of those bytes. A successful stage is reused when its signature matches, so the endpoint is not called again. The signature includes the packet hashes, model name, and endpoint. It does not include the key.

Egress is separate from the project network policy:

- `127.0.0.0/8`, `::1`, and `localhost` are loopback. They are allowed while `network_policy` is `deny`. A key is optional.
- Any other host is remote. The stage must set `allow_remote = true`, the project must set `network_policy = "allow"`, and `api_key_env` must be set in the environment. The run manifest then sets `manuscript_left_machine` to true.
- A `base_url` must not carry a userinfo, query, or fragment. Put the key only in the environment variable.

Fixture JSON (`FixtureReviewAdapter`, `AgentFindingsInspector`) stays an equal path: packets are written, and a person or agent writes schema JSON from outside the process. Select that adapter when the model should not be called from `run`.

Example:

```toml
[project]
network_policy = "deny"

[[stages]]
id = "review"
type = "execute_review"
depends_on = ["prompt_packets"]
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

[[stages]]
id = "visual"
type = "visual_inspect"
depends_on = ["render_pages", "prompt_packets"]
[stages.params]
adapter = "autopaperreview.model_exec:OpenAICompatibleVisionInspector"
base_url = "http://127.0.0.1:8000/v1"
model = "local-vision"
packet_route = "M6"
```

A remote endpoint adds `allow_remote = true`, `requires_network = true`, and `network_policy = "allow"`. `rate` still computes the venue bin. The model does not emit the overall score.

The consensus stage counts distinct route IDs as independent support. This is a conservative v0.1 mechanism, not a scientific model of reviewer independence. It does not yet preserve structured opposition or contradictory findings; see [Roadmap](roadmap.md#consensus-and-dissent).

## Plugin boundary

Built-ins and external plugins implement the stage-handler interface and are registered under the `autopaperreview.stages` entry-point group. A stage handler provides:

- A stable type name.
- A handler version included in stage signatures and successful stage metadata.
- An optional Pydantic parameter model used during configuration validation.
- Additional material for its cache signature.
- A run method returning artifacts and metadata.

Plugins must write only inside their assigned stage directory, return hashable artifacts, and declare network requirements through configuration. Third-party parser, agent, retrieval, or platform objects must be converted into canonical JSON-compatible records before promotion.

The current interface is Python-level and intentionally small. Handler versioning and parameter validation are implemented; formal request/result schema negotiation, capability manifests, determinism classes, and contract test kits are roadmap work.

## Privacy model

The default network policy is `deny`; projects may opt into `metadata_only` or `allow`. Stages requesting network access must declare the need and scope before execution. This prevents accidental execution of a stage whose declared policy conflicts with the project, but it is not packet-level enforcement.

Manuscript text, author identities, prompts, model responses, credentials, and reviewer identities are sensitive. They must not be placed in telemetry attributes, source control, or release manifests. Optional adapters should use least-privilege filesystem and network access and should snapshot any external response used as evidence.

The bundled chat adapter snapshots each raw request and response inside the run directory. It sends manuscript text off the machine only for a non-loopback URL when `allow_remote` is set and the project network policy is `allow`. That fact is stored on the run manifest as `manuscript_left_machine`. API keys stay in the environment and are redacted from stored exchanges and error strings.

## Portability and reproducibility

A portable release should include:

- Canonical review package and report artifacts.
- Source and configuration hashes.
- Run manifest and stage output hashes.
- Adapter/plugin versions and code or image digests when adapters are used.
- Immutable external-source snapshots or their independently verifiable digests.
- Relative paths only.

Timestamps document execution history but are excluded from stage signatures. Deterministic or seeded stages that produce different content for the same signature should be treated as a reproducibility defect.

## Related documents

- [Open-source reference stack](oss-reference-stack.md)
- [First-read review writing](first-read-review.md)
- [Maintenance policy](maintenance.md)
- [Roadmap](roadmap.md)
