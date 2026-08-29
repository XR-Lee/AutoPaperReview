# Open-source reference stack

This is an integration decision snapshot dated 2026-08-21. It is not a dependency lockfile. Before adopting an optional component, verify its current release, license, model-weight terms, security posture, and supported runtime.

The governing rule is simple: AutoPaperReview owns its canonical models and provenance. Parsers, retrieval services, workflow engines, model clients, telemetry backends, and journal systems are replaceable adapters.

## Verified version snapshot

The following versions and repository licenses were verified on 2026-08-21. They are evaluation anchors, not automatic dependency selections.

| Component | Verified version | Repository license | Core status |
|---|---:|---|---|
| [Docling](https://github.com/docling-project/docling) | `v2.121.0` | MIT | Optional parser adapter |
| [GROBID](https://github.com/grobidOrg/grobid) | `0.9.1` | Apache-2.0 | Optional academic-PDF sidecar |
| [Pydantic](https://github.com/pydantic/pydantic) | `v2.13.4` | MIT | Core dependency |
| [PydanticAI](https://github.com/pydantic/pydantic-ai) | `v2.33.0` | MIT | Optional model adapter |
| [PaperQA2](https://github.com/Future-House/paper-qa) | `v2026.08.12` | Apache-2.0 | Optional evidence worker |
| [Prefect](https://github.com/PrefectHQ/prefect) | `3.8.3` | Apache-2.0 | Optional external workflow adapter; not core |
| [OpenTelemetry Python](https://github.com/open-telemetry/opentelemetry-python) | `v1.44.0` | Apache-2.0 | Optional instrumentation API |
| [Promptfoo](https://github.com/promptfoo/promptfoo) | `0.122.0` | MIT | Optional external regression runner |
| [DeepEval](https://github.com/confident-ai/deepeval) | `python-v4.1.9` | Apache-2.0 | Optional model-evaluation adapter; not core |
| [OpenReview Python client](https://github.com/openreview/openreview-py) | `v2.5.0` | MIT | Optional journal-platform adapter |

## Core

| Component | Role | License / snapshot | Decision |
|---|---|---|---|
| [Pydantic](https://github.com/pydantic/pydantic) | Strict models, validation, and JSON Schema | MIT; `2.13.4` observed | Direct core dependency within the supported `2.x` range. Persist only AutoPaperReview models. |
| Python standard library | TOML, hashing, subprocesses, filesystem, DAG execution, and unittest | PSF | Prefer for the orchestration layer while the workflow remains small. |

## Document parsing

| Component | Strength | Integration boundary | Main risks |
|---|---|---|---|
| [Docling](https://github.com/docling-project/docling) | Multi-format PDF/DOCX/image parsing and normalized document output | Preferred `DocumentParser` adapter; immediately map output to canonical source, span, table, and artifact records | Model downloads, fast output-schema evolution, and imperfect OCR/table/formula/layout extraction |
| [GROBID](https://github.com/grobidOrg/grobid) | Academic PDF structure, references, TEI, and optional coordinates | Isolated Docker/JVM sidecar; retain raw TEI as an artifact and map selected fields to canonical records | PDF-only focus, heuristic structure, optional network consolidation, and coordinate configuration |
| [Pandoc](https://github.com/jgm/pandoc) | Deterministic document conversion and a broad format surface | Subprocess fallback adapter | GPL-2.0 executable boundary, loss of page coordinates, and incomplete preservation of Word comments/revisions/floating objects |
| [Marker](https://github.com/datalab-to/marker) | Optional second parser for scans, equations, and complex layouts | Isolated optional adapter | Code and model weights have different terms; model-weight licensing must be reviewed separately |

Docling and GROBID are complementary. Neither library's native object model or TEI shape becomes the AutoPaperReview database schema. Parser disagreement should be retained as separate artifacts until adjudicated.

MinerU is not a default v0.1 recommendation because its licensing and distribution conditions require a separate legal review. It may be evaluated later as an isolated sidecar.

## Citations and literature

| Component | Role | Integration guidance |
|---|---|---|
| [OpenAlex](https://github.com/ourresearch/openalex-guts) | Works, authors, identifiers, and citation-graph metadata | Primary discovery adapter. Cache the response artifact and record OpenAlex ID, DOI, query, and retrieval date. Do not treat an abstract as full-text evidence. |
| [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) | DOI, title, author, venue, and date verification | Metadata-resolution adapter. Preserve the original response because records can be corrected after retrieval. |
| [Citation.js](https://github.com/citation-js/citation-js) | BibTeX/RIS/CSL-JSON normalization and citation rendering | Optional Node subprocess adapter. Preserve raw input and convert normalized records into AutoPaperReview sources. |
| [PaperQA2](https://github.com/Future-House/paper-qa) | Scientific retrieval and evidence-oriented question answering | Optional evidence-worker adapter, not the owner of the document store or workflow |
| Tavily (hosted by Stanford SAR) | Web/arXiv search used by paperreview.ai | Not a core dependency. AutoPaperReview records query/snapshot artifacts; a live retriever is an optional declared adapter. |

Retrieval interfaces should remain capability-based, for example `search_works`, `resolve_doi`, `fetch_metadata`, `locate_fulltext`, and `retrieve_passages`. Provider response classes must not leak into canonical issue or evidence records.

## Model execution and workflow

| Component | Role | Decision |
|---|---|---|
| [PydanticAI](https://github.com/pydantic/pydantic-ai) | Typed model tools/results and provider-neutral model execution | Preferred optional model adapter. Snapshot prompts and raw responses before normalization. |
| [LangGraph](https://github.com/langchain-ai/langgraph) | Durable checkpoints, pause/resume, and human approval in larger workflows | Defer until multi-day runs or complex approval branches justify it. Hide behind a `WorkflowRunner` adapter. |
| [Prefect](https://github.com/PrefectHQ/prefect) | Scheduling, retries, deployments, and operational workflow state | Optional external `WorkflowRunner` adapter for requirements that exceed the local DAG. It is not a core dependency and must not own canonical review state. |
| [Instructor](https://github.com/instructor-ai/instructor) | Structured-output repair for model providers | Use only for a provider gap not covered by the selected model adapter; do not add alongside PydanticAI by default. |

The explicit Python DAG remains the v0.1 workflow engine. Canonical findings must never inherit a framework's message or state classes.

## Evaluation and observability

| Component | Role | Integration guidance |
|---|---|---|
| [OpenTelemetry Python](https://github.com/open-telemetry/opentelemetry-python) | Portable spans and metrics | Optional core instrumentation API with pluggable exporters. Hash or redact identifiers; never emit manuscript text or full prompts as attributes. |
| [Promptfoo](https://github.com/promptfoo/promptfoo) | Prompt and provider regression tests | External CI runner against frozen fixtures and the harness CLI/API. Deterministic checks remain authoritative. |
| [DeepEval](https://github.com/confident-ai/deepeval) | Model-output and RAG evaluation | Optional adapter or external CI runner. It is not core, and model-judged scores cannot replace deterministic evidence checks or human adjudication. |
| [Langfuse](https://github.com/langfuse/langfuse) | Optional trace backend | Connect only through OpenTelemetry. Review separately licensed enterprise directories before deployment. |
| [Ragas](https://github.com/explodinggradients/ragas) | Optional RAG-oriented evaluation | Supplemental only; model-judged metrics are not release gates for factual manuscript findings. |

## Journal platforms

| Component | Role | Integration guidance |
|---|---|---|
| [OpenReview Python client](https://github.com/openreview/openreview-py) | Submission/review import and export | Venue-specific adapter with test-venue contract tests. Keep Invitation, Note, and Edge schemas outside canonical models. |
| [Open Journal Systems](https://github.com/pkp/ojs) | Journal workflow integration | REST/plugin adapter or separate process. Do not depend on its database schema. |
| [Kotahi](https://github.com/kotahi/kotahi) | Custom publishing workflow candidate | Later adapter candidate; deployments are commonly customized and require installation-specific testing. |

## Adapter acceptance checklist

An optional component is acceptable only when its adapter:

1. Declares version, code or image digest, license, deterministic/stochastic behavior, filesystem needs, network scope, and secret names.
2. Receives artifact handles or canonical request objects rather than unrestricted repository state.
3. Writes raw third-party responses as immutable artifacts before normalization.
4. Emits schema-valid canonical records and evidence bound to exact input/output hashes.
5. Keeps all temporary output inside the assigned stage directory.
6. Has contract fixtures for success, malformed output, timeout, missing dependency, and policy denial.
7. Does not persist third-party Python objects or provider-specific response models.
8. Documents privacy and redistribution implications for code, data, and model weights separately.

## Minimal recommended deployment

For v0.1, use the built-in core plus Pydantic. Add only the adapters required by a review:

- Docling for general document parsing.
- GROBID only when academic PDF structure or citation extraction needs a second parser.
- OpenAlex and Crossref for metadata and discovery.
- PydanticAI for typed model execution.
- OpenTelemetry for local or controlled tracing.
- Promptfoo for external prompt regression tests.

PaperQA2, LangGraph, Langfuse, and journal-platform adapters should follow after the corresponding workflow requirement exists. Prefect and DeepEval are explicitly optional, non-core integrations and should be added only behind their respective workflow and evaluation adapter contracts.
