# SAR dimension bands (sar_bands_v2)

Scores are the clause, not a free float. Allowed scores are 2, 4, 6, 8, and 10. The `rate` stage reads these clause ids. It does not fit an ICLR regression. Weights and the gate are unchanged.

Set `rubric_id` to `sar_bands_v2`. The pipeline sets `score` from the clause id.

Write the evidence before the clause. Each dimension needs:

1. `supporting`: at least one item `{"excerpt","location","observable"}`. The excerpt is an exact manuscript substring. The observable is a token of the chosen band.
2. `opposing`: at least one item whose excerpt is a different passage. Its observable is from a different band. For band 6 it must be a band-8 token that is still missing.
3. `move_up`: the observable change that would make the next higher band true (at least 40 characters).
4. `move_down`: the observable change that would make the next lower band true (at least 40 characters, and not the same sentence as `move_up`).
5. Only then `clause_id` and `rationale`.

A band with no supporting or opposing excerpt is rejected. Band 6 is rejected when the support does not cite a band-6 observable or the opposition does not cite a missing band-8 observable. Do not use 6 as the score for a typical paper.

Fatal caps are unchanged. A leak, an unfair baseline, or an unsupported headline still caps the venue bin after these bands.

## claims_supported

Stop at the first matching clause.

- `cs-2` (2) A table or figure in the paper contradicts the headline sentence. Observables: `table_contradicts_headline`.
- `cs-4` (4) The headline is a proxy, answers a different question than the display, or rests on a weaker variant the authors built. Observables: `proxy_or_different_question`, `weaker_own_baseline`.
- `cs-6` (6) The main comparison supports a qualified sentence, and a second sentence claims a new setting, significance, or a general win. Observables: `qualified_main_claim`, `secondary_overclaim`.
- `cs-8` (8) The sentence a reader remembers is the comparison that was run, including a supported negative result. Observables: `memorable_sentence_matches_comparison`, `supported_negative_result`.
- `cs-10` (10) The claim was fixed before the numbers, the setting matches, and the display includes uncertainty. Observables: `claim_fixed_before_numbers`, `uncertainty_on_claim`.

## experimental_soundness

Stop at the first matching clause. A missing interval or a single seed is band 4 only when the paper also lacks an ablation that moves the decision metric and lacks a copied external number. When that ablation or copied number is present and a band-8 condition is still missing, the band is 6.

- `es-2` (2) No stated protocol produces the number, or a leak is what creates the headline result. Observables: `not_an_experiment`, `leakage_decides_result`.
- `es-4` (4) The protocol is stated, but no ablation removes the claimed piece and moves the decision metric, and no external number is copied. A needed baseline is not rerun, the run is a single seed without an interval, an ablation misses the claim, or a confound is named. Observables: `needed_baseline_not_rerun`, `single_seed_no_interval`, `ablation_misses_claim`, `named_confound`.
- `es-6` (6) An ablation removes the claimed piece and the decision metric moves, or one external number is copied, and a band-8 condition is still missing. Observables: `ablation_moves_decision_metric`, `copied_external_number`.
- `es-8` (8) Matched baselines are rerun in the same stack, the decision metric has an interval or multiple seeds, and an ablation isolates the claim. Observables: `baselines_rerun_in_stack`, `interval_or_multiseed`, `ablation_isolates_claim`.
- `es-10` (10) The es-8 conditions hold and the submission includes a protocol card or script listing seeds, split, and the decision metric. Observables: `protocol_card_released`.

## originality

- `or-2` (2) The method section adds no step that is not already in a cited method. Observables: `no_step_beyond_citation`.
- `or-4` (4) The new piece is one module swapped on a public pipeline. Observables: `module_swap`.
- `or-6` (6) Existing pieces are combined, and an ablation shows that removing one changes the decision metric, without changing what the comparison measures. Observables: `combination_ablation_hurts`.
- `or-8` (8) A new task, control, or objective forces baselines to be adapted, so the comparison measures something new. Observables: `new_task_or_control`.
- `or-10` (10) The problem statement can be reused by someone who does not adopt the method. Observables: `problem_definition_reusable`.

## importance_of_research_question

- `im-2` (2) The paper never names a person or community who would use the result. Observables: `no_stated_user`.
- `im-4` (4) The task is real, and the reported result stays on a toy or synthetic set, or the paper calls the gain too small to change a decision. Observables: `toy_or_marginal`.
- `im-6` (6) A reader in the area would rerun a confirmed version, and the paper does not show an existing public benchmark changing rank or protocol. Observables: `reader_would_rerun`.
- `im-8` (8) If the result holds, a benchmark people already run changes its ranking or its protocol. Observables: `benchmark_ranking_or_protocol`.
- `im-10` (10) The question blocks a line of work, and the paper makes it measurable by someone else. Observables: `blocks_a_line_and_measurable`.

## writing_clarity

- `wc-2` (2) A reader cannot rebuild the protocol from the text, or a lead display contradicts the text. Observables: `protocol_not_rebuildable`, `lead_display_contradicts`.
- `wc-4` (4) The protocol is rebuildable, and a lead figure or equation is broken or uses a different quantity than the text. Observables: `lead_display_broken`.
- `wc-6` (6) The decision comparison is checkable, and exactly one display is easy to misread as a stronger result. Observables: `one_display_easy_to_misread`.
- `wc-8` (8) A first-time reader can check the decision comparison from the main text, and no display overstates it. Observables: `decision_comparison_in_main_text`.
- `wc-10` (10) The display, the notation, and the limitations use the same claim. Observables: `display_notation_limitations_agree`.

## community_value

- `cv-2` (2) The paper states nothing another group could reuse. Observables: `nothing_reusable`.
- `cv-4` (4) A split, protocol, or negative result would be reusable and is only promised, not specified. Observables: `release_only_promised`.
- `cv-6` (6) The paper writes a split, hyperparameters, or a negative finding with enough detail to repeat, and attaches no archive. Observables: `repeat_recipe_in_text`.
- `cv-8` (8) Code, a split, or a protocol card is in the submission and matches the experiment that was run. Observables: `artifact_matches_experiment`.
- `cv-10` (10) The release is a benchmark or tool another group can run without the authors. Observables: `runnable_benchmark_or_tool`.

## prior_work_contextualization

- `pw-2` (2) The method the decision comparison depends on is neither cited nor used as a baseline. Observables: `decision_prior_missing`.
- `pw-4` (4) Neighbors are named and are not rows in the decision table the claim uses. Observables: `named_not_in_table`.
- `pw-6` (6) The decision table includes the relevant neighbors, and one of them is the wrong setting or is only cited. Observables: `neighbor_wrong_setting`.
- `pw-8` (8) The matched-setting neighbors are the rows the claim's comparison uses. Observables: `neighbors_are_the_rows`.
- `pw-10` (10) Every decision comparison states task, data, and metric against a real neighbor. Observables: `task_data_metric_named`.
