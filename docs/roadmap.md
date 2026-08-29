# Roadmap

The roadmap separates the implemented `0.1.x` contract from planned data-model and adapter work. Dates are intentionally omitted; each milestone ships only when its schema, migrations, tests, and privacy implications are complete.

## v0.1 baseline

Implemented in the current package:

- Strict canonical review, artifact, stage, and run models.
- TOML configuration with DAG validation.
- Hash-derived run identity, stage signatures, output validation, resume, and atomic promotion.
- Built-in ingest, command, prompt-packet, legacy-import, exact-normalized consensus, and report stages.
- Deny-by-default declared network policy.
- Entry-point stage plugins.
- Legacy bare-issue and package migration.
- JSON Schema export and a synthetic end-to-end example.

Known v0.1 constraints:

- Route output enters the pipeline as `ReviewIssue`; there is no immutable raw observation entity.
- Evidence and issue locations are free-form strings.
- Stage handlers have versions and Pydantic parameter validation, but plugins still lack a complete versioned adapter contract and capability manifest.
- Consensus chooses one representative record and records support, but opposition and contradiction are not first-class.
- Declared network policy is not an operating-system sandbox.
- Parser, model, retrieval, rendered-document QA, release packaging, and journal-platform adapters are not built in.

## Observation records

Introduce an immutable `ObservationRecord` emitted by a route or adapter before consensus.

Minimum fields:

- Stable observation ID and schema version.
- Manuscript/source artifact hash.
- Producer route, adapter version, prompt version, model/provider identifier, and optional seed.
- Independence group distinct from the route display ID.
- Proposed taxonomy/category, polarity, severity, confidence, title, evidence, impact, and required action.
- Evidence IDs and exact primary anchors.
- Creation attempt and parent artifact hashes.
- Lifecycle state without mutating the original observation.

Acceptance criteria:

- Imported legacy issues migrate to observations without data loss.
- Re-running consensus never overwrites or discards an observation.
- Consensus support counts unique independence groups, not raw record or route counts.
- Reports can trace every merged issue back to all contributing observations.

## Typed locators

Replace durable free-form locations with a discriminated locator family while retaining a human-readable display location.

Planned locator types:

- `TextOffsetLocator`: source artifact hash, encoding, start/end offsets, and excerpt hash.
- `DocxParagraphLocator`: DOCX hash, OOXML part, paragraph index or stable paragraph ID, optional run range.
- `DocxTableCellLocator`: DOCX hash, OOXML part, table/row/cell coordinates, optional paragraph/run range.
- `OoxmlLocator`: package part and constrained XPath-like selector for metadata, comments, fields, and revisions.
- `PdfRegionLocator`: rendered PDF hash, one-based page, coordinate system, bounding box, and optional text hash.
- `ImageRegionLocator`: image hash, pixel coordinate system, bounding box, and optional crop hash.
- `ExternalSnapshotLocator`: source snapshot artifact hash plus record or passage identifier.

Acceptance criteria:

- Every locator binds to an immutable artifact hash.
- Coordinate systems and page numbering are explicit.
- Locators can be rendered to display text without losing machine-readable coordinates.
- Extracted excerpts can be checked against an excerpt hash.
- Migrations preserve the original v0.1 location under legacy metadata when structured parsing is uncertain.

## Adapter contracts

Define a versioned adapter protocol independently of any parser, agent, retrieval, workflow, telemetry, or journal SDK.

Planned contract records:

- `AdapterSpec`: name, semantic version, code/image digest, supported request/result schemas, and determinism class.
- `CapabilitySet`: filesystem, network scope, secrets, CPU/GPU, manuscript sensitivity, and optional external services.
- `AdapterRequest`: role-bound artifact handles, normalized parameters, policy, and scratch directory.
- `AdapterResult`: raw-response artifacts, canonical records, observations, logs, warnings, and resource metadata.

Required behavior:

- No mutation of canonical packages, consensus decisions, or releases.
- No output outside the assigned scratch directory.
- Raw third-party responses are retained before normalization.
- Network and secret requirements are declared before execution.
- Contract tests cover success, timeout, malformed output, version mismatch, unavailable dependency, and policy denial.
- Core code remains independent of third-party response classes.

## Consensus and dissent

Replace representative-record merging with explicit decisions that preserve support, uncertainty, and disagreement.

Planned `ConsensusDecision` content:

- Stable decision ID and rule version.
- Candidate observation IDs and unique independence groups.
- Supporting, opposing, and abstaining members.
- Selected canonical claim and rationale.
- Severity/confidence derivation and any human override.
- Relationships such as `duplicate_of`, `refines`, `contradicts`, `supersedes`, and `related_to`.
- Dissent summaries and unresolved questions.

The grouping fingerprint should use manuscript hash, taxonomy, polarity, normalized claim key, and sorted primary anchors. It must exclude prose language, severity, and confidence so editorial wording changes do not silently split or merge claims.

Acceptance criteria:

- No observation is discarded during grouping.
- Contradictions are visible in canonical JSON and reports.
- A human can accept, reject, split, merge, or supersede a proposed decision with an auditable record.
- Consensus can be reproduced from frozen observations and a versioned rule.
- Semantic/LLM clustering remains advisory until deterministic anchors and human review confirm a merge.

## Later milestones

After the four data-boundary milestones above:

- Parser adapters with dual-parser comparison and extraction QA.
- External-source snapshot and literature retrieval adapters. v0.1.1 ships query generation, snapshot records, and an optional declared retriever hook; it does not add Tavily, OpenAlex, PaperQA, a vector index, or a hosted search service.
- Typed model-execution adapters with prompt/response provenance.
- Rendered PDF/DOCX structural and visual QA records.
- Release manifests that bind QA attestations to exact artifact hashes.
- Optional durable workflow runner for multi-day or approval-heavy reviews.
- OpenReview and OJS import/export adapters.
- Stable `1.0` canonical schemas after real migrations from multiple review projects.

## Scope guard

Do not add a database, hosted service, general agent framework, semantic vector index, or workflow engine to solve a problem the explicit local DAG can already handle. New infrastructure must correspond to a demonstrated review workflow and preserve the canonical data boundary.
