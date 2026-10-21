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
- method names in related work, baselines, and the bibliography, for name-only lookups

A formulation is complete only when task, inputs, outputs, assumptions, and setting are all present. An unlabeled manuscript does not produce outbound queries.

A section ends at the next real heading: a known heading name, or a short title-case line that is not a labeled field and does not end in sentence punctuation. Body prose after that heading stays out of the earlier block.

## Queries

Five families, one keyword query each:

| Family | What it looks for |
|---|---|
| `same_problem` | Same task and inputs or outputs |
| `same_technique` | Same technique on that task |
| `alternative_formulation` | Methods that already accept the extra modality as input |
| `standard_baselines` | Baselines and benchmarks for the sub-problem |
| `adjacent_tasks` | Neighboring tasks that delimit scope |

A keyword query may contain only generic tokens from those fields, plus a small glue list (`baseline`, `input`, `survey`, and so on). The submission title, abstract sentences, and contribution prose are not keyword queries. The HTTP client checks the search string again and refuses the request if it is not on that allowlist.

The manuscript's own method names are an extra family. `named_methods_from_manuscript` collects baseline names, Related Work or Prior Work labels, and bibliography titles, and drops the submission title. Order is: methods explicitly called closest or most related (always kept), then names in the experiment and baseline tables, then related-work sentences nearest the paper's own method, then names mentioned more often. The default cap is 15, set with `named_method_limit`. Each kept name becomes a name-only query (`family = named_method`, ids `Q-named-N`). Those strings may leave the keyword allowlist. They still cannot be the submission title or an abstract sentence of eight or more words. This is how a closest method the manuscript already cites is retrieved and checked.

## Retrieval

`mode = "live"` calls the arXiv API, OpenAlex, and Semantic Scholar for metadata only. It requires `requires_network = true` and a project network policy of `metadata_only` or `allow`. HTTP 429 is a transport retry and does not stop the rest of that provider's queries. Other providers retry twice and cap `Retry-After` at two seconds. Semantic Scholar retries five times with exponential backoff and jitter, honours `Retry-After` up to 60 seconds, and waits about one second between requests. If `S2_API_KEY` is set, that value is sent as `x-api-key` and is not copied into the URL, the cache key, or the log. HTTP 503 still stops that provider and is logged as `rate_limited`. A connection error is logged as `offline`. A read that exceeds the timeout is logged as `timeout`. Other providers still run. A successful response is cached by the SHA-256 of the provider name and the query text, and hits are sorted by normalized title and external id, so the same queries produce the same candidate set. The same paper from two providers is merged when they share an arXiv id, DOI, OpenAlex id, or Semantic Scholar id, or when the normalized title and year match. If every provider fails, or none are available, `status=degraded`, the search invents no papers, and it does not apply a cap. If at least one provider returns records and another fails, `status=partial`. The judgment lists `failed_providers`, and the report says the search may be incomplete.

`mode = "agent"` is the host-in-the-loop path, shaped like `AgentFindingsInspector`: the host runs the queries, then drops `findings.json`. A candidate is kept only when `provider_payload` parses as arXiv Atom, OpenAlex, Semantic Scholar, or Crossref JSON and the parsed id and title match the drop. A title with no payload, or a payload whose title differs, is dropped and logged.

`mode = "off"` writes the queries and does not retrieve.

## Candidate record

Each verified paper has a title, venue, year, id, link, one-line summary, whether the bibliography cites it, the date relation to the venue submission deadline (`prior`, `concurrent`, `unknown`), a relevance score, a tier (`must-compare`, `should-discuss`, `optional`), a confirmation flag and one-line reason, and a reason in English and Simplified Chinese.

Citation is an arXiv id, a DOI, or a normalized title. Token overlap is not a citation. A shorter title matches a longer one only when it is at least four tokens and a contiguous phrase inside the longer title.

A paper is must-compare only when the title and abstract cover the formulated task and also cover the technique or the setting. A survey, review, dataset, benchmark, or generator is should-discuss at most, even when the abstract names the same inputs. Mentioning an input modality does not mean the paper already solves the task. Relevance is `0.5 * task + 0.3 * technique + 0.2 * setting`. Modality adds nothing.

Before a retrieval cap can cite a must-compare paper, the judging agent confirms it. Live retrieval and confirmation compose: the search still runs, then the stage writes `confirmation_packet.json` with the provisional must-compare candidates and the manuscript baselines.

