# SAR dimension bands (sar_bands_v2)

Scores are the clause, not a free float. Allowed scores are 2, 4, 6, 8, and 10. The `rate` stage reads these clause ids. It does not fit an ICLR regression. Weights and the gate are unchanged.

Set `rubric_id` to `sar_bands_v2`. The pipeline sets `score` from the clause id. The clause sentences are unchanged from the previous table. Missing uncertainty with ablations present remains band 6.

Write the evidence before the clause. Each dimension needs:

1. `supporting`: at least one item `{"excerpt","location","observable"}`. The excerpt is an exact manuscript substring. The observable is a token of the chosen band.
2. `opposing`: at least one item whose excerpt is a different passage. Its observable is from a different band. For band 6 it must be a band-8 token that is still missing.
3. `move_up`: the observable change that would make the next higher band true (at least 40 characters).
4. `move_down`: the observable change that would make the next lower band true (at least 40 characters, and not the same sentence as `move_up`).
5. Only then `clause_id` and `rationale`.

A band with no supporting or opposing excerpt is rejected. Band 6 is rejected when the support does not cite a band-6 observable or the opposition does not cite a missing band-8 observable.

Fatal caps are unchanged. A leak, an unfair baseline, or an unsupported headline still caps the venue bin after these bands.

## claims_supported

Stop at the first matching clause.

- `cs-2` (2) Headline claim contradicted by the paper's own table or figure. Observables: `table_contradicts_headline`.
- `cs-4` (4) Headline uses a proxy, a copied baseline, or a figure that answers a different question. Observables: `proxy_or_different_question`, `weaker_own_baseline`.
- `cs-6` (6) The matched internal comparison supports a qualified claim; a secondary claim is overstated. Observables: `qualified_main_claim`, `secondary_overclaim`.
- `cs-8` (8) The claim a reader remembers is the claim the matched comparison supports, including a supported negative result. Observables: `memorable_sentence_matches_comparison`, `supported_negative_result`.
- `cs-10` (10) Pre-registered claim, matched setting, uncertainty, and the display agrees. Observables: `claim_fixed_before_numbers`, `uncertainty_on_claim`.

## experimental_soundness

Stop at the first matching clause.

- `es-2` (2) Protocol is not one experiment, or leakage decides the result. Observables: `leakage_decides_result`, `not_an_experiment`.
- `es-4` (4) Protocol is defined, and a confound or an unfair baseline sits on the decision comparison. Observables: `named_confound`, `unfair_baseline_on_decision`.
- `es-6` (6) Ablations exist; uncertainty is missing, or external numbers are copied beside an otherwise clean internal run. Observables: `ablations_present`, `copied_external_number`, `uncertainty_missing`.
- `es-8` (8) Matched controls, uncertainty, and the decision comparison is in-stack. Observables: `matched_controls_in_stack`, `uncertainty_reported`.
- `es-10` (10) The es-8 conditions plus a released protocol card. Observables: `protocol_card_released`.

## originality

Stop at the first matching clause.

- `or-2` (2) Restatement of a cited method. Observables: `no_step_beyond_citation`.
- `or-4` (4) A module swap on a public baseline. Observables: `module_swap`.
- `or-6` (6) A specific combination whose pieces already exist. Observables: `existing_pieces_combined`.
- `or-8` (8) A task or control that changes what a matched comparison means. Observables: `new_task_or_control`.
- `or-10` (10) A problem definition others can adopt without this method. Observables: `problem_definition_reusable`.

## importance_of_research_question

Stop at the first matching clause.

- `im-2` (2) No stated user of the result. Observables: `no_stated_user`.
- `im-4` (4) A real task whose result is too small to change practice even if true. Observables: `too_small_to_change_practice`.
- `im-6` (6) A community would use a confirmed version. Observables: `community_would_use`.
- `im-8` (8) The decision result, if it holds, changes a benchmark people already run. Observables: `benchmark_ranking_or_protocol`.
- `im-10` (10) The question blocks a line of work and the paper makes it measurable. Observables: `blocks_a_line_and_measurable`.

## writing_clarity

Stop at the first matching clause.

- `wc-2` (2) A reader cannot reconstruct the protocol, or a lead display contradicts the text. Observables: `lead_display_contradicts`, `protocol_not_rebuildable`.
- `wc-4` (4) Protocol is reconstructable and a lead figure or equation is broken or misleading. Observables: `lead_display_broken`.
- `wc-6` (6) The main argument is checkable; one display is easy to misread. Observables: `one_display_easy_to_misread`.
- `wc-8` (8) A first-time reader can check the decision comparison from the main text. Observables: `decision_comparison_in_main_text`.
- `wc-10` (10) The display, the notation, and the limitations agree. Observables: `display_notation_limitations_agree`.

## community_value

Stop at the first matching clause.

- `cv-2` (2) Nothing reusable is stated. Observables: `nothing_reusable`.
- `cv-4` (4) A protocol or split would be useful and is not released. Observables: `release_only_promised`.
- `cv-6` (6) A reusable task split or negative lesson is stated with enough detail to repeat. Observables: `repeat_recipe_in_text`.
- `cv-8` (8) Code, split, or protocol card is in the submission and matches the experiment. Observables: `artifact_matches_experiment`.
- `cv-10` (10) The release is a benchmark or tool others can run without the authors. Observables: `runnable_benchmark_or_tool`.

## prior_work_contextualization

Stop at the first matching clause.

- `pw-2` (2) The decision comparison's prior work is missing. Observables: `decision_prior_missing`.
- `pw-4` (4) Prior work is named and not used as the comparison the claim depends on. Observables: `named_not_in_table`.
- `pw-6` (6) The right neighbors are cited; one of them is overstated or under-compared. Observables: `neighbor_wrong_setting`.
- `pw-8` (8) Matched-setting neighbors are the comparison the claim uses. Observables: `neighbors_are_the_rows`.
- `pw-10` (10) Every decision comparison names task, data, and metric against a real neighbor. Observables: `task_data_metric_named`.
