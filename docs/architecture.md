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
| `OverallScore` | Optional overall score with required method/provenance. Absent until `rate` writes `declared_formula` / `apr-iclr-cap-v2`. Never an implicit LLM 0–10 and never a fitted regression. |
| `RelatedWorkQuery` / `RetrievedSourceSnapshot` | Multi-perspective search queries and retrieved source snapshots (query IDs, excerpt, arXiv ID, date, abstract vs full-text, optional task/dataset/metric). |
| `Anchor` | Typed locator (display, text offset, quote, PDF region, DOCX paragraph, external snapshot) with optional excerpt hash. |
| `LedgerClaim` / `AgendaQuestion` | Manuscript-only claim–evidence–risk ledger and investigation agenda. |
| `IntegrityRecord` | Artifact-aware reference, results, and reproducibility checks. |
| `NoveltyAssessment` | Matched-setting novelty tag. Overlap evidence requires the same task, dataset, and metric. |
| Venue deadline profile | Abstract and submission deadlines used to flag post-deadline papers as concurrent. Sidecar JSON under `profiles/*.deadlines.json`, merged onto a format profile when one exists. |
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
3. Compute a signature from framework version, handler type/version, that stage's id, type, dependencies, and parameters, dependency artifact hashes, handler-specific material, and network policy. The manuscript hash is included only when the stage reads the manuscript. Other inputs (a PDF, a supplement, a snapshot fixture, a command input file) are hashed by the handler that opens them.
4. Reuse an earlier result only when its signature matches and every output still exists with the recorded hash.
5. Otherwise execute in a unique `.attempts` directory.
6. Validate that every declared output exists inside that attempt directory.
7. Atomically promote the completed attempt to `stages/<stage-id>`, preserving the previous completed directory if promotion fails.
8. Record success or failure in `run_manifest.json` through an atomic file replacement.

The run directory name is the project id. Source and configuration hashes are stored on the manifest and do not name the directory. An export-gate change updates the report stage's parameters and reuses ingest, render, search, and judgments when those stages' own inputs are unchanged. A manuscript edit reruns stages that read the manuscript, and downstream stages whose dependency hashes changed.

## Built-in stages

