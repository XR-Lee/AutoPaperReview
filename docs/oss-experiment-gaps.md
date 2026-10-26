# Feature gaps versus stronger open review experiments

This note started as a review of AutoPaperReview `0.1.4`–`0.1.6` on 2026-08-29. The status sections below are updated to `0.1.10` (2026-10-25). It is a decision document, not a claim that this repository should become paperreview.ai, DeepReviewer, or The AI Scientist, and not a claim that AutoPaperReview matches their scores. Versions and paper claims belong to those projects; this repository has not reproduced them.

v0.1.5 implemented the first P0–P2 slice (excerpts, anchors, export gate, `execute_review`, ledger/agenda, workspace inspect, integrity, matched-setting novelty). v0.1.6 added first-read review writing. v0.1.7–v0.1.9 added the declared ICLR rating, page renders, format check, visual inspect, and related-work search. v0.1.10 added the OpenAI-compatible adapter, report-only `calibrate-eval`, `apr-iclr-cap-v2`, manuscript PDF annotation, and the locked fusion rank `forecast-k1`. Items below are marked done, partial, or still open. A recommendation that the code now implements is marked superseded as a next step.

Related documents: [Architecture](architecture.md), [Roadmap](roadmap.md), [Stanford SAR comparison](stanford-sar-comparison.md), [Open-source reference stack](oss-reference-stack.md).

## What this repository already owns

AutoPaperReview is a local-first *harness*. That is a real product, not a missing LLM wrapper.

Strengths that most generation-first reviewers still lack:

- Canonical Pydantic records and committed JSON Schema, with unknown fields rejected.
- A TOML DAG with a stable project run directory, per-stage signatures, atomic promotion, and resume.
- Deny-by-default network *declaration* (not an OS sandbox) and shell-free commands.
- Claims, dimension scores, and venue conclusions must cite evidence IDs.
- Venue conclusions stay separate from the seven dimension scores. `rate` defaults to the declared formula `apr-iclr-cap-v2` (snap onto 1/3/5/6/8/10). Opt-in `apr-fusion-v1` ranks by `forecast-k1`. Neither is a fitted ICLR regression.
- Packaged, versioned route prompts whose file hashes participate in cache signatures.
- A synthetic end-to-end example, migration, CI on CPython 3.11–3.13, and an honest SAR note.

The core calls a model only when a stage selects the bundled OpenAI-compatible adapter. It does not parse PDF or DOCX into the review schema. Live literature is the optional `related_work_search` stage (arXiv, OpenAlex, Semantic Scholar), not a bundled search package. The gaps below are what is still missing from the contract, not a plan to absorb another project's agent loop.

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

DeepReviewer 2.0's own comparison table is the most useful framing: a fluent "full review" is not the same output as an evidence-bound package with traceable anchors. AutoPaperReview already chose the second target. Much of the executable slice has landed. The work left is the holes marked open below, not a second hosted reviewer.

## Priority 0 — contract completeness

The 0.1.4 heading "the current contract is incomplete" is superseded as a description of `0.1.10`. The slice that made records executable has landed. What remains is named under each item.

### 1. Produce a review from `run` — done for one adapter

`execute_review` calls `OpenAICompatibleAdapter`, which snapshots the prompt and the raw response and validates them into canonical records. A schema failure, including an empty contribution or ledger list, retries once and then fails the stage. A missing rating dimension after anchor resolution fails the same way. Transport errors retry separately. `bilingual = true` adds a `zh-Hans` pass. `OpenAICompatibleVisionInspector` sends rendered page images. Fixture JSON and `DeterministicManuscriptAdapter` remain equal options. The synthetic example still imports `review_input.json` and does not call a model.

Still open: a provider-neutral SDK, and a seed on the model call. `doctor` lists `pydantic_ai` as `importable, not bundled` or `not installed`. It is not an adapter. That absence is no longer "the harness cannot produce a review."

### 2. Citable snapshot content — partial

`RetrievedSourceSnapshot` has optional `excerpt`, `excerpt_hash`, and `raw_artifact`. The excerpt hash must match the excerpt. The export gate can set `require_excerpt_on_snapshots`. When the excerpt is empty, `snapshots_to_records` still uses a claim of the form `{content_kind} snapshot of {title}`. A snapshot used as evidence should carry the passage. That requirement is not automatic.

### 3. Locators — partial

`Anchor` exists, with kinds `display`, `text_offset`, `quote`, `pdf_region`, `docx_paragraph`, and `external_snapshot`. The excerpt hash is checked against the excerpt. `ReviewIssue.location` and `EvidenceRecord.locator` are still free-form strings. Annotation finds a quote in the PDF text and draws a highlight; it does not store a bbox as the database. The roadmap families for table cells, OOXML, and image regions are not shipped. This is still more urgent than ObservationRecord for auditability, and it is not done.

