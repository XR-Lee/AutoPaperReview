# SAR dimension bands (sar_bands_v1)

Scores are the clause, not a free float. Allowed scores are 2, 4, 6, 8, and 10. The `rate` stage reads these clause ids. It does not fit an ICLR regression.

Set `rubric_id` to `sar_bands_v1` and `score` to the number in the clause id.

## claims_supported

- `cs-2` (2) Headline claim contradicted by the paper's own table or figure.
- `cs-4` (4) Headline uses a proxy, a copied baseline, or a figure that answers a different question.
- `cs-6` (6) The matched internal comparison supports a qualified claim; a secondary claim is overstated.
- `cs-8` (8) The claim a reader remembers is the claim the matched comparison supports, including a supported negative result.
- `cs-10` (10) Pre-registered claim, matched setting, uncertainty, and the display agrees.

## experimental_soundness

- `es-2` (2) Protocol is not one experiment, or leakage decides the result.
- `es-4` (4) Protocol is defined, and a confound or an unfair baseline sits on the decision comparison.
- `es-6` (6) Ablations exist; uncertainty is missing, or external numbers are copied beside an otherwise clean internal run.
- `es-8` (8) Matched controls, uncertainty, and the decision comparison is in-stack.
- `es-10` (10) The es-8 conditions plus a released protocol card.

## originality

- `or-2` (2) Restatement of a cited method.
- `or-4` (4) A module swap on a public baseline.
- `or-6` (6) A specific combination whose pieces already exist.
- `or-8` (8) A task or control that changes what a matched comparison means.
- `or-10` (10) A problem definition others can adopt without this method.

## importance_of_research_question

- `im-2` (2) No stated user of the result.
- `im-4` (4) A real task whose result is too small to change practice even if true.
- `im-6` (6) A community would use a confirmed version.
- `im-8` (8) The decision result, if it holds, changes a benchmark people already run.
- `im-10` (10) The question blocks a line of work and the paper makes it measurable.

## writing_clarity

- `wc-2` (2) A reader cannot reconstruct the protocol, or a lead display contradicts the text.
- `wc-4` (4) Protocol is reconstructable and a lead figure or equation is broken or misleading.
- `wc-6` (6) The main argument is checkable; one display is easy to misread.
- `wc-8` (8) A first-time reader can check the decision comparison from the main text.
- `wc-10` (10) The display, the notation, and the limitations agree.

## community_value

- `cv-2` (2) Nothing reusable is stated.
- `cv-4` (4) A protocol or split would be useful and is not released.
- `cv-6` (6) A reusable task split or negative lesson is stated with enough detail to repeat.
- `cv-8` (8) Code, split, or protocol card is in the submission and matches the experiment.
- `cv-10` (10) The release is a benchmark or tool others can run without the authors.

## prior_work_contextualization

- `pw-2` (2) The decision comparison's prior work is missing.
- `pw-4` (4) Prior work is named and not used as the comparison the claim depends on.
- `pw-6` (6) The right neighbors are cited; one of them is overstated or under-compared.
- `pw-8` (8) Matched-setting neighbors are the comparison the claim uses.
- `pw-10` (10) Every decision comparison names task, data, and metric against a real neighbor.
