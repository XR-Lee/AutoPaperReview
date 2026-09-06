# Feature gaps versus stronger open review experiments

This is a review of AutoPaperReview `0.1.6` against stronger open-source and openly documented review experiments. It is a decision document, not a claim that this repository should become paperreview.ai, DeepReviewer, or The AI Scientist. v0.1.5 implemented the first P0–P2 slice (excerpts, anchors, export gate, execute_review, ledger/agenda, workspace inspect, integrity, matched-setting novelty). v0.1.6 added first-read review writing so comments are self-contained for a reader who has not yet internalized the paper. The remaining holes are live adapters and richer PDF locators.

The comparison date is 2026-08-29. Versions and paper claims belong to those projects; this repository has not reproduced their scores.

Related documents: [Architecture](architecture.md), [Roadmap](roadmap.md), [Stanford SAR comparison](stanford-sar-comparison.md), [Open-source reference stack](oss-reference-stack.md).

## What this repository already owns

AutoPaperReview is a local-first *harness*. That is a real product, not a missing LLM wrapper.

Strengths that most generation-first reviewers still lack:

- Canonical Pydantic records and committed JSON Schema, with unknown fields rejected.
- A TOML DAG with hash-derived run identity, stage signatures, atomic promotion, and resume.
- Deny-by-default network *declaration* (not an OS sandbox) and shell-free commands.
- Claims, dimension scores, and venue conclusions must cite evidence IDs.
- No default overall 0–10. Venue conclusions are separate from dimension scores and are not a fitted ICLR map.
- Packaged, versioned route prompts whose file hashes participate in cache signatures.
- A synthetic end-to-end example, migration, CI on CPython 3.11–3.13, and an honest SAR note.

The core still does not call a model, parse PDF/DOCX, or retrieve live literature. That boundary is intentional. The gaps below are about making the *contract* executable and auditable, not about absorbing another project's agent loop.

## Reference experiments