### 4. Export gate — done

`report` can refuse an incomplete package. The synthetic example sets minimums for issues, claims, anchored issues, literature snapshots, novelty assessments, ledger, integrity, anchors, first-read notes, and quotes. The defaults on the gate are zero, so a project that does not turn the checks on can still export a thin package. The gate itself shipped in 0.1.5. The 0.1.6 writeup that said there was no gate is superseded.

### 5. Multi-specificity queries — done for the generator

As of 0.1.4, `literature_grounding` emits distinct narrow / mid / broad variants instead of appending `" 2"`. It is still deterministic. `related_work_search` adds five keyword families plus name-only lookups. Live query rewriting is not in core.

### 6. Literature on the review routes — done

In the synthetic DAG, `prompt_packets` depends on `literature` and sets `include_literature = true`. When that upstream package is present, a literature route attaches its queries and snapshots, and `include_literature = true` attaches them to the other routes as well.

### 7. Specialist prompts — done on `init`

`init` mounts `closed_book_v1`, `metric_protocol_v1`, `sar_grounded_review_v1`, `literature_audit_v1`, `reproducibility_v1`, `figure_grounded_v1`, `contribution_audit_v1`, `first_read_v1`, and `visual_qa_v1`. The synthetic example runs M0, M2, M3, M4 (literature audit), M5 (reproducibility), and M6 (first-read). It does not run the visual routes. The 0.1.6 claim that those prompts were absent from `init` is superseded.

## Priority 1 — artifact-aware integrity (ResearchArena)

Zhang, Wang, Galhotra, and Cardie review 117 agent-written papers with a 9-dimension, artifact-aware protocol. Under SAR the picture is optimistic; under artifact-aware review scores drop (Claude Code −0.85, Codex −0.42, Kimi Code −0.86). Experimental rigor is the lowest dimension. Failure modes are fabricated results, underpowered experiments, and plan/execution mismatch. Reference integrity is checked against arXiv, Semantic Scholar, and Crossref. Reviewer agents get *read-only* workspace access.

This repository already cites that paper as a reason not to clone SAR's score. The integrity family shipped in 0.1.5. The "add these records" recommendation is superseded. What is still missing is a citation-support verdict and a layout parser.

| ResearchArena dimension | AutoPaperReview at 0.1.10 |
|---|---|
| Novelty / soundness / significance / clarity | Seven SAR dimensions, plus matched-setting `novelty` tags |
| Reproducibility | `reproducibility_v1` route and `reproducibility_attestation` on `integrity` |
| Experimental rigor | `metric_protocol_v1` route. No separate protocol/counterexample record type |
| References | `literature_grounding` queries and `related_work_search` (arXiv, OpenAlex, Semantic Scholar) |
| Reference integrity | `reference_integrity` records whether a snapshot has a DOI or arXiv id. It does not call a registry. `related_work_search` does the live metadata lookup. There is no "this sentence is not supported by the cited paper" verdict |
| Results integrity | `IntegrityKind.results_integrity` compares one manuscript accuracy percentage with `accuracy` in a workspace `results.json`. It is not a general table parser |

Do **not** replace the seven SAR dimensions. Workspace inspection stays read-only.

The AI Scientist's own post-mortem (wrong citations, missing figures, unaudited numbers) is the same gap from the generator side.

## Priority 2 — DeepReviewer 2.0 process, not its stack

DeepReviewer 2.0's output is `Y = (R, A, P, N)`: structured report, anchored annotations, prioritized repair plan, novelty/value assessment. Stage I builds a manuscript-only claim–evidence–risk ledger and an investigation agenda. Stage II retrieves under a *matched-setting* gate (same task, dataset, metric) and writes anchors. Export is gated.

AutoPaperReview already has `R`-like fields (`ReviewClaim`, issues, `required_action`, `acceptance_gate`) and query/snapshot hooks. Status of the 0.1.6 gap list:

1. **Claim–evidence–risk ledger** — done. The `ledger` stage is in the synthetic DAG and in `init`.
2. **Investigation agenda** — done. The `agenda` stage feeds `literature_grounding`.
3. **Matched-setting novelty tags** — done. Tags are `supported`, `overlap`, `unclear`, and `not_comparable`, with task, dataset, and metric flags.
4. **Typed anchors** — partial. See Priority 0 item 3. `Anchor` shipped; free-form `location` remains; a bbox is not the database.
5. **Export budgets** — done as optional `report` gate flags. Defaults are off.