Confirmation can come from any of these, and a file does not replace the live results:

- `confirm_must_compare`, a hook that returns a boolean and a one-line reason
- `confirmation_path`, or a `confirmations` list on the findings drop, with `external_id` or `title`, `confirmed`, and `reason`
- `confirmation_adapter` (`module:attribute`). An object with `confirm_related_work(packet)` is called directly. Otherwise, when the OpenAI-compatible adapter from the model-execution stage is configured (`autopaperreview.model_exec:OpenAICompatibleAdapter`), that session answers the packet. A findings file that already contains `confirmations` is used instead of calling the model again.

The hook overrides the file. Items the judge rejects become should-discuss. After confirmation, at most three required neighbours remain, highest relevance first, then normalized title. The rest are discussion only. The missing-neighbor cap cites only those confirmed neighbours.

If none of those sources is supplied and provisional must-compare papers remain, `confirmation` is `awaiting_confirmation`. Those papers stay must-compare. They are not silently demoted, and the retrieval cap does not cite them. `require_related_work_confirmation = true` on the report stage fails the export gate until a confirmation is supplied.

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

`missing_closest_neighbor` requires a confirmed candidate dated on or before the venue submission deadline and verified by a provider that succeeded. Concurrent work (after the deadline) and an undated record stay in the discussion. They do not become required baselines and they do not cite a cap. The cap cites at most the three confirmed neighbours kept by the relevance cap.

`unfair_baseline` is manuscript evidence. It fires on a completed or partial search and always cites the manuscript anchor `E-manuscript-baselines`. The quote is the labeled experiment lines only (decision metric, compared baselines, privileged baselines, different-task baselines), whitespace collapsed to one line and cut at 1200 characters. An empty or unlabeled placeholder does not resolve. The report renders that quote as a Markdown blockquote so the bilingual gap detector skips it. The issue also carries a bilingual `FirstReadNote` (`paper_said`, `explanation`, `quote`, intent `issue`, `blocking`) so `require_first_read_on_major` can pass. It cites a verified prior candidate when the baseline name matches one. A missing-neighbor cap cites the same manuscript anchor plus each confirmed prior candidate (external id, venue, year, and date relation), attaches the same kind of first-read note, and is dropped if it has no confirmed prior candidate.

### Partial outage

`status=partial` means at least one provider logged success and at least one logged `offline`, `rate_limited`, `http_error`, or `timeout`. `failed_providers` lists the providers that failed. The Related Work section says the search may be incomplete, in English and in Simplified Chinese.

A partial search may emit `missing_closest_neighbor` (cap 6) when a provider that succeeded verified a confirmed must-compare paper dated on or before the deadline and that paper is absent from the baselines. It also emits `unfair_baseline` (cap 5) when the manuscript anchor resolves. Only the retrieval cap depends on provider success. A provider that only failed contributes no candidate and cannot create or strengthen that retrieval cap. `rate` keeps `unfair_baseline` on a partial package when the anchor resolves, and it keeps `missing_closest_neighbor` only when every cited candidate was confirmed and verified by a successful provider.

### Degraded search

If every requested provider fails, a provider times out before returning records, or zero providers are available, `status=degraded`. The judgment can still discuss manuscript baseline lines, and it lists whichever providers failed. `build_cap_signals` returns no signals, the stage writes no fatal issues, and `rate` ignores any related-work cap. The score does not drop because the search failed. A failed provider never creates or strengthens a cap by itself.

### Scoring order

`novelty` reads the comparison of baselines and closest neighbours before it assigns tags. `rate` reads the same comparison before it uses the originality band. When a config contains `related_work_search`, both `novelty` and `rate` must depend on it, directly or through an earlier stage. Clause ids stay `pw-2` / `pw-4` / `pw-6` / `pw-8` / `es-4` / `or-4`. Copy a clause onto a dimension score only when the reviewer accepts that advice. The ceiling inside `rate` is what changes the bin.

The report section is bilingual (`en` / `zh-Hans`) and includes suggested weakness and rebuttal sentences. The dimension table shows the assigned score and clause beside the after-ceiling band when `overall_score.metadata.bands_after_ceiling` has that dimension. The stage also writes `related_work.md`.

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
