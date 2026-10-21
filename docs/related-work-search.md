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

`mode = "live"` calls the arXiv API, OpenAlex, and Semantic Scholar for metadata only. It requires `requires_network = true` and a project network policy of `metadata_only` or `allow`. HTTP 429 or 503 stops that provider and is logged as `rate_limited`. A connection error is logged as `offline`. Other providers still run. A failed search does not invent papers and does not apply a missing-neighbor cap.

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

Cap signals use coefficient set `apr-iclr-cap-v1` and do not write a score:

- `unfair_baseline`: venue cap **5**, `experimental_soundness` ceiling 4. This matches `CLASS_CAP` / `CLASS_CEILING` in the rating stage.
- `missing_closest_neighbor`: venue cap **6**, `prior_work_contextualization` ceiling 2, `originality` ceiling 4.

Cap 6 is the next ICLR bin above the unfair-baseline cap, so the two flaws do not share a cap. `preview_venue_bin` applies the same weights, `min(claims, soundness) + 1` gate, and snap onto 1/3/5/6/8/10. Papers that share a missing-neighbor cap can still land in different bins when their other scores differ. When both caps are active, the effective cap is the minimum (5).

### Wiring after the rating stage lands

Do not import `rating.py` from this module. When that stage is on `main`:

1. Turn each `CapSignal` into a blocking, decision-relevant issue (or the record type `active_caps()` already reads).
2. Set `fatal_class = unfair_baseline` for that signal. No rating change is required; the cap is already 5.
3. Add `FatalClass.missing_closest_neighbor` with `CLASS_CAP` 6 and ceilings `prior_work_contextualization = 2`, `originality = 4`. Leaving it at cap 5 would force it to share a bin rule with `unfair_baseline`.
4. Copy `clause_id` onto the dimension score when the reviewer accepts the advice. The clause table must stay `pw-2` / `pw-4` / `pw-6` / `pw-8` / `es-4` / `or-4`.
5. Keep `rate_venue()` as the only writer of `OverallScore`.

`preview_venue_bin` is a separation check for this module. Delete the call sites that treat it as a score once `rate_venue()` consumes the signals.

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