Do not take MinerU, PASA, or the MCP agent loop into core. Map those behind parser and retriever adapters. The "missing ledger / agenda / novelty / export gate" recommendation is superseded. The anchor hole is not.

## Priority 3 — review process features (optional stages)

Worth having as *stages*, not as a new framework:

- **Author rebuttal and area-chair meta-review** (AgentReview). Still open. `RouteKind.human` has no handler. `holistic_mode = "rebuttal"` is a simulated discussion inside one model call (`rebuttal-k2`). It does not pause for an author drop or record an attestor. That mode does not close this item.
- **Dissent-preserving consensus** (MARG + the existing roadmap). Still open. The stage picks one representative, counts distinct `route_id`s, and drops groups below `min_independent_routes`. Opposition and contradiction are not first-class.
- **Review-quality eval** (ReviewEval). Partial. v0.1.6 ships the writing contract and optional first-read/quote budgets. `calibrate-eval` compares a declared score with public human scores and does not fit the formula. Promptfoo stays external. See [first-read review writing](first-read-review.md) and [public calibration eval](public-calibration.md).
- **Venue checklists**. Partial. `format_check` pins `iclr2027` and `aaai2027` (page limits, template markers, anonymity metadata). It is not the full ICLR/NeurIPS/ARR/TMLR question list (code, limitations, compute, dual submission).
- **Citation accuracy pass** (reviewer2, CiteME, CiteCheck). Partial. `related_work_search` resolves bibliography entries by title, arXiv id, and DOI, and can cap a missing pre-deadline neighbour. It does not test whether the citing sentence is supported.

Fine-tuned critic models (OpenReviewer, CycleReviewer, DeepReviewer-14B) belong behind an adapter, not in the canonical schema. `doctor` does not list those weights.

## Priority 4 — document and visual understanding

`include_source_text` is UTF-8 text. `calibrate-eval` can extract text with `pdftotext`, `pypdfium2`, or `pypdf` when one of those is installed. That extraction is not a layout parser, and the core schema still does not ingest PDF or DOCX structure.

- Parser adapter (Docling first, GROBID as academic-PDF sidecar, PaperMage if page entities are required). Still open. Dual-parser disagreement stays as two artifacts until adjudicated.
- Page images and visual routes. Shipped, so "rendering page PNGs is still optional" is superseded as a gap. `render_pages` writes hashed PNGs, including an optional supplement as `S1`, `S2`, ... . `visual_qa_v1` is layout QA. `figure_grounded_v1` requires a visible mark that is not the caption. `visual_inspect` accepts findings that cite the page-image hash. `init` mounts both visual routes.
- Quote excerpts. The stored excerpt hash must match the excerpt. The chat adapter can accept a manuscript span after Unicode normalization, ligatures, a line-break hyphen, collapsed whitespace, or a fuzzy word window at similarity 0.90. Annotation searches the PDF text, including small wording gaps. None of that is a check against a parsed layout tree.

## Priority 5 — internal quality

These are optimizations of code that already shipped.

| Issue | Status at 0.1.10 |
|---|---|
| Bilingual fill is a fixture phrase table plus word substitution | Partially fixed in 0.1.6. Long review prose fails closed, and unpaired Markdown/PDF is refused. Short fixture phrases can still be auto-filled. Real papers still need human or model `LocalizedText`. |
| `strengths` / `acceptance_gate` are `dict[str, list[str]]` | Still open. Inconsistent with `LocalizedText` on claims and issues. |
| `init` writes a three-stage stub | Superseded as a stub. `init` writes the specialist routes and prompts, ledger, agenda, workspace inspect, a deterministic `execute_review`, integrity, consensus, and a report. It does not add `literature_grounding`, `novelty`, or `rate`. |
| `doctor` lists adapters that are not implemented | Superseded. It reports `importable, not bundled` or `not installed`. |
| `excerpt_hash` is never verified | Superseded for the hash itself: a stored hash that does not match the excerpt is rejected. Checking the excerpt against parsed PDF structure is still open (Priority 4). |
| Network policy is declaration-only | Still open. Command stages can open sockets. The policy is not an OS sandbox. |
| Consensus conflicts use exact model equality | Still open. Two routes that phrase the same claim differently stay split; severity takes the max. |
| Tests are synthetic-only | Still the policy. No frozen real-paper fixture. `calibrate-eval` scores public papers only in a gitignored cache. |
| Independence = distinct `route_id` | Still open. Two prompts from the same model and seed are not independent reviewers. The forecast panel's three seats are one model call, not three independence groups. |

