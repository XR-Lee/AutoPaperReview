# Roadmap

The roadmap separates the implemented `0.1.x` contract from planned data-model and adapter work. Dates are intentionally omitted; each milestone ships only when its schema, migrations, tests, and privacy implications are complete.

## v0.1 baseline

Implemented in the current package (`0.1.10`):

- Strict canonical review, artifact, stage, and run models.
- TOML configuration with DAG validation.
- A project run directory, per-stage signatures, output validation, resume, and atomic promotion.
- Built-in ingest, command, prompt-packet, legacy-import, exact-normalized consensus, and report stages.
- Contract stages from 0.1.5: `execute_review`, `ledger`, `agenda`, `workspace_inspect`, `integrity`, matched-setting `novelty`, snapshot excerpts, `Anchor`, and a report export gate.
- `literature_grounding`, and `related_work_search` against arXiv, OpenAlex, and Semantic Scholar when that stage is configured.
- `render_pages`, `format_check` (`iclr2027`, `aaai2027`), `visual_inspect`, and `autopaperreview annotate` for a manuscript PDF.
- `rate` (`apr-iclr-cap-v2` by default) and `calibrate_batch`. Opt-in `apr-fusion-v1` ranks by the locked candidate `forecast-k1`.
- An OpenAI-compatible chat adapter for text judgments and page images. Fixture JSON and the deterministic adapter remain equal options.
- Deny-by-default declared network policy.
- Entry-point stage plugins.
- Legacy bare-issue and package migration.
- JSON Schema export and a synthetic end-to-end example.

Known v0.1 constraints:

- Route output enters the pipeline as `ReviewIssue`; there is no immutable raw observation entity.
- `ReviewIssue.location` is still a free-form string. An optional `Anchor` can record `display`, `text_offset`, `quote`, `pdf_region`, `docx_paragraph`, or `external_snapshot`, and its excerpt hash must match the excerpt. Table-cell, OOXML, and image-region locators from the typed-locator milestone are not shipped. Excerpt checks in the chat adapter are against manuscript text, not a layout parser.
- Stage handlers have versions and Pydantic parameter validation, but plugins still lack a complete versioned adapter contract and capability manifest.
- Consensus chooses one representative record and records support, but opposition and contradiction are not first-class. Independence is distinct `route_id`s.
- Declared network policy is not an operating-system sandbox.
- A PDF/DOCX layout parser and journal-platform import/export are not built in. `RouteKind.human` has no stage handler.

## Priority after 0.1.3

The four data-boundary milestones below remain the schema work. The comparison that set their order is [feature gaps versus stronger open experiments](oss-experiment-gaps.md), updated through `0.1.10`.

Shipped, and no longer the next slice:

- v0.1.5: snapshot excerpts, `Anchor`, export gate, literature on prompt packets, `doctor`/`init`, `ledger`/`agenda`, `workspace_inspect`, `integrity`, matched-setting `novelty`, and `execute_review` with deterministic and fixture adapters.
- v0.1.6: first-read review writing. See [first-read review writing](first-read-review.md).
- v0.1.7–0.1.8: declared ICLR rating `apr-iclr-cap-v1`, page renders, format check, visual inspect, AAAI-27 profile.
- v0.1.9: related-work search and retrieval caps. The confidentiality check landed in that same release.
- v0.1.10: OpenAI-compatible adapter, report-only `calibrate-eval`, `apr-iclr-cap-v2` (with `math.fsum` so Python 3.11 snaps the same bins as 3.12 and 3.13), the fusion rank now locked to `forecast-k1`, and manuscript PDF annotation. PydanticAI is still not an adapter.

Still open after 0.1.10:

1. The rest of the typed-locator family, and excerpt verification against parsed PDF/DOCX text. Quote search for annotation is not that parser.
2. A citation-support check (the citing sentence against the retrieved paper). Related-work search already resolves arXiv, OpenAlex, and Semantic Scholar metadata. Crossref is not a client.
3. A provider-neutral model SDK and a seed on the model call. The OpenAI-compatible adapter already snapshots prompts and raw responses for text judgments and visual page images.
4. ObservationRecord, dissent-preserving consensus, a parser adapter, and journal-platform adapters, as designed below.

Do not take MinerU, PASA, Tavily, ChatArena, a vector index, or a fitted ICLR map into core.

`calibrate-eval` measures a declared score against public human scores (Spearman, Kendall tau-b, MAE, bin agreement, accept/reject AUC, a bin confusion table, and score spread). The default formula is `apr-iclr-cap-v2`. `--coefficient-set apr-fusion-v1` ranks by `forecast-k1` and does not fit that rank. The measurement must not be used to update weights, caps, bins, or the locked candidate. Cached manuscripts and forum ids stay outside git. See [public calibration eval](public-calibration.md).

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
- External-source snapshot and literature retrieval adapters. v0.1.1 shipped query generation and snapshot records. v0.1.9 added optional live metadata search (arXiv, OpenAlex, Semantic Scholar) without a Tavily, OpenAlex, or PaperQA package dependency, a vector index, or a hosted search service. A citation-support verdict is still open.
- Typed model-execution adapters with prompt/response provenance. The OpenAI-compatible adapter covers one chat API. A versioned adapter contract for other providers is still the milestone above.
- Rendered PDF/DOCX structural and visual QA records. v0.1.7 shipped page renders, a format checklist, and visual findings that cite the page-image hash. v0.1.10 annotates cited quotes on a manuscript PDF. A layout parser is still open.
- Release manifests that bind QA attestations to exact artifact hashes.
- Optional durable workflow runner for multi-day or approval-heavy reviews.
- OpenReview and OJS import/export adapters.
- Stable `1.0` canonical schemas after real migrations from multiple review projects.

## Scope guard

Do not add a database, hosted service, general agent framework, semantic vector index, or workflow engine to solve a problem the explicit local DAG can already handle. New infrastructure must correspond to a demonstrated review workflow and preserve the canonical data boundary.
