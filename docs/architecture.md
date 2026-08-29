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
- Native PDF/DOCX layout extraction, literature retrieval, journal submission, or model-provider clients.
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
| `ReviewIssue` | The current canonical issue record, including localized prose, severity, confidence, references, and consensus metadata. |
| `ReviewPackage` | A validated collection of manuscript, route, source, evidence, issue, strength, acceptance-gate, optional SAR-shaped claim/score, query, and snapshot records. |
| `DimensionScore` | One of the seven SAR dimensions, a 0–10 value, rationale, and required evidence IDs. |
| `OverallScore` | Optional overall score with required method/provenance. Absent by default; never an implicit LLM 0–10. |
| `RelatedWorkQuery` / `RetrievedSourceSnapshot` | Multi-perspective search queries and retrieved source snapshots (query IDs, arXiv ID, date, abstract vs full-text). |
| `ReviewClaim` | SAR-shaped summary/strength/weakness/question/comment text that must cite evidence IDs. |
| `StageRecord` | Stage signature, status, attempt number, outputs, timestamps, metadata, and error. |
| `RunManifest` | Source/config identity, immutable configuration-snapshot artifact, framework version, policy, stages, and run lifecycle. |

`ReviewPackage` validates unique issue IDs and route/source/evidence references. Artifacts use relative logical paths when they are inside the project workspace or run directory. Absolute paths are permitted for imported external files but should not be released.

Two v0.1 fields are intentionally transitional:

- `ReviewIssue.location` and `EvidenceRecord.locator` are display strings, not typed document coordinates.
- `ReviewIssue` represents both an imported route finding and a merged issue. Raw immutable observation records are not yet a separate entity.

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
| `prompt_packet` | Builds versioned local prompt packets for declared review routes. It does not call a model. |
| `import_issues` | Migrates legacy issue/package JSON into the canonical package. |
| `consensus` | Groups by explicit `consensus_key` or exact normalized category/title/location, then selects one representative record. |
| `literature_grounding` | Generates multi-perspective related-work queries from local text, optionally attaches a snapshot fixture or a declared retriever, and emits source/evidence artifacts. Live retrieval requires a network declaration. |
| `report` | Emits canonical JSON, Markdown with SAR-shaped sections when present, and a small summary. |

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
- [Maintenance policy](maintenance.md)
- [Roadmap](roadmap.md)
