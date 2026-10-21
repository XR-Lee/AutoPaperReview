# Related-work search

Novelty and prior-work scores have to follow a search the reviewer runs, not the manuscript bibliography and not one ad-hoc query. `related_work_search` does that search before any score is written.

The stage does not call a model and does not write `OverallScore`.

## What it reads

From labeled lines in the manuscript:

- task, inputs, outputs, assumptions, setting
- technique, extra modality, sub-problem, adjacent tasks
- numbered contributions
- experiment baselines, including privileged inputs and different-task baselines
- the references section, so a candidate can be marked cited or not

A formulation is complete only when task, inputs, outputs, assumptions, and setting are all present. An unlabeled manuscript does not produce outbound queries.

## Queries

Five families, one keyword query each:

| Family | What it looks for |
|---|---|
| `same_problem` | Same task and inputs or outputs |
| `same_technique` | Same technique on that task |
| `alternative_formulation` | Methods that already accept the extra modality as input |
| `standard_baselines` | Baselines and benchmarks for the sub-problem |
| `adjacent_tasks` | Neighboring tasks that delimit scope |

A query may contain only generic tokens from those fields, plus a small glue list (`baseline`, `input`, `survey`, and so on). The submission title, abstract sentences, and contribution prose are not queries. The HTTP client checks the search string again and refuses the request if it is not on that allowlist.

Public titles are not copied out of the manuscript. The host may search those titles itself and put the provider response in the findings file.

## Retrieval

`mode = "live"` calls the arXiv API, OpenAlex, and Semantic Scholar for metadata only. It requires `requires_network = true` and a project network policy of `metadata_only` or `allow`. HTTP 429 or 503 stops that provider and is logged as `rate_limited`. A connection error is logged as `offline`. A read that exceeds the timeout is logged as `timeout`. Other providers still run. If every provider fails, or none are available, `status=degraded`, the search invents no papers, and it does not apply a cap. If at least one provider returns records and another fails, `status=partial`. The judgment lists `failed_providers`, and the report says the search may be incomplete.

`mode = "agent"` is the host-in-the-loop path, shaped like `AgentFindingsInspector`: the host runs the queries, then drops `findings.json`. A candidate is kept only when `provider_payload` parses as arXiv Atom, OpenAlex, Semantic Scholar, or Crossref JSON and the parsed id and title match the drop. A title with no payload, or a payload whose title differs, is dropped and logged.

`mode = "off"` writes the queries and does not retrieve.

## Candidate record

Each verified paper has a title, venue, year, id, link, one-line summary, whether the bibliography cites it, the date relation to the venue submission deadline (`prior`, `concurrent`, `unknown`), a tier (`must-compare`, `should-discuss`, `optional`), and a reason in English and Simplified Chinese.

A paper published after `submission_deadline` is concurrent. It can be discussed. It is not a required baseline. A missing date is not treated as required either.

## Experiments and caps

Required must-compare papers that are absent from the experiment baselines become missing-baseline gaps.

A baseline given privileged information (for example ground-truth scale) or one that solves a different task becomes an unfair-baseline gap.

Clause advice, aligned with `sar_bands_v1` once that rubric is on the branch that owns it:

| Situation | Clauses |
|---|---|
| Verified must-compare work missing from the table | `pw-2`, `or-4` |
| Required neighbors are the comparison, and it is fair | `pw-8` |
| Neighbors are present, but the comparison is unfair or off-task | `pw-6`, `es-4` |
| Privileged input or a different task | `es-4` and `unfair_baseline` |

Cap signals use coefficient set `apr-iclr-cap-v1` and do not themselves write a score:

- `unfair_baseline`: venue cap **5**, `experimental_soundness` ceiling 4. The search emits a blocking, decision-relevant issue with that `fatal_class`. `active_caps()` already applies the cap. A verified candidate is not required. Privileged inputs such as ground-truth scale, or a baseline that solves a different task, are enough when they are in the manuscript's own experiment lines.
- `missing_closest_neighbor`: venue cap **6**, `prior_work_contextualization` ceiling 2, `originality` ceiling 4. `FatalClass.missing_closest_neighbor` is on `apr-iclr-cap-v1`, so this flaw does not share the cap-5 bin rule with `unfair_baseline`.

