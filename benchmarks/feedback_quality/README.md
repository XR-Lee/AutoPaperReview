# Paired synthetic feedback benchmark

The benchmark contains 24 purpose-written scenarios covering experimental protocols, estimands, decision-time information, algorithmic claims, uncertainty and measurement. Each has two four-section vignettes. One paragraph changes to introduce a specific failure or supply the safeguard that prevents it. The two documents share a title. Each reviewer call receives **one** vignette in a fresh session. The model sees only the text in its input file. Keep the answer manifest, paired counterpart and grading materials out of reviewer inputs.

From the repository root, use a work directory outside version control.

```sh
python3 -m benchmarks.feedback_quality build --out "$WORK/dataset"
```

A model runner can iterate over `manifest.json`'s `input_order` and send each `inputs/<item_id>.txt` using the same review task in each arm. Freeze the compared prompts, input cap, review projection, generation budget and selected scenario ordinals before examining review outcomes. In synthetic studies, section labels stand in for locations in a short excerpt. For a small pilot, `build --limit N` selects the first N complete pairs.

## Blind grading

Write one JSONL file per arm, with `item_id` and `review` (a nonempty string containing the review feedback). Keep the reviewer's diagnosis, evidence and remedy together in a common rendering across arms. For a successful empty findings list, supply an explicit empty-findings statement. Represent failures using `item_id` and `error`, or omit them. The packet generator counts missing reviews separately. It accepts a contiguous registered subset of the full dataset through optional `--first` and `--last` ordinals, inclusive.

```sh
python3 -m benchmarks.feedback_quality packet --dataset "$WORK/dataset" \
  --arm baseline="$WORK/baseline.jsonl" --arm candidate="$WORK/candidate.jsonl" \
  --out "$WORK/packet"
```

Give the adjudicator `packet.jsonl`, `rubric.txt` and the `labels.jsonl` template. Keep `key.json` with the experiment operator. Every shuffled packet has the vignette, review and case-specific answer key. Adjudicators can see the evidence needed to check the allegation, while arm names, variant labels and item identifiers are withheld. Stylistic differences between review arms may remain recognizable. The assessor should record independent judgments before unblinding.

To measure what an author can read within a fixed word budget, add `--review-word-limit N` to the packet command. Choose the budget before grading. The adjudicator sees the original formatting through the last included word and grades that prefix independently. Whitespace separates words, so list markers and quotations also use part of the budget. Limited packets use grading IDs specific to their reading budget and blinded texts. The private key records each complete review's word count and digest. The score file records the limit and how many reviews it shortened in each arm.

In the template, mark `target_raised`, `localized` and `actionable` as booleans. For a target allegation, put the decisive review excerpts in `support`. If several comments raise the same mechanism, choose one comment that satisfies the most criteria, preferring a located comment when tied. A lone section number earns no location credit. A recommendation to add more experiments earns no repair credit unless it specifies how to resolve this target defect. On a repaired input, a positive grade for raising the target records a false alarm. Acknowledging the existing safeguard does not raise the target.

```sh
python3 -m benchmarks.feedback_quality score --packet "$WORK/packet" \
  --labels "$WORK/decisions.jsonl" --baseline baseline --out "$WORK/metrics.json"
```

## Endpoints and uncertainty

The primary endpoint is the proportion of scheduled scenario pairs where one review diagnoses, locates and repairs the injected defect and the matched review avoids alleging that defect after repair. A missing review in either variant fails this scheduled endpoint.

Separate rates report detection, location, action, combined location and action, targeted specificity on repaired controls, complete-pair coverage, and complete-pair discrimination. Each marginal rate uses scenarios with an available review on the relevant variant. Each complete-pair rate requires both variants. Between-arm differences keep only scenarios with a defined value in both arms. The output includes individual scenario indicators, raw denominators, discordant counts, missing cases and input/label hashes.

Marginal binomial intervals invert the equal-tailed Clopper–Pearson test at 95%. For each paired difference, the procedure subtracts the two discordant scenario proportions and combines their 97.5% exact intervals with a Bonferroni bound. This conservative procedure retains uncertainty when every observed pair is tied. Each scenario is one unit even if both reviews contain multiple comments. Intervals illustrate sampling uncertainty under an exchangeable-scenario model. The scenarios are purpose-written, so coverage on real submissions needs separate evidence. The control checks specificity for the single designated defect, and gives no estimate of the precision of unrelated criticisms. The toy manuscripts also omit the context length, literature, figures and open-ended judgments found in full papers.