| Stage | Current behavior |
|---|---|
| `ingest` | Records the source and optionally copies it into the run. An optional `supplement` path is copied beside it and described in `supplement.json`. |
| `command` | Executes an argument array with `shell=False` and an explicit environment allowlist; records stdout/stderr, logical placeholder paths, declared outputs, environment-key names, and hashes of command input files, then imports declared outputs. |
| `prompt_packet` | Builds versioned local prompt packets for declared review routes. It does not call a model. Every packet prepends the first-read writing contract. Literature routes can attach query and snapshot excerpts. When a sibling `manuscript.pdf` exists, packets tell the reviewer to inspect every numbered figure on the PDF page; caption paraphrase is not inspection. |
| `import_issues` | Migrates legacy issue/package JSON into the canonical package. |
| `execute_review` | Calls a declared `module:attribute` adapter and validates a `ReviewPackage`. Bundled adapters are fixture import, deterministic manuscript checks, `AgentJudgmentAdapter` (a JSON drop that must echo prompt-packet digests), and `OpenAICompatibleAdapter` (OpenAI-compatible chat). |
| `ledger` / `agenda` | Builds a claim–evidence–risk ledger and investigation questions from local text. When the paper lists numbered contributions, those items are the ledger units; agenda asks evidence completeness and a matched-setting comparator per item. |
| `workspace_inspect` | Read-only inventory of declared workspace paths. Refuses path escape. |
| `integrity` | Reference, results, and reproducibility records from snapshots and workspace files. |
| `novelty` | Matched-setting novelty tags. A snapshot is not overlap evidence unless task, dataset, and metric match. When a related-work judgment is in the dependencies, novelty reads baselines against closest neighbours before it assigns tags. In a config that also has `related_work_search`, this stage must depend on that search. |
| `related_work_search` | Derives the problem formulation, builds keyword-only queries across five families plus name-only lookups for methods the manuscript names (closest and baseline names first, including hyphenated, camel-case, and digit-containing names, default limit 15), and resolves bibliography entries by exact title and by arXiv id or DOI. Numeric markers (`[n]`, `n.`, `(n)`), bracket ranges, and author-year cites map back to entries. Wrapped and year-only reference lines stay with their entry. arXiv title lookups use `ti:`, arXiv ids are batched with `id_list`, and OpenAlex uses a sanitized title filter or a DOI filter. Cited references in related work, the experiments, and the method are queried first, up to `bibliography_lookup_limit` (default 60). Entries without a title do not use a slot. Cited references are candidates with `cited=true` and can be must-compare. A paper is kept only when a provider payload resolves its id and title. The same paper from two providers is merged on a shared external id, or on a title with case, punctuation, hyphens, and whitespace removed when the years differ by at most one. Must-compare requires task overlap and technique or setting overlap, never includes surveys, reviews, datasets, benchmarks, or generators, and keeps at most three neighbours a confirmation hook, JSON file, or explicitly configured confirmation adapter accepted. The judge's reason is kept verbatim. The `execute_review` adapter is not bound for confirmation. Live retrieval and that confirmation compose. With no confirmation the judgment is `awaiting_confirmation`, the stage writes `confirmation_packet.json`, and the retrieval cap does not fire. A completed or partial search emits blocking issues with a bilingual first-read note: `missing_closest_neighbor` (cap 6) cites each confirmed prior candidate from a provider that succeeded plus the manuscript baseline anchor, and `unfair_baseline` (cap 5) cites that anchor even when no verified candidate matches the privileged or different-task baseline. A partial outage lists the failed providers and notes that the search may be incomplete. Concurrent papers and a degraded search (every provider failed, timed out, or no provider) emit no cap. A failed provider never creates or strengthens a retrieval cap. HTTP 429 is retried. Semantic Scholar with `S2_API_KEY` backs off, honours `Retry-After`, and paces requests. Without a key, a remaining 429 skips that provider inside a short time budget and the status lists the skip. Successful metadata and HTTP 400 responses are cached on disk under the project directory by provider and query hash. OpenAlex title filters drop characters that make the filter invalid. Writes `related_work.md`. |
| `consensus` | Groups by explicit `consensus_key` or exact normalized category/title/location, then selects one representative record. |
| `literature_grounding` | Generates multi-perspective, multi-specificity related-work queries from local text and optional agenda questions, plus one same-problem and one related-technique query per listed contribution. Optionally attaches a snapshot fixture or a declared retriever. Live retrieval requires a network declaration. |
| `render_pages` | Optional `visual` extra. Rasterizes each main PDF page to `page-NNN.png` (`page_id` `1`, `2`, ...). An optional `supplement` path, or the file copied by `ingest`, is a second page set: `supplement-NNN.png` with page ids `S1`, `S2`, .... Fails closed when a visual route is enabled and the sibling PDF is missing. |
| `format_check` | Deterministic venue checklist from the selected profile (`iclr2027` or `aaai2027`): main-text page limit, total page limit, US Letter, margin floor, anonymity metadata and page-1 email, template marker, font embedding, raster size, blank caption pages. Supplement pages are excluded from those page limits and listed on a passing `FMT-supplement` check. Desk-reject classes set `format_desk_reject`. Other failures set a `writing_clarity` clause ceiling. |
| `visual_inspect` | Adapter contract over every rendered page, including supplement ids. `AgentFindingsInspector` reads a findings drop produced after the pages were opened. `OpenAICompatibleVisionInspector` sends each page PNG to an OpenAI-compatible chat endpoint. `HostedVisionInspector` POSTs page PNGs only under `network_policy = allow` with an optional API key; a missing key sends nothing. Findings carry the page-image hash, a bbox, a non-caption visible mark, an optional `page_id`, and an optional severity, fatal class, or clause. |
| `rate` | Pure function. Reads the related-work comparison of baselines and closest neighbours when that judgment is on the package, then applies anchored bands, fatal-flaw caps, and a floor snap onto 1/3/5/6/8/10 under `apr-iclr-cap-v2`. The venue bin is the largest bin that does not exceed the capped raw score. `missing_closest_neighbor` caps at 6. A related-work retrieval cap is kept when the search status is `complete`. On `partial`, `unfair_baseline` is kept when the manuscript anchor resolves, and `missing_closest_neighbor` is kept only when every cited candidate was confirmed by a provider that succeeded. `degraded` and `not_run` drop every related-work cap. Records `cap_ids` and the weighted/gate terms. Does not call a model. In a config that also has `related_work_search`, this stage must depend on that search. |
| `calibrate_batch` | Orders several packages by that formula. Neighbors in the same bin need `same_bin_justified`. Different active caps cannot share a bin. |
| `calibrate-eval` (CLI) | Report-only comparison of `apr-iclr-cap-v2` to public official ratings. The generated run places `related_work_search` before `rate` and can select `OpenAICompatibleAdapter`. It is not a stage and it does not fit weights, caps, or bins. `scripts/select_public_calibration.py` draws the seeded 100-paper sample into a gitignored cache. See [public calibration eval](public-calibration.md). |
| `report` | Emits canonical JSON, Markdown with SAR-shaped sections when present, and a small summary. Reports open with a first-read how-to-read note and inline quotes. The dimension table shows the assigned score and clause beside the after-ceiling band when rating has run. Evidence citations such as `[E2, E3]` link each id to the Evidence Index. Copies `review_report.md` next to `review.toml`. With the optional `pdf` extra, also writes `review_report.pdf` with the same internal links. When that extra's PyMuPDF is installed and a sibling manuscript PDF exists, the stage also writes `annotated_manuscript.pdf`: role-colored highlights, margin tags, comment popups, and an evidence-index page. Quote search ignores margin line numbers. The highlight colour follows the Strengths, Weaknesses, and Questions sections of the review markdown. Quotes that are not in the PDF become page-level notes. `autopaperreview annotate` rebuilds that file from a finished package without re-running the model. Pass `--review` to point it at a markdown file. Optional export-gate budgets refuse an unauditable package, including optional first-read/quote and figure-citation budgets. `require_related_work_confirmation` refuses export while related-work confirmation is `awaiting_confirmation`. `bilingual = true` or `languages = ["en", "zh-Hans"]` writes both languages into one Markdown file after filling missing translations, then refuses export if the package or Markdown still has unpaired English or Chinese prose. A declared formula replaces the "no overall score" sentence and the PDF footer. |