## What this repository should not become

Keep the [scope guard](roadmap.md#scope-guard):

- No hosted multi-tenant service or manuscript-text telemetry.
- No LangGraph / Prefect / ChatArena in core.
- No vector index as the review database.
- No bundled Tavily, OpenAlex, or PaperQA dependency. `related_work_search` calls arXiv, OpenAlex, and Semantic Scholar over HTTP when the stage asks for live retrieval. Those clients are not third-party packages.
- No fitted ICLR regression and no reproduced Spearman/AUC claims.
- No silent semantic merge of issues.
- Do not treat manuscript-only SAR scores as acceptance decisions (ResearchArena).

## Recommended slices

Order is by *auditability per unit of schema change*, not by research fashion.

| Slice | Ships | Status at 0.1.10 |
|---|---|---|
| **A. Contract completeness** | Snapshot excerpt + hash; report export gate; literature attached to literature-route packets; honest `doctor` / `init` | Done in 0.1.5. An empty snapshot excerpt can still become a title line (Priority 0 item 2). |
| **B. Typed locators** | Discriminated page / paragraph / quote / external-snapshot locators | Partial. `Anchor` covers several of those kinds. Free-form `location` remains. Table-cell, OOXML, and image-region locators are not shipped. |
| **C. Integrity family** | Optional `reference_integrity` and `results_integrity` records; read-only workspace inspect; Crossref/arXiv resolve | Partial. The records and `workspace_inspect` shipped in 0.1.5. arXiv, OpenAlex, and Semantic Scholar metadata search shipped in 0.1.9. Crossref and a citation-support verdict did not. |
| **D. Thin model adapter** | Optional stage: raw response artifact → canonical records | Done as `OpenAICompatibleAdapter` (#13), not as PydanticAI. The "no model call" recommendation is superseded. |
| **E. Observation + dissent** | Immutable observations; `ConsensusDecision` with support / oppose / abstain | Open. Consensus is still a representative picker. |
| **F. Parser + visual** | Docling adapter; rendered-page QA records | Partial. `render_pages`, `format_check`, `visual_inspect`, and `annotate` shipped. No Docling (or other layout) adapter. |

The 0.1.4 advice that only slice A should land before ObservationRecord is superseded: A and D have landed, and parts of B, C, and F have landed with them. The schema milestone that is still next is E. The open holes inside B, C, and F stay on the [roadmap](roadmap.md).

## Honest scorecard (0.1.10)

| Capability | SAR | DeepReviewer 2.0 | ResearchArena PR | AutoPaperReview |
|---|---|---|---|---|
| Local canonical package + hash resume | No | Partial (export package) | Experiment scripts | **Yes** |
| Evidence IDs required on claims | No | Yes (anchors) | Yes (artifact check) | **Yes** |
| Venue conclusions without a fitted 0–10 | ICLR 0–10 when selected | Overall judgment | ICLR 0–10 + 9 dims | Venue conclusions (0.1.3). `apr-iclr-cap-v2` snaps a declared bin. `forecast-k1` is an opt-in model rank, not a fitted regression |
| PDF parse + page anchors | LandingAI ADE | MinerU + page/bbox | Agent workspace | Page PNGs, format check, quote annotation. No layout parser. `Anchor` can store `pdf_region` |
| Live literature | Tavily + arXiv | PASA + matched-setting | arXiv / S2 / Crossref | Optional arXiv, OpenAlex, and Semantic Scholar. Not Tavily or Crossref. A failed search does not lower the score |
| Snapshot *content* as evidence | Summaries | Retrieved comparators | Lookups | Optional excerpt and hash. Empty excerpt falls back to a title line |
| Artifact / workspace inspect | No | No (manuscript-centric) | **Yes, read-only** | `workspace_inspect` and `integrity` |
| Export gate / coverage budget | No | **Yes** | Human meta-review | Optional budgets on `report` |
| Model call in-tree | Hosted | Agent loop | CLI agents | Optional OpenAI-compatible adapter. The synthetic example uses fixture JSON |
| Simulated reviewers | No | No | No | One call can simulate a forecast, rebuttal, matched, or mixed-role panel. That is the fusion rank, not a ChatArena loop |
| Trained critic weights | Hosted regression | Optional 7B/14B/196B | No | No (by design) |

The 0.1.4 scorecard marked PDF anchors, live literature, snapshot content, workspace inspect, the export gate, and in-tree model calls as absent. Those cells are updated above. The work this repository should still close is the open locator, citation-support, parser, and dissent items. The work it should refuse is a second hosted reviewer.