Cap 6 is the next ICLR bin above the unfair-baseline cap. `preview_venue_bin` is only a separation check. `rate_venue()` is the only writer of `OverallScore`. When both caps are active, the effective cap is the minimum (5).

`missing_closest_neighbor` requires a verified candidate dated on or before the venue submission deadline. Concurrent work (after the deadline) and an undated record stay in the discussion. They do not become required baselines and they do not cite a cap.

`unfair_baseline` is emitted on a completed search and always cites the manuscript anchor `E-manuscript-baselines`, a quote of the paper's own Experiments and References lines. That anchor has to resolve: the issue's evidence id is that record, and the quote is the paper's baseline text rather than an empty or unlabeled placeholder. It also cites a verified prior candidate when the baseline name matches one. A missing-neighbor cap cites the same manuscript anchor plus each verified prior candidate (external id, venue, year, and date relation), and it is dropped if it has no verified prior candidate.

### Partial outage

`status=partial` means at least one provider logged success and at least one logged `offline`, `rate_limited`, `http_error`, or `timeout`. `failed_providers` lists the providers that failed. The Related Work section says the search may be incomplete, in English and in Simplified Chinese.

A partial search may emit `missing_closest_neighbor` (cap 6) when a provider that succeeded verified a must-compare paper dated on or before the deadline and that paper is absent from the baselines. It does not emit `unfair_baseline`. A provider that only failed contributes no candidate and cannot lower the venue cap further. `rate` drops a related-work cap on a partial package unless it is that missing-neighbor cap and every cited candidate was verified by a successful provider.

### Degraded search

If every requested provider fails, a provider times out before returning records, or zero providers are available, `status=degraded`. The judgment can still discuss manuscript baseline lines, and it lists whichever providers failed. `build_cap_signals` returns no signals, the stage writes no fatal issues, and `rate` ignores any related-work cap. The score does not drop because the search failed. A failed provider never creates or strengthens a cap by itself.

### Scoring order

`novelty` reads the comparison of baselines and closest neighbours before it assigns tags. `rate` reads the same comparison before it uses the originality band. When a config contains `related_work_search`, both `novelty` and `rate` must depend on it, directly or through an earlier stage. Clause ids stay `pw-2` / `pw-4` / `pw-6` / `pw-8` / `es-4` / `or-4`. Copy a clause onto a dimension score only when the reviewer accepts that advice. The ceiling inside `rate` is what changes the bin.

The report section is bilingual (`en` / `zh-Hans`) and includes suggested weakness and rebuttal sentences. The stage also writes `related_work.md`.

## Venue deadlines

Deadline fields live in sidecar profiles so they do not collide with the format profiles on the rating and visual branches:

- `profiles/iclr2027.deadlines.json`: abstract 2026-09-18, submission 2026-09-25 (Anywhere on Earth), from the ICLR 2027 dates page.
- `profiles/aaai2027.deadlines.json`: abstract 2026-07-21, submission 2026-07-28 (Anywhere on Earth), from the AAAI-27 submission instructions. Concurrent checks use the full-paper deadline.

`load_venue_profile(key)` returns the sidecar. If `profiles/<key>.json` exists, deadline fields are filled in with `setdefault` and format keys are left alone. After the format profiles land, either keep the sidecars or copy `abstract_deadline`, `submission_deadline`, and `deadline_timezone` into them.

## Run the synthetic example

The example is fictional. It is not a submission.

```bash
PYTHONPATH=src python3 -m autopaperreview validate examples/synthetic/related_work/review.toml
PYTHONPATH=src python3 -m autopaperreview run examples/synthetic/related_work/review.toml
```

Read `examples/synthetic/related_work/.runs/*/stages/related_work/related_work.md`.

Tests:

```bash
PYTHONPATH=src python3 -m unittest tests.test_related_work
```