| Experiment | Why it is a better reference | What AutoPaperReview should take | What not to copy |
|---|---|---|---|
| [Stanford SAR / paperreview.ai](https://paperreview.ai/tech-overview) | Hosted, calibrated, live arXiv grounding; seven ICLR-shaped dimensions | Multi-specificity queries, evidence-bound scores, venue-aware *conclusions* (already started) | Hosted UX, Tavily as core, fitted ICLR coefficients, claimed Spearman 0.42 |
| [ResearchArena / How Far Are We From True Auto-Research?](https://arxiv.org/abs/2605.19156) | Shows SAR is a weak accept/reject discriminator and rewards polished framing | Artifact-aware dimensions: reproducibility, experimental rigor, reference integrity, results integrity; read-only workspace inspection | Treating a manuscript-only 0–10 as a release gate |
| [DeepReviewer 2.0](https://arxiv.org/html/2604.09590) (Weng et al., 2026; [ResearAI/DeepReviewer-v2](https://github.com/ResearAI/DeepReviewer-v2)) | Closest cousin: a *traceable review package* with anchors, a claim–evidence–risk ledger, matched-setting novelty, and an export gate | Page/paragraph anchors, ledger + investigation agenda, matched-setting novelty tags, export budgets | MinerU/PASA as core; a 196B agent loop; overlay PDF as the database |
| [AgentReview](https://github.com/Ahren09/AgentReview) (EMNLP 2024) | First LLM simulation of reviewer / author / area-chair phases; 37.1% decision swing from reviewer bias | Optional rebuttal and meta-review stages; independence groups; bias notes in run metadata | ChatArena as core; social-dynamics research as the product |
| [MARG / MARG-S](https://github.com/allenai/marg-reviewer) | Specialized expert groups (experiments, clarity, impact) beat a single general reviewer | Keep specialist *routes*; consensus must preserve dissent, not only a representative | GPT-4 discussion as the durable schema |
| [OpenReviewer](https://github.com/MaxIdahl/OpenReviewer) / Llama-OpenReviewer-8B | Fine-tuned critic; less score inflation than GPT-4 / Claude | Optional critic-model adapter; compare recommendation *distribution* to a venue | Shipping a fine-tuned 8B model in core |
| [CycleReviewer / DeepReviewer 1.0](https://wengsyx.github.io/Researcher) | Trained review models and DeepReview-Bench (rating, ranking, selection) | Optional model adapter + a *review-quality* eval slice | Training/serving reviewer weights as a v0.1 goal |
| [OpenAIReview](https://github.com/ChicagoHAI/OpenAIReview) / [reviewer2](https://github.com/harrywang/reviewer2) | Progressive summary, deep-check, quote-to-paragraph anchoring, citation accuracy | Typed quote/paragraph locators; citation-resolution records | Node/Inngest as core |
| [PaperQA2](https://github.com/Future-House/paper-qa) / [OpenScholar](https://arxiv.org/abs/2411.14199) | Evidence-oriented scientific RAG; citation-backed answers | Snapshot *content* + passage evidence; `search_works` / `retrieve_passages` adapters | Vector index or a 45M-paper datastore in core |
| [PaperMage](https://github.com/allenai/papermage) / Docling / GROBID | Layout-aware scientific parsing with page coordinates | Parser adapter that emits canonical spans, not native objects | Making TEI or magelib the review schema |
| [CiteME](https://github.com/bethgelab/CiteME/) / CiteCheck | Citation existence and claim-support verification | `reference_integrity` records: exists / metadata-corrupt / does-not-support-sentence | Treating a search snippet as a citation verdict |
| [The AI Scientist](https://sakana.ai/ai-scientist-first-publication/) | Full research loop plus an internal reviewer; still hallucinates citations | Reproducibility and citation-integrity routes; repeated-run checks | Automating paper *writing* in this repo |
| ReviewEval / TreeReview / PaperBench / CORE-Bench | Review *quality* metrics; hierarchical questions; artifact replication benches | Evaluate the review package (coverage, constructiveness, unsupported claims); first-read notes already encode constructiveness as restatement + quote + feasible repair | Replacing evidence hashes with LLM-as-judge scores |

DeepReviewer 2.0's own comparison table is the most useful framing: a fluent "full review" is not the same output as an evidence-bound package with traceable anchors. AutoPaperReview already chose the second target. The work left is to make that target *complete and executable*.

## Priority 0 — the current contract is incomplete

v0.1.5 ships the first executable slice of items 1–4 and 6–8: snapshot excerpts, typed anchors, a report export gate, literature attached to prompt packets, honest `doctor`/`init`, and `execute_review` with bundled deterministic/fixture adapters. A live model adapter is still not bundled. The remaining P0 hole is calling a real model behind a declared adapter.

### 1. The harness cannot produce a review

`prompt_packet` writes Markdown packets. It does not call a model. The synthetic example *imports* a finished `review_input.json`. `doctor` lists `pydantic_ai` even though no adapter exists.

Until a thin, declared model-execution adapter exists, AutoPaperReview is a review *database and runner*, not a reviewer. That is acceptable if documented as such; it is a product hole if users expect `autopaperreview run` to critique a new PDF.

Minimum adapter: snapshot the prompt, raw response, model identifier, and seed; validate into `ReviewIssue` / `ReviewClaim` / `DimensionScore`; refuse free-form prose as the only output. PydanticAI stays optional.

### 2. Retrieved snapshots have no citable content

`RetrievedSourceSnapshot` stores title, arXiv ID, date, and `content_kind`, but not an excerpt, abstract text, or content hash. `snapshots_to_records` then invents evidence of the form "abstract snapshot of {title}". A claim can cite `E-RW1` without anyone being able to re-read what was retrieved.

PaperQA2 and OpenScholar treat passage text as the evidence. DeepReviewer 2.0 keeps retrieved comparators in an explicit trail. Add optional `excerpt`, `excerpt_hash`, and `raw_artifact` fields; require one of them when a snapshot is used as evidence.

### 3. Locators are display strings

`ReviewIssue.location` and `EvidenceRecord.locator` are free-form (`"Methods and Results"`). DeepReviewer 2.0 stores `(page, line-span or bbox)`. OpenAIReview / reviewer2 fuzzy-match quotes to paragraphs. Without typed locators, authors cannot jump to the span and excerpt hashes cannot be checked.

This is already on the roadmap. Open experiments show it is more urgent than ObservationRecord for *auditability*.

### 4. There is no export gate

`report` succeeds on an empty package. DeepReviewer 2.0 refuses to export until schema, retrieval, and anchored-annotation budgets are met. AutoPaperReview already refuses silent bilingual fallback; it should likewise refuse a "complete" report that has claims without evidence, literature routes without snapshots, or zero issues and zero claims.

### 5. Related-work queries are not multi-specificity

SAR generates queries at several specificities. The early v0.1 generator was a title + bag-of-words template and, when `max_queries_per_perspective > 1`, appended `" 2"`. As of 0.1.4 it emits distinct narrow / mid / broad variants. It is still deterministic and LLM-free. Live query rewriting belongs in a declared retriever, not in core.

### 6. Literature output does not reach the review routes

In the synthetic DAG, `literature` and `prompt_packets` are siblings. The M3 SAR packet never receives the snapshot fixture. A grounded-review route that cannot see retrieved sources will hallucinate related work.

Packets for `kind = literature` should attach query and snapshot artifacts (or refuse `include_source_text` without them).

### 7. Packaged specialist prompts are unused

`closed_book_v1`, `metric_protocol_v1`, and `sar_grounded_review_v1` are wired. `literature_audit_v1`, `reproducibility_v1`, and `visual_qa_v1` are shipped but absent from `init` and the synthetic example. MARG's result — specialist reviewers beat a generalist — only holds if those routes actually run.

## Priority 1 — artifact-aware integrity (ResearchArena)

Zhang, Wang, Galhotra, and Cardie review 117 agent-written papers with a 9-dimension, artifact-aware protocol. Under SAR the picture is optimistic; under artifact-aware review scores drop (Claude Code −0.85, Codex −0.42, Kimi Code −0.86). Experimental rigor is the lowest dimension. Failure modes are fabricated results, underpowered experiments, and plan/execution mismatch. Reference integrity is checked against arXiv, Semantic Scholar, and Crossref. Reviewer agents get *read-only* workspace access.

This repository already cites that paper as a reason not to clone SAR's score. The missing product is the records:

| ResearchArena dimension | Current AutoPaperReview surface |
|---|---|
| Novelty / soundness / significance / clarity | Partial overlap with SAR dimensions |
| Reproducibility | Prompt only (`reproducibility_v1.md`); no structured release-package check |
| Experimental rigor | Prompt only (`metric_protocol_v1.md`); no protocol/counterexample records |
| References | Query + snapshot metadata |
| Reference integrity | None (no DOI/arXiv resolve, no "citation does not support sentence") |
| Results integrity | None (no paper-vs-`results.json` / table / log mismatch) |

Do **not** replace the seven SAR dimensions. Add an optional integrity family (`reference_integrity`, `results_integrity`, `reproducibility_attestation`) bound to artifact hashes. Workspace inspection must be read-only and must not mutate the manuscript or run cache.

The AI Scientist's own post-mortem (wrong citations, missing figures, unaudited numbers) is the same gap from the generator side.

## Priority 2 — DeepReviewer 2.0 process, not its stack

DeepReviewer 2.0's output is `Y = (R, A, P, N)`: structured report, anchored annotations, prioritized repair plan, novelty/value assessment. Stage I builds a manuscript-only claim–evidence–risk ledger and an investigation agenda. Stage II retrieves under a *matched-setting* gate (same task, dataset, metric) and writes anchors. Export is gated.

AutoPaperReview already has `R`-like fields (`ReviewClaim`, issues, `required_action`, `acceptance_gate`) and query/snapshot hooks. It is missing:

1. **Claim–evidence–risk ledger** — what the paper claims, what in-paper evidence is supposed to support it, and residual risk. This is the right precursor to ObservationRecord: an immutable pre-consensus object.
2. **Investigation agenda** — typed questions that drive literature_grounding instead of three static templates.
3. **Matched-setting novelty tags** — `supported` / `overlap` / `unclear` / `not_comparable`, with comparability recorded. A paper on a different dataset must not count as novelty overlap.
4. **Typed anchors** — page + paragraph/line or bbox, plus excerpt hash.
5. **Export budgets** — minimum anchored notes, minimum literature checks, no export if major claims lack anchors.

Do not take MinerU, PASA, or the MCP agent loop into core. Map those behind parser and retriever adapters.

## Priority 3 — review process features (optional stages)

Worth having as *stages*, not as a new framework:

- **Author rebuttal and area-chair meta-review** (AgentReview). `RouteKind.human` exists but has no handler. A human stage should pause for a JSON/Markdown drop and record the attestor.
- **Dissent-preserving consensus** (MARG + the existing roadmap). The current stage picks one representative, counts distinct `route_id`s, and *drops* groups below `min_independent_routes`. Opposition and contradiction are not first-class.
- **Review-quality eval** (ReviewEval): factuality of the review, constructiveness, unsupported claims. v0.1.6 ships the writing contract and optional first-read/quote budgets; Promptfoo stays external and deterministic checks remain the release gate. See [first-read review writing](first-read-review.md).
- **Venue checklists**, not only `VenueConclusion` labels. ICLR/NeurIPS/ARR/TMLR have different required questions (code, limitations, compute, dual submission). Bind checklist answers to evidence IDs.
- **Citation accuracy pass** (reviewer2, CiteME, CiteCheck): resolve each bibliography entry; test whether the citing sentence is supported.

Fine-tuned critic models (OpenReviewer, CycleReviewer, DeepReviewer-14B) belong in `doctor` as optional adapters. They are not a reason to change the canonical schema.

## Priority 4 — document and visual understanding

`include_source_text` and `literature_grounding` are UTF-8 text only. Real submissions are PDF or DOCX.

- Parser adapter (Docling first, GROBID as academic-PDF sidecar, PaperMage if page entities are required). Dual-parser disagreement stays as two artifacts until adjudicated.
- `visual_qa_v1.md` remains layout QA (clipping, broken tables). Scientific reading of plots, diagrams, and photos is `figure_grounded_v1`: inspect the PDF page, cite a visible mark that is not in the caption. Prompt packets now attach a sibling-PDF inventory. Rendering page PNGs into hashed artifacts is still optional.
- Quote excerpts should be hashed and checked against the parsed text.

## Priority 5 — internal quality

These are optimizations of code that already shipped.

| Issue | Why it matters |
|---|---|
| Bilingual fill is a fixture phrase table plus word substitution | Long review prose now fails instead of becoming word-salad, and unpaired Markdown/PDF is refused. Short fixture phrases can still be auto-filled. Real papers still need human or model `LocalizedText`. |
| `strengths` / `acceptance_gate` are `dict[str, list[str]]` | Inconsistent with `LocalizedText` on claims and issues. |
| `init` writes a three-stage stub | No routes, no prompts, no literature, no bilingual report. First-run UX under-sells the product. |
| `doctor` lists adapters that are not implemented | Misleading. Report "not bundled" vs "importable". |
| `excerpt_hash` is never verified | The field is ornamental. |
| Network policy is declaration-only | Documented, but command stages can still open sockets. Container/sandbox notes should be in `init` output. |
| Consensus conflicts use exact model equality | Two routes that phrase the same claim differently explode; two that disagree on severity silently pick `max(severity)`. |
| Tests are synthetic-only | No frozen real-paper fixture, no review-quality regression, no parser contract tests. |
| Independence = distinct `route_id` | Two prompts from the same model and seed are not independent reviewers. |

## What this repository should not become

Keep the [scope guard](roadmap.md#scope-guard):

- No hosted multi-tenant service or manuscript-text telemetry.
- No LangGraph / Prefect / ChatArena in core.
- No vector index as the review database.
- No bundled Tavily, OpenAlex, or PaperQA dependency.
- No fitted ICLR regression and no reproduced Spearman/AUC claims.
- No silent semantic merge of issues.
- Do not treat manuscript-only SAR scores as acceptance decisions (ResearchArena).

## Recommended slices

Order is by *auditability per unit of schema change*, not by research fashion.

| Slice | Ships | Why now |
|---|---|---|
| **A. Contract completeness** | Snapshot excerpt + hash; report export gate; literature attached to literature-route packets; honest `doctor` / `init` | Makes 0.1.x records mean what they say |
| **B. Typed locators** | Discriminated page / paragraph / quote / external-snapshot locators (already designed in the roadmap) | DeepReviewer 2.0 and reviewer2 show this is the audit boundary |
| **C. Integrity family** | Optional `reference_integrity` and `results_integrity` records; read-only workspace inspect stage; Crossref/arXiv resolve adapter | ResearchArena's actual finding |
| **D. Thin model adapter** | Optional PydanticAI (or equivalent) stage: raw response artifact → canonical records | Makes `run` able to review a new manuscript |
| **E. Observation + dissent** | Immutable observations; `ConsensusDecision` with support / oppose / abstain | Current consensus is a representative picker |
| **F. Parser + visual** | Docling adapter; rendered-page QA records | Unlocks PDF submissions without changing the database |

Slice A is the only one that should land before ObservationRecord if the goal is a usable 0.1 line. B and C are the first *scientific* upgrades. D is what users will assume already exists. E and F stay on the published roadmap.

## Honest scorecard (0.1.4)

| Capability | SAR | DeepReviewer 2.0 | ResearchArena PR | AutoPaperReview |
|---|---|---|---|---|
| Local canonical package + hash resume | No | Partial (export package) | Experiment scripts | **Yes** |
| Evidence IDs required on claims | No | Yes (anchors) | Yes (artifact check) | **Yes** |
| Venue conclusions without a fake 0–10 | ICLR 0–10 when selected | Overall judgment | ICLR 0–10 + 9 dims | **Yes** (0.1.3) |
| PDF parse + page anchors | LandingAI ADE | MinerU + page/bbox | Agent workspace | No |
| Live literature | Tavily + arXiv | PASA + matched-setting | arXiv / S2 / Crossref | Query + offline snapshot |
| Snapshot *content* as evidence | Summaries | Retrieved comparators | Lookups | Metadata only |
| Artifact / workspace inspect | No | No (manuscript-centric) | **Yes, read-only** | Prompt only |
| Export gate / coverage budget | No | **Yes** | Human meta-review | No |
| Model call in-tree | Hosted | Agent loop | CLI agents | No (by design) |
| Multi-agent social simulation | No | No | No | No (out of scope) |
| Trained critic weights | Hosted regression | Optional 7B/14B/196B | No | No (by design) |

The remaining work that *this* repository should close is the left-hand side of DeepReviewer 2.0 (anchors, ledger, matched-setting, export gate) plus the ResearchArena integrity checks, all behind the existing adapter boundary. The remaining work it should refuse is a second hosted reviewer.