## Model execution

`execute_review` can run the bundled chat adapter instead of an external JSON drop. The model makes the judgment. The pipeline checks it before the stage is promoted.

The adapter reads the `prompt_packet` files already produced for the run. It calls an OpenAI-compatible `POST /chat/completions` endpoint once per judgment:

| Judgment | What the model returns | What the pipeline enforces |
|---|---|---|
| `contributions` | At least one listed contribution, with an exact excerpt | The excerpt must occur in the manuscript or a retrieved snapshot. Otherwise the item is rejected. An empty list is a schema failure. |
| `ledger` | At least one ledger claim, plus agenda questions | Same excerpt rule. An agenda item that cites a rejected claim is rejected. An empty ledger list is a schema failure. |
| `dimensions` | All seven SAR dimensions, each with a `sar_bands_v1` clause id and an excerpt | The numeric score is the clause. If any excerpt does not resolve, the stage retries once with that unresolved-anchor error. If any of the seven is still missing, `execute_review` fails. |
| `fatal` | `fatal_class` for issue ids the pipeline assigned (`I-<contribution id>`) | An unknown issue id is rejected. `rate` applies the cap. |
| `zh-Hans` | Simplified Chinese for the accepted issue, ledger, and dimension fields | Optional. Set `bilingual = true`. The same schema retry and stored hashes apply. Strings must contain Chinese characters and match the accepted ids. |
| visual page | `VisualFinding` records, with the page image attached for a multimodal model | The page-image hash and bbox must match the rendered PNG. A caption restatement fails validation. |

`OpenAICompatibleVisionInspector` is the visual path. It uses the same client from `visual_inspect`, after `render_pages`, and reads the visual route's prompt packet.

Invalid JSON or a schema mismatch is retried once. The retry appends the validation error. A second failure fails the stage. An empty contribution list or an empty ledger list is that kind of schema failure. The adapter does not substitute deterministic output.

Connection errors, timeouts, HTTP 429, and HTTP 5xx are retried up to two times (three attempts) with a 0.5s then 1s backoff. Any other HTTP 4xx fails on the first response. Transport retries do not consume the schema retry. Each transport try is stored with its own SHA-256.

The model-call timeout defaults to 7200 seconds. The same default covers `execute_review`, visual inspection, hosted vision, related-work confirmation, and the CVPR tier chat and command backends. A `timeout_seconds` written in the stage config replaces that default, including a smaller one. The socket timeout is silence between reads. Streaming bytes renew it, so a call that keeps sending tokens can run longer than the budget. A gap with no bytes ends the call. There is no upper cap.

A later `run` skips a stage that already succeeded with the same signature. `contributions` and `ledger` are judgments inside one `execute_review` stage. If a later judgment times out, the finished judgment stays in the failed attempt directory. The next run replays that stored exchange when the request bytes match, and does not call the endpoint again. `--force` calls the endpoint again. `calibrate-eval cvpr-tier run` leaves a paper alone when its answer file is already non-empty.

Every request and response is stored under `stages/<id>/model/` as the bytes that were sent and received. Each file's SHA-256 is listed on the stage record in `model_exchanges`. The API key is not part of those bytes. A successful stage is reused when its signature matches, so the endpoint is not called again. The signature includes the packet hashes, model name, endpoint, and `bilingual`. It does not include the API key value. Replacing the key in the environment does not change the signature and does not rerun a cached stage.

Egress is separate from the project network policy:

- `127.0.0.0/8`, `::1`, and `localhost` (including `*.localhost`) are loopback. A hostname that is not an address is resolved, and it is loopback only when every resolved address is loopback. Loopback is allowed while `network_policy` is `deny`. A key is optional.
- Resolution failure, or any non-loopback address, is remote. The stage must set `allow_remote = true`, the project must set `network_policy = "allow"`, and `api_key_env` must be set in the environment. The run manifest then sets `manuscript_left_machine` to true.
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
bilingual = false
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

A remote endpoint adds `allow_remote = true`, `requires_network = true`, and `network_policy = "allow"`. Set `bilingual = true` to add the `zh-Hans` pass so a bilingual report can export. `rate` still computes the venue bin. The model does not emit the overall score.

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

The bundled chat adapter snapshots each raw request and response inside the run directory. It sends manuscript text off the machine only for a non-loopback URL when `allow_remote` is set and the project network policy is `allow`. A hostname that resolves only to loopback stays on the machine. That remote fact is stored on the run manifest as `manuscript_left_machine`. API keys stay in the environment and are redacted from stored exchanges and error strings. The key value is not part of the stage signature.

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
- [Branching](branching.md)
